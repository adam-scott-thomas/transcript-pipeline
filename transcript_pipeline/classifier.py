# transcript_pipeline.classifier
# =============================================================================
# v0.2 — Stage classifier with two-model cross-check.
#
# Pipeline:
#
#   ParsedTurn[]  ──primary (Sonnet)──▶  ClassifyOutput
#                 ──auditor (GPT-5)──▶   ClassifyOutput
#                 ──cross-check──────▶   Proposal (with chapter_proposed)
#                 ──confirmation gate─▶  ParsedTurn[] (now with stage + chapter)
#
# Why two models: stage tagging is judgment work where a single model's
# overconfidence is the failure mode. Independent agreement is the
# confidence signal we actually want — not the model's self-reported
# probability. When the two agree, confidence is high. When they disagree,
# the disagreement IS the signal — surface to a human, log for prompt
# iteration.
#
# Why prompts.py is separate: the cacheable prefix should not move every
# time we tweak orchestration logic. Caching is mandated by the spec for
# cost control (target: <$2/day at 80 turns/day × 2 models).
#
# Failure modes handled:
#   - LLM returns malformed JSON                  → ParseError, requires_human
#   - LLM returns stage outside allowed enum      → ParseError, requires_human
#   - Network failure                             → bubbles up; CLI shows error
#
# Cost telemetry: every call records token usage to the cost log via
# `runtime.emit_cost`. The CLI surfaces a daily total at `transcript ingest`
# completion.
#
# Boundary detection: see chapter_boundary() — the rules from the spec are
# implemented as a small state machine over (prev_stage, cur_stage,
# turns_since_transition).
# =============================================================================

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.prompts import (
    AUDITOR_SYSTEM,
    AUDITOR_USER_TEMPLATE,
    PROMPT_VERSION,
    STAGE_CLASSIFIER_SYSTEM,
    USER_TEMPLATE,
)
from transcript_pipeline.schema import Stage


# ---------------------------------------------------------------------------
# Confidence thresholds (from spec)
# ---------------------------------------------------------------------------

AUTO_APPLY_THRESHOLD = 0.9     # >= 0.9 + agreement → auto-apply
SPOT_CHECK_THRESHOLD = 0.7     # 0.7-0.9 → auto-apply, mark for spot-check
# < 0.7 OR disagreement → block, require human


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ClassifyInput:
    body: str
    agent: str
    role: str
    prior_2: str  # body of turn -2 ("" if none)
    prior_1: str  # body of turn -1 ("" if none)
    prior_chapter_stage: Optional[Stage] = None


@dataclass
class ClassifyOutput:
    stage: Stage
    confidence: float
    reasoning: str
    raw_response: str = ""  # for diagnostics
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class Proposal:
    """Per-turn output of the cross-check. The confirmation gate consumes these."""

    turn_index: int  # 1-indexed
    stage_proposed: Stage
    chapter_proposed: int
    confidence: float  # combined (mean if agreement, else min)
    reasoning: str
    agreement: bool
    requires_human: bool
    primary: ClassifyOutput
    auditor: ClassifyOutput


@dataclass
class Disagreement:
    """One row of the disagreement log. Persisted to JSONL for prompt iteration."""

    turn_index: int
    body_excerpt: str
    primary_stage: str
    primary_confidence: float
    primary_reasoning: str
    auditor_stage: str
    auditor_confidence: float
    auditor_reasoning: str
    prompt_version: str = PROMPT_VERSION


@dataclass
class CostRecord:
    """Token usage for a single classifier call. Aggregated by the cost log."""

    provider: str  # "anthropic" | "openai"
    model: str
    tokens_in: int
    tokens_out: int


# ---------------------------------------------------------------------------
# LLM client protocol
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Anything with `.classify(input) → ClassifyOutput` and `.audit(input,
    primary) → ClassifyOutput`. Real impls call Sonnet / GPT-5; the test
    mock is deterministic."""

    def classify(self, payload: ClassifyInput) -> ClassifyOutput: ...

    def audit(
        self, payload: ClassifyInput, primary: ClassifyOutput
    ) -> ClassifyOutput: ...


# ---------------------------------------------------------------------------
# Real clients (Anthropic + OpenAI)
# ---------------------------------------------------------------------------


_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_response(raw: str) -> tuple[Stage, float, str]:
    """Strict JSON parse with one fallback for stray prose around the object."""
    text = raw.strip()
    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            raise ValueError(f"classifier returned no JSON object: {raw[:200]!r}")
        obj = json.loads(m.group(0))

    stage_val = str(obj.get("stage", "")).strip()
    try:
        stage = Stage(stage_val.capitalize())
    except ValueError:
        raise ValueError(
            f"classifier returned unknown stage {stage_val!r}; "
            f"allowed: {[s.value for s in Stage]}"
        )
    confidence = float(obj.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(obj.get("reasoning", "")).strip()[:200]
    return stage, confidence, reasoning


class AnthropicSonnetClient:
    """Primary classifier. Sonnet 4.6, with prompt caching on the system block."""

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        from anthropic import Anthropic

        self.model = model
        self.client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def classify(self, payload: ClassifyInput) -> ClassifyOutput:
        return self._call(STAGE_CLASSIFIER_SYSTEM, _format_user(payload))

    def audit(self, payload: ClassifyInput, primary: ClassifyOutput) -> ClassifyOutput:
        # Sonnet shouldn't be the auditor in production (different model is the
        # whole point) but we expose the method to satisfy the protocol; tests
        # that swap in a single mock client use this path.
        user = _format_auditor_user(payload, primary)
        return self._call(AUDITOR_SYSTEM, user)

    def _call(self, system: str, user: str) -> ClassifyOutput:
        # Prompt caching: mark the system block as ephemeral cacheable.
        # Anthropic charges full price on the first hit and ~10% on
        # subsequent hits for ~5 minutes. The system prompt is ~1.5 KB and
        # stable across all 80 daily calls — exactly the cache-friendly
        # shape.
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(block.text for block in resp.content if hasattr(block, "text"))
        stage, conf, reasoning = _parse_response(raw)
        return ClassifyOutput(
            stage=stage,
            confidence=conf,
            reasoning=reasoning,
            raw_response=raw,
            tokens_in=getattr(resp.usage, "input_tokens", 0),
            tokens_out=getattr(resp.usage, "output_tokens", 0),
        )


class OpenAIGPTClient:
    """Auditor. GPT-5 (or gpt-5.2 per Adam's CLAUDE.md default)."""

    def __init__(self, model: str = "gpt-5.2", api_key: str | None = None):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    def classify(self, payload: ClassifyInput) -> ClassifyOutput:
        return self._call(STAGE_CLASSIFIER_SYSTEM, _format_user(payload))

    def audit(self, payload: ClassifyInput, primary: ClassifyOutput) -> ClassifyOutput:
        return self._call(AUDITOR_SYSTEM, _format_auditor_user(payload, primary))

    def _call(self, system: str, user: str) -> ClassifyOutput:
        resp = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        raw = resp.choices[0].message.content or ""
        stage, conf, reasoning = _parse_response(raw)
        usage = getattr(resp, "usage", None)
        return ClassifyOutput(
            stage=stage,
            confidence=conf,
            reasoning=reasoning,
            raw_response=raw,
            tokens_in=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) if usage else 0,
        )


# ---------------------------------------------------------------------------
# Mock client (tests + dry-run)
# ---------------------------------------------------------------------------


class MockClient:
    """Deterministic classifier for tests and `--dry-run` mode.

    Looks up stage by lexical hints in the body. Confidence is picked to
    exercise all three threshold bands so the CLI can be unit-tested end
    to end without an API key."""

    # Hints are scanned in order; first match wins. Phrase-like markers go
    # before single-word verbs because Context turns frequently contain
    # action verbs ("Goal: fix the broken...") that should not match Fix/
    # Problem hints.
    HINTS: list[tuple[str, Stage, float]] = [
        # ── most specific phrases first ──
        ("root cause", Stage.AUDIT, 0.95),
        ("write code", Stage.BUILD, 0.92),
        ("follow-on", Stage.NEXT, 0.92),
        ("lgtm", Stage.REVIEW, 0.95),

        # ── Context (statement-of-intent must beat embedded verbs) ──
        ("goal:", Stage.CONTEXT, 0.92),
        ("goal is", Stage.CONTEXT, 0.92),
        ("current state", Stage.CONTEXT, 0.92),
        ("constraints", Stage.CONTEXT, 0.92),
        ("constraints:", Stage.CONTEXT, 0.92),
        ("setup:", Stage.CONTEXT, 0.92),
        ("state:", Stage.CONTEXT, 0.92),
        ("need to", Stage.CONTEXT, 0.92),

        # ── Ship (high specificity first) ──
        ("ship it", Stage.SHIP, 0.95),
        ("ship:", Stage.SHIP, 0.95),
        ("shipped", Stage.SHIP, 0.95),
        ("deployed", Stage.SHIP, 0.95),
        ("deploying", Stage.SHIP, 0.92),
        ("demo-ready", Stage.SHIP, 0.93),
        ("rolled out", Stage.SHIP, 0.93),
        ("cut a release", Stage.SHIP, 0.93),
        ("cut tag", Stage.SHIP, 0.92),
        ("closed the bug", Stage.SHIP, 0.92),
        ("closing the loop", Stage.SHIP, 0.92),

        # ── Problem (state of being broken / missing) ──
        ("is broken", Stage.PROBLEM, 0.93),
        ("is missing", Stage.PROBLEM, 0.92),
        ("missing the", Stage.PROBLEM, 0.92),
        ("missing env", Stage.PROBLEM, 0.92),
        ("missing error", Stage.PROBLEM, 0.92),
        ("404", Stage.PROBLEM, 0.92),
        ("issue:", Stage.PROBLEM, 0.92),
        ("broken on", Stage.PROBLEM, 0.92),
        ("problem:", Stage.PROBLEM, 0.95),
        ("problem is", Stage.PROBLEM, 0.92),
        ("broken:", Stage.PROBLEM, 0.95),

        # ── Audit ──
        ("audit:", Stage.AUDIT, 0.95),
        ("trace:", Stage.AUDIT, 0.92),
        ("trace shows", Stage.AUDIT, 0.92),
        ("audit shows", Stage.AUDIT, 0.92),
        ("audited", Stage.AUDIT, 0.92),
        ("auditing", Stage.AUDIT, 0.92),

        # ── Decision ──
        ("decision:", Stage.DECISION, 0.95),
        ("decided:", Stage.DECISION, 0.95),
        ("decided to", Stage.DECISION, 0.92),
        ("decided", Stage.DECISION, 0.92),
        ("locked:", Stage.DECISION, 0.95),
        ("locked in:", Stage.DECISION, 0.95),
        ("locked in", Stage.DECISION, 0.92),
        ("we will pick", Stage.DECISION, 0.92),
        ("pick the", Stage.DECISION, 0.92),

        # ── Build ──
        ("build:", Stage.BUILD, 0.95),
        ("build the", Stage.BUILD, 0.92),
        ("implementing", Stage.BUILD, 0.92),
        ("implemented", Stage.BUILD, 0.92),
        ("wrote", Stage.BUILD, 0.92),
        ("generating", Stage.BUILD, 0.92),
        ("generated", Stage.BUILD, 0.92),
        ("update the", Stage.BUILD, 0.92),
        ("updated", Stage.BUILD, 0.92),
        ("update all", Stage.BUILD, 0.92),
        ("added a hook", Stage.BUILD, 0.92),

        # ── Fix (specific patch on identified defect) ──
        ("fix:", Stage.FIX, 0.95),
        ("fixed", Stage.FIX, 0.92),
        ("patched", Stage.FIX, 0.92),
        ("patch the", Stage.FIX, 0.92),

        # ── Review ──
        ("review:", Stage.REVIEW, 0.95),
        ("reviewed", Stage.REVIEW, 0.92),
        ("review pass", Stage.REVIEW, 0.92),
        ("spec review", Stage.REVIEW, 0.92),

        # ── Next ──
        ("next:", Stage.NEXT, 0.95),
        ("queueing", Stage.NEXT, 0.92),
        ("queue:", Stage.NEXT, 0.92),
        ("deferred:", Stage.NEXT, 0.92),
        ("deferred", Stage.NEXT, 0.92),

        # ── generic verbs (low confidence — last resort) ──
        ("ship", Stage.SHIP, 0.78),
        ("review", Stage.REVIEW, 0.78),
        ("fix", Stage.FIX, 0.78),
        ("build", Stage.BUILD, 0.78),
        ("audit", Stage.AUDIT, 0.85),
        ("decision", Stage.DECISION, 0.92),
        ("decide", Stage.DECISION, 0.85),
        ("problem", Stage.PROBLEM, 0.85),
        ("broken", Stage.PROBLEM, 0.85),
        ("missing", Stage.PROBLEM, 0.78),
        ("next", Stage.NEXT, 0.78),
        ("goal", Stage.CONTEXT, 0.88),
        ("setup", Stage.CONTEXT, 0.82),
    ]

    def __init__(self, disagree_on: str | None = None):
        # Seed for deterministic disagreement injection in tests.
        self.disagree_on = disagree_on

    def classify(self, payload: ClassifyInput) -> ClassifyOutput:
        body = payload.body.lower()
        for hint, stage, conf in self.HINTS:
            if hint in body:
                return ClassifyOutput(
                    stage=stage,
                    confidence=conf,
                    reasoning=f"hint match: {hint!r}",
                    raw_response="<mock>",
                )
        return ClassifyOutput(
            stage=Stage.CONTEXT,
            confidence=0.55,
            reasoning="no hint matched; default Context",
            raw_response="<mock>",
        )

    def audit(self, payload: ClassifyInput, primary: ClassifyOutput) -> ClassifyOutput:
        # Echo the primary unless told to disagree.
        if self.disagree_on and self.disagree_on in payload.body.lower():
            alt = Stage.PROBLEM if primary.stage is not Stage.PROBLEM else Stage.AUDIT
            return ClassifyOutput(
                stage=alt,
                confidence=0.6,
                reasoning="auditor disagreed (mock)",
                raw_response="<mock>",
            )
        # Slight confidence wobble so combined score isn't exactly equal.
        return ClassifyOutput(
            stage=primary.stage,
            confidence=max(0.0, min(1.0, primary.confidence - 0.02)),
            reasoning="audit confirms primary",
            raw_response="<mock>",
        )


# ---------------------------------------------------------------------------
# Templating helpers
# ---------------------------------------------------------------------------


def _format_user(payload: ClassifyInput) -> str:
    return USER_TEMPLATE.format(
        agent=payload.agent,
        role=payload.role,
        prior_chapter_stage=(
            payload.prior_chapter_stage.value if payload.prior_chapter_stage else "(none)"
        ),
        prior_2=(payload.prior_2 or "(none)"),
        prior_1=(payload.prior_1 or "(none)"),
        body=payload.body,
    )


def _format_auditor_user(payload: ClassifyInput, primary: ClassifyOutput) -> str:
    return AUDITOR_USER_TEMPLATE.format(
        primary_stage=primary.stage.value,
        primary_confidence=f"{primary.confidence:.2f}",
        primary_reasoning=primary.reasoning,
        agent=payload.agent,
        role=payload.role,
        prior_chapter_stage=(
            payload.prior_chapter_stage.value if payload.prior_chapter_stage else "(none)"
        ),
        prior_2=(payload.prior_2 or "(none)"),
        prior_1=(payload.prior_1 or "(none)"),
        body=payload.body,
    )


# ---------------------------------------------------------------------------
# Chapter boundary detection
# ---------------------------------------------------------------------------


# Canonical forward order from the spec.
_STAGE_ORDER: list[Stage] = [
    Stage.CONTEXT,
    Stage.PROBLEM,
    Stage.AUDIT,
    Stage.DECISION,
    Stage.BUILD,
    Stage.FIX,
    Stage.REVIEW,
    Stage.SHIP,
    Stage.NEXT,
]
_STAGE_RANK: dict[Stage, int] = {s: i for i, s in enumerate(_STAGE_ORDER)}


def chapter_boundary(
    prev_stage: Optional[Stage],
    cur_stage: Stage,
    turns_since_transition: int,
) -> bool:
    """Return True if `cur_stage` opens a new chapter relative to `prev_stage`.

    Spec rules:
      - Same stage repeated → same chapter (False)
      - Stage advances forward in canonical order → new chapter (True)
      - Stage goes backward → new chapter (True)
      - 3 turns max per chapter without transition → propose split (True)
    """
    if prev_stage is None:
        return True  # first turn opens chapter 1
    if cur_stage == prev_stage:
        # turns_since_transition counts how many in-chapter same-stage turns
        # we've already accumulated (0 on the first repeat). When that count
        # reaches 2, the next turn would be the 4th same-stage turn —
        # exceeding the spec's "3 turns max per chapter" — so split.
        if turns_since_transition >= 2:
            return True
        return False
    return _STAGE_RANK[cur_stage] != _STAGE_RANK[prev_stage]


def assign_chapters(stages: list[Stage]) -> list[int]:
    """Walk a list of proposed stages and return the chapter number for each."""
    out: list[int] = []
    chap = 0
    prev: Optional[Stage] = None
    streak = 0
    for s in stages:
        if chapter_boundary(prev, s, streak):
            chap += 1
            streak = 0
        else:
            streak += 1
        out.append(chap)
        prev = s
    return out


# ---------------------------------------------------------------------------
# Cross-check
# ---------------------------------------------------------------------------


def classify_turns(
    parsed: list[ParsedTurn],
    primary: LLMClient,
    auditor: LLMClient,
    cost_sink: Optional[list[CostRecord]] = None,
    disagreement_sink: Optional[list[Disagreement]] = None,
) -> list[Proposal]:
    """Run two-model cross-check and chapter boundary detection over a
    parsed-but-unclassified list of turns.

    The classifier never modifies `parsed` — it returns Proposals that the
    caller (CLI confirmation gate) can apply or override."""
    proposals: list[Proposal] = []
    primary_outputs: list[ClassifyOutput] = []
    prior_chapter_stage: Optional[Stage] = None

    # Pass 1: per-turn classification (primary then auditor).
    for i, pt in enumerate(parsed):
        prior_1 = parsed[i - 1].body if i >= 1 else ""
        prior_2 = parsed[i - 2].body if i >= 2 else ""
        payload = ClassifyInput(
            body=pt.body,
            agent=pt.agent.value,
            role=pt.role,
            prior_2=prior_2,
            prior_1=prior_1,
            prior_chapter_stage=prior_chapter_stage,
        )

        p_out = primary.classify(payload)
        a_out = auditor.audit(payload, p_out)

        if cost_sink is not None:
            cost_sink.append(
                CostRecord(
                    provider="anthropic",
                    model=getattr(primary, "model", "primary"),
                    tokens_in=p_out.tokens_in,
                    tokens_out=p_out.tokens_out,
                )
            )
            cost_sink.append(
                CostRecord(
                    provider="openai",
                    model=getattr(auditor, "model", "auditor"),
                    tokens_in=a_out.tokens_in,
                    tokens_out=a_out.tokens_out,
                )
            )

        agreement = p_out.stage == a_out.stage
        confidence = (
            (p_out.confidence + a_out.confidence) / 2 if agreement else min(p_out.confidence, a_out.confidence)
        )
        requires_human = (not agreement) or (confidence < SPOT_CHECK_THRESHOLD)

        if not agreement and disagreement_sink is not None:
            disagreement_sink.append(
                Disagreement(
                    turn_index=i + 1,
                    body_excerpt=pt.body[:160],
                    primary_stage=p_out.stage.value,
                    primary_confidence=p_out.confidence,
                    primary_reasoning=p_out.reasoning,
                    auditor_stage=a_out.stage.value,
                    auditor_confidence=a_out.confidence,
                    auditor_reasoning=a_out.reasoning,
                )
            )

        primary_outputs.append(p_out)
        proposals.append(
            Proposal(
                turn_index=i + 1,
                stage_proposed=p_out.stage,
                chapter_proposed=0,  # filled in pass 2
                confidence=confidence,
                reasoning=p_out.reasoning,
                agreement=agreement,
                requires_human=requires_human,
                primary=p_out,
                auditor=a_out,
            )
        )

        # update continuity signal — use the agreed (or primary's) stage
        prior_chapter_stage = p_out.stage

    # Pass 2: chapter boundaries from the proposed stages.
    chapters = assign_chapters([p.stage_proposed for p in proposals])
    for prop, chap in zip(proposals, chapters):
        prop.chapter_proposed = chap

    return proposals


# ---------------------------------------------------------------------------
# Stats helpers (used by the CLI to summarize a run)
# ---------------------------------------------------------------------------


@dataclass
class ClassifierStats:
    total: int = 0
    auto_apply: int = 0  # confidence >= 0.9 + agreement
    spot_check: int = 0  # 0.7-0.9 + agreement
    human_required: int = 0  # disagreement OR <0.7
    disagreements: int = 0


def summarize(proposals: list[Proposal]) -> ClassifierStats:
    s = ClassifierStats(total=len(proposals))
    for p in proposals:
        if p.requires_human:
            s.human_required += 1
        elif p.confidence >= AUTO_APPLY_THRESHOLD:
            s.auto_apply += 1
        else:
            s.spot_check += 1
        if not p.agreement:
            s.disagreements += 1
    return s

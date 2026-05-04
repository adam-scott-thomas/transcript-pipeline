# transcript_pipeline.window_classifier
# =============================================================================
# Per-window stage classifier using a generative model (default qwen3:8b).
#
# Distinct from `classifier.py`:
#
#   classifier.py        — per-turn cross-check (Sonnet + GPT-5) for
#                          fresh production transcripts. 80 turns/day cadence.
#                          Two models, agreement signal, confirmation gate.
#
#   window_classifier.py — per-token-window single-model labeling for
#                          archived chats. Hundreds of windows per anchor weave.
#                          One model (cheaper, faster), with confidence < 0.7
#                          flagged for human review per the spec.
#
# Why two paths: the per-turn cross-check is great for live work where
# every turn matters. For archive backfill across thousands of windows,
# two-model cost dominates — and the windows that fall to "requires_human"
# can be reviewed in batch by Adam without round-tripping cloud LLMs.
#
# System prompt comes from `docs/SPEC.md` directly. The 9-stage taxonomy
# lives in one canonical place — if Adam edits SPEC.md, the classifier
# picks up the change on next run with no code edits.
#
# Output schema (strict JSON, one object per window):
#
#   {
#     "stage": "<one of Context|Problem|Audit|Decision|Build|Fix|Review|Ship|Next>",
#     "outcome": "<short phrase, 3-7 words>",
#     "confidence": 0.0-1.0
#   }
#
# Boundary cases per spec (Audit/Problem, Build/Fix, Review/Ship): the
# system prompt explicitly tells the model to lower confidence (<0.7)
# rather than auto-resolve, surfacing those windows to a human.
# =============================================================================

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from transcript_pipeline.embeddings import (
    DEFAULT_CLASSIFIER_TAG,
    OLLAMA_HOST,
    WindowLabel,
)


_SPEC_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "SPEC.md"
)


_OUTPUT_INSTRUCTIONS = """
You are a stage classifier for transcript windows.

The 9 valid stages and their boundary cases are documented in the spec
loaded above (docs/SPEC.md). Use ONLY the stages listed there.

Read the window content provided and return a single JSON object — no
prose, no preamble, no trailing comments — with this exact shape:

{
  "stage": "<one of: Context, Problem, Audit, Decision, Build, Fix, Review, Ship, Next>",
  "outcome": "<short phrase summarizing this window, 3-7 words, no punctuation at end>",
  "confidence": 0.0-1.0
}

Confidence guidance (per spec):
  - >= 0.9 unambiguous from prose, no boundary-case tension
  - 0.7-0.9 leaning, but a reasonable second reading exists
  - < 0.7 boundary case (Audit vs Problem, Build vs Fix, Review vs Ship,
    or content too thin to decide). LOWERING confidence below 0.7
    surfaces this window to a human reviewer; that is correct behavior.

Never invent stages outside the spec. Never output anything outside the
JSON object.
"""


_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def load_system_prompt() -> str:
    """Read SPEC.md + the JSON output instructions. Single source of truth
    for the taxonomy."""
    try:
        spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    except OSError:
        # If SPEC.md isn't reachable (vendored install, etc.), fall back to
        # an embedded copy of just the stage list. The instruction layer
        # still references the spec for boundary cases.
        spec_text = (
            "STAGES (closed set):\n"
            "  Context, Problem, Audit, Decision, Build, Fix, Review, Ship, Next\n"
            "Boundary cases that should LOWER confidence below 0.7:\n"
            "  - Audit vs Problem (diagnosis vs identification)\n"
            "  - Build vs Fix (new work vs patch on identified defect)\n"
            "  - Review vs Ship (checked vs shipped)\n"
        )
    return f"{spec_text}\n\n---\n\n{_OUTPUT_INSTRUCTIONS}"


# ---------------------------------------------------------------------------
# Ollama generative call
# ---------------------------------------------------------------------------


def _call_ollama(
    system: str,
    user: str,
    *,
    model: str,
    host: str = OLLAMA_HOST,
    timeout_s: float = 180.0,
) -> str:
    """One generative completion. Returns the raw response string."""
    import urllib.request

    body = json.dumps(
        {
            "model": model,
            "system": system,
            "prompt": user,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "") or ""


# ---------------------------------------------------------------------------
# Parse + normalize
# ---------------------------------------------------------------------------


_VALID_STAGES = {
    "Context", "Problem", "Audit", "Decision",
    "Build", "Fix", "Review", "Ship", "Next",
}


def _parse_label(raw: str, *, threshold: float) -> WindowLabel:
    """Strict-ish parse: try JSON, fall back to scanning for first {…}.
    Always returns a WindowLabel — boundary cases / parse failures get
    confidence 0.0 + requires_human=True so they surface for human review."""
    text = (raw or "").strip()
    obj: dict | None = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                obj = None

    if not isinstance(obj, dict):
        return WindowLabel(
            stage="Context",
            outcome="(parse failure)",
            confidence=0.0,
            requires_human=True,
        )

    stage_raw = str(obj.get("stage", "")).strip().capitalize()
    stage = stage_raw if stage_raw in _VALID_STAGES else "Context"
    outcome = str(obj.get("outcome", "")).strip()[:80]
    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    requires_human = (
        stage_raw not in _VALID_STAGES  # invented stage
        or conf < threshold              # below user-set bar
    )
    return WindowLabel(
        stage=stage,
        outcome=outcome,
        confidence=conf,
        requires_human=requires_human,
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def classify_windows(
    windows: list[str],
    *,
    model: str = DEFAULT_CLASSIFIER_TAG,
    host: str = OLLAMA_HOST,
    confidence_threshold: float = 0.7,
    progress: bool = False,
) -> list[WindowLabel]:
    """Run the classifier across every window. Returns labels aligned 1:1
    with `windows`. Empty windows get a placeholder label."""
    system = load_system_prompt()
    out: list[WindowLabel] = []
    n = len(windows)
    for i, w in enumerate(windows):
        if not w.strip():
            out.append(
                WindowLabel(
                    stage="Context",
                    outcome="(empty window)",
                    confidence=0.0,
                    requires_human=True,
                )
            )
            continue
        if progress:
            print(f"  classify {i + 1}/{n}", flush=True)
        try:
            raw = _call_ollama(system, w, model=model, host=host)
        except Exception as exc:  # network / timeout / json decode upstream
            out.append(
                WindowLabel(
                    stage="Context",
                    outcome=f"(call failed: {type(exc).__name__})",
                    confidence=0.0,
                    requires_human=True,
                )
            )
            continue
        out.append(_parse_label(raw, threshold=confidence_threshold))
    return out

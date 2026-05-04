# transcript_pipeline.temporal_weaver
# =============================================================================
# Anchor stream + N other streams → one merged ParsedTurn[] sorted by
# wall-clock timestamp, with per-stream `instance` numbers assigned for
# the renderer's outline-disambiguation feature.
#
# Why an anchor: when Adam is multitasking across CC + GPT + Spidey-Claude
# at the same hour, we don't want to interleave EVERY conversation Adam
# ever had — only the ones that overlap with the focus session. The CC
# JSONL is the natural anchor because (a) it has the highest-resolution
# timestamps and (b) it captures Adam's primary work. Other streams are
# pulled in only if their timestamp range overlaps the anchor's window
# (configurable, default ±2h on either side).
#
# Instance assignment:
#   For each Agent (ADAM, GPT, CLAUDE_CODE, etc.), count distinct
#   conversation_ids encountered in the merged output. First conversation
#   for an agent class = instance 1; second = 2; third = 3. The renderer
#   maps these to outlines (none, white, double white).
#
#   ADAM is a special case: every source has an "ADAM" speaker (it's the
#   user). We treat all human turns as instance 1 — Adam is one human in
#   one chair, regardless of how many AI windows he has open. Distinct
#   non-human conversation streams per agent class get the outlines.
#
# Sort order is strict by timestamp. Ties (rare, but possible across
# streams) break on (conversation_id, original_turn_no) for determinism.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from transcript_pipeline.adapters.cc_jsonl import SourceStream
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent


# Default temporal window: pull other streams within ±2 hours of the
# anchor's overall span. Configurable per call.
DEFAULT_WINDOW_SECONDS: float = 2 * 60 * 60


@dataclass
class SemanticConfig:
    """v0.4 — semantic filtering on top of temporal. Two-stage retrieval:

      stage 1: time filter + conversation-level cosine (fast, drops 90%+
               of off-topic candidates)
      stage 2: window-level max cosine on survivors only

    Defaults match the spec: qwen3-embedding:8b, 2500-token windows,
    50% overlap, cosine threshold 0.55. Pass `enabled=False` to bypass
    semantic filtering entirely (time-only weave, v0.3 behavior)."""

    enabled: bool = False
    out_dir: Path | None = None
    embedder_tag: str = "qwen3-embedding:8b"
    window_tokens: int = 2500
    window_overlap: float = 0.5
    threshold: float = 0.55
    top_k: int | None = None  # if set, overrides threshold


@dataclass
class WeaveResult:
    """Output of weave(). `merged` is the single ordered list. `included`
    enumerates which source streams contributed (handy for logging).
    `dropped_semantic` records streams the time filter let in but the
    semantic filter rejected — useful for explaining what was filtered."""

    merged: list[ParsedTurn]
    included: list[tuple[str, str]]  # (agent_class, conversation_id)
    window: tuple[float, float]      # epoch range used for inclusion
    dropped_semantic: list[tuple[str, str, float]] = None  # (agent, convo_id, similarity)

    def __post_init__(self):
        if self.dropped_semantic is None:
            self.dropped_semantic = []


def weave(
    anchor: SourceStream,
    others: Iterable[SourceStream],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    semantic: SemanticConfig | None = None,
) -> WeaveResult:
    """Merge the anchor and any overlapping `others` streams into a single
    timestamp-sorted ParsedTurn[] with per-conversation `instance`
    numbers.

    When `semantic.enabled` is True, candidates surviving the time filter
    are further filtered by:
      stage 1: cosine(other.convo_vector, anchor.convo_vector) >= threshold
      stage 2: max cosine over windows for the surviving candidates

    Anchor is always included. Threshold of 0.55 is roughly "same project,
    different angle"; raise to 0.65+ for stricter matching."""
    anchor_start = anchor.started_at or 0.0
    anchor_end = anchor.ended_at or anchor_start
    win_start = anchor_start - window_seconds
    win_end = anchor_end + window_seconds

    pool: list[ParsedTurn] = []
    dropped_semantic: list[tuple[str, str, float]] = []

    # anchor turns always included
    pool.extend(_clone_with_id(anchor.turns, anchor.conversation_id))

    # ── compute anchor embeddings if semantic is on ──
    anchor_emb = None
    if semantic and semantic.enabled and semantic.out_dir is not None:
        from transcript_pipeline.embeddings import compute_embeddings, max_cosine
        anchor_emb = compute_embeddings(
            anchor.conversation_id,
            anchor.turns,
            out_dir=semantic.out_dir,
            embedder_tag=semantic.embedder_tag,
            window_tokens=semantic.window_tokens,
            window_overlap=semantic.window_overlap,
        )

    # ── time-window filter, then optional semantic gate ──
    included: list[tuple[str, str]] = [
        (_agent_class(anchor.turns), anchor.conversation_id),
    ]
    candidates_with_sims: list[tuple[SourceStream, list, float]] = []

    for other in others:
        if not other.turns:
            continue
        ostart = other.started_at if other.started_at is not None else 0.0
        oend = other.ended_at if other.ended_at is not None else ostart
        if oend < win_start or ostart > win_end:
            continue
        sliced = [
            t for t in other.turns
            if t.timestamp is not None and win_start <= t.timestamp <= win_end
        ]
        if not sliced:
            continue

        # ── semantic gate ──
        if semantic and semantic.enabled and anchor_emb is not None:
            from transcript_pipeline.embeddings import compute_embeddings, max_cosine, _cosine
            other_emb = compute_embeddings(
                other.conversation_id,
                other.turns,
                out_dir=semantic.out_dir,
                embedder_tag=semantic.embedder_tag,
                window_tokens=semantic.window_tokens,
                window_overlap=semantic.window_overlap,
            )
            # stage 1: conversation-level cosine
            convo_sim = _cosine(anchor_emb.convo_vector, other_emb.convo_vector)
            # stage 2 — only run if stage 1 passes (cheap pre-filter)
            if convo_sim < semantic.threshold * 0.85:
                # well below threshold; don't waste cycles on window-level
                dropped_semantic.append(
                    (_agent_class(other.turns), other.conversation_id, convo_sim)
                )
                continue
            # stage 2: max window-level cosine to anchor's convo vector
            win_sim = max_cosine(anchor_emb.convo_vector, other_emb.window_vectors)
            best = max(convo_sim, win_sim)
            if semantic.top_k is None and best < semantic.threshold:
                dropped_semantic.append(
                    (_agent_class(other.turns), other.conversation_id, best)
                )
                continue
            candidates_with_sims.append((other, sliced, best))
        else:
            candidates_with_sims.append((other, sliced, 1.0))

    # if top_k is set, sort by similarity desc and take top K
    if semantic and semantic.enabled and semantic.top_k is not None:
        candidates_with_sims.sort(key=lambda x: x[2], reverse=True)
        kept = candidates_with_sims[: semantic.top_k]
        dropped = candidates_with_sims[semantic.top_k:]
        for other, _, sim in dropped:
            dropped_semantic.append(
                (_agent_class(other.turns), other.conversation_id, sim)
            )
        candidates_with_sims = kept

    for other, sliced, _sim in candidates_with_sims:
        pool.extend(_clone_with_id(sliced, other.conversation_id))
        included.append((_agent_class(other.turns), other.conversation_id))

    # ── stable timestamp sort ──
    pool.sort(
        key=lambda t: (
            t.timestamp if t.timestamp is not None else 0.0,
            t.conversation_id or "",
            t.turn,
        )
    )

    # ── re-number turns 1..N (sequential) ──
    for new_no, t in enumerate(pool, start=1):
        t.turn = new_no

    # ── assign instance numbers per (agent class, conversation_id) ──
    _assign_instances(pool)

    return WeaveResult(
        merged=pool,
        included=included,
        window=(win_start, win_end),
        dropped_semantic=dropped_semantic,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _clone_with_id(turns: list[ParsedTurn], convo_id: str) -> list[ParsedTurn]:
    """Defensive clone so the weaver doesn't mutate adapter outputs."""
    out: list[ParsedTurn] = []
    for t in turns:
        out.append(
            ParsedTurn(
                turn=t.turn,
                agent=t.agent,
                role=t.role,
                body=t.body,
                stage=t.stage,
                chapter=t.chapter,
                chapter_outcome=t.chapter_outcome,
                status_tag=t.status_tag,
                references=list(t.references or []),
                visual=t.visual,
                instance=t.instance,
                timestamp=t.timestamp,
                conversation_id=t.conversation_id or convo_id,
            )
        )
    return out


def _agent_class(turns: list[ParsedTurn]) -> str:
    """Pick the dominant non-ADAM agent in a stream — that's the stream's
    'class' for instance counting."""
    for t in turns:
        if t.agent is not Agent.ADAM:
            return t.agent.value
    return Agent.ADAM.value


def _assign_instances(turns: list[ParsedTurn]) -> None:
    """Set `instance` per turn:
       - human turns (ADAM): always 1 (one Adam, one chair)
       - everyone else: enumerate distinct conversation_ids per agent class
         in order of first appearance, assign instance 1, 2, 3, ...
    """
    seen_per_agent: dict[str, dict[str, int]] = {}
    for t in turns:
        if t.agent is Agent.ADAM:
            t.instance = 1
            continue
        agent_key = t.agent.value
        convo = t.conversation_id or "_unknown"
        bucket = seen_per_agent.setdefault(agent_key, {})
        if convo not in bucket:
            bucket[convo] = len(bucket) + 1
        t.instance = bucket[convo]

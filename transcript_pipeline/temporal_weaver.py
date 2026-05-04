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
from typing import Iterable

from transcript_pipeline.adapters.cc_jsonl import SourceStream
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent


# Default temporal window: pull other streams within ±2 hours of the
# anchor's overall span. Configurable per call.
DEFAULT_WINDOW_SECONDS: float = 2 * 60 * 60


@dataclass
class WeaveResult:
    """Output of weave(). `merged` is the single ordered list. `included`
    enumerates which source streams contributed (handy for logging)."""

    merged: list[ParsedTurn]
    included: list[tuple[str, str]]  # (agent_class, conversation_id)
    window: tuple[float, float]      # epoch range used for inclusion


def weave(
    anchor: SourceStream,
    others: Iterable[SourceStream],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> WeaveResult:
    """Merge the anchor and any overlapping `others` streams into a single
    timestamp-sorted ParsedTurn[] with per-conversation `instance`
    numbers."""
    anchor_start = anchor.started_at or 0.0
    anchor_end = anchor.ended_at or anchor_start
    win_start = anchor_start - window_seconds
    win_end = anchor_end + window_seconds

    pool: list[ParsedTurn] = []

    # anchor turns always included
    pool.extend(_clone_with_id(anchor.turns, anchor.conversation_id))

    # only include other streams whose [started_at, ended_at] overlaps the window
    included: list[tuple[str, str]] = [
        (_agent_class(anchor.turns), anchor.conversation_id),
    ]
    for other in others:
        if not other.turns:
            continue
        ostart = other.started_at if other.started_at is not None else 0.0
        oend = other.ended_at if other.ended_at is not None else ostart
        if oend < win_start or ostart > win_end:
            continue
        # filter to turns actually inside the window — a long claude.ai
        # conversation might span days; only the slice that aligns with
        # the anchor belongs in the woven view
        sliced = [
            t for t in other.turns
            if t.timestamp is not None and win_start <= t.timestamp <= win_end
        ]
        if not sliced:
            continue
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

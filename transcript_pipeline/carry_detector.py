# transcript_pipeline.carry_detector
# =============================================================================
# v0.5.2 — Detect when ADAM has pasted a prior AI bubble's content into
# another agent's chat (a "copy-paste carry"). When this happens we don't
# want to render the duplicate ADAM bubble; the source bubble already said
# the thing. Instead the source bubble shows a thumbs-up indicator with the
# target agent's abbreviation.
#
# Algorithm:
#
#   For each ADAM turn in chronological order:
#     - embed the turn body via the v0.4.1 embedder (qwen3-embedding:8b
#       by default)
#     - compute cosine similarity vs the previous 10 AI turns' content
#       vectors (sliding window — embeds are cached per turn within a
#       single detector run)
#     - if max_similarity >= carry_threshold (default 0.85):
#         - mark this ADAM turn:        is_carry=True, carry_source=<turn#>,
#                                       carry_similarity=<float>
#         - mark the source turn:       carried_to.append(<next_agent>)
#                                       where <next_agent> is the agent of
#                                       the FIRST non-ADAM, non-SYSTEM turn
#                                       AFTER this ADAM paste — that's who
#                                       actually received the paste.
#       (If no follow-up agent exists, the carry is still tagged on ADAM,
#        but carried_to remains empty.)
#
# Why post-weave: we need timestamp-merged turns to compute "previous 10
# AI turns" coherently. Pre-weave, each source stream is its own world.
#
# Why not partial-carry rendering: out of scope for v0.5.2 per Adam's spec.
# We log partial-similarity windows (0.30 < sim < threshold) for v0.5.3
# review but don't surface them.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from transcript_pipeline.embeddings import (
    DEFAULT_EMBEDDER_TAG,
    OLLAMA_HOST,
    _cosine,
    embed_text,
)
from transcript_pipeline.woven_jsonl import WovenTurn


DEFAULT_CARRY_THRESHOLD = 0.85
DEFAULT_LOOKBACK = 10  # how many prior AI turns to consider for any one ADAM


@dataclass
class CarryStats:
    """Summary of a carry-detection pass — printed by the CLI for sanity."""

    total_adam_turns: int = 0
    carries_detected: int = 0
    partial_carries: int = 0  # 0.30 < sim < threshold
    skipped: int = 0  # no AI turns in lookback


def detect_carries(
    turns: list[WovenTurn],
    *,
    threshold: float = DEFAULT_CARRY_THRESHOLD,
    lookback: int = DEFAULT_LOOKBACK,
    embedder_tag: str = DEFAULT_EMBEDDER_TAG,
    host: str = OLLAMA_HOST,
    embedder=None,
) -> CarryStats:
    """Mutate `turns` in place: tag carries on ADAM bodies and update
    `carried_to` on source bubbles.

    `embedder` is an injection point for tests — a callable
    `(text: str) -> np.ndarray`. When None, calls Ollama via embed_text.
    """
    if embedder is None:
        def _real_embed(text: str) -> np.ndarray:
            return embed_text(text, model=embedder_tag, host=host)
        embedder = _real_embed

    stats = CarryStats()
    # cache per-turn embeddings so we don't re-call the model
    vec_cache: dict[int, np.ndarray] = {}

    def _get_vec(t: WovenTurn) -> np.ndarray:
        if t.turn in vec_cache:
            return vec_cache[t.turn]
        body = (t.body or "").strip()
        if not body:
            v = np.zeros((1,), dtype=np.float32)
        else:
            v = embedder(body)
        vec_cache[t.turn] = v
        return v

    # walk in chronological order
    for i, turn in enumerate(turns):
        if turn.agent != "ADAM":
            continue
        stats.total_adam_turns += 1

        # candidate source pool: prior `lookback` non-ADAM, non-SYSTEM turns
        candidates: list[WovenTurn] = []
        j = i - 1
        while j >= 0 and len(candidates) < lookback:
            t = turns[j]
            if t.agent not in ("ADAM", "SYSTEM"):
                candidates.append(t)
            j -= 1

        if not candidates:
            stats.skipped += 1
            continue

        adam_vec = _get_vec(turn)
        if adam_vec.shape[0] <= 1:
            stats.skipped += 1
            continue

        best_sim = -1.0
        best_source: WovenTurn | None = None
        had_partial = False
        for cand in candidates:
            cand_vec = _get_vec(cand)
            if cand_vec.shape[0] <= 1:
                continue
            sim = _cosine(adam_vec, cand_vec)
            if sim > best_sim:
                best_sim = sim
                best_source = cand
            if 0.30 < sim < threshold:
                had_partial = True

        if best_source is None:
            stats.skipped += 1
            continue

        if best_sim >= threshold:
            turn.is_carry = True
            turn.carry_source = best_source.turn
            turn.carry_similarity = float(best_sim)

            # who received the paste? first non-ADAM, non-SYSTEM turn AFTER
            # this ADAM bubble in chronological order.
            target_agent: str | None = None
            for k in range(i + 1, len(turns)):
                nxt = turns[k]
                if nxt.agent not in ("ADAM", "SYSTEM"):
                    target_agent = nxt.agent
                    break
            if target_agent and target_agent not in best_source.carried_to:
                best_source.carried_to.append(target_agent)

            stats.carries_detected += 1
        elif had_partial:
            stats.partial_carries += 1

    return stats

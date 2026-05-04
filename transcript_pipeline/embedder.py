# transcript_pipeline.embedder
# =============================================================================
# ParsedTurn[] + minimal user-supplied metadata → canonical embedded file.
#
# The embedded format is a single text file with one YAML frontmatter block
# per record (header first, then one block per turn), each followed by the
# message body. Multiple YAML blocks are separated by `---` delimiters.
#
# Layout:
#
#   ---
#   kind: header
#   project: GL
#   project_number: 4
#   status: Fixed
#   outcome: Auth Key Flow
#   session_id: 2026-05-04-1830
#   resumed: false
#   ---
#   kind: turn
#   turn: 1
#   agent: ADAM
#   role: HUMAN
#   stage: Context
#   chapter: 1
#   chapter_outcome: Demo Goal Defined
#   status_tag: null
#   references: []
#   visual: bubble_black
#   ---
#   Fix the auth flow.
#   ---
#   kind: turn
#   turn: 2
#   ...
#
# Body sits between the closing `---` of a turn block and the next opening
# `---`. This keeps the file human-editable while staying machine-parseable.
#
# Auto-fill rules:
#
#   - chapter numbers: assigned 1..N from stage transitions. A new chapter
#     starts whenever the stage changes from the previous turn (with the
#     first turn always being chapter 1). Explicit chapter values from the
#     parser override this.
#   - chapter_outcome: if absent, the embedder reuses the first non-empty
#     outcome seen for that chapter; if none provided anywhere, it leaves an
#     empty string and the validator emits no error (outcome is required by
#     spec but the embedder is not the rule-enforcement seam).
#   - visual: derived from AGENT_DEFAULT_VISUAL unless the parser set it.
#
# All embedded files round-trip: load_embedded(embed_to_string(t)) == t.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import (
    AGENT_DEFAULT_VISUAL,
    Agent,
    Stage,
    Status,
    StatusTag,
    Transcript,
    Turn,
    VideoHeader,
    Visual,
)


@dataclass
class EmbedRequest:
    """Caller-supplied header fields; everything else is derived from turns."""

    project: str
    project_number: int
    status: Status
    outcome: str
    session_id: str
    resumed: bool = False


def embed(
    request: EmbedRequest, parsed: list[ParsedTurn]
) -> Transcript:
    """Build a Transcript from a list of ParsedTurns and a header request.

    Auto-assigns chapter numbers from stage transitions for any turns that
    came in without an explicit chapter."""
    if not parsed:
        raise ValueError("cannot embed an empty turn list")

    # ── auto-assign chapters ──
    chap_no = 0
    last_stage: Stage | None = None
    chap_outcomes: dict[int, str] = {}
    for pt in parsed:
        if pt.chapter is not None:
            chap_no = max(chap_no, pt.chapter)
            if pt.chapter_outcome:
                chap_outcomes.setdefault(pt.chapter, pt.chapter_outcome)
            last_stage = pt.stage
            continue
        # implicit chapter: open a new one on stage transitions
        if pt.stage is None:
            raise ValueError(
                f"turn #{pt.turn} ({pt.agent.value}): stage is required "
                f"(set [STAGE: ...] under the header or in the tag header)"
            )
        if last_stage is None or pt.stage != last_stage:
            chap_no += 1
        pt.chapter = chap_no
        if pt.chapter_outcome:
            chap_outcomes.setdefault(chap_no, pt.chapter_outcome)
        last_stage = pt.stage

    # ── back-fill chapter_outcome where missing ──
    for pt in parsed:
        if not pt.chapter_outcome:
            pt.chapter_outcome = chap_outcomes.get(pt.chapter or 0, "")

    # ── default visual ──
    for pt in parsed:
        if pt.visual is None:
            pt.visual = AGENT_DEFAULT_VISUAL[pt.agent]

    header = VideoHeader(
        project=request.project,
        project_number=request.project_number,
        status=request.status,
        outcome=request.outcome,
        session_id=request.session_id,
        resumed=request.resumed,
    )
    turns = [
        Turn(
            turn=pt.turn,
            agent=pt.agent,
            role=pt.role,
            stage=pt.stage,  # type: ignore[arg-type]
            chapter=pt.chapter or 1,
            chapter_outcome=pt.chapter_outcome or "",
            body=pt.body,
            status_tag=pt.status_tag,
            references=tuple(pt.references),
            visual=pt.visual,
            instance=getattr(pt, "instance", 1),
            timestamp=getattr(pt, "timestamp", None),
            conversation_id=getattr(pt, "conversation_id", None),
        )
        for pt in parsed
    ]
    return Transcript(header=header, turns=turns)


# ---------------------------------------------------------------------------
# Serialization (round-trip)
# ---------------------------------------------------------------------------


def embed_to_string(t: Transcript) -> str:
    """Serialize Transcript to the embedded text format."""
    out: list[str] = []

    # header
    out.append("---")
    out.append("kind: header")
    out.append(yaml.safe_dump(t.header.to_dict(), sort_keys=False).rstrip())

    # turns
    for turn in t.turns:
        out.append("---")
        out.append("kind: turn")
        out.append(yaml.safe_dump(turn.to_dict(), sort_keys=False).rstrip())
        out.append("---")
        out.append(turn.body.rstrip())

    out.append("")  # trailing newline
    return "\n".join(out)


def embed_to_file(
    request: EmbedRequest,
    parsed: list[ParsedTurn],
    out_path: Path,
) -> Path:
    """Embed and write to disk. Returns the written path."""
    t = embed(request, parsed)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(embed_to_string(t), encoding="utf-8")
    return out_path


def load_embedded(text: str) -> Transcript:
    """Inverse of embed_to_string. Used by validator/renderer + round-trip tests."""
    blocks = _split_blocks(text)
    if not blocks:
        raise ValueError("empty embedded file")

    header_block = blocks[0]
    if header_block.kind != "header":
        raise ValueError("first block must be kind: header")
    header = VideoHeader.from_dict(header_block.meta)

    turns: list[Turn] = []
    i = 1
    while i < len(blocks):
        meta_block = blocks[i]
        if meta_block.kind != "turn":
            raise ValueError(
                f"expected kind: turn at block {i}; got {meta_block.kind!r}"
            )
        if i + 1 >= len(blocks):
            raise ValueError(
                f"turn block {i} has no body block following it"
            )
        body_block = blocks[i + 1]
        if body_block.kind != "_body":
            raise ValueError(
                f"expected body block after turn meta at {i + 1}; "
                f"got {body_block.kind!r}"
            )
        turns.append(Turn.from_dict(meta_block.meta, body_block.body))
        i += 2

    return Transcript(header=header, turns=turns)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    kind: str  # "header" | "turn" | "_body"
    meta: dict
    body: str = ""


def _split_blocks(text: str) -> list[_Block]:
    """Split embedded text on '---' delimiters into alternating meta + body."""
    # Tokenize: every line that's exactly '---' opens a section.
    lines = text.splitlines()
    sections: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if ln.strip() == "---":
            if cur:
                sections.append(cur)
                cur = []
            continue
        cur.append(ln)
    if cur:
        sections.append(cur)

    blocks: list[_Block] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        joined = "\n".join(sec).strip()
        if joined.startswith("kind: header"):
            meta = yaml.safe_load(joined) or {}
            kind = meta.pop("kind", "header")
            blocks.append(_Block(kind=kind, meta=meta))
            i += 1
        elif joined.startswith("kind: turn"):
            meta = yaml.safe_load(joined) or {}
            kind = meta.pop("kind", "turn")
            blocks.append(_Block(kind=kind, meta=meta))
            # next section is the body (free text, not YAML)
            if i + 1 < len(sections):
                body_text = "\n".join(sections[i + 1])
                blocks.append(_Block(kind="_body", meta={}, body=body_text))
                i += 2
            else:
                i += 1
        else:
            # free-text body that wasn't preceded by a turn meta — orphan
            blocks.append(_Block(kind="_body", meta={}, body="\n".join(sec)))
            i += 1
    return blocks

# transcript_pipeline.renderer
# =============================================================================
# Transcript → final video-ready text + sidecar artifacts.
#
# Three outputs per render:
#
#   1. transcript.txt   — the format-spec-v1.0 text used as the on-screen
#                         transcript and as a YouTube description backstop.
#                         Title line, chapter headers, ASCII speaker bubbles.
#
#   2. chapters.md      — YouTube chapter markdown:
#                            00:00 — Chapter 01: Context — Demo Goal Defined
#                            00:42 — Chapter 02: Problem — Auth Failures Identified
#                         The renderer can't know real video timecodes, so it
#                         emits placeholder offsets (00:00, 01:00, 02:00, ...)
#                         that the editor patches to actual cut points. The
#                         file is canonical apart from those minutes.
#
#   3. bubbles.json     — per-turn JSON for the frame generator (separate
#                         tool, not in this repo). Each entry carries every
#                         field needed to draw the bubble: agent, role,
#                         visual, body, chapter, status_tag, references,
#                         timestamp placeholder.
#
# The renderer is pure: same input → same output, every time. It never reads
# from the spine registry except for `path.out_dir`. Everything else comes
# from the Transcript object.
# =============================================================================

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from transcript_pipeline.schema import Agent, Transcript, Turn, Visual


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


@dataclass
class RenderResult:
    """Paths of the three artifacts written. All under out_dir/<code>/."""

    transcript_path: Path
    chapters_path: Path
    bubbles_path: Path


def render_transcript(t: Transcript, out_dir: Path) -> RenderResult:
    """Write all three artifacts. Returns paths."""
    out_dir = Path(out_dir)
    code_dir = out_dir / t.header.code
    code_dir.mkdir(parents=True, exist_ok=True)

    tpath = code_dir / "transcript.txt"
    cpath = code_dir / "chapters.md"
    bpath = code_dir / "bubbles.json"

    tpath.write_text(render_text(t), encoding="utf-8")
    cpath.write_text(render_chapters_md(t), encoding="utf-8")
    bpath.write_text(json.dumps(render_bubbles(t), indent=2), encoding="utf-8")

    return RenderResult(transcript_path=tpath, chapters_path=cpath, bubbles_path=bpath)


# ---------------------------------------------------------------------------
# transcript.txt
# ---------------------------------------------------------------------------


_BUBBLE_WIDTH = 60  # characters; matches spec example


def render_text(t: Transcript) -> str:
    """ASCII-art transcript per spec section 3 + 6."""
    lines: list[str] = []

    # title line (spec section 1)
    lines.append(t.header.title_line)
    lines.append("")

    # chapter sections (spec section 6)
    last_chapter = -1
    for turn in t.turns:
        if turn.chapter != last_chapter:
            lines.append(_chapter_header(turn))
            lines.append("")
            last_chapter = turn.chapter
        lines.extend(_render_bubble(turn))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _chapter_header(turn: Turn) -> str:
    """`[CHAPTER ##] Stage — Outcome`."""
    return (
        f"[CHAPTER {turn.chapter:02d}] {turn.stage.value}"
        f" — {turn.chapter_outcome}".rstrip(" —")
    )


def _render_bubble(turn: Turn) -> list[str]:
    """Spec section 3 + 4. CODEX gets the white-card style; everyone else
    gets the bubble-black style. ASCII art is identical in shape — the
    `visual` field is for the renderer downstream (bubbles.json) to switch
    backgrounds. The ASCII transcript is monochrome by design."""
    width = _BUBBLE_WIDTH
    is_card = turn.effective_visual is Visual.CARD_WHITE

    # header line: agent on left, role on right, padded
    role_label = turn.role.upper()
    inner_w = width - 2  # account for │ │
    name_pad = inner_w - len(turn.agent.value) - len(role_label) - 1
    if name_pad < 1:
        name_pad = 1
    head = f" {turn.agent.value}{' ' * name_pad}{role_label} "

    # body lines wrapped to inner width
    body_lines = _wrap_body(turn.body, inner_w - 2)

    # status tag suffix
    if turn.status_tag is not None:
        body_lines.append("")
        body_lines.append(f"[{turn.status_tag.value}]")

    # references
    if turn.references:
        body_lines.append("")
        body_lines.append("refs: " + ", ".join(turn.references))

    # timestamp placeholder right-aligned (HH:MM zero-fill from turn order)
    body_lines.append("")
    ts = f"{(turn.turn - 1) // 60:02d}:{(turn.turn - 1) % 60:02d}"
    body_lines.append(ts.rjust(inner_w - 2))

    # box drawing
    if is_card:
        # white card: thin solid borders, slightly different chars (╭╮╰╯)
        top = "╭" + "─" * (width - 2) + "╮"
        sep = "├" + "─" * (width - 2) + "┤"
        bot = "╰" + "─" * (width - 2) + "╯"
    else:
        top = "┌" + "─" * (width - 2) + "┐"
        sep = "├" + "─" * (width - 2) + "┤"
        bot = "└" + "─" * (width - 2) + "┘"

    out: list[str] = []
    out.append(top.replace("─", "─", 1))
    # rebuild head line with box edges
    out.append(_with_edges(head, width, is_card))
    out.append(sep)
    for ln in body_lines:
        out.append(_with_edges(" " + ln + " ", width, is_card))
    out.append(bot)
    return out


def _with_edges(content: str, width: int, is_card: bool) -> str:
    edge = "│"
    inner = content.ljust(width - 2)[: width - 2]
    return f"{edge}{inner}{edge}"


def _wrap_body(body: str, width: int) -> list[str]:
    """Greedy wrap to `width` columns, preserving paragraph breaks."""
    if width <= 0:
        return [body]
    out: list[str] = []
    for paragraph in body.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        words = paragraph.split(" ")
        line = ""
        for w in words:
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= width:
                line += " " + w
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# chapters.md
# ---------------------------------------------------------------------------


def render_chapters_md(t: Transcript) -> str:
    """YouTube chapter markdown. Timecodes are placeholder minutes — the
    editor patches actual cut points after upload."""
    lines: list[str] = [f"# {t.header.title_line}", ""]
    chapters = t.chapters()
    for idx, (n, stage, outcome) in enumerate(chapters):
        ts = f"{idx:02d}:00"
        lines.append(f"{ts} — Chapter {n:02d}: {stage.value} — {outcome}".rstrip(" —"))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# bubbles.json
# ---------------------------------------------------------------------------


def render_bubbles(t: Transcript) -> list[dict]:
    """Per-turn JSON for the frame generator. Schema deliberately flat."""
    out: list[dict] = []
    for turn in t.turns:
        out.append(
            {
                "turn": turn.turn,
                "agent": turn.agent.value,
                "role": turn.role,
                "stage": turn.stage.value,
                "chapter": turn.chapter,
                "chapter_outcome": turn.chapter_outcome,
                "status_tag": turn.status_tag.value if turn.status_tag else None,
                "references": list(turn.references),
                "visual": turn.effective_visual.value,
                "body": turn.body,
                "timestamp_placeholder": _placeholder_ts(turn.turn),
                "title": t.header.title_line,
                "code": t.header.code,
            }
        )
    return out


def _placeholder_ts(turn_no: int) -> str:
    return f"{(turn_no - 1) // 60:02d}:{(turn_no - 1) % 60:02d}"

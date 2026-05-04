# transcript_pipeline.parser
# =============================================================================
# Raw chat log → list[Turn].
#
# Input format: a paste-in raw log where each turn opens with a tag header
# matching one of:
#
#     [AGENT | ROLE]
#     [AGENT | ROLE | STAGE]
#     [AGENT | ROLE | STAGE | OUTCOME]
#     [AGENT | ROLE]    @CHAPTER 02
#
# Body text follows on subsequent lines until the next tag header (or EOF).
# Optional inline directives are tolerated:
#
#     [STATUS: SHIPPED]      # status_tag
#     [REF: GL-002, POAW-7]  # references for this turn
#     [STAGE: Audit]         # override stage if not in tag header
#     [OUTCOME: ...]         # chapter outcome if not in tag header
#     [CHAPTER: 03]          # explicit chapter assignment
#
# These directives may appear on lines by themselves immediately under the
# tag header. The parser strips them from the body before emitting the Turn.
#
# Design rules:
#   - Fail loud on ambiguous turns. An unknown agent name, an unparseable
#     header, or two tag headers without intervening text → ParseError.
#   - The parser does NOT auto-classify stage from prose. The user provides
#     stage explicitly (in the header, in [STAGE:], or via the embedder).
#     Auto-stage classification is explicitly out of scope for v0.1.
#   - Turn numbers are assigned sequentially in this file (1-indexed).
#     Chapter numbers are NOT assigned here — that's the embedder's job
#     because it requires global knowledge of stage transitions.
#
# Output: list[ParsedTurn], where ParsedTurn is a partially-populated Turn
# (chapter and chapter_outcome are placeholders, filled by the embedder).
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass, field

from transcript_pipeline.schema import Agent, Stage, StatusTag, Turn, Visual


class ParseError(ValueError):
    """Raised on any unrecoverable parse ambiguity."""


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


# Tag header: [AGENT | ROLE] with optional pipe-separated extras for stage
# and outcome. AGENT must be one of the canonical names (case-insensitive).
# ROLE is free text. Inline @CHAPTER NN suffix is allowed.
_HEADER_RE = re.compile(
    r"""
    ^\s*\[\s*
    (?P<agent>[A-Z][A-Z\-]+)
    \s*\|\s*
    (?P<role>[^\]|]+?)
    (?:\s*\|\s*(?P<stage>[A-Za-z]+))?
    (?:\s*\|\s*(?P<outcome>[^\]]+?))?
    \s*\]
    (?:\s*@CHAPTER\s+(?P<chapter>\d+))?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

_DIRECTIVE_RE = re.compile(
    r"^\s*\[(?P<key>STATUS|REF|STAGE|OUTCOME|CHAPTER|VISUAL)\s*:\s*(?P<val>[^\]]+)\]\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Intermediate type — embedder fills the rest
# ---------------------------------------------------------------------------


@dataclass
class ParsedTurn:
    """Output of the parser. The embedder converts these to Turn objects."""

    turn: int
    agent: Agent
    role: str
    body: str
    stage: Stage | None = None
    chapter: int | None = None
    chapter_outcome: str | None = None
    status_tag: StatusTag | None = None
    references: list[str] = field(default_factory=list)
    visual: Visual | None = None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def parse_log(raw: str) -> list[ParsedTurn]:
    """Parse a raw chat log into a list of ParsedTurns.

    Raises ParseError on any ambiguity. Refuses to silently drop content."""
    if not raw or not raw.strip():
        raise ParseError("empty log")

    lines = raw.splitlines()
    turns: list[ParsedTurn] = []
    cur: ParsedTurn | None = None
    body_buf: list[str] = []

    def flush() -> None:
        if cur is None:
            return
        cur.body = "\n".join(body_buf).strip()
        if not cur.body:
            raise ParseError(
                f"turn {cur.turn} for {cur.agent.value} has no body"
            )
        turns.append(cur)

    for ln_no, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()

        # --- new tag header? ---
        m = _HEADER_RE.match(line)
        if m:
            # close previous turn
            if cur is not None:
                flush()
            agent_raw = m.group("agent").upper().strip()
            try:
                agent = Agent(agent_raw)
            except ValueError as exc:
                raise ParseError(
                    f"line {ln_no}: unknown agent '{agent_raw}' "
                    f"(must be one of {[a.value for a in Agent]})"
                ) from exc

            stage_raw = (m.group("stage") or "").strip().capitalize()
            try:
                stage = Stage(stage_raw) if stage_raw else None
            except ValueError as exc:
                raise ParseError(
                    f"line {ln_no}: unknown stage '{stage_raw}'"
                ) from exc

            chapter_raw = m.group("chapter")
            chapter = int(chapter_raw) if chapter_raw else None

            cur = ParsedTurn(
                turn=len(turns) + 1,
                agent=agent,
                role=m.group("role").strip(),
                body="",
                stage=stage,
                chapter=chapter,
                chapter_outcome=(m.group("outcome") or "").strip() or None,
            )
            body_buf = []
            continue

        # --- inline directive? ---
        d = _DIRECTIVE_RE.match(line)
        if d and cur is not None:
            key = d.group("key").upper()
            val = d.group("val").strip()
            if key == "STATUS":
                try:
                    cur.status_tag = StatusTag(val.upper())
                except ValueError as exc:
                    raise ParseError(
                        f"line {ln_no}: unknown status '{val}'"
                    ) from exc
            elif key == "REF":
                cur.references = [r.strip() for r in val.split(",") if r.strip()]
            elif key == "STAGE":
                try:
                    cur.stage = Stage(val.capitalize())
                except ValueError as exc:
                    raise ParseError(
                        f"line {ln_no}: unknown stage '{val}'"
                    ) from exc
            elif key == "OUTCOME":
                cur.chapter_outcome = val
            elif key == "CHAPTER":
                cur.chapter = int(val)
            elif key == "VISUAL":
                try:
                    cur.visual = Visual(val.lower())
                except ValueError as exc:
                    raise ParseError(
                        f"line {ln_no}: unknown visual '{val}'"
                    ) from exc
            continue

        # --- body line ---
        if cur is None:
            # leading content before any tag → reject; the parser refuses to
            # guess speakers
            if line.strip() and not line.lstrip().startswith("#"):
                raise ParseError(
                    f"line {ln_no}: content before any [AGENT | ROLE] header — "
                    f"refusing to assign a speaker silently"
                )
            continue
        body_buf.append(raw_line)

    # flush trailing turn
    if cur is not None:
        flush()

    if not turns:
        raise ParseError("no turns found in log")

    return turns

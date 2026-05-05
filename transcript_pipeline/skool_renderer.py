# transcript_pipeline.skool_renderer
# =============================================================================
# v0.5 — woven JSONL → self-contained bubble HTML page(s) for Skool / YouTube.
#
# Layer 2 of 3 toward scrolling group-chat mp4. v0.5 = open in browser, scroll,
# see a believable group chat. v0.6 = playwright drives the scroll, ffmpeg
# captures. The HTML emitted here is playwright-friendly: no react, no build,
# stable CSS grid, per-segment data-dwell-ms attrs ready for the capture layer.
#
# Source of truth: docs/SPEC.md. The palette and surface chrome live in that
# file; this module parses them at render time. Do NOT hardcode palette values.
#
# Key implementation rules (per the v0.5 spec):
#
#   1. CODEX is a bubble, not a card. Same shape as everyone else, only color
#      inverts (white bg / black text / graphite border).
#   2. Lane-based turn cap with auto-split:
#        production=12, archive=1000, uncapped=None
#      Overflow → Part 1 / Part 2 / ... with sequential project codes
#      (GL-004, GL-005, ...). Each part = its own HTML file.
#   3. Chapter count 3-8 is a warning band, not a hard cap.
#   4. Instance outlines for parallel conversations of the same agent class.
#      Enumerate by conversation_id. ADAM always instance 1.
#   5. Role label = "[AGENT | model · version]" when transcript carries a
#      model field, falls back to "[AGENT | ROLE]".
#   6. Tool-call recess: per-context palette (different inside white CODEX
#      bubbles vs colored bubbles). ✓/✗ stay the same gray.
#   7. Surface chrome from SPEC.md.
#   8. Validation refuses to render bad title/status/outcome combos.
#
# Layout (1920×1080, usable canvas accounts for skool feed embed):
#   top strip 80px:    title bar
#   left rail 240px:   chapter rail (current chapter highlighted)
#   main column 1440px: bubble stream, max-width 1100px per bubble
#   right rail 240px:  metadata strip
# =============================================================================

from __future__ import annotations

import html as html_lib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from transcript_pipeline.woven_jsonl import WovenFile, WovenTurn, read_woven


# ---------------------------------------------------------------------------
# Spec / palette parsing
# ---------------------------------------------------------------------------


_SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "SPEC.md"


@dataclass
class AgentColors:
    bg: str
    fg: str
    border: str
    glow: str
    border_style: str = "solid"


@dataclass
class Palette:
    agents: dict[str, AgentColors]
    surface: dict[str, str]
    tool_recess_colored: dict[str, str]
    tool_recess_codex: dict[str, str]


_STATUS_CLOSED_SET = {
    "Shipped", "Building", "Incomplete", "Blocked",
    "Fixed", "Audit", "Reset", "Field Notes",
}
_STAGE_SET = {
    "Context", "Problem", "Audit", "Decision",
    "Build", "Fix", "Review", "Ship", "Next",
}
_PROJECT_CODE_RE = re.compile(r"^[A-Z]+-\d{3}$")


def load_palette(spec_path: Path = _SPEC_PATH) -> Palette:
    """Parse the palette tables out of SPEC.md. Section 4.1 = agents,
    4.2 = surface chrome, 4.3 = tool recess. Tables are markdown."""
    if not spec_path.exists():
        raise FileNotFoundError(f"SPEC.md not found at {spec_path}")
    text = spec_path.read_text(encoding="utf-8")

    agents = _parse_agent_table(text)
    surface = _parse_surface_table(text)
    colored_recess, codex_recess = _parse_tool_recess(text)

    return Palette(
        agents=agents,
        surface=surface,
        tool_recess_colored=colored_recess,
        tool_recess_codex=codex_recess,
    )


def _parse_agent_table(spec_text: str) -> dict[str, AgentColors]:
    """Pull the agent palette out of the section 4.1 markdown table."""
    out: dict[str, AgentColors] = {}
    section = _slice_section(spec_text, "### 4.1 Color Code")
    for row in _table_rows(section):
        if len(row) < 5:
            continue
        agent, bg, fg, border, glow = row[0], row[1], row[2], row[3], row[4]
        agent = agent.strip().upper()
        if agent.startswith("AGENT"):  # header row
            continue
        if not agent:
            continue
        style = "dashed" if agent == "SYSTEM" else "solid"
        out[agent] = AgentColors(
            bg=bg.strip(),
            fg=fg.strip(),
            border=border.strip(),
            glow=glow.strip(),
            border_style=style,
        )
    return out


def _parse_surface_table(spec_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    section = _slice_section(spec_text, "### 4.2 Surface chrome")
    for row in _table_rows(section):
        if len(row) < 2:
            continue
        token, value = row[0].strip(), row[1].strip()
        if token.upper() == "TOKEN":
            continue
        if token:
            out[token] = value
    return out


def _parse_tool_recess(spec_text: str) -> tuple[dict[str, str], dict[str, str]]:
    # v0.5.2 — heading is "Tool-call terminal chrome"; the recess palette is
    # the first table under it.
    section = _slice_section(spec_text, "### 4.3 Tool-call terminal chrome")
    if not section:
        # back-compat with older SPEC heading
        section = _slice_section(spec_text, "### 4.3 Tool-call recess")
    colored: dict[str, str] = {}
    codex: dict[str, str] = {}
    for row in _table_rows(section):
        if len(row) < 4:
            continue
        ctx, bg, fg, border = (c.strip() for c in row[:4])
        if ctx.upper() == "CONTEXT":
            continue
        target = codex if "CODEX" in ctx.upper() else colored
        target["bg"] = bg
        target["fg"] = fg
        target["border"] = border
    return colored, codex


def _slice_section(spec_text: str, heading: str) -> str:
    """Return the text from `heading` up to the next heading at the same level."""
    idx = spec_text.find(heading)
    if idx < 0:
        return ""
    rest = spec_text[idx:]
    # next heading at any level
    m = re.search(r"\n##+ ", rest[len(heading):])
    if m:
        return rest[: len(heading) + m.start()]
    return rest


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            continue  # separator row
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# Title validation (spec section 1)
# ---------------------------------------------------------------------------


def validate_title(project_code: str, status: str, outcome: str) -> list[str]:
    """Return list of error messages; empty list = valid."""
    errors: list[str] = []
    if not _PROJECT_CODE_RE.match(project_code):
        errors.append(
            f"project_code {project_code!r} must match ^[A-Z]+-\\d{{3}}$ "
            f"(zero-padded to 3 digits, e.g. GL-004)"
        )
    if status not in _STATUS_CLOSED_SET:
        errors.append(
            f"status {status!r} not in closed set {sorted(_STATUS_CLOSED_SET)}"
        )
    word_count = len(outcome.split())
    if word_count > 6:
        errors.append(
            f"outcome has {word_count} words; max 6 — got {outcome!r}"
        )
    return errors


# ---------------------------------------------------------------------------
# Lane caps (mirrors schema.py — kept here so renderer is self-contained)
# ---------------------------------------------------------------------------


_LANE_CAPS = {"production": 12, "archive": 1000, "uncapped": None}


def split_by_lane(turns: list[WovenTurn], lane: str, project_code: str) -> list[tuple[str, list[WovenTurn]]]:
    """Split into parts by lane cap. Returns list of (part_project_code, turns).
    Sequential codes: GL-004 → GL-004, GL-005, GL-006, ..."""
    cap = _LANE_CAPS.get(lane)
    if cap is None or len(turns) <= cap:
        return [(project_code, turns)]

    m = re.match(r"^([A-Z]+)-(\d+)$", project_code)
    if not m:
        # validator already caught this — shouldn't reach here
        return [(project_code, turns)]
    prefix, num_str = m.group(1), m.group(2)
    base_num = int(num_str)
    width = len(num_str)

    parts: list[tuple[str, list[WovenTurn]]] = []
    for i in range(0, len(turns), cap):
        chunk = turns[i : i + cap]
        # renumber turns 1..N within each part
        for new_no, t in enumerate(chunk, start=1):
            t.turn = new_no
        part_idx = i // cap
        part_code = f"{prefix}-{base_num + part_idx:0{width}d}"
        parts.append((part_code, chunk))
    return parts


# ---------------------------------------------------------------------------
# Segment parsing (CC + CODEX bodies)
# ---------------------------------------------------------------------------


_TOOL_LINE_RE = re.compile(r"^\s*\[tool:\s*(.+?)\]\s*$")
_RESULT_LINE_RE = re.compile(r"^\s*\[result:\s*(.+?)\]\s*$")


@dataclass
class Segment:
    """One slice of a bubble's body. v0.5.2 — tool-call segments now carry
    structured `tool_type` and `command` so the renderer can emit terminal
    chrome (prompt char, badge, color split) per SPEC §4.3."""

    kind: str  # "prose" | "tool-call" | "code-output"
    text: str
    dwell_ms: int = 0
    tool_type: str = ""  # "Bash" | "Write" | "Edit" | ... (tool-call only)
    command: str = ""    # the invocation                  (tool-call only)


# Recognized tool types. First whitespace-delimited token after `[tool:` is
# matched against this list; if it matches we split into (type, command).
# Anything else falls through as a single-line tool-call with empty type.
_TOOL_TYPES = {
    "Bash", "Write", "Edit", "Read", "Grep", "Glob", "WebFetch", "WebSearch",
    "Task", "Codex", "MCP", "MultiEdit", "NotebookEdit", "Diff",
}


def _split_tool_line(text: str) -> tuple[str, str]:
    """Pull the leading tool-type token off a `[tool: ...]` body if it
    matches the known set. Returns (tool_type, command). When no match,
    tool_type='' and the whole text is the command."""
    parts = text.split(None, 1)
    if not parts:
        return "", ""
    head, tail = parts[0], (parts[1] if len(parts) > 1 else "")
    if head in _TOOL_TYPES:
        return head, tail.strip()
    return "", text.strip()


def parse_segments(body: str, agent: str) -> list[Segment]:
    """For CLAUDE-CODE / CODEX, split into (prose / tool-call / code-output).
    All other agents: one prose segment."""
    if agent not in ("CLAUDE-CODE", "CODEX"):
        return [Segment(kind="prose", text=body)]

    segments: list[Segment] = []
    prose_buf: list[str] = []

    def flush_prose():
        if prose_buf:
            joined = "\n".join(prose_buf).strip()
            if joined:
                segments.append(Segment(kind="prose", text=joined))
            prose_buf.clear()

    for line in body.splitlines():
        m_tool = _TOOL_LINE_RE.match(line)
        m_result = _RESULT_LINE_RE.match(line)
        if m_tool:
            flush_prose()
            raw = m_tool.group(1).strip()
            tool_type, command = _split_tool_line(raw)
            segments.append(Segment(
                kind="tool-call",
                text=raw,
                tool_type=tool_type,
                command=command,
            ))
        elif m_result:
            flush_prose()
            segments.append(Segment(kind="code-output", text=m_result.group(1).strip()))
        else:
            prose_buf.append(line)
    flush_prose()

    if not segments:
        segments.append(Segment(kind="prose", text=body))
    return segments


# ---------------------------------------------------------------------------
# Dwell math (per spec)
# ---------------------------------------------------------------------------


# v0.5.2 — fast-pace dwell table per SPEC §4.7. Group-chat pacing, not
# editorial. Was 4000/2500/1500; now 1500/1000/700.
_PROSE_DWELL_BY_STAGE = {
    "Decision": 1500,
    "Ship": 1500,
    "Audit": 1000,
    "Review": 1000,
    "Context": 700,
    "Problem": 700,
    "Build": 700,
    "Fix": 700,
    "Next": 700,
}
_MIN_DWELL_MS = 400
_REQUIRES_HUMAN_PROSE_BONUS = 400  # was 500
_TOOL_CALL_FRACTION = 0.50  # was 0.40
_CODE_OUTPUT_FRACTION = 0.35  # was 0.30


def compute_dwell(segment: Segment, *, stage: str, requires_human: bool) -> int:
    base = _PROSE_DWELL_BY_STAGE.get(stage, 700)
    if segment.kind == "prose":
        v = base + (_REQUIRES_HUMAN_PROSE_BONUS if requires_human else 0)
    elif segment.kind == "tool-call":
        v = int(base * _TOOL_CALL_FRACTION)
    elif segment.kind == "code-output":
        v = int(base * _CODE_OUTPUT_FRACTION)
    else:
        v = base
    return max(_MIN_DWELL_MS, v)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


_AGENT_INITIALS = {
    "ADAM": "AT",
    "GPT": "GP",
    "CLAUDE": "CL",
    "CLAUDE-CODE": "CC",
    "CLAUDE-BROWSER": "CB",
    "CODEX": "CX",
    "GROK": "GR",
    "GEMINI": "GE",
    "SYSTEM": "SY",
}


@dataclass
class RenderRequest:
    project_code: str
    status: str
    outcome: str
    lane: str = "production"
    spec_path: Path = _SPEC_PATH


def _format_role(turn: WovenTurn) -> str:
    if turn.model:
        return f"model · {turn.model}"
    return turn.role or "—"


def _format_timestamp(turn: WovenTurn) -> str:
    if turn.timestamp is None:
        return ""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(turn.timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _outline_class(instance: int) -> str:
    if instance <= 1:
        return ""
    if instance == 2:
        return "outline-2"
    if instance == 3:
        return "outline-3"
    return "outline-4"


def _render_segment(
    seg: Segment, *, dwell_ms: int, in_codex_bubble: bool
) -> str:
    """v0.5.2 — terminal chrome for tool-call cells (prompt char + badge +
    color split + hang-indent for multi-line commands). Recess palette
    inverts inside CODEX bubbles per SPEC §4.3."""
    if seg.kind == "prose":
        safe = html_lib.escape(seg.text)
        return (
            f'<div class="segment seg-prose" data-dwell-ms="{dwell_ms}" '
            f'data-md="1">{safe}</div>'
        )

    recess_class = "seg-recess-codex" if in_codex_bubble else "seg-recess-colored"

    if seg.kind == "tool-call":
        # terminal chrome: $ prompt + badge + colored command
        badge = ""
        if seg.tool_type:
            badge = (
                f'<span class="tool-badge">{html_lib.escape(seg.tool_type.upper())}</span>'
            )
        cmd = seg.command if seg.command else seg.text
        return (
            f'<div class="segment seg-tool {recess_class}" '
            f'data-dwell-ms="{dwell_ms}">{badge}'
            f'<span class="prompt">$</span>'
            f'<span class="cmd">{html_lib.escape(cmd)}</span>'
            f'</div>'
        )

    # code-output
    return (
        f'<div class="segment seg-output {recess_class}" '
        f'data-dwell-ms="{dwell_ms}">'
        f'<span class="output">{html_lib.escape(seg.text)}</span>'
        f'</div>'
    )


def _carry_indicator_html(carried_to: list[str]) -> str:
    if not carried_to:
        return ""
    chips: list[str] = []
    for agent in carried_to:
        abbrev = _AGENT_INITIALS.get(agent, agent[:2])
        chips.append(
            f'<span class="carry-chip">👍 → {html_lib.escape(abbrev)}</span>'
        )
    return f'<div class="carry-indicators">{"".join(chips)}</div>'


def _render_bubble(turn: WovenTurn) -> str:
    """v0.5.2 — no avatar circle, no side-rail wrapping. Bubble row only.
    Carry-tagged ADAM bubbles are filtered upstream and never reach this
    function; source bubbles with non-empty carried_to render the indicator."""
    is_right = turn.agent == "ADAM"
    row_class = "row right" if is_right else "row"
    role = _format_role(turn)
    timestamp = _format_timestamp(turn)
    instance_suffix = "" if turn.instance <= 1 else f" #{turn.instance}"
    outline_cls = _outline_class(turn.instance)
    in_codex = turn.agent == "CODEX"
    bubble_classes = " ".join(
        c for c in [
            "bubble",
            f"agent-{turn.agent.lower().replace('-', '_')}",
            outline_cls,
            "low-confidence" if turn.requires_human else "",
        ] if c
    )

    segments = parse_segments(turn.body, turn.agent)
    seg_html: list[str] = []
    for s in segments:
        d = compute_dwell(s, stage=turn.stage, requires_human=turn.requires_human)
        seg_html.append(
            _render_segment(s, dwell_ms=d, in_codex_bubble=in_codex)
        )

    head = (
        '<div class="head">'
        f'<span class="speaker">{html_lib.escape(turn.agent)}{instance_suffix}</span>'
        f'<span class="role">{html_lib.escape(role)}</span>'
        f'<span class="ts">{html_lib.escape(timestamp)}</span>'
        '</div>'
    )

    carry_html = _carry_indicator_html(turn.carried_to)

    bubble = (
        f'<div class="{bubble_classes}">'
        f'{head}'
        '<div class="body">'
        f'{"".join(seg_html)}'
        '</div>'
        f'{carry_html}'
        '</div>'
    )

    chapter_attr = f'data-chapter="{turn.chapter}"'
    return (
        f'<div class="{row_class}" {chapter_attr}>{bubble}</div>'
    )


def _render_inline_divider(turn: WovenTurn) -> str:
    """v0.5.2 — chapter changes render INLINE between bubbles, not in a
    side rail. First chapter (the title bar already frames it) is
    suppressed by the caller, not here."""
    text = f"[CHAPTER {turn.chapter:02d}] {turn.stage}"
    if turn.chapter_outcome:
        text += f" — {turn.chapter_outcome}"
    return (
        f'<div class="chapter-divider" data-chapter="{turn.chapter}">'
        f'<span class="rule"></span>'
        f'<span class="marker">{html_lib.escape(text)}</span>'
        f'<span class="rule"></span>'
        f'</div>'
    )


def _build_css(palette: Palette) -> str:
    """Build the per-agent CSS rules from the palette table."""
    surface = palette.surface
    page_bg = surface.get("page.bg", "#0B0D11")
    container_bg = surface.get("container.bg", "#11141A")
    rule = surface.get("rule", "#1F2530")
    ink = surface.get("ink.primary", "#E6EDF3")
    ink_dim = surface.get("ink.dim", "#98A2B3")
    ink_muted = surface.get("ink.muted", "#6B7280")

    tc = palette.tool_recess_colored
    tcx = palette.tool_recess_codex

    agent_rules: list[str] = []
    for name, c in palette.agents.items():
        cls_name = name.lower().replace("-", "_")
        agent_rules.append(
            f".bubble.agent-{cls_name}{{"
            f"background:{c.bg};color:{c.fg};"
            f"border:1px {c.border_style} {c.border};"
            f"box-shadow:0 8px 24px -16px {c.glow};"
            "}"
            f".avatar.avatar-{cls_name}{{"
            f"background:{c.bg};color:{c.fg};border-color:{c.border};"
            "}"
        )

    return f"""
:root {{
  --bg: {page_bg};
  --container-bg: {container_bg};
  --rule: {rule};
  --ink: {ink};
  --ink-dim: {ink_dim};
  --ink-muted: {ink_muted};
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--ink); }}
body {{
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-size: 22px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.code, code, pre, .body, .seg-prose, .role, .ts {{
  font-family: 'JetBrains Mono', ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}}

/* ── v0.5.2 layout: 16:9, slim title, no rails, no avatars ── */
.frame {{
  width: 1920px; min-height: 1080px;
  display: grid;
  grid-template-rows: 56px 1fr;
  grid-template-columns: 1fr;
  background: var(--bg);
  margin: 0 auto;
}}
.title-bar {{
  display: flex; align-items: center; gap: 14px;
  padding: 0 28px;
  border-bottom: 1px solid var(--rule);
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-size: 26px;
  letter-spacing: -0.01em;
}}
.title-bar .code {{ color: var(--ink); }}
.title-bar .sep {{ color: var(--ink-muted); }}
.title-bar .status {{ color: var(--ink); }}
.title-bar .outcome {{ color: var(--ink-dim); }}
.title-bar .part-tag {{
  margin-left: auto; font-size: 14px;
  padding: 3px 10px; border: 1px solid var(--rule); border-radius: 999px;
  color: var(--ink-dim);
  font-family: ui-monospace, Menlo, Consolas, monospace;
}}

.main {{
  padding: 24px 80px 80px;
  background: var(--bg);
  overflow-y: auto;
}}
.transcript {{
  display: flex; flex-direction: column; gap: 0;
  width: 100%;
  max-width: none;
}}

.row {{ display: flex; margin: 10px 0; }}
.row.right {{ justify-content: flex-end; }}

.bubble {{
  padding: 14px 18px;
  border-radius: 18px;
  max-width: 1400px;
  word-wrap: break-word;
  position: relative;
}}
.bubble.outline-2 {{ box-shadow: 0 0 0 2px #ffffff, 0 8px 24px -16px rgba(0,0,0,0.4); }}
.bubble.outline-3 {{ box-shadow: 0 0 0 2px #ffffff, 0 0 0 6px var(--bg), 0 0 0 8px #ffffff; }}
.bubble.outline-4 {{
  box-shadow:
    0 0 0 2px #ffffff,
    0 0 0 6px var(--bg),
    0 0 0 8px #ffffff,
    0 0 0 12px var(--bg),
    0 0 0 14px #ffffff;
}}
.bubble.low-confidence {{ outline: 2px dashed #FFD84A; outline-offset: 3px; }}

.bubble .head {{
  display: flex; gap: 10px; align-items: baseline;
  margin-bottom: 6px;
  font-size: 18px;
  letter-spacing: 0.04em;
}}
.bubble .speaker {{
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 26px;
}}
.bubble .role {{
  font-size: 14px;
  opacity: 0.78;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(0,0,0,0.18);
}}
.bubble.agent-codex .role {{ background: rgba(0,0,0,0.06); }}
.bubble .ts {{
  margin-left: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 14px;
  opacity: 0.6;
}}

.bubble .body {{ display: flex; flex-direction: column; gap: 6px; }}
.segment {{ font-size: 22px; line-height: 1.5; }}
.segment.seg-prose {{ white-space: pre-wrap; }}

/* ── v0.5.2 inline chapter divider ── */
.chapter-divider {{
  display: flex; align-items: center; gap: 12px;
  margin: 20px 0 10px;
}}
.chapter-divider .rule {{
  flex: 1; height: 1px; background: var(--rule);
}}
.chapter-divider .marker {{
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 14px; color: var(--ink-dim);
  letter-spacing: 0.04em;
}}

/* ── v0.5.2 terminal chrome on tool-call cells ── */
.seg-recess-colored {{
  background: {tc.get('bg', '#000000')};
  color: {tc.get('fg', '#5A626F')};
  border: 1px solid {tc.get('border', '#1A1A1A')};
}}
.seg-recess-codex {{
  background: {tcx.get('bg', '#EBEBEB')};
  color: {tcx.get('fg', '#B5B5B5')};
  border: 1px solid {tcx.get('border', '#D4D4D4')};
}}
.seg-tool, .seg-output {{
  border-radius: 8px;
  padding: 8px 12px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 16px;
  margin: 10px 0;
  position: relative;
  overflow-x: auto;
}}
.seg-tool .prompt {{
  display: inline-block;
  width: 16px;
  color: inherit;          /* recess.fg */
  font-weight: 600;
  user-select: none;
  margin-right: 6px;
}}
.seg-tool .cmd {{
  color: var(--ink);
  font-weight: 500;
  white-space: pre-wrap;
  /* hang-indent: when a long command wraps, lines align under the $ */
  display: inline;
  padding-left: 0;
}}
.bubble.agent-codex .seg-tool .cmd {{
  color: #0B0D11;          /* dark text inside the white CODEX bubble */
}}
.seg-tool .tool-badge {{
  position: absolute;
  top: 6px; right: 10px;
  font-size: 10px; letter-spacing: 0.10em;
  color: #3A4250;
  text-transform: uppercase;
  user-select: none;
}}
.bubble.agent-codex .seg-tool .tool-badge {{
  color: #B5B5B5;
}}
.seg-output {{
  white-space: pre-wrap;
  font-weight: 400;
}}
.seg-output .output {{ color: inherit; }}

/* ── v0.5.2 carry indicator ── */
.carry-indicators {{
  display: flex; gap: 8px; flex-wrap: wrap;
  position: absolute;
  bottom: -10px; right: 14px;
}}
.carry-chip {{
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 14px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--rule);
  color: var(--ink-dim);
}}

pre {{ max-height: 400px; overflow: hidden; position: relative; margin: 0; }}
pre::after {{
  content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 60px;
  background: linear-gradient(to bottom, transparent, currentColor);
  opacity: 0.06;
  pointer-events: none;
}}
code.hljs {{ background: transparent; }}

/* per-agent palette (parsed from SPEC.md) */
{"".join(agent_rules)}
"""


# ---------------------------------------------------------------------------
# Top-level: render one woven file → N HTML parts
# ---------------------------------------------------------------------------


def render_woven_to_html_parts(
    woven: WovenFile,
    *,
    request: RenderRequest,
    out_stem: Path,
) -> list[Path]:
    """Render a woven jsonl into self-contained HTML, one file per
    lane-cap-split part. Returns list of written paths."""
    errors = validate_title(request.project_code, request.status, request.outcome)
    if errors:
        raise ValueError("title validation failed:\n  - " + "\n  - ".join(errors))

    palette = load_palette(request.spec_path)
    parts = split_by_lane(list(woven.turns), request.lane, request.project_code)

    out_paths: list[Path] = []
    for idx, (part_code, part_turns) in enumerate(parts, start=1):
        page = _render_page(
            project_code=part_code,
            status=request.status,
            outcome=request.outcome,
            turns=part_turns,
            palette=palette,
            part_num=idx,
            total_parts=len(parts),
        )
        path = Path(f"{out_stem}-part-{idx:02d}.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
        out_paths.append(path)
    return out_paths


def _render_page(
    *,
    project_code: str,
    status: str,
    outcome: str,
    turns: list[WovenTurn],
    palette: Palette,
    part_num: int,
    total_parts: int,
) -> str:
    """v0.5.2 — no rails, no metadata strip, no avatars. Inline chapter
    dividers between bubbles; carry-tagged ADAM bubbles skipped."""
    css = _build_css(palette)
    title = f"{project_code} — {status} — {outcome}"

    body_html: list[str] = []
    last_chapter = -1
    for t in turns:
        # v0.5.2 — skip carry-tagged ADAM bubbles entirely. The source
        # bubble already said this; the indicator on the source carries
        # the cross-paste signal.
        if t.is_carry and t.agent == "ADAM":
            continue
        # inline chapter divider on chapter change (suppress before first
        # non-skipped turn — the title bar frames chapter 01)
        if last_chapter != -1 and t.chapter != last_chapter:
            body_html.append(_render_inline_divider(t))
        last_chapter = t.chapter
        body_html.append(_render_bubble(t))

    part_tag = (
        f"part {part_num:02d} / {total_parts:02d}"
        if total_parts > 1 else "single"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1920">
<meta name="robots" content="noindex">
<title>{html_lib.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/atom-one-dark.min.css">
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
<style>{css}</style>
</head>
<body>
<div class="frame">
  <div class="title-bar">
    <span class="code">{html_lib.escape(project_code)}</span>
    <span class="sep">—</span>
    <span class="status">{html_lib.escape(status)}</span>
    <span class="sep">—</span>
    <span class="outcome">{html_lib.escape(outcome)}</span>
    <span class="part-tag">{html_lib.escape(part_tag)}</span>
  </div>
  <main class="main">
    <div class="transcript">
      {"".join(body_html)}
    </div>
  </main>
</div>
<script>
  // Markdown rendering pass over .seg-prose blocks. We keep raw text in the
  // attribute to survive Skool sanitization; marked renders client-side.
  document.querySelectorAll('[data-md="1"]').forEach((el) => {{
    const raw = el.textContent;
    el.innerHTML = marked.parse(raw, {{ breaks: true, gfm: true }});
  }});
  // Syntax highlight code blocks.
  document.querySelectorAll('pre code').forEach((el) => {{
    try {{ hljs.highlightElement(el); }} catch (e) {{}}
  }});
</script>
</body>
</html>"""


def render_file(
    woven_path: Path,
    *,
    request: RenderRequest,
    out_stem: Path,
) -> list[Path]:
    """Convenience: read a .woven.jsonl and render to HTML parts."""
    woven = read_woven(woven_path)
    return render_woven_to_html_parts(woven, request=request, out_stem=out_stem)

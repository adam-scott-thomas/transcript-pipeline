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
    kind: str  # "prose" | "tool-call" | "code-output"
    text: str
    dwell_ms: int = 0


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
            segments.append(Segment(kind="tool-call", text=m_tool.group(1).strip()))
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


_PROSE_DWELL_BY_STAGE = {
    "Audit": 4000,
    "Decision": 4000,
    "Problem": 2500,
    "Review": 2500,
    "Ship": 2500,
    "Context": 1500,
    "Build": 1500,
    "Fix": 1500,
    "Next": 1500,
}
_MIN_DWELL_MS = 400


def compute_dwell(segment: Segment, *, stage: str, requires_human: bool) -> int:
    base = _PROSE_DWELL_BY_STAGE.get(stage, 1500)
    if segment.kind == "prose":
        v = base + (500 if requires_human else 0)
    elif segment.kind == "tool-call":
        v = int(base * 0.40)
    elif segment.kind == "code-output":
        v = int(base * 0.30)
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


def _render_segment(seg: Segment, *, kind_class: str, dwell_ms: int, in_codex_bubble: bool) -> str:
    safe = html_lib.escape(seg.text)
    classes = f"segment seg-{kind_class}"
    if seg.kind != "prose" and in_codex_bubble:
        classes += " seg-recess-codex"
    elif seg.kind != "prose":
        classes += " seg-recess-colored"
    if seg.kind == "prose":
        # marked.js renders this client-side
        return (
            f'<div class="{classes}" data-dwell-ms="{dwell_ms}" data-md="1">'
            f'{safe}</div>'
        )
    return (
        f'<div class="{classes}" data-dwell-ms="{dwell_ms}">'
        f'{safe}</div>'
    )


def _render_bubble(turn: WovenTurn) -> str:
    is_right = turn.agent == "ADAM"
    row_class = "row right" if is_right else "row"
    initials = _AGENT_INITIALS.get(turn.agent, turn.agent[:2])
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
    avatar_classes = f"avatar avatar-{turn.agent.lower().replace('-', '_')}"

    segments = parse_segments(turn.body, turn.agent)
    seg_html: list[str] = []
    for s in segments:
        d = compute_dwell(s, stage=turn.stage, requires_human=turn.requires_human)
        seg_html.append(
            _render_segment(s, kind_class=s.kind, dwell_ms=d, in_codex_bubble=in_codex)
        )

    head = (
        '<div class="head">'
        f'<span class="speaker">{html_lib.escape(turn.agent)}{instance_suffix}</span>'
        f'<span class="role">{html_lib.escape(role)}</span>'
        f'<span class="ts">{html_lib.escape(timestamp)}</span>'
        '</div>'
    )

    bubble = (
        f'<div class="{bubble_classes}">'
        f'{head}'
        '<div class="body">'
        f'{"".join(seg_html)}'
        '</div>'
        '</div>'
    )

    chapter_attr = f'data-chapter="{turn.chapter}"'
    return (
        f'<div class="{row_class}" {chapter_attr}>'
        f'<div class="{avatar_classes}">{html_lib.escape(initials)}</div>'
        f'<div class="bubble-wrap">{bubble}</div>'
        '</div>'
    )


def _render_chapter_rail(turns: list[WovenTurn]) -> str:
    seen: dict[int, tuple[str, str]] = {}
    order: list[int] = []
    for t in turns:
        if t.chapter not in seen:
            seen[t.chapter] = (t.stage, t.chapter_outcome)
            order.append(t.chapter)
    parts = ['<nav class="chapter-rail">']
    for n in order:
        stage, outcome = seen[n]
        text = f"[CHAPTER {n:02d}] {stage}"
        if outcome:
            text += f" — {outcome}"
        parts.append(
            f'<div class="chapter-marker" data-chapter="{n}">'
            f'<span class="chapter-num">{n:02d}</span>'
            f'<span class="chapter-text">{html_lib.escape(text)}</span>'
            f'</div>'
        )
    parts.append("</nav>")
    return "".join(parts)


def _render_metadata(turns: list[WovenTurn]) -> str:
    n_low = sum(1 for t in turns if t.requires_human)
    return (
        '<aside class="metadata">'
        '<div class="meta-block">'
        '<div class="meta-label">turns</div>'
        f'<div class="meta-value">{len(turns)}</div>'
        '</div>'
        '<div class="meta-block">'
        '<div class="meta-label">low-confidence</div>'
        f'<div class="meta-value">{n_low}</div>'
        '</div>'
        '<div class="meta-block">'
        '<div class="meta-label">version</div>'
        '<div class="meta-value">v1.0</div>'
        '</div>'
        '</aside>'
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

/* ── 16:9 grid ── */
.frame {{
  width: 1920px; min-height: 1080px;
  display: grid;
  grid-template-rows: 80px 1fr;
  grid-template-columns: 240px 1440px 240px;
  background: var(--bg);
  margin: 0 auto;
}}
.title-bar {{
  grid-column: 1 / -1;
  display: flex; align-items: center; gap: 16px;
  padding: 0 28px;
  border-bottom: 1px solid var(--rule);
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-size: 36px;
  letter-spacing: -0.01em;
}}
.title-bar .code {{ color: var(--ink); }}
.title-bar .sep {{ color: var(--ink-muted); }}
.title-bar .status {{ color: var(--ink); }}
.title-bar .outcome {{ color: var(--ink-dim); }}
.title-bar .part-tag {{
  margin-left: auto; font-size: 18px;
  padding: 4px 10px; border: 1px solid var(--rule); border-radius: 999px;
  color: var(--ink-dim);
  font-family: ui-monospace, Menlo, Consolas, monospace;
}}

.chapter-rail {{
  border-right: 1px solid var(--rule);
  padding: 24px 14px;
  overflow-y: auto;
}}
.chapter-marker {{
  display: flex; gap: 8px; align-items: flex-start;
  padding: 10px 8px; margin-bottom: 4px;
  border-radius: 8px;
  font-size: 18px;
  color: var(--ink-dim);
}}
.chapter-marker.active {{ background: var(--container-bg); color: var(--ink); }}
.chapter-marker .chapter-num {{
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 16px;
  opacity: 0.7;
}}
.chapter-marker .chapter-text {{
  flex: 1;
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-size: 20px;
  line-height: 1.3;
}}

.main {{
  padding: 24px 32px 80px;
  background: var(--bg);
  overflow-y: auto;
}}
.transcript {{
  background: var(--container-bg);
  border: 1px solid var(--rule);
  border-radius: 18px;
  padding: 24px;
  display: flex; flex-direction: column; gap: 18px;
}}

.row {{ display: flex; gap: 12px; align-items: flex-end; }}
.row.right {{ flex-direction: row-reverse; }}

.avatar {{
  width: 36px; height: 36px;
  border-radius: 50%;
  display: grid; place-items: center;
  font-size: 12px; font-weight: 700; letter-spacing: 0.04em;
  border: 1px solid var(--rule);
  flex: none;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}}
.bubble-wrap {{ flex: 1; min-width: 0; max-width: 1100px; }}

.bubble {{
  padding: 14px 18px;
  border-radius: 18px;
  max-width: 1100px;
  word-wrap: break-word;
}}
.bubble.outline-2 {{ box-shadow: 0 0 0 1px #ffffff, 0 8px 24px -16px rgba(0,0,0,0.4); }}
.bubble.outline-3 {{ box-shadow: 0 0 0 1px #ffffff, 0 0 0 4px transparent, 0 0 0 5px #ffffff; }}
.bubble.outline-4 {{
  box-shadow:
    0 0 0 1px #ffffff,
    0 0 0 4px transparent,
    0 0 0 5px #ffffff,
    0 0 0 8px transparent,
    0 0 0 9px #ffffff;
}}
.bubble.low-confidence {{ outline: 1px dashed currentColor; outline-offset: 2px; }}

.bubble .head {{
  display: flex; gap: 10px; align-items: baseline;
  margin-bottom: 8px;
  font-size: 20px;
  letter-spacing: 0.04em;
}}
.bubble .speaker {{
  font-family: 'Instrument Serif', ui-serif, Georgia, serif;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 28px;
}}
.bubble .role {{
  font-size: 18px;
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
  font-size: 18px;
  opacity: 0.6;
}}

.bubble .body {{ display: flex; flex-direction: column; gap: 8px; }}
.segment {{ font-size: 22px; line-height: 1.5; }}
.segment.seg-prose {{ white-space: pre-wrap; }}

.seg-recess-colored {{
  background: {tc.get('bg', '#000000')};
  color: {tc.get('fg', '#5A626F')};
  border: 1px solid {tc.get('border', '#1A1A1A')};
  border-radius: 8px;
  padding: 8px 12px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 18px;
  white-space: pre-wrap;
  overflow-x: auto;
}}
.seg-recess-codex {{
  background: {tcx.get('bg', '#EBEBEB')};
  color: {tcx.get('fg', '#B5B5B5')};
  border: 1px solid {tcx.get('border', '#D4D4D4')};
  border-radius: 8px;
  padding: 8px 12px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 18px;
  white-space: pre-wrap;
  overflow-x: auto;
}}

pre {{ max-height: 400px; overflow: hidden; position: relative; margin: 0; }}
pre::after {{
  content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 60px;
  background: linear-gradient(to bottom, transparent, currentColor);
  opacity: 0.06;
  pointer-events: none;
}}
code.hljs {{ background: transparent; }}

.metadata {{
  border-left: 1px solid var(--rule);
  padding: 24px 18px;
  display: flex; flex-direction: column; gap: 18px;
  font-size: 18px;
}}
.meta-block .meta-label {{
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 14px;
  color: var(--ink-muted);
}}
.meta-block .meta-value {{
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 24px;
  color: var(--ink);
  margin-top: 4px;
}}

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
    css = _build_css(palette)
    title = f"{project_code} — {status} — {outcome}"
    body_html: list[str] = []
    last_chapter = -1
    for t in turns:
        body_html.append(_render_bubble(t))

    chapter_rail = _render_chapter_rail(turns)
    metadata = _render_metadata(turns)
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
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
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
  {chapter_rail}
  <main class="main">
    <div class="transcript">
      {"".join(body_html)}
    </div>
  </main>
  {metadata}
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
  // Active-chapter highlight: scroll-link the rail markers to the row in view.
  const rail = document.querySelectorAll('.chapter-marker');
  const rows = document.querySelectorAll('.row[data-chapter]');
  function syncActive() {{
    const yMid = window.scrollY + window.innerHeight / 2;
    let bestChap = null, bestDist = Infinity;
    rows.forEach((r) => {{
      const t = r.getBoundingClientRect().top + window.scrollY;
      const d = Math.abs(t - yMid);
      if (d < bestDist) {{ bestDist = d; bestChap = r.getAttribute('data-chapter'); }}
    }});
    rail.forEach((m) => {{
      m.classList.toggle('active', m.getAttribute('data-chapter') === bestChap);
    }});
  }}
  window.addEventListener('scroll', syncActive, {{ passive: true }});
  syncActive();
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

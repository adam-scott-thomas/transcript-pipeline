# transcript_pipeline.html_renderer
# =============================================================================
# Transcript → standalone Skool-overlay-quality HTML page.
#
# Why a separate renderer from `renderer.py`:
#   - `renderer.py` emits ASCII bubbles + bubbles.json (machine-friendly,
#     monospace transcript). That's the canonical artifact the video frame
#     generator consumes downstream.
#   - This module emits HTML for direct paste into Skool posts. Different
#     audience (humans reading on the web), different aesthetic (rich
#     colors-when-decided, currently all-black per format spec v1.0),
#     different output shape (one HTML file).
#
# Design choices:
#   - Single self-contained HTML file. No external CSS, no JS, no fonts.
#     Skool sanitizes pasted HTML; minimal surface = maximum survival.
#   - Bubble style mirrors `/skool/sample-terminal-colors` reference page.
#   - `instance` field drives outline weight:
#         instance=1 → no extra outline
#         instance=2 → 1px white outline
#         instance=3 → double outline (2px white + 1px gap simulated via box-shadow)
#         instance>=4 → triple outline (rare, but supported)
#   - CODEX always renders as a white card per spec section 4.
#   - Tool annotations in the body (lines like `[tool: ...]`, `[result: ...]`)
#     get pulled out of the bubble proper and rendered as a graphite chip
#     beneath, matching the live sample's tool-call style.
#   - All-black bubbles until per-agent colors are locked. Speaker labels
#     stay distinct (uppercase + role tag), and instance outlines are the
#     primary disambiguator until colors are decided.
# =============================================================================

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path

from transcript_pipeline.schema import Agent, Transcript, Turn, Visual


# ---------------------------------------------------------------------------
# Tool-annotation extraction
# ---------------------------------------------------------------------------


_TOOL_LINE_RE = re.compile(r"^\s*\[(tool|result):\s*(.+?)\]\s*$")


def _split_body_and_tools(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a turn body into (prose, tool_annotations).

    Tool-annotations are lines like `[tool: Bash mkdir -p ...]` that the
    cc_jsonl adapter folds in. They render as a separate graphite chip
    under the bubble; the prose stays in the bubble itself."""
    prose_lines: list[str] = []
    tools: list[tuple[str, str]] = []
    for line in body.splitlines():
        m = _TOOL_LINE_RE.match(line)
        if m:
            tools.append((m.group(1), m.group(2)))
        else:
            prose_lines.append(line)
    prose = "\n".join(prose_lines).strip()
    return prose, tools


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------


def _outline_class(instance: int) -> str:
    if instance <= 1:
        return ""
    if instance == 2:
        return "outline-1"
    if instance == 3:
        return "outline-2"
    return "outline-3"


def _agent_class(agent: Agent) -> str:
    """CSS class slug for an agent. Matches the existing /skool/ sample."""
    return {
        Agent.ADAM: "b-adam",
        Agent.GPT: "b-gpt",
        Agent.CLAUDE: "b-claude",
        Agent.CLAUDE_CODE: "b-code",
        Agent.CLAUDE_BROWSER: "b-browser",
        Agent.CODEX: "b-codex",
        Agent.SYSTEM: "b-system",
    }[agent]


def _avatar_class(agent: Agent) -> str:
    return {
        Agent.ADAM: "a-adam",
        Agent.GPT: "a-gpt",
        Agent.CLAUDE: "a-claude",
        Agent.CLAUDE_CODE: "a-code",
        Agent.CLAUDE_BROWSER: "a-browser",
        Agent.CODEX: "a-codex",
        Agent.SYSTEM: "a-system",
    }[agent]


def _avatar_initials(agent: Agent) -> str:
    return {
        Agent.ADAM: "AT",
        Agent.GPT: "GP",
        Agent.CLAUDE: "CL",
        Agent.CLAUDE_CODE: "CC",
        Agent.CLAUDE_BROWSER: "CB",
        Agent.CODEX: "CX",
        Agent.SYSTEM: "SY",
    }[agent]


def _ts_label(turn: Turn) -> str:
    if turn.timestamp is None:
        return f"#{turn.turn:03d}"
    dt = datetime.fromtimestamp(turn.timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# CSS — black-bubble v1.0 with instance outlines
# ---------------------------------------------------------------------------


_CSS = r"""
:root {
  --bg: #0b0d11;
  --bg-elev: #11141a;
  --bg-elev-2: #161a22;
  --rule: #1f2530;
  --ink: #e6edf3;
  --ink-dim: #98a2b3;
  --ink-muted: #6b7280;
  --bubble-bg: #000000;
  --bubble-fg: #ffffff;
  --bubble-bd: #2a2a2a;
  --tool-bg: #000000;
  --tool-fg: #5a626f;
  --codex-bg: #ffffff;
  --codex-fg: #0b0d11;
  --codex-border: #2a3140;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 20px 80px; }
header.title {
  border-bottom: 1px solid var(--rule);
  padding-bottom: 18px;
  margin-bottom: 28px;
}
header.title h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: -0.01em; }
header.title p { margin: 0; color: var(--ink-dim); font-size: 14px; }
header.title .pill {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid var(--rule);
  border-radius: 999px;
  font-size: 11px;
  color: var(--ink-dim);
  margin-right: 6px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.transcript {
  background: var(--bg-elev);
  border: 1px solid var(--rule);
  border-radius: 16px;
  padding: 20px 18px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.row { display: flex; gap: 10px; align-items: flex-end; }
.row.right { flex-direction: row-reverse; }
.avatar {
  width: 28px; height: 28px;
  border-radius: 50%;
  background: var(--bg-elev-2);
  color: var(--ink);
  display: grid; place-items: center;
  font-size: 11px; font-weight: 700; letter-spacing: 0.03em;
  border: 1px solid var(--rule);
  flex: none;
}
.bubble {
  background: var(--bubble-bg);
  color: var(--bubble-fg);
  border: 1px solid var(--bubble-bd);
  border-radius: 16px;
  padding: 10px 14px 11px;
  max-width: min(78%, 720px);
  position: relative;
}
.bubble.outline-1 { box-shadow: 0 0 0 1px #ffffff; }
.bubble.outline-2 { box-shadow: 0 0 0 1px #ffffff, 0 0 0 4px #000000, 0 0 0 5px #ffffff; }
.bubble.outline-3 {
  box-shadow:
    0 0 0 1px #ffffff,
    0 0 0 4px #000000,
    0 0 0 5px #ffffff,
    0 0 0 8px #000000,
    0 0 0 9px #ffffff;
}
.bubble.b-codex {
  background: var(--codex-bg);
  color: var(--codex-fg);
  border-color: var(--codex-border);
}
.bubble.b-system { font-style: italic; border-style: dashed; }
.bubble .head {
  display: flex; align-items: baseline; gap: 8px;
  margin-bottom: 4px; font-size: 11.5px; letter-spacing: 0.04em;
}
.bubble .speaker {
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
}
.bubble .role {
  opacity: 0.78; text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 10.5px; padding: 1px 6px; border-radius: 4px;
  background: rgba(255,255,255,0.10);
}
.bubble.b-codex .role { background: rgba(0,0,0,0.10); }
.bubble .ts {
  margin-left: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  opacity: 0.6; font-size: 10.5px;
}
.bubble .body {
  font-size: 14.5px; line-height: 1.55;
  word-wrap: break-word; white-space: pre-wrap;
}
.toolcall {
  margin: 4px 0 0 38px;
  background: var(--tool-bg);
  border: 1px solid #1a1a1a;
  border-radius: 8px;
  padding: 7px 10px;
  font-family: ui-monospace, "JetBrains Mono", "Fira Code", Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--tool-fg);
  line-height: 1.5;
  max-width: min(78%, 720px);
  overflow-x: auto;
  white-space: pre;
}
.row.right .toolcall { margin: 4px 38px 0 0; }
.chapter-bar {
  margin: 22px 0 6px;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ink-dim);
  border-top: 1px solid var(--rule);
  padding-top: 16px;
}
footer.foot {
  margin-top: 36px;
  padding-top: 18px;
  border-top: 1px solid var(--rule);
  color: var(--ink-muted);
  font-size: 12px;
}
@media (max-width: 540px) {
  body { font-size: 14px; }
  .wrap { padding: 22px 14px 60px; }
  .bubble { max-width: 86%; padding: 9px 12px 10px; border-radius: 14px; }
  .avatar { width: 24px; height: 24px; font-size: 10px; }
}
"""


# ---------------------------------------------------------------------------
# Public render
# ---------------------------------------------------------------------------


def render_html(t: Transcript, *, page_title: str | None = None) -> str:
    """Return a full standalone HTML document for `t`."""
    title = page_title or t.header.title_line
    title_esc = html.escape(title)

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        '<meta name="robots" content="noindex">',
        f"<title>{title_esc}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        '<header class="title">',
        f'<h1>{title_esc}</h1>',
        f'<p><span class="pill">v1</span>'
        f'<span>{t.header.session_id} · {len(t.turns)} turns'
        f' · {t.chapter_count} chapter(s)</span></p>',
        "</header>",
        '<div class="transcript">',
    ]

    last_chapter = -1
    for turn in t.turns:
        if turn.chapter != last_chapter:
            parts.append(_chapter_bar(turn))
            last_chapter = turn.chapter
        parts.append(_render_row(turn))

    parts.extend([
        "</div>",
        '<footer class="foot">'
        f'<span>generated {datetime.now(timezone.utc).isoformat(timespec="seconds")}</span>'
        '</footer>',
        "</div>",
        "</body>",
        "</html>",
    ])
    return "\n".join(parts)


def render_html_to_file(t: Transcript, path: Path, *, page_title: str | None = None) -> Path:
    """Render and write to disk."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(t, page_title=page_title), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _chapter_bar(turn: Turn) -> str:
    label = f"CHAPTER {turn.chapter:02d} · {turn.stage.value}"
    if turn.chapter_outcome:
        label += f" — {html.escape(turn.chapter_outcome)}"
    return f'<div class="chapter-bar">{html.escape(label)}</div>'


def _render_row(turn: Turn) -> str:
    is_right = turn.agent is Agent.ADAM
    row_class = "row right" if is_right else "row"

    prose, tools = _split_body_and_tools(turn.body)

    bubble_cls = " ".join(c for c in [
        "bubble",
        _agent_class(turn.agent),
        _outline_class(turn.instance),
    ] if c)

    avatar = (
        f'<div class="avatar {_avatar_class(turn.agent)}">'
        f'{_avatar_initials(turn.agent)}</div>'
    )

    instance_suffix = "" if turn.instance <= 1 else f' #{turn.instance}'
    head = (
        '<div class="head">'
        f'<span class="speaker">{html.escape(turn.agent.value)}{instance_suffix}</span>'
        f'<span class="role">{html.escape(turn.role)}</span>'
        f'<span class="ts">{html.escape(_ts_label(turn))}</span>'
        '</div>'
    )

    body_html = (
        f'<div class="body">{html.escape(prose).replace(chr(10), "<br>")}</div>'
        if prose else
        '<div class="body"><em style="opacity:0.6">(empty)</em></div>'
    )

    tool_chips = "".join(
        f'<div class="toolcall">{html.escape(kind)} → {html.escape(arg)}</div>'
        for kind, arg in tools
    )

    bubble = f'<div class="{bubble_cls}">{head}{body_html}</div>'
    inner = f'{avatar}<div>{bubble}{tool_chips}</div>'
    return f'<div class="{row_class}">{inner}</div>'

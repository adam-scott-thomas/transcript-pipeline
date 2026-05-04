# transcript_pipeline.adapters.cc_jsonl
# =============================================================================
# Claude Code session JSONL → list[ParsedTurn].
#
# Source: `~/.claude/projects/<project>/<sessionId>.jsonl` — one line per event.
# Event shape varies; we care about:
#
#   {type: "user",      message: {content: <string|list>},   timestamp: ISO}
#   {type: "assistant", message: {content: <string|list>},   timestamp: ISO}
#   {type: "tool_use",  name, input}                          (subordinate to assistant)
#   {type: "tool_result", tool_use_id, content}               (subordinate to user)
#
# We compress each user turn to AGENT=ADAM/ROLE=HUMAN, each assistant turn
# to AGENT=CLAUDE-CODE/ROLE=IMPLEMENTATION (Adam's standard pairing for
# Claude Code sessions). Tool calls are rolled into the preceding assistant
# turn's body as a short trailing annotation — they keep the bubble count
# down while still showing the work happened.
#
# Stage tagging is NOT done here. The weaver / classifier handles that.
# =============================================================================

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent


@dataclass
class SourceStream:
    """One conversation worth of timestamped turns."""

    conversation_id: str
    turns: list[ParsedTurn]
    started_at: float | None  # epoch
    ended_at: float | None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _to_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        # Claude Code uses ISO 8601 with timezone, e.g. 2026-05-04T05:32:37.123Z
        s = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _flatten_content(content) -> str:
    """Content may be a string, or a list of {type:text|tool_use|tool_result|...}."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        t = block.get("type")
        if t == "text":
            parts.append(block.get("text", ""))
        elif t == "tool_use":
            name = block.get("name", "tool")
            inp = block.get("input", {})
            # Compress tool input to one line — the bubble is for humans, not
            # an audit log. Detailed args go to the JSON sidecar later.
            arg_summary = _summarize_tool_input(name, inp)
            parts.append(f"\n[tool: {name}{(' ' + arg_summary) if arg_summary else ''}]")
        elif t == "tool_result":
            content_inner = block.get("content")
            text = _flatten_content(content_inner) if content_inner else ""
            text = (text or "").strip().replace("\n", " ")
            if len(text) > 240:
                text = text[:240] + "…"
            if text:
                parts.append(f"\n[result: {text}]")
        elif t == "thinking":
            # extended thinking blocks aren't shown in the rendered transcript
            continue
        elif "text" in block:  # fallback
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def _summarize_tool_input(name: str, inp: dict) -> str:
    """One-line summary of the most identifying field, for the tool annotation."""
    if not isinstance(inp, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
        if key in inp and isinstance(inp[key], str):
            v = inp[key]
            if len(v) > 80:
                v = v[:80] + "…"
            return v
    return ""


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------


def load_cc_jsonl(path: Path) -> SourceStream:
    """Read one Claude Code session JSONL → SourceStream of ParsedTurns."""
    path = Path(path)
    session_id = path.stem
    turns: list[ParsedTurn] = []
    started_at: float | None = None
    ended_at: float | None = None
    pending_tool_uses: list[dict] = []  # tool_uses to fold into NEXT assistant body
    turn_no = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue

            etype = ev.get("type")
            ts = _to_epoch(ev.get("timestamp"))
            if ts is not None:
                started_at = started_at if started_at is not None else ts
                ended_at = ts

            if etype not in ("user", "assistant"):
                continue

            msg = ev.get("message") or {}
            content = msg.get("content")
            body = _flatten_content(content)
            if not body.strip():
                # empty user/assistant turns are skipped (e.g. pure tool_result
                # wrappers that flatten to nothing meaningful)
                continue

            turn_no += 1
            agent = Agent.ADAM if etype == "user" else Agent.CLAUDE_CODE
            role = "HUMAN" if etype == "user" else "IMPLEMENTATION"

            turns.append(
                ParsedTurn(
                    turn=turn_no,
                    agent=agent,
                    role=role,
                    body=body,
                )
            )
            # stash timestamp + conversation_id on the parsed turn via private attrs;
            # the weaver picks them up.
            turns[-1].timestamp = ts  # type: ignore[attr-defined]
            turns[-1].conversation_id = session_id  # type: ignore[attr-defined]

    return SourceStream(
        conversation_id=session_id,
        turns=turns,
        started_at=started_at,
        ended_at=ended_at,
    )

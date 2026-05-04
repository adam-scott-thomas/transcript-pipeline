# transcript_pipeline.adapters.gpt
# =============================================================================
# OpenAI conversations export → list[ParsedTurn].
#
# Source: a directory of `conversations-NNN.json` files (the user's
# self-export from chatgpt.com). Each file is a JSON array; each element is
# a conversation with:
#
#   conversation_id, create_time (epoch float), title, mapping (graph),
#   current_node (id of last leaf in the visible thread)
#
# `mapping` is a dict of message_id → {parent, children, message}. To
# reconstruct the visible thread we walk parent-pointers from `current_node`
# back to root, then reverse to get chronological order. ChatGPT's UI
# allows branching; the export retains every branch but only `current_node`
# is the "selected" path the user actually saw last.
#
# Each `message` has author.role ("user" | "assistant" | "system" | "tool"),
# create_time (epoch float, may be None for system stubs), and content.parts
# (list of strings or richer blocks).
#
# Mapping to ParsedTurn:
#
#   role=user      → AGENT=ADAM, role="HUMAN"
#   role=assistant → AGENT=GPT,  role="STRATEGY" (Adam's standard label)
#   role=system    → skipped (not part of the visible chat)
#   role=tool      → folded into preceding assistant body as [tool: ...]
#
# Empty messages and messages without create_time are skipped.
# =============================================================================

from __future__ import annotations

import json
from pathlib import Path

from transcript_pipeline.adapters.cc_jsonl import SourceStream
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent


def _walk_thread(mapping: dict, leaf_id: str | None) -> list[dict]:
    """Walk parent pointers from `leaf_id` back to root, then return the
    chronological forward sequence of message nodes (skipping nodes with
    no message)."""
    if not leaf_id or leaf_id not in mapping:
        # fall back to whatever order the keys came in
        return [n for n in mapping.values() if n.get("message")]
    chain: list[dict] = []
    cur = mapping.get(leaf_id)
    while cur is not None:
        chain.append(cur)
        parent_id = cur.get("parent")
        if not parent_id:
            break
        cur = mapping.get(parent_id)
    chain.reverse()
    return [n for n in chain if n.get("message")]


def _content_to_text(content) -> str:
    if not content:
        return ""
    parts = content.get("parts")
    if not parts:
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            # newer formats wrap text in dicts; pick whatever's stringy
            for k in ("text", "content"):
                v = p.get(k)
                if isinstance(v, str):
                    out.append(v)
                    break
    return "\n".join(s for s in out if s.strip()).strip()


def _convo_to_stream(convo: dict) -> SourceStream | None:
    """One OpenAI conversation → SourceStream. Returns None for empties."""
    convo_id = convo.get("conversation_id") or convo.get("id") or ""
    if not convo_id:
        return None

    chain = _walk_thread(convo.get("mapping") or {}, convo.get("current_node"))
    turns: list[ParsedTurn] = []
    started_at: float | None = None
    ended_at: float | None = None
    turn_no = 0

    pending_tool_lines: list[str] = []  # tool messages waiting for next assistant turn

    for node in chain:
        msg = node.get("message") or {}
        author = (msg.get("author") or {}).get("role", "")
        ts = msg.get("create_time")
        text = _content_to_text(msg.get("content"))

        if author == "system":
            continue
        if author == "tool":
            if text.strip():
                pending_tool_lines.append(f"[tool: {text[:160]}]")
            continue

        if not text.strip():
            continue

        # fold any pending tool lines into the next assistant turn body
        if author == "assistant" and pending_tool_lines:
            text = text + "\n" + "\n".join(pending_tool_lines)
            pending_tool_lines.clear()

        agent = Agent.ADAM if author == "user" else Agent.GPT
        role = "HUMAN" if author == "user" else "STRATEGY"

        if ts is not None:
            started_at = started_at if started_at is not None else float(ts)
            ended_at = float(ts)

        turn_no += 1
        turns.append(
            ParsedTurn(
                turn=turn_no,
                agent=agent,
                role=role,
                body=text,
                timestamp=float(ts) if ts is not None else None,
                conversation_id=convo_id,
            )
        )

    if not turns:
        return None
    return SourceStream(
        conversation_id=convo_id,
        turns=turns,
        started_at=started_at,
        ended_at=ended_at,
    )


def load_gpt_export(export_dir: Path) -> list[SourceStream]:
    """Walk an OpenAI export dir for conversations-*.json. Returns one
    SourceStream per non-empty conversation."""
    export_dir = Path(export_dir)
    streams: list[SourceStream] = []
    files = sorted(export_dir.glob("conversations*.json"))
    if not files:
        # `conversations.json` (single file) variant
        single = export_dir / "conversations.json"
        if single.exists():
            files = [single]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for convo in data:
            if not isinstance(convo, dict):
                continue
            s = _convo_to_stream(convo)
            if s is not None:
                streams.append(s)
    return streams

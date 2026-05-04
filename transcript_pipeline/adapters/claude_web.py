# transcript_pipeline.adapters.claude_web
# =============================================================================
# Claude.ai (web app — "Spidey-Claude") data export → list[ParsedTurn].
#
# Source: a Claude data-export zip. The interesting payload is
# `conversations.json` at the zip root: a JSON array of conversation
# objects with shape:
#
#   {
#     uuid, name, summary, created_at, updated_at, account, chat_messages
#   }
#
# Each `chat_messages[]` entry has:
#
#   uuid, text, content (list of {type, text}), sender ("human"|"assistant"),
#   created_at (ISO 8601 with Z), parent_message_uuid
#
# Mapping:
#
#   sender=human     → AGENT=ADAM,   role="HUMAN"
#   sender=assistant → AGENT=CLAUDE, role="REASONING" (Adam's standard label
#                                    for Spidey-Claude vs CLAUDE-CODE)
#
# This adapter accepts either a path to the .zip OR a path to an already
# unzipped conversations.json — useful for tests and for incremental
# pinch-test runs that don't want to re-unzip a 200 MB file each time.
# =============================================================================

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from transcript_pipeline.adapters.cc_jsonl import SourceStream
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent


def _to_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _content_to_text(message: dict) -> str:
    """Prefer `content[].text`; fall back to top-level `text`."""
    blocks = message.get("content") or []
    if isinstance(blocks, list):
        parts = [
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        joined = "\n".join(p for p in parts if p.strip()).strip()
        if joined:
            return joined
    return (message.get("text") or "").strip()


def _convo_to_stream(convo: dict) -> SourceStream | None:
    convo_id = convo.get("uuid") or ""
    if not convo_id:
        return None
    msgs = convo.get("chat_messages") or []
    turns: list[ParsedTurn] = []
    started_at: float | None = None
    ended_at: float | None = None
    turn_no = 0

    for m in msgs:
        if not isinstance(m, dict):
            continue
        sender = m.get("sender")
        if sender not in ("human", "assistant"):
            continue
        text = _content_to_text(m)
        if not text:
            continue
        ts = _to_epoch(m.get("created_at"))
        if ts is not None:
            started_at = started_at if started_at is not None else ts
            ended_at = ts

        turn_no += 1
        agent = Agent.ADAM if sender == "human" else Agent.CLAUDE
        role = "HUMAN" if sender == "human" else "REASONING"
        turns.append(
            ParsedTurn(
                turn=turn_no,
                agent=agent,
                role=role,
                body=text,
                timestamp=ts,
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


def load_claude_web_export(path: Path) -> list[SourceStream]:
    """Read either a Claude.ai export .zip or its unzipped conversations.json."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            try:
                with zf.open("conversations.json") as fh:
                    data = json.loads(fh.read().decode("utf-8", errors="replace"))
            except KeyError:
                return []
    elif path.is_file() and path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    elif path.is_dir() and (path / "conversations.json").exists():
        data = json.loads(
            (path / "conversations.json").read_text(encoding="utf-8", errors="replace")
        )
    else:
        return []

    if not isinstance(data, list):
        return []

    streams: list[SourceStream] = []
    for convo in data:
        if not isinstance(convo, dict):
            continue
        s = _convo_to_stream(convo)
        if s is not None:
            streams.append(s)
    return streams

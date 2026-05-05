# transcript_pipeline.woven_jsonl
# =============================================================================
# Persistent on-disk handoff between v0.4.1 (weaver + classifier) and v0.5
# (skool bubble renderer).
#
# A `.woven.jsonl` file = one header line + one turn line per message.
# JSONL because we want streaming-friendly format and per-line greppability;
# this is what the bubble renderer (v0.5) and the playwright→ffmpeg layer
# (v0.6) both read.
#
# Schema:
#
#   line 0 (header):
#     {"_kind": "header",
#      "session_id": "<anchor uuid>",
#      "anchor_id": "<anchor convo_id>",
#      "started_at": <epoch>,
#      "ended_at": <epoch>,
#      "n_turns": <int>,
#      "version": "0.4.1"}
#
#   lines 1..N (turns):
#     {"_kind": "turn",
#      "turn": <1-indexed>,
#      "agent": "ADAM"|"GPT"|"CLAUDE"|"CLAUDE-CODE"|...|"SYSTEM",
#      "role": "<role label>",
#      "model": "<opus-4.7|gpt-5.2|gpt-5.2-codex|sonnet-4.6|...|null>",
#      "body": "<message text, markdown allowed>",
#      "timestamp": <epoch or null>,
#      "conversation_id": "<source convo uuid>",
#      "instance": <1|2|3|4+>,
#      "stage": "Context|Problem|Audit|Decision|Build|Fix|Review|Ship|Next",
#      "outcome": "<short phrase, 3-7 words>",
#      "confidence": <0.0-1.0>,
#      "requires_human": <bool>,
#      "chapter": <int>,
#      "chapter_outcome": "<short phrase>"}
#
# Some fields are optional (model, timestamp). When the v0.4.1 weave step
# couldn't classify a turn (no labels in cache + --no-classify), the
# stage/outcome/confidence/chapter come from the embedder/parser defaults.
# =============================================================================

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent, Stage


VERSION = "0.4.1"


@dataclass
class WovenHeader:
    session_id: str
    anchor_id: str
    started_at: float | None
    ended_at: float | None
    n_turns: int
    version: str = VERSION

    def to_obj(self) -> dict:
        return {
            "_kind": "header",
            "session_id": self.session_id,
            "anchor_id": self.anchor_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "n_turns": self.n_turns,
            "version": self.version,
        }


@dataclass
class WovenTurn:
    turn: int
    agent: str
    role: str
    body: str
    timestamp: float | None = None
    model: str | None = None
    conversation_id: str | None = None
    instance: int = 1
    stage: str = "Context"
    outcome: str = ""
    confidence: float = 0.0
    requires_human: bool = False
    chapter: int = 1
    chapter_outcome: str = ""

    def to_obj(self) -> dict:
        return {
            "_kind": "turn",
            "turn": self.turn,
            "agent": self.agent,
            "role": self.role,
            "model": self.model,
            "body": self.body,
            "timestamp": self.timestamp,
            "conversation_id": self.conversation_id,
            "instance": self.instance,
            "stage": self.stage,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "requires_human": self.requires_human,
            "chapter": self.chapter,
            "chapter_outcome": self.chapter_outcome,
        }

    @classmethod
    def from_obj(cls, d: dict) -> "WovenTurn":
        return cls(
            turn=int(d["turn"]),
            agent=str(d["agent"]),
            role=str(d.get("role", "")),
            body=str(d.get("body", "")),
            timestamp=d.get("timestamp"),
            model=d.get("model"),
            conversation_id=d.get("conversation_id"),
            instance=int(d.get("instance", 1)),
            stage=str(d.get("stage", "Context")),
            outcome=str(d.get("outcome", "")),
            confidence=float(d.get("confidence", 0.0)),
            requires_human=bool(d.get("requires_human", False)),
            chapter=int(d.get("chapter", 1)),
            chapter_outcome=str(d.get("chapter_outcome", "")),
        )


@dataclass
class WovenFile:
    """In-memory representation of a parsed .woven.jsonl."""

    header: WovenHeader
    turns: list[WovenTurn] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_woven(
    path: Path,
    *,
    session_id: str,
    anchor_id: str,
    started_at: float | None,
    ended_at: float | None,
    turns: Iterable[WovenTurn],
) -> Path:
    """Atomically write the woven jsonl. Header line first, one turn per
    line. Returns the written path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    turns_list = list(turns)
    header = WovenHeader(
        session_id=session_id,
        anchor_id=anchor_id,
        started_at=started_at,
        ended_at=ended_at,
        n_turns=len(turns_list),
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header.to_obj(), ensure_ascii=False) + "\n")
        for t in turns_list:
            fh.write(json.dumps(t.to_obj(), ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_woven(path: Path) -> WovenFile:
    """Parse a .woven.jsonl produced by `write_woven`."""
    path = Path(path)
    header: WovenHeader | None = None
    turns: list[WovenTurn] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            kind = obj.get("_kind")
            if kind == "header":
                header = WovenHeader(
                    session_id=str(obj["session_id"]),
                    anchor_id=str(obj["anchor_id"]),
                    started_at=obj.get("started_at"),
                    ended_at=obj.get("ended_at"),
                    n_turns=int(obj.get("n_turns", 0)),
                    version=str(obj.get("version", VERSION)),
                )
            elif kind == "turn":
                turns.append(WovenTurn.from_obj(obj))
            else:
                # tolerate unknown kinds for forward compatibility
                continue
    if header is None:
        raise ValueError(f"{path}: no header line found")
    return WovenFile(header=header, turns=turns)


# ---------------------------------------------------------------------------
# Adapter: ParsedTurn[] (plus per-source labels) → WovenTurn[]
# ---------------------------------------------------------------------------


_AGENT_DEFAULT_MODEL = {
    Agent.ADAM: None,
    Agent.GPT: "gpt-5.2",
    Agent.CLAUDE: "opus-4.7",
    Agent.CLAUDE_CODE: "opus-4.7",
    Agent.CLAUDE_BROWSER: "sonnet-4.6",
    Agent.CODEX: "gpt-5.2-codex",
    Agent.SYSTEM: None,
}


def parsed_to_woven(
    parsed: list[ParsedTurn],
    *,
    chapter_outcomes: dict[int, str] | None = None,
    label_lookup: dict | None = None,
) -> list[WovenTurn]:
    """Convert woven ParsedTurn[] → WovenTurn[]. Optionally apply per-turn
    stage/confidence labels from a `(conversation_id, turn_text_prefix)`
    keyed lookup. The renderer doesn't strictly need labels — but when
    they're present, dwell timing + low-conf rendering work end-to-end."""
    chapter_outcomes = chapter_outcomes or {}
    out: list[WovenTurn] = []
    for pt in parsed:
        agent_value = pt.agent.value if isinstance(pt.agent, Agent) else str(pt.agent)
        model = _AGENT_DEFAULT_MODEL.get(pt.agent) if isinstance(pt.agent, Agent) else None
        stage_v = pt.stage.value if isinstance(pt.stage, Stage) else (pt.stage or "Context")
        chap = pt.chapter or 1
        out.append(
            WovenTurn(
                turn=pt.turn,
                agent=agent_value,
                role=pt.role,
                body=pt.body,
                timestamp=pt.timestamp,
                model=model,
                conversation_id=pt.conversation_id,
                instance=pt.instance,
                stage=str(stage_v),
                outcome=pt.chapter_outcome or "",
                confidence=1.0,  # parser-supplied; classifier overrides via label_lookup
                requires_human=False,
                chapter=chap,
                chapter_outcome=pt.chapter_outcome or chapter_outcomes.get(chap, ""),
            )
        )
    return out

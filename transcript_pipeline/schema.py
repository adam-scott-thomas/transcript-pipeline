# transcript_pipeline.schema
# =============================================================================
# Source of truth for the Transcript Format v1.0 data model.
#
# Every other component in the package reads from this file. Validator rules
# operate on these dataclasses; renderer formats them; embedder constructs
# them; parser produces them. If a field is added or an enum value changes,
# this is the only file you should edit — every other component picks up the
# change through normal type checking.
#
# Design notes:
#   - Enums are str-backed so YAML round-trips cleanly without tag prefixes.
#   - VideoHeader and Turn are frozen dataclasses: instances are immutable
#     once built. All construction goes through `from_dict` / `to_dict` to
#     keep YAML serialization explicit.
#   - `Transcript` is the in-memory container: a header plus an ordered list
#     of turns. The renderer never iterates anything else.
#   - `ALLOWED_PROJECT_CODES` is intentionally open: validator only enforces
#     format (uppercase letters, no spaces), not membership in a closed set.
#     Adam coins new project codes regularly; closing the set would be churn.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums — closed sets per spec
# ---------------------------------------------------------------------------


class Status(str, Enum):
    """Title status. Closed set per spec section 1."""

    SHIPPED = "Shipped"
    BUILDING = "Building"
    INCOMPLETE = "Incomplete"
    BLOCKED = "Blocked"
    FIXED = "Fixed"
    AUDIT = "Audit"
    RESET = "Reset"
    FIELD_NOTES = "Field Notes"


class Stage(str, Enum):
    """Chapter stage. Closed set per spec section 2."""

    CONTEXT = "Context"
    PROBLEM = "Problem"
    AUDIT = "Audit"
    DECISION = "Decision"
    BUILD = "Build"
    FIX = "Fix"
    REVIEW = "Review"
    SHIP = "Ship"
    NEXT = "Next"


class Agent(str, Enum):
    """Speaker. Closed set per spec section 3."""

    ADAM = "ADAM"
    GPT = "GPT"
    CLAUDE = "CLAUDE"
    CLAUDE_CODE = "CLAUDE-CODE"
    CLAUDE_BROWSER = "CLAUDE-BROWSER"
    CODEX = "CODEX"
    SYSTEM = "SYSTEM"


class StatusTag(str, Enum):
    """Optional per-message/chapter status tag. Closed set per spec section 5."""

    SHIPPED = "SHIPPED"
    BUILDING = "BUILDING"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    FIXED = "FIXED"


class Visual(str, Enum):
    """Bubble vs card. Closed set per spec section 4."""

    BUBBLE_BLACK = "bubble_black"
    CARD_WHITE = "card_white"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Defaults per spec section 4: CODEX always card_white, every other agent is
# bubble_black until per-agent colors are locked.
AGENT_DEFAULT_VISUAL: dict[Agent, Visual] = {
    Agent.ADAM: Visual.BUBBLE_BLACK,
    Agent.GPT: Visual.BUBBLE_BLACK,
    Agent.CLAUDE: Visual.BUBBLE_BLACK,
    Agent.CLAUDE_CODE: Visual.BUBBLE_BLACK,
    Agent.CLAUDE_BROWSER: Visual.BUBBLE_BLACK,
    Agent.CODEX: Visual.CARD_WHITE,
    Agent.SYSTEM: Visual.BUBBLE_BLACK,
}

# Project code format: 2-6 uppercase letters, optionally with digits suffix.
# Closed set is documented but not enforced (Adam coins codes regularly).
ALLOWED_PROJECT_CODES: tuple[str, ...] = ("GL", "MS", "POAW", "EVX", "ARC", "ARB")

# Per-lane turn caps. The spec's hard cap (12) is for hand-edited fresh
# production videos; archive material (woven from existing chat history)
# isn't pace-constrained the same way and is allowed to run long. The
# validator parameterizes on `lane` so it can apply the right cap.
TURN_CAPS: dict[str, int | None] = {
    "production": 12,   # spec section 6 — fresh content, video pacing
    "archive": 1000,    # woven historical chats — read-friendly, can run long
    "uncapped": None,   # disable the check entirely (cross-session weaves)
}
DEFAULT_LANE: str = "production"

# Backwards-compat alias used by tests + older callers.
MAX_TURNS_PER_VIDEO: int = TURN_CAPS[DEFAULT_LANE]


def turn_cap(lane: str = DEFAULT_LANE) -> int | None:
    """Return the turn cap for a lane, or None if uncapped."""
    return TURN_CAPS.get(lane, TURN_CAPS[DEFAULT_LANE])

# Chapter count guidance from spec section 2 (warning band, not error).
CHAPTER_MIN: int = 3
CHAPTER_MAX: int = 8

# Outcome length cap from spec section 1.
OUTCOME_MAX_WORDS: int = 6

# Cross-video reference format per spec section 7. PROJECT-NUMBER, where
# NUMBER is zero-padded to 3 digits (matching project_number serialization).
REF_PATTERN: str = r"^[A-Z]{2,6}\d?-\d{2,3}$"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoHeader:
    """The first YAML block in every transcript file. One per video."""

    project: str  # e.g. "GL"
    project_number: int  # zero-padded on serialization
    status: Status
    outcome: str  # 6 words max
    session_id: str  # ISO timestamp YYYY-MM-DD-HHMM
    resumed: bool = False

    @property
    def code(self) -> str:
        """Cross-video ref form: e.g. 'GL-004'."""
        return f"{self.project}-{self.project_number:03d}"

    @property
    def title_line(self) -> str:
        """Spec section 1 format: 'PROJECT-CODE — STATUS — OUTCOME'."""
        return f"{self.code} — {self.status.value} — {self.outcome}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "project_number": self.project_number,
            "status": self.status.value,
            "outcome": self.outcome,
            "session_id": self.session_id,
            "resumed": self.resumed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoHeader":
        return cls(
            project=str(d["project"]),
            project_number=int(d["project_number"]),
            status=Status(d["status"]),
            outcome=str(d["outcome"]),
            session_id=str(d["session_id"]),
            resumed=bool(d.get("resumed", False)),
        )


@dataclass(frozen=True)
class Turn:
    """One message in the transcript.

    `instance` disambiguates multiple parallel conversations of the same
    agent class. When Adam was talking to two Claude Code sessions
    simultaneously, the temporal weaver assigns instance=1 to the first
    seen and instance=2 to the second. The HTML renderer uses this to draw
    progressively heavier outlines (none, white, double white) so a viewer
    can tell which channel a bubble belongs to.

    `timestamp` is wall-clock when the message was sent, used by the
    temporal weaver to merge multiple streams. Optional because hand-edited
    fresh transcripts don't need it.

    `conversation_id` carries the source conversation (CC session, GPT
    chat, claude.ai project). Used purely for tracing — humans don't see
    it in the rendered output."""

    turn: int
    agent: Agent
    role: str
    stage: Stage
    chapter: int
    chapter_outcome: str
    body: str
    status_tag: StatusTag | None = None
    references: tuple[str, ...] = field(default_factory=tuple)
    visual: Visual | None = None  # falls back to AGENT_DEFAULT_VISUAL
    instance: int = 1
    timestamp: float | None = None  # epoch seconds
    conversation_id: str | None = None

    @property
    def effective_visual(self) -> Visual:
        if self.visual is not None:
            return self.visual
        return AGENT_DEFAULT_VISUAL[self.agent]

    def to_dict(self) -> dict[str, Any]:
        d = {
            "turn": self.turn,
            "agent": self.agent.value,
            "role": self.role,
            "stage": self.stage.value,
            "chapter": self.chapter,
            "chapter_outcome": self.chapter_outcome,
            "status_tag": self.status_tag.value if self.status_tag else None,
            "references": list(self.references),
            "visual": self.effective_visual.value,
            "instance": self.instance,
        }
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        if self.conversation_id is not None:
            d["conversation_id"] = self.conversation_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], body: str) -> "Turn":
        st = d.get("status_tag")
        vis = d.get("visual")
        return cls(
            turn=int(d["turn"]),
            agent=Agent(d["agent"]),
            role=str(d["role"]),
            stage=Stage(d["stage"]),
            chapter=int(d["chapter"]),
            chapter_outcome=str(d["chapter_outcome"]),
            body=body,
            status_tag=StatusTag(st) if st else None,
            references=tuple(d.get("references") or []),
            visual=Visual(vis) if vis else None,
            instance=int(d.get("instance", 1)),
            timestamp=d.get("timestamp"),
            conversation_id=d.get("conversation_id"),
        )


@dataclass
class Transcript:
    """In-memory container — header + ordered turns. Renderer's only input."""

    header: VideoHeader
    turns: list[Turn]

    @property
    def chapter_count(self) -> int:
        return len({t.chapter for t in self.turns})

    def chapters(self) -> list[tuple[int, Stage, str]]:
        """Return (chapter_number, stage, chapter_outcome) for each chapter,
        in order. Stage and outcome come from the first turn of each chapter."""
        seen: dict[int, tuple[Stage, str]] = {}
        order: list[int] = []
        for t in self.turns:
            if t.chapter not in seen:
                seen[t.chapter] = (t.stage, t.chapter_outcome)
                order.append(t.chapter)
        return [(n, *seen[n]) for n in order]

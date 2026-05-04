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

# Hard cap from spec section 6.
MAX_TURNS_PER_VIDEO: int = 12

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
    """One message in the transcript."""

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

    @property
    def effective_visual(self) -> Visual:
        if self.visual is not None:
            return self.visual
        return AGENT_DEFAULT_VISUAL[self.agent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "agent": self.agent.value,
            "role": self.role,
            "stage": self.stage.value,
            "chapter": self.chapter,
            "chapter_outcome": self.chapter_outcome,
            "status_tag": self.status_tag.value if self.status_tag else None,
            "references": list(self.references),
            "visual": self.effective_visual.value,
        }

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

"""Validator: verify all 7 error/warn classes from the brief fire correctly."""

from transcript_pipeline.embedder import EmbedRequest, embed
from transcript_pipeline.parser import parse_log
from transcript_pipeline.renderer import render_text  # noqa: F401  (smoke import)
from transcript_pipeline.schema import (
    Agent,
    Stage,
    Status,
    StatusTag,
    Transcript,
    Turn,
    VideoHeader,
)
from transcript_pipeline.validator import validate_transcript


def _mk_turn(n, agent=Agent.ADAM, stage=Stage.CONTEXT, **kw):
    return Turn(
        turn=n,
        agent=agent,
        role="HUMAN",
        stage=stage,
        chapter=kw.get("chapter", n),
        chapter_outcome=kw.get("chapter_outcome", "Outcome"),
        body=kw.get("body", "msg"),
        status_tag=kw.get("status_tag"),
        references=kw.get("references", ()),
    )


def _mk_header(**kw):
    return VideoHeader(
        project=kw.get("project", "GL"),
        project_number=kw.get("project_number", 1),
        status=kw.get("status", Status.BUILDING),
        outcome=kw.get("outcome", "A B C"),
        session_id=kw.get("session_id", "2026-05-04-0001"),
        resumed=kw.get("resumed", False),
    )


# 1. turn > 12
def test_turn_cap_exceeded(core):
    turns = [_mk_turn(i, chapter=1) for i in range(1, 14)]  # 13 turns
    t = Transcript(header=_mk_header(), turns=turns)
    diags = validate_transcript(t)
    codes = {d.code for d in diags}
    assert "turn_cap_exceeded" in codes


# 2. stage not in allowed set — covered by enum coercion at construction.
#    The schema check defensively verifies enum membership; emulate by
#    bypassing dataclass typing.
def test_stage_unknown_defensive(core):
    t = _mk_turn(1)
    object.__setattr__(t, "stage", type("X", (), {"value": "Bogus"})())  # type: ignore
    transcript = Transcript(header=_mk_header(), turns=[t])
    diags = validate_transcript(transcript)
    assert any(d.code == "stage_unknown" for d in diags)


# 3. status contradicts title status
def test_status_contradiction(core):
    # Title BLOCKED, message tagged SHIPPED → contradiction
    turn = _mk_turn(1, status_tag=StatusTag.SHIPPED)
    t = Transcript(header=_mk_header(status=Status.BLOCKED), turns=[turn])
    diags = validate_transcript(t)
    assert any(d.code == "status_contradiction" for d in diags)


# 4. outcome > 6 words
def test_outcome_too_long(core):
    h = _mk_header(outcome="one two three four five six seven")  # 7 words
    t = Transcript(header=h, turns=[_mk_turn(1)])
    diags = validate_transcript(t)
    assert any(d.code == "outcome_too_long" for d in diags)


# 5. chapter count outside 3-8 (warn)
def test_chapter_count_warning_low(core):
    # 1 chapter
    t = Transcript(header=_mk_header(), turns=[_mk_turn(1, chapter=1)])
    diags = validate_transcript(t)
    assert any(d.severity == "warn" and d.code == "chapter_count_out_of_band" for d in diags)


def test_chapter_count_warning_high(core):
    turns = [_mk_turn(i, chapter=i) for i in range(1, 11)]  # 10 chapters > 8
    t = Transcript(header=_mk_header(), turns=turns)
    diags = validate_transcript(t)
    assert any(d.severity == "warn" and d.code == "chapter_count_out_of_band" for d in diags)


# 6. resumed=true with turn>1 (or rather: resumed file whose first turn is not 1)
def test_resumed_with_history(core):
    h = _mk_header(resumed=True)
    # first turn is #5 — invalid for a resumed file (must restart at 1)
    t = Transcript(header=h, turns=[_mk_turn(5, chapter=1)])
    diags = validate_transcript(t)
    assert any(d.code == "resumed_with_history" for d in diags)


# 7. reference format mismatch
def test_reference_malformed(core):
    turn = _mk_turn(1, references=("not-a-real-ref",))
    t = Transcript(header=_mk_header(), turns=[turn])
    diags = validate_transcript(t)
    assert any(d.code == "reference_malformed" for d in diags)


def test_clean_transcript_emits_no_errors(core):
    """Sanity: a well-formed transcript with 4 chapters passes."""
    turns = [
        _mk_turn(1, stage=Stage.CONTEXT, chapter=1, chapter_outcome="Setup"),
        _mk_turn(2, stage=Stage.PROBLEM, chapter=2, chapter_outcome="Diagnosed"),
        _mk_turn(3, stage=Stage.DECISION, chapter=3, chapter_outcome="Picked"),
        _mk_turn(4, stage=Stage.SHIP, chapter=4, chapter_outcome="Shipped",
                 references=("GL-002",)),
    ]
    h = _mk_header(status=Status.SHIPPED, outcome="Auth Key Flow")
    t = Transcript(header=h, turns=turns)
    diags = validate_transcript(t)
    errors = [d for d in diags if d.severity == "error"]
    assert errors == []

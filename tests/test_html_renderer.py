"""HTML renderer: structure, instance outlines, CODEX card override."""

from transcript_pipeline.html_renderer import render_html
from transcript_pipeline.schema import (
    Agent,
    Stage,
    Status,
    Transcript,
    Turn,
    VideoHeader,
)


def _mk(turns):
    h = VideoHeader(
        project="GL",
        project_number=1,
        status=Status.FIELD_NOTES,
        outcome="Smoke",
        session_id="2026-05-04-2000",
    )
    return Transcript(header=h, turns=turns)


def _turn(idx, agent, instance=1, body="hello"):
    return Turn(
        turn=idx,
        agent=agent,
        role="HUMAN" if agent is Agent.ADAM else "STRATEGY",
        stage=Stage.CONTEXT,
        chapter=1,
        chapter_outcome="setup",
        body=body,
        instance=instance,
    )


def test_html_has_doctype_and_no_external_refs(core):
    t = _mk([_turn(1, Agent.ADAM)])
    out = render_html(t)
    assert out.startswith("<!DOCTYPE html>")
    # no external CSS / JS / fonts — paste-safe for Skool
    for forbidden in ('href="http', 'src="http', "<script", "@import", "<link "):
        assert forbidden not in out, f"unexpected external ref: {forbidden}"


def test_instance_outlines_render_as_classes(core):
    t = _mk([
        _turn(1, Agent.CLAUDE_CODE, instance=1),
        _turn(2, Agent.CLAUDE_CODE, instance=2),
        _turn(3, Agent.CLAUDE_CODE, instance=3),
    ])
    out = render_html(t)
    assert "outline-1" in out
    assert "outline-2" in out
    # instance=1 should produce no outline-N class on that bubble
    # (we can't assert global absence, but the first turn's bubble div
    # shouldn't carry an outline-X token directly)
    assert 'class="bubble b-code"' in out  # instance=1 bubble


def test_codex_renders_as_card_white(core):
    t = _mk([_turn(1, Agent.CODEX)])
    out = render_html(t)
    assert "b-codex" in out
    # codex CSS forces white background and dark text
    assert "--codex-bg: #ffffff" in out


def test_adam_aligns_right(core):
    t = _mk([_turn(1, Agent.ADAM)])
    out = render_html(t)
    assert 'class="row right"' in out


def test_chapter_bar_emitted_per_chapter(core):
    t = _mk([
        Turn(turn=1, agent=Agent.ADAM, role="HUMAN", stage=Stage.CONTEXT,
             chapter=1, chapter_outcome="ctx", body="hi"),
        Turn(turn=2, agent=Agent.GPT, role="STRATEGY", stage=Stage.PROBLEM,
             chapter=2, chapter_outcome="prob", body="ack"),
    ])
    out = render_html(t)
    assert "CHAPTER 01" in out
    assert "CHAPTER 02" in out

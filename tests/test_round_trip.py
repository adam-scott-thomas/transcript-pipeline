"""Round-trip acceptance: raw log → embed → render → matches v1.0 spec."""

from pathlib import Path

from transcript_pipeline.embedder import (
    EmbedRequest,
    embed,
    embed_to_string,
    load_embedded,
)
from transcript_pipeline.parser import parse_log
from transcript_pipeline.renderer import render_text, render_chapters_md, render_bubbles
from transcript_pipeline.schema import Agent, Stage, Status, StatusTag


SAMPLE_PATH = Path(__file__).parent.parent / "examples" / "sample_raw.log"


def test_parse_sample_yields_seven_turns(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    parsed = parse_log(raw)
    assert len(parsed) == 7
    assert parsed[0].agent is Agent.ADAM
    assert parsed[0].stage is Stage.CONTEXT
    assert parsed[1].references == ["GL-002"]
    assert parsed[6].status_tag is StatusTag.SHIPPED


def test_embed_assigns_chapters_from_stage_transitions(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    parsed = parse_log(raw)
    req = EmbedRequest(
        project="GL",
        project_number=4,
        status=Status.SHIPPED,
        outcome="Auth Key Flow",
        session_id="2026-05-04-1830",
    )
    t = embed(req, parsed)
    # 7 stages: Context, Problem, Decision, Build, Review, Fix, Ship → 7 chapters
    assert t.chapter_count == 7
    assert t.turns[0].chapter == 1
    assert t.turns[-1].chapter == 7


def test_round_trip_embed_then_load_is_identity(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    parsed = parse_log(raw)
    req = EmbedRequest(
        project="GL",
        project_number=4,
        status=Status.SHIPPED,
        outcome="Auth Key Flow",
        session_id="2026-05-04-1830",
    )
    t = embed(req, parsed)
    text = embed_to_string(t)
    t2 = load_embedded(text)

    assert t2.header.code == "GL-004"
    assert t2.header.title_line == "GL-004 — Shipped — Auth Key Flow"
    assert len(t2.turns) == len(t.turns)
    for a, b in zip(t.turns, t2.turns):
        assert a.turn == b.turn
        assert a.agent is b.agent
        assert a.role == b.role
        assert a.stage is b.stage
        assert a.chapter == b.chapter
        assert a.body == b.body
        assert a.status_tag == b.status_tag
        assert tuple(a.references) == tuple(b.references)
        assert a.effective_visual == b.effective_visual


def test_render_text_starts_with_title_and_includes_all_speakers(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    parsed = parse_log(raw)
    t = embed(
        EmbedRequest(
            project="GL",
            project_number=4,
            status=Status.SHIPPED,
            outcome="Auth Key Flow",
            session_id="2026-05-04-1830",
        ),
        parsed,
    )
    text = render_text(t)
    assert text.startswith("GL-004 — Shipped — Auth Key Flow")
    for agent in ("ADAM", "GPT", "CLAUDE", "CLAUDE-CODE", "CODEX"):
        assert agent in text
    assert "[CHAPTER 01]" in text
    assert "[CHAPTER 07]" in text


def test_chapters_md_lists_every_chapter(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    t = embed(
        EmbedRequest(
            project="GL",
            project_number=4,
            status=Status.SHIPPED,
            outcome="Auth Key Flow",
            session_id="2026-05-04-1830",
        ),
        parse_log(raw),
    )
    md = render_chapters_md(t)
    assert "Chapter 01:" in md
    assert "Chapter 07:" in md
    assert "Chapter 08:" not in md


def test_bubbles_json_codex_is_card_white(core):
    raw = SAMPLE_PATH.read_text(encoding="utf-8")
    t = embed(
        EmbedRequest(
            project="GL",
            project_number=4,
            status=Status.SHIPPED,
            outcome="Auth Key Flow",
            session_id="2026-05-04-1830",
        ),
        parse_log(raw),
    )
    bubbles = render_bubbles(t)
    codex = next(b for b in bubbles if b["agent"] == "CODEX")
    assert codex["visual"] == "card_white"
    others = [b for b in bubbles if b["agent"] != "CODEX"]
    assert all(b["visual"] == "bubble_black" for b in others)

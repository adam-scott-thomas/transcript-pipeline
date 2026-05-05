"""v0.5 — bubble HTML renderer: SPEC.md palette parser, validation, lane split,
segment parsing, dwell math, end-to-end render."""

from pathlib import Path

import pytest

from transcript_pipeline.skool_renderer import (
    Palette,
    RenderRequest,
    Segment,
    compute_dwell,
    load_palette,
    parse_segments,
    render_woven_to_html_parts,
    split_by_lane,
    validate_title,
)
from transcript_pipeline.woven_jsonl import (
    WovenFile,
    WovenHeader,
    WovenTurn,
)


# ---------------------------------------------------------------------------
# SPEC.md palette parser
# ---------------------------------------------------------------------------


def test_load_palette_finds_all_nine_agents():
    p = load_palette()
    expected = {
        "ADAM", "GPT", "CLAUDE", "CLAUDE-CODE", "CLAUDE-BROWSER",
        "CODEX", "GROK", "GEMINI", "SYSTEM",
    }
    assert set(p.agents.keys()) == expected


def test_load_palette_codex_is_white_on_dark():
    p = load_palette()
    cx = p.agents["CODEX"]
    assert cx.bg == "#FFFFFF"
    assert cx.fg == "#0B0D11"
    assert cx.border == "#2A3140"


def test_load_palette_system_is_dashed():
    p = load_palette()
    assert p.agents["SYSTEM"].border_style == "dashed"


def test_load_palette_surface_chrome():
    p = load_palette()
    assert p.surface["page.bg"] == "#0B0D11"
    assert p.surface["container.bg"] == "#11141A"
    assert p.surface["ink.primary"] == "#E6EDF3"


def test_load_palette_tool_recess_two_contexts():
    p = load_palette()
    assert p.tool_recess_colored["bg"] == "#000000"
    assert p.tool_recess_codex["bg"] == "#EBEBEB"


# ---------------------------------------------------------------------------
# Title validation
# ---------------------------------------------------------------------------


def test_validate_title_clean():
    errors = validate_title("GL-004", "Fixed", "Auth Key Flow")
    assert errors == []


def test_validate_title_rejects_unpadded_code():
    errors = validate_title("GL-4", "Fixed", "x")
    assert any("3 digits" in e or "GL-4" in e for e in errors)


def test_validate_title_rejects_unknown_status():
    errors = validate_title("GL-004", "WhateverNew", "x")
    assert any("WhateverNew" in e for e in errors)


def test_validate_title_rejects_outcome_over_six_words():
    errors = validate_title("GL-004", "Fixed", "one two three four five six seven")
    assert any("7 words" in e or "max 6" in e for e in errors)


# ---------------------------------------------------------------------------
# Lane-cap split
# ---------------------------------------------------------------------------


def _mk_turns(n: int) -> list[WovenTurn]:
    return [
        WovenTurn(
            turn=i + 1,
            agent="ADAM" if i % 2 == 0 else "CLAUDE-CODE",
            role="HUMAN" if i % 2 == 0 else "IMPLEMENTATION",
            body=f"turn {i+1}",
            stage="Build",
            chapter=1,
        )
        for i in range(n)
    ]


def test_split_by_lane_under_cap_no_split():
    turns = _mk_turns(10)
    parts = split_by_lane(turns, "production", "GL-004")
    assert len(parts) == 1
    assert parts[0][0] == "GL-004"
    assert len(parts[0][1]) == 10


def test_split_by_lane_over_cap_splits_with_sequential_codes():
    turns = _mk_turns(28)  # production cap is 12 → 3 parts (12, 12, 4)
    parts = split_by_lane(turns, "production", "GL-004")
    assert [code for code, _ in parts] == ["GL-004", "GL-005", "GL-006"]
    assert [len(t) for _, t in parts] == [12, 12, 4]


def test_split_by_lane_uncapped_returns_one_part():
    turns = _mk_turns(500)
    parts = split_by_lane(turns, "uncapped", "POAW-007")
    assert len(parts) == 1


def test_split_by_lane_renumbers_within_each_part():
    turns = _mk_turns(15)
    parts = split_by_lane(turns, "production", "GL-004")
    # part 1: turns 1..12, part 2: turns 1..3
    assert [t.turn for t in parts[0][1]] == list(range(1, 13))
    assert [t.turn for t in parts[1][1]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Segment parsing
# ---------------------------------------------------------------------------


def test_parse_segments_non_cc_is_one_prose_segment():
    segs = parse_segments("Hello world.", "GPT")
    assert len(segs) == 1 and segs[0].kind == "prose"


def test_parse_segments_cc_splits_tool_call_lines():
    body = (
        "Setting up the parser.\n"
        "[tool: Write src/parser.py]\n"
        "Done.\n"
        "[result: 1.2 KB written]"
    )
    segs = parse_segments(body, "CLAUDE-CODE")
    kinds = [s.kind for s in segs]
    assert kinds.count("prose") >= 1
    assert "tool-call" in kinds
    assert "code-output" in kinds


def test_parse_segments_codex_handles_tool_lines():
    segs = parse_segments(
        "Reviewing.\n[tool: Codex review src/foo.py]\nLooks fine.",
        "CODEX",
    )
    assert any(s.kind == "tool-call" for s in segs)


# ---------------------------------------------------------------------------
# Dwell math
# ---------------------------------------------------------------------------


def test_dwell_audit_prose_is_1000_v0_5_2():
    """v0.5.2 fast pace: Audit/Review = 1000ms (was 4000)."""
    s = Segment(kind="prose", text="trace")
    assert compute_dwell(s, stage="Audit", requires_human=False) == 1000


def test_dwell_low_confidence_adds_400_v0_5_2():
    """v0.5.2 fast pace: requires_human bonus = +400 (was +500)."""
    s = Segment(kind="prose", text="trace")
    assert compute_dwell(s, stage="Audit", requires_human=True) == 1400


def test_dwell_tool_call_is_50_percent_v0_5_2():
    """v0.5.2 fast pace: tool-call = 50% prose (was 40%)."""
    s = Segment(kind="tool-call", text="Bash mkdir")
    # Decision base 1500 → 50% = 750
    assert compute_dwell(s, stage="Decision", requires_human=False) == 750


def test_dwell_code_output_is_35_percent_v0_5_2():
    """v0.5.2 fast pace: code-output = 35% prose (was 30%)."""
    s = Segment(kind="code-output", text="ok")
    # Decision base 1500 → 35% = 525
    assert compute_dwell(s, stage="Decision", requires_human=False) == 525


def test_dwell_minimum_is_400():
    s = Segment(kind="code-output", text="ok")
    # Context base 700 × 35% = 245 → floored to 400
    assert compute_dwell(s, stage="Context", requires_human=False) == 400


# ---------------------------------------------------------------------------
# End-to-end render
# ---------------------------------------------------------------------------


def _mk_woven() -> WovenFile:
    header = WovenHeader(
        session_id="abc123",
        anchor_id="abc123",
        started_at=1000.0,
        ended_at=2000.0,
        n_turns=3,
    )
    turns = [
        WovenTurn(
            turn=1, agent="ADAM", role="HUMAN", body="Fix the auth.",
            timestamp=1000.0, conversation_id="abc",
            instance=1, stage="Context", outcome="Goal stated",
            confidence=0.95, chapter=1, chapter_outcome="Goal stated",
        ),
        WovenTurn(
            turn=2, agent="CLAUDE-CODE", role="IMPLEMENTATION", body="Building it.\n[tool: Write src/auth.py]",
            timestamp=1500.0, conversation_id="abc", model="opus-4.7",
            instance=1, stage="Build", outcome="Adapter scaffolded",
            confidence=0.92, chapter=2, chapter_outcome="Adapter scaffolded",
        ),
        WovenTurn(
            turn=3, agent="CODEX", role="REVIEW", body="LGTM.",
            timestamp=2000.0, conversation_id="cdx", model="gpt-5.2-codex",
            instance=1, stage="Review", outcome="Approved",
            confidence=0.55, requires_human=True,  # boundary case
            chapter=3, chapter_outcome="Approved",
        ),
    ]
    return WovenFile(header=header, turns=turns)


def test_render_emits_one_file_under_cap(tmp_path):
    woven = _mk_woven()
    req = RenderRequest(
        project_code="GL-004", status="Fixed", outcome="Auth Key Flow",
        lane="production",
    )
    paths = render_woven_to_html_parts(
        woven, request=req, out_stem=tmp_path / "gl-004"
    )
    assert len(paths) == 1
    assert paths[0].name == "gl-004-part-01.html"


def test_render_validation_failure_raises(tmp_path):
    woven = _mk_woven()
    req = RenderRequest(
        project_code="GL-4",  # unpadded
        status="Fixed", outcome="Auth Key Flow", lane="production",
    )
    with pytest.raises(ValueError):
        render_woven_to_html_parts(woven, request=req, out_stem=tmp_path / "x")


def test_rendered_html_has_palette_css_and_no_react(tmp_path):
    woven = _mk_woven()
    req = RenderRequest(
        project_code="GL-004", status="Fixed", outcome="Auth Key Flow",
        lane="production",
    )
    paths = render_woven_to_html_parts(
        woven, request=req, out_stem=tmp_path / "gl-004"
    )
    text = paths[0].read_text(encoding="utf-8")
    # palette colors are present (parsed from SPEC.md and embedded)
    assert "#007AFF" in text  # ADAM blue
    assert "#D9651F" in text  # CLAUDE-CODE orange
    assert "#FFFFFF" in text  # CODEX white
    # no react / no build artifacts
    assert "react" not in text.lower()
    assert "<script src=\"https://cdn.tailwindcss.com\"></script>" in text
    # marked + highlight CDNs
    assert "marked" in text
    assert "highlight" in text
    # codex bubble class is present (bubble, not card)
    assert "agent-codex" in text
    # data-dwell-ms emitted per segment
    assert "data-dwell-ms" in text
    # low-confidence flag survives
    assert "low-confidence" in text


def test_instance_outlines_render_in_html(tmp_path):
    """Adam's hard rule: second parallel agent of the same class gets a
    white outline; third gets double; fourth+ gets triple. Verify the chain
    survives WovenFile → HTML."""
    header = WovenHeader(
        session_id="multi", anchor_id="multi",
        started_at=0.0, ended_at=1.0, n_turns=4,
    )
    turns = [
        WovenTurn(
            turn=1, agent="ADAM", role="HUMAN", body="kick",
            timestamp=0.0, conversation_id="anchor",
            instance=1, stage="Context", chapter=1,
        ),
        WovenTurn(
            turn=2, agent="CLAUDE-CODE", role="IMPLEMENTATION", body="cc#1",
            timestamp=0.1, conversation_id="cc-a", model="opus-4.7",
            instance=1, stage="Build", chapter=1,
        ),
        WovenTurn(
            turn=3, agent="CLAUDE-CODE", role="IMPLEMENTATION", body="cc#2",
            timestamp=0.2, conversation_id="cc-b", model="opus-4.7",
            instance=2, stage="Build", chapter=1,
        ),
        WovenTurn(
            turn=4, agent="CLAUDE-CODE", role="IMPLEMENTATION", body="cc#3",
            timestamp=0.3, conversation_id="cc-c", model="opus-4.7",
            instance=3, stage="Build", chapter=1,
        ),
    ]
    woven = WovenFile(header=header, turns=turns)
    req = RenderRequest(
        project_code="GL-050", status="Building", outcome="Multi-CC Weave",
        lane="production",
    )
    paths = render_woven_to_html_parts(
        woven, request=req, out_stem=tmp_path / "multi"
    )
    text = paths[0].read_text(encoding="utf-8")
    # First CC bubble: NO outline-N class
    # Second CC bubble: outline-2 (1px white)
    # Third CC bubble: outline-3 (double)
    assert "outline-2" in text
    assert "outline-3" in text
    # All three CC bubbles still carry agent-claude_code
    assert text.count("agent-claude_code") >= 3
    # Adam stays instance 1 (no outline classes added)
    # — sanity that we didn't accidentally outline the human
    adam_idx = text.find("agent-adam")
    # find the closing of that bubble's class attr
    close_idx = text.find("\"", adam_idx)
    adam_class_block = text[adam_idx:close_idx]
    assert "outline" not in adam_class_block


def test_render_splits_when_over_lane_cap(tmp_path):
    # 13 turns under production cap (12) → 2 parts
    turns = _mk_turns(13)
    woven = WovenFile(
        header=WovenHeader(
            session_id="x", anchor_id="x", started_at=0.0, ended_at=13.0, n_turns=13,
        ),
        turns=turns,
    )
    req = RenderRequest(
        project_code="GL-010", status="Building", outcome="Big build",
        lane="production",
    )
    paths = render_woven_to_html_parts(
        woven, request=req, out_stem=tmp_path / "gl-010"
    )
    assert len(paths) == 2
    assert paths[0].name == "gl-010-part-01.html"
    assert paths[1].name == "gl-010-part-02.html"
    # part 2 should reference GL-011 in title
    p2_text = paths[1].read_text(encoding="utf-8")
    assert "GL-011" in p2_text

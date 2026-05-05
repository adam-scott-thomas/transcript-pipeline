"""v0.5.2 — Part A (rails/avatars killed, fast dwells, inline divider),
Part B (terminal chrome on tool-call cells), Part C (carry detection +
render skip + thumbs-up indicator)."""

from pathlib import Path

import numpy as np
import pytest

from transcript_pipeline.carry_detector import (
    DEFAULT_CARRY_THRESHOLD,
    detect_carries,
)
from transcript_pipeline.skool_renderer import (
    RenderRequest,
    Segment,
    compute_dwell,
    parse_segments,
    render_woven_to_html_parts,
)
from transcript_pipeline.woven_jsonl import (
    WovenFile,
    WovenHeader,
    WovenTurn,
)


def _hdr(n=3):
    return WovenHeader(session_id="t", anchor_id="t",
                       started_at=0.0, ended_at=float(n), n_turns=n)


# ---------------------------------------------------------------------------
# PART A — aesthetic recalibration
# ---------------------------------------------------------------------------


def test_no_chapter_rail_in_html(tmp_path):
    woven = WovenFile(
        header=_hdr(2),
        turns=[
            WovenTurn(turn=1, agent="ADAM", role="HUMAN", body="hi",
                      stage="Context", chapter=1),
            WovenTurn(turn=2, agent="GPT", role="STRATEGY", body="hello",
                      stage="Problem", chapter=2, model="gpt-5.2"),
        ],
    )
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Fixed",
                              outcome="Strip Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    assert "chapter-rail" not in text
    assert "chapter-marker" not in text


def test_no_metadata_strip_in_html(tmp_path):
    woven = WovenFile(header=_hdr(1), turns=[
        WovenTurn(turn=1, agent="ADAM", role="HUMAN", body="hi",
                  stage="Context", chapter=1),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Fixed",
                              outcome="Strip Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    assert "metadata" not in text or "<aside class=\"metadata\">" not in text
    assert "meta-block" not in text


def test_no_avatar_circles_in_html(tmp_path):
    woven = WovenFile(header=_hdr(1), turns=[
        WovenTurn(turn=1, agent="CLAUDE-CODE", role="IMPL", body="hi",
                  stage="Build", chapter=1),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Fixed",
                              outcome="Strip Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    assert "class=\"avatar" not in text
    assert "class=\"bubble-wrap\"" not in text


def test_inline_chapter_divider_appears_at_chapter_changes(tmp_path):
    woven = WovenFile(header=_hdr(3), turns=[
        WovenTurn(turn=1, agent="ADAM", role="HUMAN", body="a",
                  stage="Context", chapter=1, chapter_outcome="Goal"),
        WovenTurn(turn=2, agent="GPT", role="-", body="b", model="gpt-5.2",
                  stage="Problem", chapter=2, chapter_outcome="Diagnosed"),
        WovenTurn(turn=3, agent="CLAUDE-CODE", role="-", body="c",
                  model="opus-4.7",
                  stage="Build", chapter=3, chapter_outcome="Built"),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Fixed",
                              outcome="Divider Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    # 2 dividers (between ch1→ch2 and ch2→ch3); ch1 has no preceding divider.
    # Count actual <div> elements, not CSS class references.
    assert text.count('<div class="chapter-divider"') == 2
    assert "[CHAPTER 02]" in text
    assert "[CHAPTER 03]" in text


def test_bubble_max_width_1400_in_css(tmp_path):
    woven = WovenFile(header=_hdr(1), turns=[
        WovenTurn(turn=1, agent="ADAM", role="HUMAN", body="hi",
                  stage="Context", chapter=1),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Fixed",
                              outcome="Width Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    assert "max-width: 1400px" in text


def test_dwells_use_fast_table():
    # Decision/Ship: 1500
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Decision", requires_human=False) == 1500
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Ship", requires_human=False) == 1500
    # Audit/Review: 1000
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Audit", requires_human=False) == 1000
    # Context/Build: 700
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Context", requires_human=False) == 700
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Build", requires_human=False) == 700
    # requires_human +400
    assert compute_dwell(Segment(kind="prose", text="-"),
                        stage="Audit", requires_human=True) == 1400
    # tool-call 50%, code-output 35%
    assert compute_dwell(Segment(kind="tool-call", text="-"),
                        stage="Decision", requires_human=False) == 750
    assert compute_dwell(Segment(kind="code-output", text="-"),
                        stage="Decision", requires_human=False) == 525
    # min floor 400
    assert compute_dwell(Segment(kind="tool-call", text="-"),
                        stage="Context", requires_human=False) == 400
    assert compute_dwell(Segment(kind="code-output", text="-"),
                        stage="Build", requires_human=False) == 400


# ---------------------------------------------------------------------------
# PART B — terminal chrome
# ---------------------------------------------------------------------------


def test_tool_call_segment_carries_tool_type_and_command():
    segs = parse_segments(
        "Setting up.\n[tool: Bash mkdir -p src/parsers]\nDone.",
        "CLAUDE-CODE",
    )
    tool_segs = [s for s in segs if s.kind == "tool-call"]
    assert len(tool_segs) == 1
    assert tool_segs[0].tool_type == "Bash"
    assert tool_segs[0].command == "mkdir -p src/parsers"


def test_unknown_tool_type_falls_back_to_empty_type():
    segs = parse_segments(
        "[tool: SomeMadeUpTool foo bar]",
        "CLAUDE-CODE",
    )
    tool_segs = [s for s in segs if s.kind == "tool-call"]
    assert len(tool_segs) == 1
    assert tool_segs[0].tool_type == ""
    assert "SomeMadeUpTool" in tool_segs[0].command


def test_terminal_chrome_in_rendered_html(tmp_path):
    woven = WovenFile(header=_hdr(1), turns=[
        WovenTurn(
            turn=1, agent="CLAUDE-CODE", role="-",
            body="Building.\n[tool: Bash cargo build --release]\n[result: Built in 47s]",
            model="opus-4.7", stage="Build", chapter=1,
        ),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Building",
                              outcome="Terminal Chrome", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    # prompt char inside .seg-tool
    assert 'class="prompt">$' in text
    # tool-type badge
    assert 'class="tool-badge">BASH' in text
    # command in bright class
    assert 'class="cmd">cargo build' in text
    # NO traffic-light dot
    assert "●" not in text
    assert "traffic-light" not in text
    # NO inset shadow on tool segments
    assert "inset" not in text or "shadow: inset" not in text


def test_codex_inverted_recess_in_html(tmp_path):
    woven = WovenFile(header=_hdr(1), turns=[
        WovenTurn(
            turn=1, agent="CODEX", role="-",
            body="Reviewing.\n[tool: Codex review src/foo.py]\n[result: ok]",
            model="gpt-5.2-codex", stage="Review", chapter=1,
        ),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Building",
                              outcome="Codex Recess", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    # CODEX recess palette (#EBEBEB / #B5B5B5 / #D4D4D4)
    assert "#EBEBEB" in text
    assert "#B5B5B5" in text


# ---------------------------------------------------------------------------
# PART C — carry detection
# ---------------------------------------------------------------------------


def _stub_embedder():
    """Return a deterministic embedder that maps text → fixed-dim vector
    keyed by content prefix. Identical-prefix bodies get identical vectors;
    different-prefix bodies get orthogonal vectors. Lets us test the
    detection logic without Ollama."""
    def _embed(text: str) -> np.ndarray:
        # 8-dim vectors per prefix bucket (first 6 chars of content)
        key = text.strip()[:6].lower()
        # hash to a stable axis 0..7
        idx = sum(ord(c) for c in key) % 8
        v = np.zeros(8, dtype=np.float32)
        v[idx] = 1.0
        return v
    return _embed


def test_carry_detector_flags_verbatim_paste():
    turns = [
        WovenTurn(turn=1, agent="ADAM", role="HUMAN", body="kick it off"),
        WovenTurn(turn=2, agent="CLAUDE", role="-", body="alpha-beta-gamma plan",
                  model="opus-4.7"),
        WovenTurn(turn=3, agent="ADAM", role="HUMAN", body="alpha-beta-gamma plan"),
        WovenTurn(turn=4, agent="GPT", role="-", body="ack",
                  model="gpt-5.2"),
    ]
    stats = detect_carries(turns, embedder=_stub_embedder(), threshold=0.85)
    assert stats.carries_detected == 1
    # the 2nd ADAM was the carry (turn 3)
    carry = next(t for t in turns if t.is_carry)
    assert carry.turn == 3
    assert carry.carry_source == 2  # source was CLAUDE turn 2
    # source bubble (turn 2) records that it carried to GPT
    src = turns[1]
    assert "GPT" in src.carried_to


def test_carry_detector_does_not_flag_substantively_new_adam():
    turns = [
        WovenTurn(turn=1, agent="CLAUDE", role="-", body="alpha plan",
                  model="opus-4.7"),
        WovenTurn(turn=2, agent="ADAM", role="HUMAN", body="distinct topic xyz"),
    ]
    stats = detect_carries(turns, embedder=_stub_embedder(), threshold=0.85)
    assert stats.carries_detected == 0
    assert turns[1].is_carry is False


def test_carry_threshold_flag_respected():
    turns = [
        WovenTurn(turn=1, agent="GPT", role="-", body="alpha-beta-gamma plan",
                  model="gpt-5.2"),
        WovenTurn(turn=2, agent="ADAM", role="HUMAN", body="alpha-beta-gamma plan"),
        WovenTurn(turn=3, agent="CLAUDE", role="-", body="ack",
                  model="opus-4.7"),
    ]
    stats_low = detect_carries(turns, embedder=_stub_embedder(), threshold=0.5)
    assert stats_low.carries_detected >= 1
    # Reset annotations and re-run with impossibly high threshold
    for t in turns:
        t.is_carry = False
        t.carry_source = None
        t.carry_similarity = None
        t.carried_to = []
    stats_high = detect_carries(turns, embedder=_stub_embedder(), threshold=1.01)
    assert stats_high.carries_detected == 0


def test_carry_skipped_in_render(tmp_path):
    woven = WovenFile(header=_hdr(3), turns=[
        WovenTurn(turn=1, agent="GPT", role="-", body="seed idea",
                  model="gpt-5.2",
                  stage="Context", chapter=1, carried_to=["CLAUDE"]),
        WovenTurn(turn=2, agent="ADAM", role="HUMAN", body="seed idea (paste)",
                  stage="Context", chapter=1,
                  is_carry=True, carry_source=1, carry_similarity=0.95),
        WovenTurn(turn=3, agent="CLAUDE", role="-", body="picking it up",
                  model="opus-4.7",
                  stage="Build", chapter=2),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Building",
                              outcome="Carry Test", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    # ADAM carry bubble should NOT appear (its body text suppressed)
    assert "seed idea (paste)" not in text
    # source bubble (GPT) should show thumbs-up indicator with CL
    assert "👍" in text
    assert "carry-chip" in text
    assert "→ CL" in text


def test_multiple_carries_stack_on_source(tmp_path):
    woven = WovenFile(header=_hdr(5), turns=[
        WovenTurn(turn=1, agent="GPT", role="-", body="strategy idea",
                  model="gpt-5.2",
                  stage="Context", chapter=1,
                  carried_to=["CLAUDE", "CLAUDE-CODE"]),
        WovenTurn(turn=2, agent="ADAM", role="HUMAN", body="(paste 1)",
                  stage="Context", chapter=1, is_carry=True, carry_source=1),
        WovenTurn(turn=3, agent="CLAUDE", role="-", body="ack 1",
                  model="opus-4.7", stage="Build", chapter=2),
        WovenTurn(turn=4, agent="ADAM", role="HUMAN", body="(paste 2)",
                  stage="Build", chapter=2, is_carry=True, carry_source=1),
        WovenTurn(turn=5, agent="CLAUDE-CODE", role="-", body="ack 2",
                  model="opus-4.7", stage="Build", chapter=2),
    ])
    paths = render_woven_to_html_parts(
        woven,
        request=RenderRequest(project_code="GL-001", status="Building",
                              outcome="Multi Carry", lane="production"),
        out_stem=tmp_path / "x",
    )
    text = paths[0].read_text(encoding="utf-8")
    # 2 chip <span>s — exclude the .carry-chip CSS class definition
    assert text.count('<span class="carry-chip">') == 2
    assert "→ CL" in text
    assert "→ CC" in text

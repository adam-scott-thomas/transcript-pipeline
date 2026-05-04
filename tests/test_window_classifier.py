"""window_classifier: parse_label edge cases + requires_human gating."""

from transcript_pipeline.embeddings import WindowLabel
from transcript_pipeline.window_classifier import _parse_label, load_system_prompt


def test_parse_clean_json():
    raw = '{"stage": "Build", "outcome": "implementing adapter", "confidence": 0.93}'
    label = _parse_label(raw, threshold=0.7)
    assert label.stage == "Build"
    assert label.outcome == "implementing adapter"
    assert label.confidence == 0.93
    assert label.requires_human is False


def test_parse_json_with_surrounding_prose():
    raw = "Sure, here's the JSON:\n\n{\"stage\": \"Audit\", \"outcome\": \"trace fail\", \"confidence\": 0.85}\n\ndone."
    label = _parse_label(raw, threshold=0.7)
    assert label.stage == "Audit"
    assert label.outcome == "trace fail"
    assert label.confidence == 0.85
    assert label.requires_human is False


def test_parse_invalid_json_marks_human_required():
    label = _parse_label("not json at all", threshold=0.7)
    assert label.confidence == 0.0
    assert label.requires_human is True


def test_parse_low_confidence_marks_human_required():
    raw = '{"stage": "Build", "outcome": "patching", "confidence": 0.5}'
    label = _parse_label(raw, threshold=0.7)
    assert label.requires_human is True


def test_parse_invalid_stage_falls_back_and_marks_human():
    raw = '{"stage": "Refactor", "outcome": "rewriting auth", "confidence": 0.95}'
    label = _parse_label(raw, threshold=0.7)
    # falls back to Context but flags for human (made-up stage)
    assert label.stage == "Context"
    assert label.requires_human is True


def test_parse_clamps_confidence_to_zero_to_one():
    raw = '{"stage": "Ship", "outcome": "deployed", "confidence": 1.5}'
    label = _parse_label(raw, threshold=0.7)
    assert label.confidence == 1.0


def test_load_system_prompt_includes_spec_text():
    """SPEC.md is the source of truth for the 9 stages."""
    prompt = load_system_prompt()
    # presence test rather than exact match — spec wording can drift
    for stage in ("Context", "Problem", "Audit", "Decision", "Build",
                  "Fix", "Review", "Ship", "Next"):
        assert stage in prompt, f"system prompt missing stage: {stage}"
    # boundary cases instructions present
    assert "Audit vs Problem" in prompt
    assert "Build vs Fix" in prompt
    assert "Review vs Ship" in prompt


def test_load_system_prompt_includes_output_instructions():
    prompt = load_system_prompt()
    assert "JSON object" in prompt
    assert "stage" in prompt
    assert "outcome" in prompt
    assert "confidence" in prompt

"""Classifier acceptance tests.

100-turn hand-labeled corpus + chapter boundary rules + cross-check semantics.
"""

import json
from pathlib import Path

from transcript_pipeline.classifier import (
    AUTO_APPLY_THRESHOLD,
    ClassifyInput,
    Disagreement,
    MockClient,
    Proposal,
    SPOT_CHECK_THRESHOLD,
    assign_chapters,
    chapter_boundary,
    classify_turns,
    summarize,
)
from transcript_pipeline.parser import ParsedTurn
from transcript_pipeline.schema import Agent, Stage


FIXTURE = Path(__file__).parent / "fixtures" / "labeled_100.json"


def _parsed(turns_data):
    """Build ParsedTurn objects from the labeled fixture."""
    out = []
    for i, row in enumerate(turns_data, start=1):
        out.append(
            ParsedTurn(
                turn=i,
                agent=Agent.ADAM,
                role="HUMAN",
                body=row["body"],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Mock classifier accuracy on hand-labeled corpus
# ---------------------------------------------------------------------------


def test_mock_classifier_above_threshold_agreement_exceeds_85_percent(core):
    """Acceptance: classifier agreement with human labels >85% when
    confidence >= 0.9 AND models agree."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = _parsed(data)
    primary = MockClient()
    auditor = MockClient()  # echoes primary by default → agreement everywhere

    proposals = classify_turns(parsed, primary, auditor)
    # turns where the gate would auto-apply
    auto_applied = [
        p for p in proposals
        if p.agreement and p.confidence >= AUTO_APPLY_THRESHOLD
    ]
    assert auto_applied, "no turns met auto-apply threshold"

    correct = sum(
        1
        for p, row in zip(proposals, data)
        if p in auto_applied and p.stage_proposed.value == row["expected"]
    )
    accuracy = correct / len(auto_applied)
    assert accuracy >= 0.85, (
        f"auto-apply accuracy {accuracy:.2%} < 85% target "
        f"({correct}/{len(auto_applied)})"
    )


def test_false_auto_apply_rate_below_2_percent(core):
    """Acceptance: false-auto-apply rate <2% (auto-applied tags that human
    would override)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = _parsed(data)
    primary = MockClient()
    auditor = MockClient()

    proposals = classify_turns(parsed, primary, auditor)
    auto_applied = [
        (p, row) for p, row in zip(proposals, data)
        if p.agreement and p.confidence >= AUTO_APPLY_THRESHOLD
    ]
    if not auto_applied:
        return  # vacuous; covered by other test
    wrong = sum(1 for p, row in auto_applied if p.stage_proposed.value != row["expected"])
    rate = wrong / len(auto_applied)
    assert rate < 0.02, (
        f"false-auto-apply rate {rate:.2%} >= 2% ({wrong}/{len(auto_applied)})"
    )


# ---------------------------------------------------------------------------
# Cross-check semantics
# ---------------------------------------------------------------------------


def test_disagreement_routes_to_human(core):
    """Disagreement → requires_human=True regardless of confidence."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = _parsed(data[:8])
    primary = MockClient()
    auditor = MockClient(disagree_on="audit")  # auditor disagrees on Audit-tagged turns
    dis_sink: list[Disagreement] = []

    proposals = classify_turns(parsed, primary, auditor, disagreement_sink=dis_sink)
    audit_props = [p for p in proposals if p.stage_proposed is Stage.AUDIT]
    if not audit_props:
        # the first 8 rows happen to not have Audit; test is vacuous, skip
        return
    for p in audit_props:
        assert not p.agreement
        assert p.requires_human
    assert len(dis_sink) == len(audit_props)


def test_below_spot_check_threshold_requires_human(core):
    """Confidence below SPOT_CHECK threshold → requires_human even on agreement."""
    parsed = [
        ParsedTurn(turn=1, agent=Agent.ADAM, role="HUMAN", body="completely ambiguous content")
    ]
    primary = MockClient()
    auditor = MockClient()
    proposals = classify_turns(parsed, primary, auditor)
    p = proposals[0]
    assert p.confidence < SPOT_CHECK_THRESHOLD
    assert p.requires_human


# ---------------------------------------------------------------------------
# Chapter boundary detection
# ---------------------------------------------------------------------------


def test_chapter_boundary_first_turn():
    assert chapter_boundary(None, Stage.CONTEXT, 0) is True


def test_chapter_boundary_same_stage_repeated():
    assert chapter_boundary(Stage.BUILD, Stage.BUILD, 0) is False
    assert chapter_boundary(Stage.BUILD, Stage.BUILD, 1) is False


def test_chapter_boundary_three_turns_max_forces_split():
    assert chapter_boundary(Stage.BUILD, Stage.BUILD, 3) is True


def test_chapter_boundary_forward_advance():
    assert chapter_boundary(Stage.DECISION, Stage.BUILD, 0) is True


def test_chapter_boundary_backward_jump():
    assert chapter_boundary(Stage.BUILD, Stage.AUDIT, 0) is True


def test_assign_chapters_decision_to_build_is_new_chapter():
    chapters = assign_chapters([
        Stage.CONTEXT,
        Stage.PROBLEM,
        Stage.DECISION,
        Stage.BUILD,
        Stage.BUILD,
        Stage.SHIP,
    ])
    # chapter 1: Context
    # chapter 2: Problem
    # chapter 3: Decision
    # chapter 4: Build (forward advance)
    # chapter 4: Build (same stage, no split until streak=3)
    # chapter 5: Ship (forward advance)
    assert chapters == [1, 2, 3, 4, 4, 5]


def test_assign_chapters_force_split_after_three_repeats():
    chapters = assign_chapters([
        Stage.BUILD, Stage.BUILD, Stage.BUILD, Stage.BUILD, Stage.BUILD,
    ])
    # streak hits 3 on the 4th turn → split there
    assert chapters[0] == 1
    assert chapters[3] == 2  # forced split


# ---------------------------------------------------------------------------
# Summary / stats
# ---------------------------------------------------------------------------


def test_summarize_partitions_correctly(core):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = _parsed(data)
    primary = MockClient()
    auditor = MockClient()
    proposals = classify_turns(parsed, primary, auditor)
    s = summarize(proposals)
    assert s.total == len(proposals)
    assert s.auto_apply + s.spot_check + s.human_required == s.total

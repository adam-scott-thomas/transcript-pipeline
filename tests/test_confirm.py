"""Confirmation gate behavior in non-interactive modes."""

from transcript_pipeline.classifier import (
    ClassifyOutput,
    Proposal,
)
from transcript_pipeline.confirm import (
    ConfirmMode,
    Decision,
    confirm,
)
from transcript_pipeline.schema import Stage


def _mk_proposal(idx, stage=Stage.BUILD, conf=0.95, agreement=True, requires_human=False):
    o = ClassifyOutput(stage=stage, confidence=conf, reasoning="-")
    return Proposal(
        turn_index=idx,
        stage_proposed=stage,
        chapter_proposed=1,
        confidence=conf,
        reasoning="-",
        agreement=agreement,
        requires_human=requires_human,
        primary=o,
        auditor=o,
    )


def test_dry_run_accepts_everything_without_blocking(core):
    proposals = [_mk_proposal(i) for i in range(1, 6)]
    bodies = ["b"] * 5
    final, log = confirm(proposals, bodies, mode=ConfirmMode.DRY_RUN)
    assert len(final) == 5
    assert all(c.decision is Decision.ACCEPTED for c in log)


def test_auto_mode_silently_applies_above_threshold(core):
    proposals = [_mk_proposal(i, conf=0.95) for i in range(1, 4)]
    bodies = ["b"] * 3
    final, log = confirm(
        proposals,
        bodies,
        mode=ConfirmMode.AUTO,
        auto_confirm_above=0.9,
    )
    assert len(final) == 3
    assert all(c.decision is Decision.ACCEPTED for c in log)


def test_auto_mode_silently_applies_above_threshold_and_quits_on_subthreshold_in_ci(core):
    """In CI (stdin captured by pytest), the first turn auto-applies; the
    second hits the interactive branch and quits cleanly via the EOF/OSError
    fallback — instead of deadlocking."""
    proposals = [
        _mk_proposal(1, conf=0.95),
        _mk_proposal(2, conf=0.65, requires_human=True),
    ]
    bodies = ["b1", "b2"]
    final, log = confirm(
        proposals,
        bodies,
        mode=ConfirmMode.AUTO,
        auto_confirm_above=0.9,
    )
    # First auto-applied; second short-circuits to QUIT.
    assert log[0].decision is Decision.ACCEPTED
    assert log[-1].decision is Decision.QUIT
    # Only the auto-applied proposal makes it through.
    assert len(final) == 1
    assert final[0].turn_index == 1

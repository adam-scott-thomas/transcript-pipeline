# transcript_pipeline.validator
# =============================================================================
# Validate a Transcript against the Transcript Format v1.0 spec.
#
# Seven error/warn classes mandated by the brief:
#
#   1. turn > 12                                   → error  turn_cap_exceeded
#   2. stage not in allowed set                     → error  stage_unknown
#   3. status_tag contradicts title status          → error  status_contradiction
#   4. outcome > 6 words                            → error  outcome_too_long
#   5. chapter count outside 3–8                    → warn   chapter_count_out_of_band
#   6. resumed=true with turn>1                     → error  resumed_with_history
#   7. reference format mismatch                    → error  reference_malformed
#
# Findings emit through the spine DiagnosticBus so subscribers (CLI, MCP) see
# them in real time. The function also returns the list of findings so unit
# tests can assert without subscribing.
#
# Validator is purely a reader. It never mutates the Transcript.
# =============================================================================

from __future__ import annotations

import re
from typing import Iterable

from transcript_pipeline.runtime import Diagnostic, emit
from transcript_pipeline.schema import (
    CHAPTER_MAX,
    CHAPTER_MIN,
    MAX_TURNS_PER_VIDEO,
    OUTCOME_MAX_WORDS,
    REF_PATTERN,
    Stage,
    Status,
    StatusTag,
    Transcript,
)


_REF_RE = re.compile(REF_PATTERN)


# Map StatusTag → set of compatible title Statuses. A title in BLOCKED can't
# carry a SHIPPED tag on any message, etc. (spec section 5: "Do not contradict
# title status"). Audit/Reset/Field Notes don't take any tags by default.
_TAG_COMPATIBILITY: dict[StatusTag, set[Status]] = {
    StatusTag.SHIPPED: {Status.SHIPPED},
    StatusTag.BUILDING: {Status.BUILDING, Status.INCOMPLETE},
    StatusTag.INCOMPLETE: {Status.INCOMPLETE, Status.BUILDING},
    StatusTag.BLOCKED: {Status.BLOCKED, Status.INCOMPLETE},
    StatusTag.FIXED: {Status.FIXED, Status.SHIPPED},
}


def validate_transcript(t: Transcript) -> list[Diagnostic]:
    """Run every check, emit diagnostics, return them."""
    findings: list[Diagnostic] = []

    def add(severity: str, code: str, message: str, location: str | None = None) -> None:
        d = Diagnostic(severity=severity, code=code, message=message, location=location)
        findings.append(d)
        emit(d)

    _check_outcome_length(t, add)
    _check_resumed_consistency(t, add)
    _check_turn_cap(t, add)
    _check_stages(t, add)
    _check_status_tags(t, add)
    _check_references(t, add)
    _check_chapter_count(t, add)

    return findings


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_outcome_length(
    t: Transcript, add
) -> None:
    words = t.header.outcome.split()
    if len(words) > OUTCOME_MAX_WORDS:
        add(
            "error",
            "outcome_too_long",
            f"outcome has {len(words)} words; max is {OUTCOME_MAX_WORDS} "
            f"({t.header.outcome!r})",
            location="header.outcome",
        )


def _check_resumed_consistency(
    t: Transcript, add
) -> None:
    # Spec section 8: resumed = new file, chapter numbering restarts at 01.
    # If header.resumed is true, we expect this file to look like a fresh
    # session — turn count alone won't catch every case, but the strongest
    # signal we have is "this file claims to be a new session yet has prior
    # turns recorded with turn > 1" handled by spec interpretation: a
    # resumed file with turn>1 is a malformed split.
    if t.header.resumed:
        for turn in t.turns:
            if turn.turn > 1 and turn == t.turns[0]:
                # the very first turn of a resumed file should be turn=1
                add(
                    "error",
                    "resumed_with_history",
                    f"file marks resumed=true but its first turn is "
                    f"#{turn.turn} (must be 1; resumed files restart numbering)",
                    location=f"turns[0]",
                )


def _check_turn_cap(t: Transcript, add) -> None:
    for turn in t.turns:
        if turn.turn > MAX_TURNS_PER_VIDEO:
            add(
                "error",
                "turn_cap_exceeded",
                f"turn #{turn.turn} exceeds hard cap of "
                f"{MAX_TURNS_PER_VIDEO}; split into Part 2 (e.g. {t.header.code} → "
                f"{t.header.project}-{t.header.project_number + 1:03d})",
                location=f"turns[{turn.turn - 1}]",
            )
    if len(t.turns) > MAX_TURNS_PER_VIDEO:
        add(
            "error",
            "turn_cap_exceeded",
            f"transcript has {len(t.turns)} turns; max per video is "
            f"{MAX_TURNS_PER_VIDEO}",
        )


def _check_stages(t: Transcript, add) -> None:
    allowed = {s.value for s in Stage}
    for turn in t.turns:
        # Stage is enum-typed at construction so "unknown" is normally
        # impossible — but if dataclass-frozen instances were built from a
        # dict that bypassed the enum (e.g. raw JSON load), the value falls
        # outside the enum. Re-check defensively.
        if turn.stage.value not in allowed:
            add(
                "error",
                "stage_unknown",
                f"stage {turn.stage!r} not in allowed set {sorted(allowed)}",
                location=f"turns[{turn.turn - 1}].stage",
            )


def _check_status_tags(t: Transcript, add) -> None:
    for turn in t.turns:
        if turn.status_tag is None:
            continue
        compatible = _TAG_COMPATIBILITY.get(turn.status_tag, set())
        if t.header.status not in compatible:
            add(
                "error",
                "status_contradiction",
                f"turn #{turn.turn} carries [STATUS: {turn.status_tag.value}] "
                f"but title status is '{t.header.status.value}' — "
                f"compatible title statuses for this tag: "
                f"{sorted(s.value for s in compatible)}",
                location=f"turns[{turn.turn - 1}].status_tag",
            )


def _check_references(t: Transcript, add) -> None:
    for turn in t.turns:
        for ref in turn.references:
            if not _REF_RE.match(ref):
                add(
                    "error",
                    "reference_malformed",
                    f"reference {ref!r} on turn #{turn.turn} does not match "
                    f"PROJECT-NUMBER format (e.g. GL-002)",
                    location=f"turns[{turn.turn - 1}].references",
                )


def _check_chapter_count(t: Transcript, add) -> None:
    n = t.chapter_count
    if n < CHAPTER_MIN or n > CHAPTER_MAX:
        add(
            "warn",
            "chapter_count_out_of_band",
            f"transcript has {n} chapters; recommended band is "
            f"{CHAPTER_MIN}–{CHAPTER_MAX}",
        )


# Convenience: severity-aware exit code helper used by the CLI.
def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diagnostics)


def has_warnings(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.severity == "warn" for d in diagnostics)

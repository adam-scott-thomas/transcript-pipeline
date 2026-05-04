# transcript_pipeline.confirm
# =============================================================================
# Interactive confirmation gate for v0.2 classifier proposals.
#
# Spec rule: no silent application. Every proposal that gets applied must
# either pass the auto-confirm threshold (--auto-confirm-above 0.9) AND have
# model agreement, OR be confirmed by a human keystroke. Sub-threshold and
# disagreement turns are always surfaced.
#
# Modes:
#
#   ConfirmMode.INTERACTIVE  — every turn is shown; auto-applied turns scroll
#                              by with a confirmation summary at the end
#   ConfirmMode.AUTO         — sub-threshold + disagreement still surface
#                              one at a time; the rest are applied silently
#                              (still logged)
#   ConfirmMode.DRY_RUN      — never blocks on input; returns the proposals
#                              as-is. For test/CI use.
#
# Keystroke contract:
#
#   y / <Enter>  — accept the proposal
#   n            — reject, prompt for override (numeric stage selector)
#   s            — skip this turn (leaves stage unset; embedder will reject
#                  unless the parser captured an explicit tag earlier)
#   q            — quit the gate; nothing applied beyond this point
#
# Override flow on `n`:
#
#   Display a numbered menu of all 9 stages. User types one digit, hits
#   <Enter>. The override is recorded as `human_chosen`.
#
# Implementation notes:
#   - Single-keystroke I/O: msvcrt on Windows, termios on POSIX. Falls back
#     to line-buffered input when stdin isn't a TTY (CI, piped runs).
#   - The gate never re-prompts on the same turn — once decided, decision is
#     final. This matches the spec rule "Human override always wins, never
#     re-prompted on same turn."
# =============================================================================

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

from transcript_pipeline.classifier import AUTO_APPLY_THRESHOLD, Proposal
from transcript_pipeline.schema import Stage


class ConfirmMode(str, Enum):
    INTERACTIVE = "interactive"
    AUTO = "auto"
    DRY_RUN = "dry_run"


class Decision(str, Enum):
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"
    SKIPPED = "skipped"
    QUIT = "quit"


@dataclass
class Confirmation:
    """One row of the confirmation log. Emitted to the diagnostic JSONL so
    end-of-day review can spot patterns (which proposals get rejected most)."""

    turn_index: int
    decision: Decision
    proposal_stage: Stage
    final_stage: Stage | None  # None if SKIPPED or QUIT
    confidence: float
    requires_human: bool


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _getch() -> str:
    """Read one character. Falls back to line-buffered input when stdin
    isn't a TTY (so CI doesn't hang). If the input layer can't read at all
    (e.g. pytest captures stdin and refuses), bail with 'q' to abort the
    gate cleanly."""
    if not sys.stdin.isatty():
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return "q"
        return line[:1] if line else "q"

    try:
        # Windows
        import msvcrt  # type: ignore[import-not-found]

        ch = msvcrt.getch()
        if isinstance(ch, bytes):
            try:
                return ch.decode("utf-8")
            except UnicodeDecodeError:
                return ""
        return ch
    except ImportError:
        pass

    # POSIX
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _print_proposal(p: Proposal, body: str) -> None:
    bar = "─" * 70
    print(bar)
    print(
        f"#{p.turn_index:02d}  proposed: {p.stage_proposed.value:8s}  "
        f"chap: {p.chapter_proposed:02d}  "
        f"conf: {p.confidence:.2f}  "
        f"agreement: {'yes' if p.agreement else 'NO'}"
    )
    print(f"reason: {p.reasoning}")
    body_short = body if len(body) <= 240 else (body[:240] + "…")
    print(f"body  : {body_short}")
    if p.requires_human:
        print(
            f"primary : {p.primary.stage.value:8s} ({p.primary.confidence:.2f}) "
            f"{p.primary.reasoning}"
        )
        print(
            f"auditor : {p.auditor.stage.value:8s} ({p.auditor.confidence:.2f}) "
            f"{p.auditor.reasoning}"
        )


def _prompt_override() -> Stage | None:
    """Show numbered menu and read one digit. Empty/invalid → None (skip)."""
    stages = list(Stage)
    print("\noverride — pick stage by number:")
    for i, s in enumerate(stages, start=1):
        print(f"  {i}. {s.value}")
    sys.stdout.write("> ")
    sys.stdout.flush()
    ch = _getch()
    print(ch)
    try:
        idx = int(ch)
        if 1 <= idx <= len(stages):
            return stages[idx - 1]
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def confirm(
    proposals: list[Proposal],
    bodies: list[str],
    mode: ConfirmMode = ConfirmMode.INTERACTIVE,
    auto_confirm_above: float = AUTO_APPLY_THRESHOLD,
) -> tuple[list[Proposal], list[Confirmation]]:
    """Walk proposals, surface what needs human review, return final
    proposals (with `human_chosen` mutations) plus a confirmation log."""
    if len(proposals) != len(bodies):
        raise ValueError("proposals and bodies must align 1:1")

    confirmations: list[Confirmation] = []
    final: list[Proposal] = []

    for prop, body in zip(proposals, bodies):
        # ── auto mode + above threshold + agreement: silent ──
        if (
            mode is ConfirmMode.AUTO
            and prop.agreement
            and prop.confidence >= auto_confirm_above
        ):
            confirmations.append(
                Confirmation(
                    turn_index=prop.turn_index,
                    decision=Decision.ACCEPTED,
                    proposal_stage=prop.stage_proposed,
                    final_stage=prop.stage_proposed,
                    confidence=prop.confidence,
                    requires_human=False,
                )
            )
            final.append(prop)
            continue

        # ── dry-run: never block ──
        if mode is ConfirmMode.DRY_RUN:
            confirmations.append(
                Confirmation(
                    turn_index=prop.turn_index,
                    decision=Decision.ACCEPTED,
                    proposal_stage=prop.stage_proposed,
                    final_stage=prop.stage_proposed,
                    confidence=prop.confidence,
                    requires_human=prop.requires_human,
                )
            )
            final.append(prop)
            continue

        # ── interactive: ask ──
        _print_proposal(prop, body)
        sys.stdout.write("[y/n/s/q]> ")
        sys.stdout.flush()
        ch = _getch().lower()
        print(ch)

        if ch in ("q",):
            confirmations.append(
                Confirmation(
                    turn_index=prop.turn_index,
                    decision=Decision.QUIT,
                    proposal_stage=prop.stage_proposed,
                    final_stage=None,
                    confidence=prop.confidence,
                    requires_human=prop.requires_human,
                )
            )
            break

        if ch in ("s",):
            confirmations.append(
                Confirmation(
                    turn_index=prop.turn_index,
                    decision=Decision.SKIPPED,
                    proposal_stage=prop.stage_proposed,
                    final_stage=None,
                    confidence=prop.confidence,
                    requires_human=prop.requires_human,
                )
            )
            continue

        if ch in ("n",):
            chosen = _prompt_override()
            if chosen is None:
                # invalid override — treat as skip rather than guess
                confirmations.append(
                    Confirmation(
                        turn_index=prop.turn_index,
                        decision=Decision.SKIPPED,
                        proposal_stage=prop.stage_proposed,
                        final_stage=None,
                        confidence=prop.confidence,
                        requires_human=prop.requires_human,
                    )
                )
                continue
            # mutate proposal in place
            new_prop = Proposal(
                turn_index=prop.turn_index,
                stage_proposed=chosen,
                chapter_proposed=prop.chapter_proposed,
                confidence=1.0,
                reasoning=f"human override (was {prop.stage_proposed.value})",
                agreement=True,
                requires_human=False,
                primary=prop.primary,
                auditor=prop.auditor,
            )
            final.append(new_prop)
            confirmations.append(
                Confirmation(
                    turn_index=prop.turn_index,
                    decision=Decision.OVERRIDDEN,
                    proposal_stage=prop.stage_proposed,
                    final_stage=chosen,
                    confidence=prop.confidence,
                    requires_human=prop.requires_human,
                )
            )
            continue

        # default = accept (y or anything else)
        confirmations.append(
            Confirmation(
                turn_index=prop.turn_index,
                decision=Decision.ACCEPTED,
                proposal_stage=prop.stage_proposed,
                final_stage=prop.stage_proposed,
                confidence=prop.confidence,
                requires_human=prop.requires_human,
            )
        )
        final.append(prop)

    return final, confirmations

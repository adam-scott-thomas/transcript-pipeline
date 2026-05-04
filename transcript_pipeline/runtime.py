# transcript_pipeline.runtime
# =============================================================================
# Spine boot and capability registry.
#
# Why this exists: every component (parser, validator, embedder, renderer,
# CLI, MCP server) needs a single agreed-upon registry for paths, config, and
# inter-component handles. spine.Core is exactly that: register before boot,
# read after. After boot the registry is frozen — no late writes.
#
# What gets registered:
#
#   path.out_dir              → Path  (where to write embedded + rendered files)
#   config.fail_on_warn       → bool  (CI flag from TRANSCRIPT_FAIL_ON_WARN)
#   config.log_level          → str   (debug|info|warn|error)
#   capability.parser         → callable  (parse_log)
#   capability.validator      → callable  (validate_transcript)
#   capability.embedder       → callable  (embed_to_file)
#   capability.renderer       → callable  (render_transcript)
#   diagnostics.bus           → DiagnosticBus  (validator findings sink)
#
# The DiagnosticBus is a tiny list-of-subscribers pattern. Validator emits
# Diagnostic records; the CLI subscribes to print them; the MCP server
# subscribes to attach them to tool results. spine.observers exists, but is
# scoped to registry-usage telemetry — typed validator findings deserve their
# own channel.
#
# Boot is idempotent through `_core` memoization. Tests that need a fresh
# registry call `_reset_for_tests()`.
# =============================================================================

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from spine import Core


_core: Optional[Core] = None


@dataclass(frozen=True)
class Diagnostic:
    """A single validator finding. Severity is `error` or `warn`."""

    severity: str
    code: str  # short identifier, e.g. "turn_cap_exceeded"
    message: str
    location: str | None = None

    def __str__(self) -> str:
        line = f"[{self.severity}] {self.code}: {self.message}"
        if self.location:
            line += f"  @ {self.location}"
        return line


class DiagnosticBus:
    """List-of-subscribers. Subscribers are called synchronously, in
    registration order, on every emit. Subscribers raising propagate."""

    def __init__(self) -> None:
        self._subs: list[Callable[[Diagnostic], None]] = []
        self._record: list[Diagnostic] = []

    def subscribe(self, fn: Callable[[Diagnostic], None]) -> None:
        self._subs.append(fn)

    def emit(self, d: Diagnostic) -> None:
        self._record.append(d)
        for fn in self._subs:
            fn(d)

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return tuple(self._record)

    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self._record if d.severity == "error")

    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self._record if d.severity == "warn")

    def clear(self) -> None:
        self._record.clear()


def _resolve_out_dir() -> Path:
    raw = os.environ.get("TRANSCRIPT_OUT_DIR", "./out")
    p = Path(raw).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _register_capabilities(core: Core) -> None:
    """Wire every component's primary callable into the registry. Imports are
    deferred so the schema module stays import-cheap."""
    from transcript_pipeline.parser import parse_log
    from transcript_pipeline.validator import validate_transcript
    from transcript_pipeline.embedder import embed_to_file
    from transcript_pipeline.renderer import render_transcript

    core.register("capability.parser", parse_log)
    core.register("capability.validator", validate_transcript)
    core.register("capability.embedder", embed_to_file)
    core.register("capability.renderer", render_transcript)


def boot(env: str = "dev", session: str | None = None) -> Core:
    """Create-or-return the singleton Core, register everything, freeze.

    Idempotent: a second call returns the same Core. Tests use
    `_reset_for_tests()` to start fresh."""
    global _core
    if _core is not None:
        return _core

    core = Core()

    # ── paths + config from env (envmanifest contract) ──
    core.register("path.out_dir", _resolve_out_dir())
    core.register("config.log_level", os.environ.get("TRANSCRIPT_LOG_LEVEL", "info"))
    core.register("config.fail_on_warn", os.environ.get("TRANSCRIPT_FAIL_ON_WARN", "0") == "1")

    # ── diagnostic bus ──
    core.register("diagnostics.bus", DiagnosticBus())

    # ── component callables ──
    _register_capabilities(core)

    core.boot(env=env, session=session)
    _core = core
    return core


def get_core() -> Core:
    """Return the booted Core. Raises if `boot()` hasn't been called."""
    if _core is None:
        raise RuntimeError("transcript_pipeline.runtime.boot() must be called first")
    return _core


def _reset_for_tests() -> None:
    """Forget the singleton. Tests use this between cases."""
    global _core
    _core = None


def emit(d: Diagnostic) -> None:
    """Publish a diagnostic. Falls back to stderr if no spine is booted (so
    tests can call the validator directly)."""
    if _core is None:
        print(str(d), file=sys.stderr)
        return
    bus: DiagnosticBus = _core.get("diagnostics.bus")
    bus.emit(d)

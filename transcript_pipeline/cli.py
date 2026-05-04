# transcript_pipeline.cli
# =============================================================================
# Command-line entry. Wraps parser → embedder → validator → renderer.
#
# Subcommands:
#
#   transcript ingest   <raw_log> [--project GL --number 4 --status Fixed
#                                   --outcome "Auth Key Flow" --session ...]
#         → write embedded YAML+body file under TRANSCRIPT_OUT_DIR
#
#   transcript validate <embedded.yml>
#         → exit 0 (clean) / 1 (errors) / 2 (errors or warnings if
#           TRANSCRIPT_FAIL_ON_WARN=1)
#
#   transcript render   <embedded.yml>
#         → write transcript.txt + chapters.md + bubbles.json under
#           TRANSCRIPT_OUT_DIR/<code>/
#
#   transcript batch    <dir>
#         → for every .yml in <dir>, validate then render. Aggregates timing
#           so we can keep an eye on the 8–10 videos/day cadence (target:
#           <30s for 10 files on local hardware).
#
# Spine boots once at process start. Components are reached via `core.get(...)`
# so this module never imports parser/embedder/validator/renderer internals
# beyond what spine exposes.
# =============================================================================

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from transcript_pipeline.runtime import (
    Diagnostic,
    DiagnosticBus,
    boot,
    get_core,
)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="transcript", description="transcript-pipeline CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="raw log → embedded YAML")
    ing.add_argument("raw_log", type=Path)
    ing.add_argument("--project", required=True)
    ing.add_argument("--number", type=int, required=True)
    ing.add_argument("--status", required=True, help="title status (e.g. Fixed)")
    ing.add_argument("--outcome", required=True, help="6 words max")
    ing.add_argument("--session", required=True, help="ISO YYYY-MM-DD-HHMM")
    ing.add_argument("--resumed", action="store_true")
    ing.add_argument("--out", type=Path, help="output path (default: out_dir/<code>/embedded.yml)")

    val = sub.add_parser("validate", help="check embedded file against spec v1.0")
    val.add_argument("embedded", type=Path)

    rnd = sub.add_parser("render", help="embedded → transcript.txt + chapters.md + bubbles.json")
    rnd.add_argument("embedded", type=Path)

    bat = sub.add_parser("batch", help="validate+render every .yml in a directory")
    bat.add_argument("dir", type=Path)

    return p


# ---------------------------------------------------------------------------
# subcommand impls
# ---------------------------------------------------------------------------


def _cmd_ingest(args) -> int:
    from transcript_pipeline.embedder import EmbedRequest, embed_to_file
    from transcript_pipeline.schema import Status

    core = get_core()
    parse_log = core.get("capability.parser")

    raw = Path(args.raw_log).read_text(encoding="utf-8")
    parsed = parse_log(raw)

    req = EmbedRequest(
        project=args.project,
        project_number=args.number,
        status=Status(args.status),
        outcome=args.outcome,
        session_id=args.session,
        resumed=bool(args.resumed),
    )
    out_dir: Path = core.get("path.out_dir")
    code = f"{args.project}-{args.number:03d}"
    out_path = Path(args.out) if args.out else (out_dir / code / "embedded.yml")
    written = embed_to_file(req, parsed, out_path)
    print(f"wrote {written}")
    return 0


def _cmd_validate(args) -> int:
    from transcript_pipeline.embedder import load_embedded

    core = get_core()
    bus: DiagnosticBus = core.get("diagnostics.bus")
    bus.clear()
    validator = core.get("capability.validator")

    text = Path(args.embedded).read_text(encoding="utf-8")
    transcript = load_embedded(text)
    diagnostics = validator(transcript)

    for d in diagnostics:
        print(str(d))

    fail_on_warn: bool = core.get("config.fail_on_warn")
    has_err = any(d.severity == "error" for d in diagnostics)
    has_warn = any(d.severity == "warn" for d in diagnostics)

    if has_err:
        print(f"FAILED: {sum(1 for d in diagnostics if d.severity == 'error')} error(s)")
        return 1
    if fail_on_warn and has_warn:
        print(f"FAILED (CI strict): {sum(1 for d in diagnostics if d.severity == 'warn')} warning(s)")
        return 2
    print("OK")
    return 0


def _cmd_render(args) -> int:
    from transcript_pipeline.embedder import load_embedded

    core = get_core()
    renderer = core.get("capability.renderer")
    out_dir: Path = core.get("path.out_dir")

    text = Path(args.embedded).read_text(encoding="utf-8")
    transcript = load_embedded(text)
    result = renderer(transcript, out_dir)
    print(f"transcript: {result.transcript_path}")
    print(f"chapters:   {result.chapters_path}")
    print(f"bubbles:    {result.bubbles_path}")
    return 0


def _cmd_batch(args) -> int:
    from transcript_pipeline.embedder import load_embedded

    core = get_core()
    bus: DiagnosticBus = core.get("diagnostics.bus")
    validator = core.get("capability.validator")
    renderer = core.get("capability.renderer")
    out_dir: Path = core.get("path.out_dir")

    files = sorted(Path(args.dir).glob("**/embedded.yml"))
    if not files:
        files = sorted(Path(args.dir).glob("*.yml"))
    if not files:
        print(f"no embedded files found under {args.dir}", file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    failed = 0
    for f in files:
        bus.clear()
        text = f.read_text(encoding="utf-8")
        transcript = load_embedded(text)
        diagnostics = validator(transcript)
        if any(d.severity == "error" for d in diagnostics):
            print(f"× {f}  ({sum(1 for d in diagnostics if d.severity=='error')} errors)")
            failed += 1
            continue
        result = renderer(transcript, out_dir)
        print(f"✓ {f}  → {result.transcript_path.parent}")

    dt = time.perf_counter() - t0
    print(f"\nbatch: {len(files)} file(s), {failed} failed, {dt:.2f}s")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    boot()
    handler = {
        "ingest": _cmd_ingest,
        "validate": _cmd_validate,
        "render": _cmd_render,
        "batch": _cmd_batch,
    }[args.cmd]
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

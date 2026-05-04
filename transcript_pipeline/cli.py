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

    # v0.2 — classifier flags
    ing.add_argument(
        "--classify",
        choices=["off", "interactive", "auto", "dry-run", "mock"],
        default="off",
        help=(
            "off: requires explicit [STAGE: ...] tags (v0.1 behavior). "
            "interactive: every turn confirmed. "
            "auto: silent above --auto-confirm-above, surface the rest. "
            "dry-run: classify but don't apply (logs only). "
            "mock: deterministic stub classifier (no API keys required)."
        ),
    )
    ing.add_argument(
        "--auto-confirm-above",
        type=float,
        default=0.9,
        help="confidence threshold for auto-apply in --classify auto (default 0.9)",
    )

    val = sub.add_parser("validate", help="check embedded file against spec v1.0")
    val.add_argument("embedded", type=Path)

    rnd = sub.add_parser("render", help="embedded → transcript.txt + chapters.md + bubbles.json")
    rnd.add_argument("embedded", type=Path)

    bat = sub.add_parser("batch", help="validate+render every .yml in a directory")
    bat.add_argument("dir", type=Path)

    # v0.3 — temporal weave: anchor CC session + GPT + Spidey-Claude
    wv = sub.add_parser(
        "weave",
        help="anchor CC session + other chats happening at the same time → woven HTML",
    )
    wv.add_argument("--anchor", type=Path, required=True, help="path to a CC session JSONL")
    wv.add_argument("--gpt-export", type=Path, help="OpenAI export dir or conversations.json")
    wv.add_argument("--claude-export", type=Path, help="Claude.ai export .zip or conversations.json")
    wv.add_argument("--lane", default="archive", choices=["production", "archive", "uncapped"])
    wv.add_argument("--window-hours", type=float, default=2.0,
                    help="±hours around anchor span to pull others (default 2)")
    wv.add_argument("--project", default="GL")
    wv.add_argument("--number", type=int, required=True)
    wv.add_argument("--status", default="Field Notes")
    wv.add_argument("--outcome", default="Cross-AI Session")
    wv.add_argument("--out", type=Path, help="HTML output (default: out_dir/skool/<sessionId>.html)")

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

    # v0.2 — classifier
    if args.classify != "off":
        parsed = _run_classifier(parsed, args.classify, args.auto_confirm_above, core)

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


def _run_classifier(parsed, mode_flag, auto_threshold, core):
    """Apply two-model classification + confirmation gate. Mutates parsed
    turns to set stage / chapter from confirmed proposals. Skipped/quit
    turns leave whatever the parser captured (caller's problem)."""
    from transcript_pipeline.classifier import (
        AnthropicSonnetClient,
        CostRecord,
        Disagreement,
        MockClient,
        OpenAIGPTClient,
        classify_turns,
        summarize,
    )
    from transcript_pipeline.confirm import ConfirmMode, confirm
    from transcript_pipeline.diagnostics import (
        append_confirmations,
        append_costs,
        append_disagreements,
        estimate_cost_usd,
    )

    if mode_flag == "mock":
        primary = MockClient()
        auditor = MockClient()
        gate_mode = ConfirmMode.AUTO
    elif mode_flag == "dry-run":
        primary = MockClient()
        auditor = MockClient()
        gate_mode = ConfirmMode.DRY_RUN
    else:
        primary = AnthropicSonnetClient()
        auditor = OpenAIGPTClient()
        gate_mode = ConfirmMode.INTERACTIVE if mode_flag == "interactive" else ConfirmMode.AUTO

    cost_sink: list[CostRecord] = []
    dis_sink: list[Disagreement] = []
    proposals = classify_turns(parsed, primary, auditor, cost_sink=cost_sink, disagreement_sink=dis_sink)

    bodies = [pt.body for pt in parsed]
    final, confirmations = confirm(
        proposals,
        bodies,
        mode=gate_mode,
        auto_confirm_above=auto_threshold,
    )

    # apply final proposals onto parsed turns
    by_turn = {p.turn_index: p for p in final}
    for pt in parsed:
        prop = by_turn.get(pt.turn)
        if prop is None:
            continue  # skipped or quit
        if pt.stage is None:
            pt.stage = prop.stage_proposed
        if pt.chapter is None:
            pt.chapter = prop.chapter_proposed

    # persist diagnostics
    out_dir: Path = core.get("path.out_dir")
    if dis_sink:
        append_disagreements(out_dir, dis_sink)
    if cost_sink:
        append_costs(out_dir, cost_sink)
    if confirmations:
        append_confirmations(out_dir, confirmations)

    stats = summarize(proposals)
    cost = estimate_cost_usd(cost_sink)
    print(
        f"classifier: total={stats.total} auto={stats.auto_apply} "
        f"spot={stats.spot_check} human={stats.human_required} "
        f"disagree={stats.disagreements} est_cost=${cost:.4f}"
    )
    return parsed


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
    # Windows defaults to cp1252 stdout; force UTF-8 so unicode in
    # transcripts and rendered output doesn't crash the print path.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = _build_parser().parse_args(argv)
    boot()
    handler = {
        "ingest": _cmd_ingest,
        "validate": _cmd_validate,
        "render": _cmd_render,
        "batch": _cmd_batch,
        "weave": _cmd_weave,
    }[args.cmd]
    return handler(args)


def _cmd_weave(args) -> int:
    """v0.3 — anchor CC session + overlapping GPT + Spidey-Claude → woven HTML."""
    from transcript_pipeline.adapters import (
        load_cc_jsonl,
        load_gpt_export,
        load_claude_web_export,
    )
    from transcript_pipeline.embedder import EmbedRequest, embed
    from transcript_pipeline.html_renderer import render_html_to_file
    from transcript_pipeline.schema import Stage, Status
    from transcript_pipeline.temporal_weaver import weave

    core = get_core()
    out_dir: Path = core.get("path.out_dir")

    print(f"loading anchor: {args.anchor}")
    anchor = load_cc_jsonl(Path(args.anchor))
    if not anchor.turns:
        print(f"anchor session has no turns; aborting", file=sys.stderr)
        return 1
    print(f"  anchor: {len(anchor.turns)} turns, {anchor.started_at} → {anchor.ended_at}")

    others: list = []
    if args.gpt_export:
        print(f"loading GPT export: {args.gpt_export}")
        gpt_streams = load_gpt_export(Path(args.gpt_export))
        print(f"  GPT: {len(gpt_streams)} conversations")
        others.extend(gpt_streams)
    if args.claude_export:
        print(f"loading Claude.ai export: {args.claude_export}")
        cw_streams = load_claude_web_export(Path(args.claude_export))
        print(f"  Claude.ai: {len(cw_streams)} conversations")
        others.extend(cw_streams)

    window_seconds = args.window_hours * 3600.0
    result = weave(anchor, others, window_seconds=window_seconds)
    print(
        f"woven: {len(result.merged)} turns from "
        f"{len(result.included)} stream(s) within ±{args.window_hours}h"
    )
    for ag, cid in result.included:
        print(f"  · {ag} {cid[:18]}…")

    # Every woven turn defaults to stage=Context (single-chapter archive view).
    # Future iteration: classify with OllamaClient when Ollama is up.
    for pt in result.merged:
        if pt.stage is None:
            pt.stage = Stage.CONTEXT
        if pt.chapter is None:
            pt.chapter = 1
        if pt.chapter_outcome is None:
            pt.chapter_outcome = "Session"

    req = EmbedRequest(
        project=args.project,
        project_number=args.number,
        status=Status(args.status),
        outcome=args.outcome,
        session_id=anchor.conversation_id,
    )
    transcript = embed(req, result.merged)

    # validator (archive lane)
    bus = core.get("diagnostics.bus")
    bus.clear()
    validator = core.get("capability.validator")
    diagnostics = validator(transcript, lane=args.lane)
    errors = [d for d in diagnostics if d.severity == "error"]
    if errors:
        print(f"\nvalidation errors:")
        for d in errors:
            print(f"  {d}")
        return 1

    out_path = (
        Path(args.out)
        if args.out
        else out_dir / "skool" / f"{anchor.conversation_id}.html"
    )
    render_html_to_file(transcript, out_path)
    print(f"\nwrote {out_path}")
    print(
        f"REMINDER: per the never-publish-without-scan rule, run scan-cast.mjs "
        f"+ visual review BEFORE pasting into Skool."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

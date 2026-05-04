# transcript_pipeline.mcp_server
# =============================================================================
# FastMCP server exposing ingest / validate / render as MCP tools.
#
# This is the integration seam for AI editors and orchestrators (Claude
# Desktop, ghostserver-style relays, custom agents). Run it as:
#
#     transcript-mcp                   # stdio transport (default)
#     python -m transcript_pipeline.mcp_server
#
# Tools exposed:
#
#   ingest(raw_log, project, number, status, outcome, session, resumed=false)
#     Parse a raw chat log and write the embedded YAML+body file. Returns
#     the absolute path of the written file.
#
#   validate(embedded_path)
#     Validate an embedded file. Returns a structured diagnostics list:
#     {"errors": [...], "warnings": [...], "ok": bool}.
#
#   render(embedded_path)
#     Render an embedded file. Returns the three artifact paths.
#
# Spine boots once at server start. Each tool is a thin wrapper that uses
# the registered capability — no end-runs around spine. The DiagnosticBus is
# cleared at the start of every validate call so diagnostics reflect only
# that file.
# =============================================================================

from __future__ import annotations

from pathlib import Path

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover
    raise SystemExit(
        "fastmcp not installed; pip install fastmcp"
    )

from transcript_pipeline.runtime import DiagnosticBus, boot, get_core


_app = FastMCP("transcript-pipeline")


@_app.tool()
def ingest(
    raw_log: str,
    project: str,
    number: int,
    status: str,
    outcome: str,
    session: str,
    resumed: bool = False,
) -> dict:
    """Parse a raw multi-agent chat log and write an embedded YAML+body file.

    `raw_log` is the literal log text (not a path). Returns the absolute
    path of the written file."""
    from transcript_pipeline.embedder import EmbedRequest, embed_to_file
    from transcript_pipeline.schema import Status

    core = get_core()
    parse_log = core.get("capability.parser")
    out_dir: Path = core.get("path.out_dir")

    parsed = parse_log(raw_log)
    req = EmbedRequest(
        project=project,
        project_number=number,
        status=Status(status),
        outcome=outcome,
        session_id=session,
        resumed=resumed,
    )
    code = f"{project}-{number:03d}"
    out_path = out_dir / code / "embedded.yml"
    written = embed_to_file(req, parsed, out_path)
    return {"path": str(written), "code": code}


@_app.tool()
def validate(embedded_path: str) -> dict:
    """Validate an embedded file against Transcript Format v1.0. Returns
    a structured result with errors and warnings split out."""
    from transcript_pipeline.embedder import load_embedded

    core = get_core()
    bus: DiagnosticBus = core.get("diagnostics.bus")
    bus.clear()
    validator = core.get("capability.validator")

    transcript = load_embedded(Path(embedded_path).read_text(encoding="utf-8"))
    diagnostics = validator(transcript)

    errors = [
        {"code": d.code, "message": d.message, "location": d.location}
        for d in diagnostics
        if d.severity == "error"
    ]
    warnings = [
        {"code": d.code, "message": d.message, "location": d.location}
        for d in diagnostics
        if d.severity == "warn"
    ]
    return {"ok": not errors, "errors": errors, "warnings": warnings}


@_app.tool()
def render(embedded_path: str) -> dict:
    """Render an embedded file to transcript.txt + chapters.md + bubbles.json."""
    from transcript_pipeline.embedder import load_embedded

    core = get_core()
    renderer = core.get("capability.renderer")
    out_dir: Path = core.get("path.out_dir")

    transcript = load_embedded(Path(embedded_path).read_text(encoding="utf-8"))
    result = renderer(transcript, out_dir)
    return {
        "transcript_path": str(result.transcript_path),
        "chapters_path": str(result.chapters_path),
        "bubbles_path": str(result.bubbles_path),
    }


def main() -> None:  # pragma: no cover
    boot()
    _app.run()


if __name__ == "__main__":  # pragma: no cover
    main()

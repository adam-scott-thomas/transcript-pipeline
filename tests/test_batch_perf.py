"""Acceptance: batch processes 10 files in <30s on local hardware."""

import time
from pathlib import Path

from transcript_pipeline.embedder import EmbedRequest, embed, embed_to_string
from transcript_pipeline.parser import parse_log
from transcript_pipeline.renderer import render_transcript
from transcript_pipeline.schema import Status
from transcript_pipeline.validator import validate_transcript


SAMPLE = Path(__file__).parent.parent / "examples" / "sample_raw.log"


def test_batch_ten_files_under_thirty_seconds(core, tmp_path):
    raw = SAMPLE.read_text(encoding="utf-8")
    parsed = parse_log(raw)

    files = []
    for i in range(10):
        req = EmbedRequest(
            project="GL",
            project_number=i + 1,
            status=Status.SHIPPED,
            outcome="Auth Key Flow",
            session_id=f"2026-05-04-{i:04d}",
        )
        t = embed(req, parse_log(raw))
        path = tmp_path / f"embedded_{i}.yml"
        path.write_text(embed_to_string(t), encoding="utf-8")
        files.append(path)

    out_dir = tmp_path / "rendered"
    t0 = time.perf_counter()
    for f in files:
        from transcript_pipeline.embedder import load_embedded
        transcript = load_embedded(f.read_text(encoding="utf-8"))
        diagnostics = validate_transcript(transcript)
        # warnings are OK; errors would fail the test
        assert not [d for d in diagnostics if d.severity == "error"]
        render_transcript(transcript, out_dir)
    dt = time.perf_counter() - t0

    assert dt < 30.0, f"batch of 10 took {dt:.2f}s — exceeds 30s budget"

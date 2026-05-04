# transcript_pipeline.diagnostics
# =============================================================================
# Append-only logs for the v0.2 classifier.
#
# Two JSONL files under TRANSCRIPT_OUT_DIR:
#
#   classifier-disagreements.jsonl
#     One row per primary/auditor disagreement. Used to iterate the
#     classifier prompt — patterns of disagreement on specific phrasings
#     are exactly what we want to feed back into prompt revisions.
#
#   classifier-cost.jsonl
#     One row per LLM call (provider, model, tokens_in, tokens_out, ts).
#     The CLI summarizes daily totals at the end of an `ingest` run; the
#     spec budget is <$2/day at 80 turns/day × 2 models. Persisting raw
#     rows means we can re-price retroactively when rate cards change.
#
# Both are append-only and survive across runs by design — they're a record,
# not state. There is no rotation here; if they get unwieldy, archive to
# the deep-freeze with the rest of the workstation logs.
# =============================================================================

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from transcript_pipeline.classifier import (
    CostRecord,
    Disagreement,
)
from transcript_pipeline.confirm import Confirmation


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_disagreements(out_dir: Path, rows: list[Disagreement]) -> Path:
    """Append every Disagreement to classifier-disagreements.jsonl. Returns
    the path written for the CLI to surface."""
    p = Path(out_dir) / "classifier-disagreements.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for d in rows:
            obj = asdict(d)
            obj["ts"] = _ts()
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return p


def append_costs(out_dir: Path, rows: list[CostRecord]) -> Path:
    p = Path(out_dir) / "classifier-cost.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = _ts()
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            obj = asdict(r)
            obj["ts"] = ts
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return p


def append_confirmations(out_dir: Path, rows: list["Confirmation"]) -> Path:
    """Confirmation log — what the human accepted, overrode, skipped."""
    p = Path(out_dir) / "classifier-confirmations.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = _ts()
    with p.open("a", encoding="utf-8") as fh:
        for c in rows:
            obj = {
                "ts": ts,
                "turn_index": c.turn_index,
                "decision": c.decision.value,
                "proposal_stage": c.proposal_stage.value,
                "final_stage": c.final_stage.value if c.final_stage else None,
                "confidence": c.confidence,
                "requires_human": c.requires_human,
            }
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return p


# ---------------------------------------------------------------------------
# Cost summary helpers
# ---------------------------------------------------------------------------


# Pricing snapshot in USD per 1M tokens. Conservative estimates — swap with
# the live rate card when invoicing matters. Values picked to surface
# whether we're inside the $2/day target.
_RATES = {
    "anthropic": {"in": 3.0, "out": 15.0},   # claude-sonnet-4-6 base
    "openai": {"in": 2.5, "out": 10.0},      # gpt-5.2 base estimate
}


def estimate_cost_usd(rows: list[CostRecord]) -> float:
    total = 0.0
    for r in rows:
        rates = _RATES.get(r.provider)
        if not rates:
            continue
        total += (r.tokens_in / 1_000_000) * rates["in"]
        total += (r.tokens_out / 1_000_000) * rates["out"]
    return round(total, 4)

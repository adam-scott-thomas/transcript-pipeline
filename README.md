# transcript-pipeline

Convert raw multi-agent chat logs into structured, video-ready transcripts. Built for an
8–10 videos/day production cadence. Output deterministically renders to the **Transcript
Format v1.0** spec.

> Inline-comment READMEs at the top of each `.py` file are the source of truth. This
> top-level README is a navigation aid only.

## Pipeline

```
raw chat log  ──parser──▶  Turn[]  ──embedder──▶  YAML+body file
                                                       │
                                                       ▼
                                                  validator
                                                       │
                                                       ▼
                                                   renderer  ──▶  transcript.txt
                                                                   chapters.md
                                                                   bubbles.json
```

## Install

```bash
pip install -e .[dev]
```

## CLI

```bash
transcript ingest path/to/raw.log              # raw → embedded.yml
transcript validate path/to/embedded.yml        # exit 0 / 1
transcript render   path/to/embedded.yml        # → transcript.txt + chapters.md + bubbles.json
transcript batch    path/to/dir/                # 8-10 files in one pass
```

### v0.2: stage classifier

If your raw log is missing explicit `[STAGE: ...]` tags, the classifier
proposes them via a two-model cross-check (Sonnet drafts, GPT-5 audits)
with confirmation gate:

```bash
# every turn confirmed (high-touch)
transcript ingest raw.log ... --classify interactive

# silently apply when confidence ≥ 0.9 AND models agree; surface the rest
transcript ingest raw.log ... --classify auto --auto-confirm-above 0.9

# offline — deterministic stub, no API keys, ideal for CI tests
transcript ingest raw.log ... --classify mock
```

Diagnostics (under `TRANSCRIPT_OUT_DIR/`):

- `classifier-disagreements.jsonl` — every primary/auditor mismatch, for
  prompt iteration
- `classifier-cost.jsonl` — token usage per call; daily budget target $2
- `classifier-confirmations.jsonl` — what humans accepted/overrode/skipped

## MCP

```bash
transcript-mcp                  # FastMCP server over stdio
```

Exposes `ingest`, `validate`, `render` as tools.

## Architecture

`spine` (maelspine) is the single coordination layer. Every component registers its
capabilities at boot, then operates against the frozen registry. No cross-module imports
of internals — go through spine. See `transcript_pipeline/__init__.py` for the boot
sequence.

`manifest.yml` is the envmanifest contract for runtime env vars.

## Spec

`docs/SPEC.md` — Transcript Format v1.0 (title format, chapter rules, agent roster,
visual rules, status tags, structure).

## License

Apache 2.0.

# transcript_pipeline.adapters
# =============================================================================
# Format-specific readers that convert third-party chat exports into
# ParsedTurn objects the rest of the pipeline understands.
#
# Each adapter exports a `load_*` function returning a SourceStream:
# (conversation_id, list[ParsedTurn]) where every ParsedTurn carries a
# wall-clock timestamp on `pt.timestamp`. Adapters do NOT call the
# classifier or set stages — they emit timestamped, agent-tagged turns
# and let the weaver/classifier decide everything downstream.
#
#   cc_jsonl    — Claude Code session JSONL (~/.claude/projects/.../<id>.jsonl)
#   gpt         — OpenAI conversations export (conversations-NNN.json)
#   claude_web  — Claude.ai data export (conversations.json inside the zip)
# =============================================================================

from transcript_pipeline.adapters.cc_jsonl import load_cc_jsonl
from transcript_pipeline.adapters.gpt import load_gpt_export
from transcript_pipeline.adapters.claude_web import load_claude_web_export

__all__ = ["load_cc_jsonl", "load_gpt_export", "load_claude_web_export"]

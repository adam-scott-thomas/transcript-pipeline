# transcript_pipeline
# =============================================================================
# Public surface: schema, spine boot helper, version.
#
# Why this file is small: the pipeline is composed of independent components
# (parser, validator, embedder, renderer, cli, mcp_server) coordinated through
# a single spine.Core registry. Each component registers its capability at
# boot, then operates against the frozen registry. There is no plugin system,
# no DI container, no abstract factories — just the registry. See spine docs
# in maelspine/spine/core.py for the full contract.
#
# A consumer that just wants to render a transcript should reach for the CLI
# (`transcript render ...`) or the MCP server. A consumer that wants to embed
# the pipeline in another Python program should call `boot()` from this
# module exactly once, then use `core.get(...)` for registered capabilities.
# =============================================================================

from transcript_pipeline.schema import (  # re-export for ergonomics
    Agent,
    Stage,
    Status,
    StatusTag,
    Visual,
    Turn,
    VideoHeader,
    Transcript,
    ALLOWED_PROJECT_CODES,
    AGENT_DEFAULT_VISUAL,
)
from transcript_pipeline.runtime import boot, get_core

__version__ = "0.5.0"
__all__ = [
    "Agent",
    "Stage",
    "Status",
    "StatusTag",
    "Visual",
    "Turn",
    "VideoHeader",
    "Transcript",
    "ALLOWED_PROJECT_CODES",
    "AGENT_DEFAULT_VISUAL",
    "boot",
    "get_core",
    "__version__",
]

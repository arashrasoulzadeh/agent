"""ProjectContext: the private project map seeded into an analysis
session, however it was built.

A pure data shape. Two different producers exist: agent/collector.py's
ContextCollector (a fresh metadata-tool walk, agent/'s own default) and
service/rooms.py's _workspace_context() (workspace/'s cached,
signature-based index, rendered by workspace/serialize.py) — the shape
itself doesn't know or care which one filled it in.
"""

from dataclasses import dataclass


@dataclass
class ProjectContext:
    """The private project map seeded into an analysis session."""

    path: str
    # Format depends on the producer: JSON from ContextCollector's
    # default metadata-tool walk, or workspace/serialize.py's compact
    # text block from _workspace_context() — either way, never full
    # source content.
    raw: str

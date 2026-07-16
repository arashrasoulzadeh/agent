"""ProjectContext: the immutable output of the context-collection stage.

A pure data shape — the behavior that produces one (ContextCollector)
lives in agent/collector.py, not here.
"""

from dataclasses import dataclass


@dataclass
class ProjectContext:
    """Immutable output of the context-collection stage."""

    path: str
    raw: str  # JSON metadata string produced by the metadata tool

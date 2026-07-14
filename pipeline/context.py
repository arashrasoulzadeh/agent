"""Stage 1 — context collection.

A single-responsibility stage that gathers a private structural map of a
project. It produces a ProjectContext that later stages consume; it does
no reasoning and talks to no LLM.
"""

from dataclasses import dataclass

from tools import metadata


@dataclass
class ProjectContext:
    """Immutable output of the context-collection stage."""

    path: str
    raw: str  # JSON metadata string produced by the metadata tool


class ContextCollector:
    """Collect a private, structural map of a project directory."""

    def collect(self, path: str = ".") -> ProjectContext:
        raw = metadata.invoke({"path": path})
        return ProjectContext(path=path, raw=raw)

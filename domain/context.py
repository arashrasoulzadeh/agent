"""Stage 1 — context collection.

A single-responsibility stage that gathers a private structural map of a
project. It produces a ProjectContext that later stages consume; it does
no reasoning and talks to no LLM.

This module never imports `modules/` — the function that actually
produces the metadata blob (in the real app, `modules.metadata.invoke`)
is handed in by whoever constructs a ContextCollector (application/rooms.py),
not hardcoded here. Same reasoning as domain/analyst.py's `tools`
parameter: `domain/` stays reusable without any specific concrete tool.
"""

from collections.abc import Callable
from dataclasses import dataclass

from core.guard import set_project_root


@dataclass
class ProjectContext:
    """Immutable output of the context-collection stage."""

    path: str
    raw: str  # JSON metadata string produced by the metadata tool


class ContextCollector:
    """Collect a private, structural map of a project directory."""

    def __init__(self, metadata_fn: Callable[[str], str]):
        self._metadata_fn = metadata_fn

    def collect(self, path: str = ".") -> ProjectContext:
        # Confine every tool to this folder for the rest of the session.
        root = set_project_root(path)
        raw = self._metadata_fn(str(root))
        return ProjectContext(path=str(root), raw=raw)

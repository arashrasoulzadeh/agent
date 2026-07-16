"""Stage 1 — context collection.

A single-responsibility stage that gathers a private structural map of a
project. It produces a ProjectContext (models/context.py) that later
stages consume; it does no reasoning and talks to no LLM.

This module never imports `tool/` — the function that actually produces
the metadata blob (in the real app, `tool.metadata.invoke`) is handed in
by whoever constructs a ContextCollector (service/rooms.py), not
hardcoded here. Same reasoning as agent/analyst.py's `tools` parameter:
ContextCollector takes its one real-world dependency as a plain
constructor argument, so it stays reusable without depending on any
specific concrete tool.
"""

from collections.abc import Callable

from core.guard import set_project_root
from models.context import ProjectContext


class ContextCollector:
    """Collect a private, structural map of a project directory."""

    def __init__(self, metadata_fn: Callable[[str], str]):
        self._metadata_fn = metadata_fn

    def collect(self, path: str = ".") -> ProjectContext:
        # Confine every tool to this folder for the rest of the session.
        root = set_project_root(path)
        raw = self._metadata_fn(str(root))
        return ProjectContext(path=str(root), raw=raw)

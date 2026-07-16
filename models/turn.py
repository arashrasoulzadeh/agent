"""Turn: the unit of work threaded through the agent's pipeline.

A pure data shape — the Stage/Pipeline machinery that reads and writes it
lives in agent/stage.py, not here.
"""

from dataclasses import dataclass

from models.context import ProjectContext


@dataclass
class Turn:
    """What's known so far about one query. Stages read what they need
    off it and write their result back onto it — the same instance,
    mutated, flows to the next stage."""

    path: str
    query: str
    context: ProjectContext | None = None
    answer: str | None = None
    synthesized: str | None = None

"""ProjectSynthesis: a cached prior analysis of a project, so a room
doesn't have to spend a fresh LLM call every time it's opened.

A pure data shape — the behavior that produces, checks, and invalidates
one (service/rooms.py, workspace/synthesis_repository.py) lives outside
this file.
"""

from dataclasses import dataclass


@dataclass
class ProjectSynthesis:
    """A previously-computed analysis of a project.

    `answer` is the analyst's natural-language bootstrap answer — shown
    to the user on a cache hit exactly as it would be after a fresh
    analysis. `synthesized` is the compact, AI-ready ContextSynthesizer
    output. `file_count` is the tracked-file count in the workspace
    index at the moment this synthesis was made, the baseline a later
    reconcile's change-fraction is measured against.
    """

    answer: str
    synthesized: str
    created_at: str  # ISO 8601
    file_count: int

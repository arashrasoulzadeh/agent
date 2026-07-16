"""SessionManifest: which projects a session has attached, and how.

Pure data shapes — the behavior that builds and maintains one
(SessionManager, ManifestRepository) lives in workspace/, not here.
"""

from dataclasses import dataclass, field


@dataclass
class ProjectAttachment:
    """One project attached to a session."""

    name: str
    root: str  # absolute path on disk
    attached_at: str  # ISO 8601
    ignore_extra: list[str] = field(default_factory=list)


@dataclass
class SessionManifest:
    """A session's identity and its attached projects.

    `projects` is keyed by the short project name (e.g. "p1") rather
    than held as a list, for the same O(1)-by-name reason
    ProjectIndex.files is keyed by path.
    """

    name: str
    created_at: str  # ISO 8601
    projects: dict[str, ProjectAttachment] = field(default_factory=dict)

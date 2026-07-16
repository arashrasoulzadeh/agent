"""ProjectIndex: the full metadata mirror of one attached project.

A pure data shape — the behavior that builds and maintains one
(ProjectIndexer, ProjectWatcher, IndexRepository) lives in workspace/,
not here.
"""

from dataclasses import dataclass, field

from models.file_metadata import FileMetadata


@dataclass
class ProjectIndex:
    """One project's metadata mirror.

    `files` is keyed by relative, forward-slash-normalized path rather
    than held as a list — every incremental update (a watcher event
    naming one changed path) is an O(1) dict operation instead of a scan.
    """

    project_name: str
    project_root: str
    last_sync: str  # ISO 8601
    files: dict[str, FileMetadata] = field(default_factory=dict)

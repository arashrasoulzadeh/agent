"""FileMetadata: one tracked file's metadata, never its content.

A pure data shape — the behavior that produces and maintains these
(ProjectIndexer, ProjectWatcher) lives in workspace/, not here. No
(de)serialization methods: workspace/index_repository.py owns the
dict<->dataclass mapping, the same separation
service/room_repository.py's RoomRepository draws around Room.
"""

from dataclasses import dataclass


@dataclass
class FileMetadata:
    """Metadata for one file inside an attached project.

    `path` is always relative to the project root and forward-slash
    normalized (`.as_posix()`), regardless of host OS, so index.json is
    portable across machines. `sha256` is None for a file whose content
    was never hashed (skipped as binary/oversized/symlink by
    workspace/ignore.py). `derived` is a lazily-filled slot (e.g.
    exported symbols, imports, a short summary) — None until something
    populates it via SessionManager.set_derived(), and reset to None
    whenever `sha256` changes, since derived data describing old content
    must never survive a content change.
    """

    path: str
    size: int
    mtime: float
    sha256: str | None
    language: str | None
    binary: bool
    derived: dict | None = None

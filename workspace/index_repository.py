"""Project index persistence: index.json, written atomically.

Mirrors service/room_repository.py's RoomRepository exactly: write to a
`.tmp` file next to the target, then os.replace() to atomically swap it
into place. index.json is a CACHE, never a ledger — correctness always
comes from the next full reconcile (workspace/indexer.py's reconcile()),
so a crash between the tmp write and the replace leaves only an
old-but-valid file, or an old file plus a harmless orphaned `.tmp`;
nothing here needs to recover from a partial write, because there's no
"partial" state os.replace can ever expose to a reader.

Hand-rolls the dict<->dataclass mapping instead of dataclasses.asdict()/
a generic loader — asdict() deep-copies recursively, real overhead on a
project with tens of thousands of tracked files. No indent: this file is
a regenerable cache, not something a person edits by hand, and skipping
pretty-printing meaningfully shrinks it on a large project.
"""

import json
import os
from pathlib import Path

from models.file_metadata import FileMetadata
from models.project_index import ProjectIndex


class IndexRepository:
    """Reads and writes one project's index.json."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def save(self, index: ProjectIndex) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "project_name": index.project_name,
            "project_root": index.project_root,
            "last_sync": index.last_sync,
            "files": {
                rel: {
                    "size": meta.size,
                    "mtime": meta.mtime,
                    "sha256": meta.sha256,
                    "language": meta.language,
                    "binary": meta.binary,
                    "derived": meta.derived,
                }
                for rel, meta in index.files.items()
            },
        }
        target = self.project_dir / "index.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, target)

    def load(self) -> ProjectIndex | None:
        target = self.project_dir / "index.json"
        if not target.exists():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
        files = {
            rel: FileMetadata(
                path=rel,
                size=entry["size"],
                mtime=entry["mtime"],
                sha256=entry["sha256"],
                language=entry["language"],
                binary=entry["binary"],
                derived=entry.get("derived"),
            )
            for rel, entry in raw["files"].items()
        }
        return ProjectIndex(
            project_name=raw["project_name"],
            project_root=raw["project_root"],
            last_sync=raw["last_sync"],
            files=files,
        )

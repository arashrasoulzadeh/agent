"""Session manifest persistence: manifest.json, written atomically.

Same tmp-then-os.replace pattern as workspace/index_repository.py and
service/room_repository.py's RoomRepository — see index_repository.py's
docstring for why a crash here can never leave a torn file.
"""

import json
import os
from pathlib import Path

from models.session_manifest import ProjectAttachment, SessionManifest


class ManifestRepository:
    """Reads and writes one session's manifest.json."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir

    def save(self, manifest: SessionManifest) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": manifest.name,
            "created_at": manifest.created_at,
            "projects": {
                name: {
                    "name": attachment.name,
                    "root": attachment.root,
                    "attached_at": attachment.attached_at,
                    "ignore_extra": attachment.ignore_extra,
                }
                for name, attachment in manifest.projects.items()
            },
        }
        target = self.session_dir / "manifest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def load(self) -> SessionManifest | None:
        target = self.session_dir / "manifest.json"
        if not target.exists():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
        projects = {
            name: ProjectAttachment(
                name=entry["name"],
                root=entry["root"],
                attached_at=entry["attached_at"],
                ignore_extra=entry.get("ignore_extra", []),
            )
            for name, entry in raw["projects"].items()
        }
        return SessionManifest(
            name=raw["name"],
            created_at=raw["created_at"],
            projects=projects,
        )

"""Room persistence: rooms/{uuid}.json, written atomically.

Extracted out of `service/rooms.py`'s `Room` so that use-case logic
(turn orchestration) and the on-disk format are separate concerns. Room
still builds its own payload dict — that's knowledge of its own state,
not a persistence detail — and just hands it to this repository to
write; loading works the same way in reverse.
"""

import json
import os
from pathlib import Path
from typing import Any


class RoomRepository:
    """Reads and writes room state as one `{room_id}.json` file per room
    under a single directory."""

    def __init__(self, rooms_dir: Path) -> None:
        self.rooms_dir = rooms_dir

    def save(self, room_id: str, payload: dict[str, Any]) -> None:
        self.rooms_dir.mkdir(parents=True, exist_ok=True)
        target = self.rooms_dir / f"{room_id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def load(self, room_id: str) -> dict[str, Any] | None:
        file = self.rooms_dir / f"{room_id}.json"
        if not file.exists():
            return None
        return json.loads(file.read_text(encoding="utf-8"))

    def list_saved(self) -> list[dict[str, Any]]:
        if not self.rooms_dir.exists():
            return []
        rooms = []
        for file in self.rooms_dir.glob("*.json"):
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rooms.append(
                {
                    "id": raw.get("id", file.stem),
                    "path": raw.get("path"),
                    "updated_at": raw.get("updated_at"),
                }
            )
        return sorted(rooms, key=lambda r: r["updated_at"] or "", reverse=True)

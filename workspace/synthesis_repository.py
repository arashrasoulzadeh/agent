"""Project synthesis persistence: synthesis.json, written atomically.

Same tmp-then-os.replace pattern as index_repository.py and
manifest_repository.py — see index_repository.py's docstring for why a
crash here can never leave a torn file. Small and human-inspectable
(the whole point is one project's cached analysis), so this one uses
`indent=2` like manifest_repository.py, unlike index.json's compact form.
"""

import json
import os
from pathlib import Path

from models.project_synthesis import ProjectSynthesis


class SynthesisRepository:
    """Reads and writes one project's synthesis.json."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def save(self, synthesis: ProjectSynthesis) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "answer": synthesis.answer,
            "synthesized": synthesis.synthesized,
            "created_at": synthesis.created_at,
            "file_count": synthesis.file_count,
        }
        target = self.project_dir / "synthesis.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def load(self) -> ProjectSynthesis | None:
        target = self.project_dir / "synthesis.json"
        if not target.exists():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
        return ProjectSynthesis(
            answer=raw["answer"],
            synthesized=raw["synthesized"],
            created_at=raw["created_at"],
            file_count=raw["file_count"],
        )

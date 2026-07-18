"""Tests for workspace/synthesis_repository.py: atomic save/load of one
project's cached ProjectSynthesis.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from models.project_synthesis import ProjectSynthesis
from workspace.synthesis_repository import SynthesisRepository


class TestSynthesisRepository(unittest.TestCase):
    def setUp(self):
        self.project_dir = Path(tempfile.mkdtemp())
        self.repo = SynthesisRepository(self.project_dir)

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_load_before_save_returns_none(self):
        self.assertIsNone(self.repo.load())

    def test_save_then_load_roundtrips(self):
        synthesis = ProjectSynthesis(
            answer="this is a Python CLI agent",
            synthesized="compact synthesized context",
            created_at="2026-01-01T00:00:00+00:00",
            file_count=42,
        )
        self.repo.save(synthesis)
        loaded = self.repo.load()
        self.assertEqual(loaded, synthesis)

    def test_save_creates_missing_project_dir(self):
        nested = self.project_dir / "nested" / "project"
        repo = SynthesisRepository(nested)
        repo.save(
            ProjectSynthesis(answer="a", synthesized="b", created_at="c", file_count=0)
        )
        self.assertTrue((nested / "synthesis.json").exists())

    def test_save_overwrites_previous_synthesis(self):
        self.repo.save(
            ProjectSynthesis(
                answer="old", synthesized="old", created_at="t1", file_count=1
            )
        )
        self.repo.save(
            ProjectSynthesis(
                answer="new", synthesized="new", created_at="t2", file_count=2
            )
        )
        loaded = self.repo.load()
        self.assertEqual(loaded.answer, "new")
        self.assertEqual(loaded.file_count, 2)

    def test_save_leaves_no_leftover_tmp_file(self):
        self.repo.save(
            ProjectSynthesis(answer="a", synthesized="b", created_at="c", file_count=0)
        )
        self.assertFalse((self.project_dir / "synthesis.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()

"""Tests for tool/cat.py — the simplest resolve_in_root()-based tool,
proving the shared confinement pattern every other file tool (ls, edit,
write, delete, create_directory, tree) follows identically.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core import guard
from tool.cat import cat


class TestCatSingleProject(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        (self.tmp_dir / "hello.txt").write_text("hello world")
        guard.set_project_root(self.tmp_dir)

    def tearDown(self):
        guard._project_roots.set(None)
        guard._primary_project.set(None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reads_file_contents(self):
        self.assertEqual(cat.invoke({"path": "hello.txt"}), "hello world")

    def test_outside_root_refused(self):
        result = cat.invoke({"path": "../../etc/passwd"})
        self.assertIn("outside the project folder", result)

    def test_env_file_refused(self):
        result = cat.invoke({"path": ".env"})
        self.assertIn("protected env file", result)

    def test_not_a_file_reports_error(self):
        (self.tmp_dir / "subdir").mkdir()
        result = cat.invoke({"path": "subdir"})
        self.assertIn("is not a file", result)


class TestCatMultiProject(unittest.TestCase):
    def setUp(self):
        self.frontend = Path(tempfile.mkdtemp())
        self.backend = Path(tempfile.mkdtemp())
        (self.frontend / "index.html").write_text("<html></html>")
        (self.backend / "app.py").write_text("x = 1")
        guard.set_project_roots(
            {"frontend": self.frontend, "backend": self.backend}, primary="frontend"
        )

    def tearDown(self):
        guard._project_roots.set(None)
        guard._primary_project.set(None)
        shutil.rmtree(self.frontend, ignore_errors=True)
        shutil.rmtree(self.backend, ignore_errors=True)

    def test_reads_from_named_project(self):
        result = cat.invoke({"path": "app.py", "project": "backend"})
        self.assertEqual(result, "x = 1")

    def test_omitted_project_defaults_to_primary(self):
        result = cat.invoke({"path": "index.html"})
        self.assertEqual(result, "<html></html>")

    def test_unknown_project_refused(self):
        result = cat.invoke({"path": "app.py", "project": "mobile"})
        self.assertIn("not an attached project", result)

    def test_escape_still_refused_within_named_project(self):
        result = cat.invoke({"path": "../../etc/passwd", "project": "backend"})
        self.assertIn("outside the project folder", result)


if __name__ == "__main__":
    unittest.main()

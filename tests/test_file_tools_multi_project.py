"""Lightweight smoke tests for the file tools that share tool/cat.py's
exact confinement pattern with no logic of their own: ls, edit, write,
delete, create_directory, tree. Each just proves project=<non-primary>
resolves against the right root — tool/cat.py's and tool/execute.py's
own test files already cover the full unknown-project/omitted-project/
escape matrix these tools inherit unchanged from core/guard.py.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core import guard
from tool.create_directory import create_directory
from tool.delete import delete
from tool.edit import edit
from tool.ls import ls
from tool.tree import tree
from tool.write import write


class TestFileToolsMultiProject(unittest.TestCase):
    def setUp(self):
        self.frontend = Path(tempfile.mkdtemp())
        self.backend = Path(tempfile.mkdtemp())
        (self.backend / "app.py").write_text("x = 1")
        (self.backend / "old.txt").write_text("bye")
        guard.set_project_roots(
            {"frontend": self.frontend, "backend": self.backend}, primary="frontend"
        )

    def tearDown(self):
        guard._project_roots.set(None)
        guard._primary_project.set(None)
        shutil.rmtree(self.frontend, ignore_errors=True)
        shutil.rmtree(self.backend, ignore_errors=True)

    def test_ls_lists_named_project(self):
        result = ls.invoke({"path": ".", "project": "backend"})
        self.assertIn("app.py", result)
        self.assertIn("old.txt", result)

    def test_ls_omitted_project_is_primary_and_empty(self):
        result = ls.invoke({"path": "."})
        self.assertEqual(result, ". is empty.")

    def test_edit_writes_into_named_project(self):
        result = edit.invoke(
            {"path": "app.py", "content": "x = 2", "project": "backend"}
        )
        self.assertIn("Updated", result)
        self.assertEqual((self.backend / "app.py").read_text(), "x = 2")

    def test_write_creates_in_named_project(self):
        result = write.invoke(
            {"path": "new.py", "content": "y = 3", "project": "backend"}
        )
        self.assertIn("Wrote", result)
        self.assertEqual((self.backend / "new.py").read_text(), "y = 3")
        self.assertFalse((self.frontend / "new.py").exists())

    def test_delete_removes_from_named_project(self):
        result = delete.invoke({"path": "old.txt", "project": "backend"})
        self.assertIn("Deleted", result)
        self.assertFalse((self.backend / "old.txt").exists())

    def test_delete_refuses_named_projects_own_root(self):
        result = delete.invoke({"path": ".", "project": "backend"})
        self.assertIn("refusing to delete the project root", result)

    def test_create_directory_in_named_project(self):
        result = create_directory.invoke({"path": "sub", "project": "backend"})
        self.assertIn("Created directory", result)
        self.assertTrue((self.backend / "sub").is_dir())

    def test_tree_of_named_project(self):
        result = tree.invoke({"path": ".", "project": "backend"})
        self.assertIn("app.py", result)

    def test_unknown_project_refused_uniformly(self):
        for call in (
            lambda: ls.invoke({"path": ".", "project": "mobile"}),
            lambda: edit.invoke({"path": "a", "content": "x", "project": "mobile"}),
            lambda: write.invoke({"path": "a", "content": "x", "project": "mobile"}),
            lambda: delete.invoke({"path": "a", "project": "mobile"}),
            lambda: create_directory.invoke({"path": "a", "project": "mobile"}),
            lambda: tree.invoke({"path": ".", "project": "mobile"}),
        ):
            self.assertIn("not an attached project", call())


if __name__ == "__main__":
    unittest.main()

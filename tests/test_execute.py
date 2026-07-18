import shutil
import tempfile
import unittest
from pathlib import Path

from core import guard
from tool.execute import execute


class TestExecuteTool(unittest.TestCase):
    def test_echo(self):
        result = execute.invoke({"command": "echo hello"})
        self.assertIn("hello", result)

    def test_outside_project_is_refused(self):
        result = execute.invoke({"command": "cat /etc/passwd"})
        self.assertIn("outside the project folder", result)

    def test_env_file_is_refused(self):
        result = execute.invoke({"command": "cat .env"})
        self.assertIn("protected env file", result)


class TestExecuteToolMultiProject(unittest.TestCase):
    """No project= given here still resolves via guard.project_root()'s
    cwd fallback (the class above relies on this too, with nothing
    pinned at all) - these cases specifically pin a multi-project set
    to exercise project=."""

    def setUp(self):
        self.frontend = Path(tempfile.mkdtemp())
        self.backend = Path(tempfile.mkdtemp())
        (self.backend / "app.py").write_text("x = 1\n")
        guard.set_project_roots(
            {"frontend": self.frontend, "backend": self.backend}, primary="frontend"
        )

    def tearDown(self):
        guard._project_roots.set(None)
        guard._primary_project.set(None)
        shutil.rmtree(self.frontend, ignore_errors=True)
        shutil.rmtree(self.backend, ignore_errors=True)

    def test_runs_in_named_project(self):
        result = execute.invoke({"command": "cat app.py", "project": "backend"})
        self.assertIn("x = 1", result)

    def test_omitted_project_runs_in_primary(self):
        # "app.py" only exists in backend - omitted project= runs the
        # command in frontend (the primary) instead, where it doesn't
        # exist, proving it never silently ran in backend.
        result = execute.invoke({"command": "cat app.py"})
        self.assertIn("No such file or directory", result)

    def test_unknown_project_refused(self):
        result = execute.invoke({"command": "echo hi", "project": "mobile"})
        self.assertIn("not an attached project", result)

    def test_escape_still_refused_within_named_project(self):
        result = execute.invoke(
            {"command": "cat ../../etc/passwd", "project": "backend"}
        )
        self.assertIn("outside the project folder", result)


if __name__ == "__main__":
    unittest.main()

"""Tests for tool/describe.py: the on-demand, tier-2 structural-
signature lookup tool. Real temp project dir + real SessionManager/
IndexRepository (pointed at a temp session root, never the real
~/.agent-session-root) + core.guard/core.room_context set up manually —
no server, no LLM.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core import guard, room_context
from tool.describe import describe
from workspace.config import WORKSPACE_PROJECT_NAME
from workspace.manager import SessionManager


class TestDescribe(unittest.TestCase):
    def setUp(self):
        self.session_root = Path(tempfile.mkdtemp())
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "foo.py").write_text(
            '"""Does foo things."""\n\ndef do_foo(x: int) -> str:\n    return str(x)\n'
        )
        (self.project_dir / "README.md").write_text("# hi\n")

        self.other_dir = Path(tempfile.mkdtemp())
        (self.other_dir / "bar.py").write_text(
            '"""Does bar things."""\n\ndef do_bar() -> None:\n    pass\n'
        )

        manager = SessionManager(session_root=self.session_root)
        self.room_id = "test-room"
        manager.create(self.room_id)
        manager.attach(
            self.room_id, self.project_dir, project_name=WORKSPACE_PROJECT_NAME
        )
        manager.attach(self.room_id, self.other_dir, project_name="other")

        # Matches how service/rooms.py's Room really confines a
        # multi-project room — set_project_roots() with the primary
        # named WORKSPACE_PROJECT_NAME, not the single-project
        # set_project_root() convenience wrapper (which pins an
        # internal-only sentinel name unrelated to the workspace's own
        # project-naming convention).
        guard.set_project_roots(
            {WORKSPACE_PROJECT_NAME: self.project_dir, "other": self.other_dir},
            primary=WORKSPACE_PROJECT_NAME,
        )
        room_context.set_current_room(self.room_id)

    def tearDown(self):
        shutil.rmtree(self.session_root, ignore_errors=True)
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.other_dir, ignore_errors=True)
        guard._project_roots.set(None)
        guard._primary_project.set(None)
        room_context.set_current_room(None)

    def _describe(self, path: str, project: str | None = None) -> str:
        # workspace/config.py's SESSION_ROOT is read at call time inside
        # describe() itself, not import time - patch the module
        # attribute exactly the way tests/stubs.py's running_server does
        # for the real server, so this test never touches the real
        # ~/.agent-session-root.
        from workspace import config as workspace_config

        original = workspace_config.SESSION_ROOT
        workspace_config.SESSION_ROOT = self.session_root
        try:
            args = {"path": path}
            if project is not None:
                args["project"] = project
            return describe.invoke(args)
        finally:
            workspace_config.SESSION_ROOT = original

    def test_full_detail_for_file_with_signatures(self):
        result = self._describe("foo.py")
        self.assertIn("foo.py", result)
        self.assertIn("Does foo things.", result)
        self.assertIn("def do_foo(x: int) -> str", result)

    def test_fallback_line_for_file_without_signatures(self):
        result = self._describe("README.md")
        self.assertIn("README.md", result)
        self.assertIn("(no extracted functions, classes, or variables)", result)

    def test_path_outside_project_root_refused(self):
        result = self._describe("../../etc/passwd")
        self.assertIn("outside the project folder", result)

    def test_env_file_refused(self):
        result = self._describe(".env")
        self.assertIn("protected env file", result)

    def test_path_not_in_index_reports_clear_error(self):
        (self.project_dir / "untracked.py").write_text("x = 1\n")
        # Not reconciled into the index (attach() already ran) - describe
        # must not crash, just report it isn't tracked.
        result = self._describe("untracked.py")
        self.assertIn("not tracked in the project index", result)

    def test_no_active_room_reports_clear_error(self):
        room_context.set_current_room(None)
        result = self._describe("foo.py")
        self.assertIn("no active project session", result)

    def test_named_non_primary_project_resolves_correctly(self):
        result = self._describe("bar.py", project="other")
        self.assertIn("bar.py", result)
        self.assertIn("Does bar things.", result)
        self.assertIn("def do_bar()", result)

    def test_unknown_project_name_refused(self):
        result = self._describe("bar.py", project="mobile")
        self.assertIn("not an attached project", result)

    def test_omitted_project_defaults_to_primary_not_other(self):
        # "bar.py" only exists in "other" - omitting project= resolves
        # against the primary project instead (never silently reaching
        # into "other"), where no such file exists.
        result = self._describe("bar.py")
        self.assertIn("is not a file", result)


if __name__ == "__main__":
    unittest.main()

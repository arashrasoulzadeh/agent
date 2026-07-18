"""Tests for core/guard.py: single- and multi-project confinement,
secret-file refusal, and the shell-command escape check.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core import guard


class TestGuardBase(unittest.TestCase):
    def tearDown(self):
        guard._project_roots.set(None)
        guard._primary_project.set(None)


class TestSetProjectRoot(TestGuardBase):
    """The single-project convenience wrapper — agent/collector.py's
    own contract, unchanged by the multi-project redesign."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pins_and_returns_resolved_root(self):
        root = guard.set_project_root(self.tmp_dir)
        self.assertEqual(root, self.tmp_dir.resolve())
        self.assertEqual(guard.project_root(), self.tmp_dir.resolve())

    def test_resolve_in_root_unchanged_behavior(self):
        guard.set_project_root(self.tmp_dir)
        (self.tmp_dir / "a.py").write_text("x")
        resolved = guard.resolve_in_root("a.py")
        self.assertEqual(resolved, self.tmp_dir.resolve() / "a.py")

    def test_resolve_in_root_rejects_escape(self):
        guard.set_project_root(self.tmp_dir)
        self.assertIsNone(guard.resolve_in_root("../../etc/passwd"))

    def test_known_projects_and_primary(self):
        guard.set_project_root(self.tmp_dir)
        self.assertEqual(len(guard.known_projects()), 1)
        self.assertEqual(guard.primary_project(), guard.known_projects()[0])


class TestSetProjectRoots(TestGuardBase):
    def setUp(self):
        self.frontend = Path(tempfile.mkdtemp())
        self.backend = Path(tempfile.mkdtemp())
        (self.frontend / "index.html").write_text("<html></html>")
        (self.backend / "app.py").write_text("x = 1")
        guard.set_project_roots(
            {"frontend": self.frontend, "backend": self.backend}, primary="frontend"
        )

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.frontend, ignore_errors=True)
        shutil.rmtree(self.backend, ignore_errors=True)

    def test_raises_if_primary_not_among_roots(self):
        with self.assertRaises(ValueError):
            guard.set_project_roots({"a": self.frontend}, primary="b")

    def test_known_projects_sorted(self):
        self.assertEqual(guard.known_projects(), ["backend", "frontend"])

    def test_primary_project(self):
        self.assertEqual(guard.primary_project(), "frontend")

    def test_project_root_omitted_uses_primary(self):
        self.assertEqual(guard.project_root(), self.frontend.resolve())

    def test_project_root_named(self):
        self.assertEqual(guard.project_root("backend"), self.backend.resolve())

    def test_project_root_unknown_falls_back_to_primary(self):
        # Defensive fallback only — real callers never reach this because
        # resolve_in_root()/outside_refusal() already refuse first.
        self.assertEqual(guard.project_root("nope"), self.frontend.resolve())

    def test_resolve_in_root_resolves_against_named_project(self):
        resolved = guard.resolve_in_root("app.py", project="backend")
        self.assertEqual(resolved, self.backend.resolve() / "app.py")

    def test_resolve_in_root_omitted_uses_primary(self):
        resolved = guard.resolve_in_root("index.html")
        self.assertEqual(resolved, self.frontend.resolve() / "index.html")

    def test_resolve_in_root_omitting_project_never_reaches_another_root(self):
        # resolve_in_root() is a pure confinement check (no existence
        # check - that's the tool layer's job), so "app.py" resolves
        # under frontend (the primary) when project is omitted, even
        # though that file only actually exists in backend. Confirms it
        # never silently substitutes a different attached project's root.
        resolved = guard.resolve_in_root("app.py")
        self.assertEqual(resolved.parent, self.frontend.resolve())
        self.assertNotEqual(resolved.parent, self.backend.resolve())

    def test_resolve_in_root_unknown_project_returns_none(self):
        self.assertIsNone(guard.resolve_in_root("app.py", project="mobile"))

    def test_outside_refusal_unknown_project_distinct_message(self):
        msg = guard.outside_refusal("app.py", project="mobile")
        self.assertIn("not an attached project", msg)
        self.assertIn("backend", msg)
        self.assertIn("frontend", msg)

    def test_outside_refusal_genuine_escape_message(self):
        msg = guard.outside_refusal("../../etc/passwd", project="backend")
        self.assertIn("outside the project folder", msg)
        self.assertNotIn("not an attached project", msg)

    def test_escapes_root_unknown_project_short_circuits_true(self):
        self.assertTrue(guard.escapes_root("cat app.py", project="mobile"))

    def test_escapes_root_valid_command_in_named_project(self):
        self.assertFalse(guard.escapes_root("cat app.py", project="backend"))

    def test_escapes_root_detects_dotdot(self):
        self.assertTrue(guard.escapes_root("cat ../../etc/passwd", project="backend"))

    def test_escapes_refusal_unknown_vs_escaped(self):
        unknown = guard.escapes_refusal(project="mobile")
        self.assertIn("not an attached project", unknown)
        escaped = guard.escapes_refusal(project="backend")
        self.assertIn("outside the project folder", escaped)


class TestNoRootsPinned(TestGuardBase):
    def test_project_root_falls_back_to_cwd(self):
        self.assertEqual(guard.project_root(), Path.cwd().resolve())

    def test_known_projects_empty(self):
        self.assertEqual(guard.known_projects(), [])

    def test_primary_project_none(self):
        self.assertIsNone(guard.primary_project())


class TestSecretsAndCommandChecks(TestGuardBase):
    """Project-independent checks, confirmed unchanged by the redesign."""

    def test_is_secret_matches_env_files(self):
        self.assertTrue(guard.is_secret(".env"))
        self.assertTrue(guard.is_secret(".env.local"))
        self.assertFalse(guard.is_secret("main.py"))

    def test_refusal_message(self):
        self.assertIn("protected env file", guard.refusal(".env"))

    def test_mentions_secret(self):
        self.assertTrue(guard.mentions_secret("cat .env"))
        self.assertFalse(guard.mentions_secret("cat main.py"))


if __name__ == "__main__":
    unittest.main()

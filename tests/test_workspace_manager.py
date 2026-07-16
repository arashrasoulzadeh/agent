"""Tests for workspace/manager.py: SessionManager's create/attach/detach/
list/status/set_derived, the name-collision/idempotency rules on
attach(), and the reconcile-before-watch sequencing that keeps a fresh
watcher from racing the startup scan.
"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from workspace.index_repository import IndexRepository
from workspace.manager import (
    ProjectNameConflict,
    ProjectNotFound,
    SessionAlreadyExists,
    SessionManager,
    SessionNotFound,
)


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.session_root = Path(tempfile.mkdtemp())
        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "a.py").write_text("hello")
        self.manager = SessionManager(session_root=self.session_root)

    def tearDown(self):
        shutil.rmtree(self.session_root, ignore_errors=True)
        shutil.rmtree(self.project_root, ignore_errors=True)

    def test_create_then_list_sessions(self):
        self.manager.create("s1")
        self.assertEqual(self.manager.list_sessions(), ["s1"])

    def test_create_twice_raises(self):
        self.manager.create("s1")
        with self.assertRaises(SessionAlreadyExists):
            self.manager.create("s1")

    def test_operations_on_missing_session_raise_session_not_found(self):
        with self.assertRaises(SessionNotFound):
            self.manager.list_projects("nope")
        with self.assertRaises(SessionNotFound):
            self.manager.attach("nope", str(self.project_root))
        with self.assertRaises(SessionNotFound):
            self.manager.status("nope")

    def test_attach_builds_index_synchronously(self):
        self.manager.create("s1")
        attachment = self.manager.attach(
            "s1", str(self.project_root), project_name="p1"
        )
        self.assertEqual(attachment.name, "p1")
        status = self.manager.status("s1", project_name="p1")
        self.assertEqual(status["projects"][0]["file_count"], 1)

    def test_attach_default_project_name_is_directory_name(self):
        self.manager.create("s1")
        attachment = self.manager.attach("s1", str(self.project_root))
        self.assertEqual(attachment.name, self.project_root.name)

    def test_attach_name_collision_with_different_path_raises(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")
        other_root = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ProjectNameConflict):
                self.manager.attach("s1", str(other_root), project_name="p1")
        finally:
            shutil.rmtree(other_root, ignore_errors=True)

    def test_reattaching_same_path_is_idempotent_and_updates_ignore(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")
        updated = self.manager.attach(
            "s1", str(self.project_root), project_name="p1", ignore_extra=["*.log"]
        )
        self.assertEqual(updated.ignore_extra, ["*.log"])
        self.assertEqual(len(self.manager.list_projects("s1")), 1)

    def test_detach_removes_project_and_its_index_dir(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")
        self.manager.detach("s1", "p1")
        self.assertEqual(self.manager.list_projects("s1"), [])
        self.assertFalse((self.session_root / "s1" / "p1").exists())

    def test_detach_unknown_project_raises(self):
        self.manager.create("s1")
        with self.assertRaises(ProjectNotFound):
            self.manager.detach("s1", "nope")

    def test_load_reconciles_before_returning_watchers(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")

        # Changed after attach, before load - load()'s reconcile must
        # pick this up before any watcher for it exists.
        (self.project_root / "a.py").write_text("changed content, much longer")

        loaded = self.manager.load("s1")
        try:
            self.assertIn("p1", loaded.watchers)
            meta = loaded.watchers["p1"].index.files["a.py"]
            expected = hashlib.sha256(b"changed content, much longer").hexdigest()
            self.assertEqual(meta.sha256, expected)
        finally:
            loaded.stop_watchers()

    def test_set_derived_writes_when_hash_matches(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")
        index = IndexRepository(self.session_root / "s1" / "p1").load()
        sha = index.files["a.py"].sha256

        ok = self.manager.set_derived("s1", "p1", "a.py", sha, {"summary": "greets"})
        self.assertTrue(ok)

        reloaded = IndexRepository(self.session_root / "s1" / "p1").load()
        self.assertEqual(reloaded.files["a.py"].derived, {"summary": "greets"})

    def test_set_derived_no_op_when_hash_stale(self):
        self.manager.create("s1")
        self.manager.attach("s1", str(self.project_root), project_name="p1")
        ok = self.manager.set_derived(
            "s1", "p1", "a.py", "stale-hash", {"summary": "x"}
        )
        self.assertFalse(ok)
        reloaded = IndexRepository(self.session_root / "s1" / "p1").load()
        self.assertIsNone(reloaded.files["a.py"].derived)


if __name__ == "__main__":
    unittest.main()

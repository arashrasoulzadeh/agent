"""Tests for workspace/indexer.py: build/reconcile/update_paths/
resync_subtree/rename_subtree, the derived-data invalidation invariant,
and the mtime+size fast-path before ever rehashing.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from workspace.ignore import IgnoreRules
from workspace.indexer import ProjectIndexer


class TestProjectIndexer(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.indexer = ProjectIndexer("p1", self.tmp_dir, IgnoreRules(self.tmp_dir))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, rel_path: str, content: str) -> None:
        full = self.tmp_dir / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def test_build_finds_all_tracked_files(self):
        self._write("a.py", "a")
        self._write("sub/b.py", "b")
        index = self.indexer.build()
        self.assertEqual(set(index.files), {"a.py", "sub/b.py"})

    def test_build_computes_sha256_size_and_language(self):
        self._write("a.py", "hello")
        index = self.indexer.build()
        meta = index.files["a.py"]
        self.assertEqual(meta.language, "python")
        self.assertIsNotNone(meta.sha256)
        self.assertEqual(meta.size, 5)
        self.assertIsNone(meta.derived)

    def test_build_auto_populates_signatures_for_python(self):
        self._write("a.py", "def greet(name: str) -> str:\n    return name\n")
        index = self.indexer.build()
        derived = index.files["a.py"].derived
        self.assertIsNotNone(derived)
        self.assertEqual(derived["signatures"]["functions"][0]["name"], "greet")

    def test_build_leaves_derived_none_for_language_without_extractor(self):
        self._write("a.go", "func main() {}\n")
        index = self.indexer.build()
        self.assertIsNone(index.files["a.go"].derived)

    def test_reconcile_recomputes_signatures_on_content_change(self):
        self._write("a.py", "def old_name():\n    pass\n")
        index = self.indexer.build()
        self.assertEqual(
            index.files["a.py"].derived["signatures"]["functions"][0]["name"],
            "old_name",
        )

        self._write("a.py", "def new_name():\n    pass\n")
        index = self.indexer.reconcile(index)
        self.assertEqual(
            index.files["a.py"].derived["signatures"]["functions"][0]["name"],
            "new_name",
        )

    def test_reconcile_preserves_signatures_when_content_unchanged(self):
        self._write("a.py", "def stable_name():\n    pass\n")
        index = self.indexer.build()
        original_derived = index.files["a.py"].derived

        full = self.tmp_dir / "a.py"
        new_mtime = full.stat().st_mtime + 5
        os.utime(full, (new_mtime, new_mtime))

        index = self.indexer.reconcile(index)
        self.assertEqual(index.files["a.py"].derived, original_derived)

    def test_build_respects_ignore_rules(self):
        self._write(".gitignore", "*.log\n")
        self._write("debug.log", "x")
        self._write("main.py", "x")
        # IgnoreRules reads .gitignore once at construction time, so a
        # fresh instance is needed after writing it (self.indexer's was
        # built in setUp, before .gitignore existed).
        indexer = ProjectIndexer("p1", self.tmp_dir, IgnoreRules(self.tmp_dir))
        index = indexer.build()
        self.assertNotIn("debug.log", index.files)
        self.assertIn("main.py", index.files)

    def test_reconcile_adds_new_file(self):
        index = self.indexer.build()
        self._write("new.py", "x")
        index = self.indexer.reconcile(index)
        self.assertIn("new.py", index.files)

    def test_reconcile_removes_deleted_file(self):
        self._write("gone.py", "x")
        index = self.indexer.build()
        (self.tmp_dir / "gone.py").unlink()
        index = self.indexer.reconcile(index)
        self.assertNotIn("gone.py", index.files)

    def test_reconcile_with_no_existing_index_behaves_like_build(self):
        self._write("a.py", "a")
        index = self.indexer.reconcile(None)
        self.assertIn("a.py", index.files)

    def test_reconcile_detects_content_change_and_invalidates_derived(self):
        self._write("a.py", "version 1")
        index = self.indexer.build()
        index.files["a.py"].derived = {"summary": "old summary"}

        self._write("a.py", "version 2 - totally different length")
        index = self.indexer.reconcile(index)
        self.assertIsNone(index.files["a.py"].derived)

    def test_reconcile_preserves_derived_when_content_unchanged(self):
        self._write("a.py", "same content")
        index = self.indexer.build()
        index.files["a.py"].derived = {"summary": "still valid"}

        # Touch: bump mtime without changing a single byte of content.
        full = self.tmp_dir / "a.py"
        new_mtime = full.stat().st_mtime + 5
        os.utime(full, (new_mtime, new_mtime))

        index = self.indexer.reconcile(index)
        self.assertEqual(index.files["a.py"].derived, {"summary": "still valid"})

    def test_reconcile_skips_rehash_when_mtime_and_size_unchanged(self):
        self._write("a.py", "stable")
        index = self.indexer.build()
        original = index.files["a.py"]

        index2 = self.indexer.reconcile(index)
        # The cheap check found nothing changed - same object, not just
        # an equal one, proving no rehash (and no new allocation) happened.
        self.assertIs(index2.files["a.py"], original)

    def test_update_paths_incremental_single_file(self):
        self._write("a.py", "a")
        self._write("b.py", "b")
        index = self.indexer.build()
        self._write("a.py", "a changed")
        self.indexer.update_paths(index, {"a.py"})
        self.assertIn("b.py", index.files)
        self.assertNotEqual(index.files["a.py"].size, 1)

    def test_update_paths_removes_deleted_path(self):
        self._write("a.py", "a")
        index = self.indexer.build()
        (self.tmp_dir / "a.py").unlink()
        self.indexer.update_paths(index, {"a.py"})
        self.assertNotIn("a.py", index.files)

    def test_update_paths_removes_now_ignored_path(self):
        self._write("a.py", "a")
        index = self.indexer.build()
        self._write(".gitignore", "a.py\n")
        # A fresh IgnoreRules picks up the new .gitignore contents.
        indexer = ProjectIndexer("p1", self.tmp_dir, IgnoreRules(self.tmp_dir))
        indexer.update_paths(index, {"a.py"})
        self.assertNotIn("a.py", index.files)

    def test_resync_subtree_prunes_and_readds(self):
        self._write("dir/a.py", "a")
        self._write("dir/b.py", "b")
        index = self.indexer.build()
        (self.tmp_dir / "dir" / "b.py").unlink()
        self._write("dir/c.py", "c")
        self.indexer.resync_subtree(index, "dir")
        self.assertEqual(
            {rel for rel in index.files if rel.startswith("dir/")},
            {"dir/a.py", "dir/c.py"},
        )

    def test_resync_subtree_of_a_now_gone_directory_drops_everything(self):
        self._write("dir/a.py", "a")
        index = self.indexer.build()
        shutil.rmtree(self.tmp_dir / "dir")
        self.indexer.resync_subtree(index, "dir")
        self.assertEqual({rel for rel in index.files if rel.startswith("dir/")}, set())

    def test_rename_subtree_preserves_derived_data(self):
        self._write("old/a.py", "a")
        index = self.indexer.build()
        index.files["old/a.py"].derived = {"summary": "kept"}

        (self.tmp_dir / "old").rename(self.tmp_dir / "new")
        self.indexer.rename_subtree(index, "old", "new")

        self.assertNotIn("old/a.py", index.files)
        self.assertEqual(index.files["new/a.py"].derived, {"summary": "kept"})
        self.assertEqual(index.files["new/a.py"].path, "new/a.py")


if __name__ == "__main__":
    unittest.main()

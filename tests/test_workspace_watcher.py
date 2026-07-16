"""Tests for workspace/watcher.py: a real watchdog Observer against a
temp project root, debounced flush, directory/move handling, and a
graceful stop that flushes a still-pending change.

Uses a synchronous, bounded-timeout `wait_until` (the thread-based
equivalent of tests/test_app.py's async one) since ProjectWatcher runs
on real OS threads, not asyncio.
"""

import hashlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from workspace.ignore import IgnoreRules
from workspace.index_repository import IndexRepository
from workspace.indexer import ProjectIndexer
from workspace.watcher import ProjectWatcher


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met within timeout")
        time.sleep(interval)


class TestProjectWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.project_dir = self.tmp_dir / "project"
        self.project_dir.mkdir()
        self.index_dir = self.tmp_dir / "index"

        self.indexer = ProjectIndexer(
            "p1", self.project_dir, IgnoreRules(self.project_dir)
        )
        self.repo = IndexRepository(self.index_dir)
        self.index = self.indexer.build()
        self.repo.save(self.index)

        # A short debounce keeps these tests fast.
        self.watcher = ProjectWatcher(
            self.indexer, self.index, self.repo, debounce_seconds=0.1
        )
        self.watcher.start()

    def tearDown(self):
        self.watcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, rel_path: str, content: str) -> None:
        full = self.project_dir / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def test_creating_a_file_is_indexed_after_debounce(self):
        self._write("new.py", "hello")
        wait_until(lambda: "new.py" in self.index.files)
        self.assertIsNotNone(self.index.files["new.py"].sha256)

    def test_modifying_a_file_updates_its_hash(self):
        self._write("a.py", "v1")
        wait_until(lambda: "a.py" in self.index.files)
        old_sha = self.index.files["a.py"].sha256

        self._write("a.py", "v2 - a different length of content")
        wait_until(lambda: self.index.files["a.py"].sha256 != old_sha)

    def test_deleting_a_file_removes_it_from_the_index(self):
        self._write("gone.py", "x")
        wait_until(lambda: "gone.py" in self.index.files)

        (self.project_dir / "gone.py").unlink()
        wait_until(lambda: "gone.py" not in self.index.files)

    def test_flush_lands_on_disk_after_debounce_settles(self):
        self._write("saved.py", "x")
        wait_until(lambda: "saved.py" in self.index.files)

        def _on_disk() -> bool:
            reloaded = self.repo.load()
            return reloaded is not None and "saved.py" in reloaded.files

        wait_until(_on_disk)

    def test_rapid_burst_of_edits_settles_on_the_final_content(self):
        for i in range(5):
            self._write("burst.py", f"version {i}")
        expected = hashlib.sha256(b"version 4").hexdigest()
        wait_until(
            lambda: self.index.files.get("burst.py")
            and self.index.files["burst.py"].sha256 == expected
        )

    def test_moving_a_directory_preserves_derived_data(self):
        self._write("dir/a.py", "a")
        wait_until(lambda: "dir/a.py" in self.index.files)
        self.index.files["dir/a.py"].derived = {"summary": "kept"}

        (self.project_dir / "dir").rename(self.project_dir / "dir2")
        wait_until(lambda: "dir2/a.py" in self.index.files)
        self.assertEqual(self.index.files["dir2/a.py"].derived, {"summary": "kept"})
        self.assertNotIn("dir/a.py", self.index.files)

    def test_graceful_stop_flushes_a_still_pending_change(self):
        # Its own isolated project dir/indexer/repo/index, so it doesn't
        # race with setUp's already-running self.watcher on the same
        # directory. A long debounce that would never fire on its own
        # within this test's lifetime proves stop() itself does the
        # final flush, not the timer.
        project_dir = self.tmp_dir / "project2"
        project_dir.mkdir()
        indexer = ProjectIndexer("p2", project_dir, IgnoreRules(project_dir))
        repo = IndexRepository(self.tmp_dir / "index2")
        index = indexer.build()
        repo.save(index)

        watcher = ProjectWatcher(indexer, index, repo, debounce_seconds=10.0)
        watcher.start()
        try:
            (project_dir / "last.py").write_text("x")
            wait_until(lambda: "last.py" in watcher._pending_files)
        finally:
            watcher.stop()

        reloaded = repo.load()
        self.assertIn("last.py", reloaded.files)

    def test_stop_is_safe_to_call_more_than_once(self):
        self.watcher.stop()
        self.watcher.stop()  # must not raise


if __name__ == "__main__":
    unittest.main()

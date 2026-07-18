"""Tests for self_update.py (`agent update` / `agent uninstall`) and its
dispatch from cli.py.

Git operations run for real against disposable temp repos — safe, local,
and the whole point of exercising the actual fast-forward/dirty-tree
logic. `_run_pip` is always replaced with a fake that records calls
instead of running anything — actually invoking pip install/uninstall
against this checkout would be slow at best and could break the
environment running the suite at worst.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import self_update


def _run_git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init", "-q")
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")


def _commit(path: Path, filename: str, content: str, message: str) -> None:
    (path / filename).write_text(content)
    _run_git(path, "add", filename)
    _run_git(path, "commit", "-q", "-m", message)


class TestRunUpdate(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.origin = self.base / "origin"
        self.local = self.base / "local"
        _init_repo(self.origin)
        _commit(self.origin, "a.txt", "1", "initial")
        _run_git(self.base, "clone", "-q", str(self.origin), str(self.local))
        _run_git(self.local, "config", "user.email", "test@example.com")
        _run_git(self.local, "config", "user.name", "Test")

        self._original_repo_root = self_update.REPO_ROOT
        self._original_run_pip = self_update._run_pip
        self_update.REPO_ROOT = self.local
        self.pip_calls: list[tuple] = []
        self_update._run_pip = self._fake_run_pip

    def tearDown(self):
        self_update.REPO_ROOT = self._original_repo_root
        self_update._run_pip = self._original_run_pip
        shutil.rmtree(self.base, ignore_errors=True)

    def _fake_run_pip(self, *args):
        self.pip_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def test_not_a_git_checkout_refuses(self):
        non_git = Path(tempfile.mkdtemp())
        try:
            self_update.REPO_ROOT = non_git
            code = self_update.run_update([])
            self.assertEqual(code, 1)
            self.assertEqual(self.pip_calls, [])
        finally:
            shutil.rmtree(non_git, ignore_errors=True)

    def test_dirty_working_tree_refuses(self):
        (self.local / "a.txt").write_text("dirty")
        code = self_update.run_update([])
        self.assertEqual(code, 1)
        self.assertEqual(self.pip_calls, [])

    def test_dry_run_reports_up_to_date(self):
        code = self_update.run_update(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pip_calls, [])

    def test_dry_run_reports_commits_behind(self):
        _commit(self.origin, "b.txt", "2", "second")
        code = self_update.run_update(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pip_calls, [])
        # dry-run must not have pulled anything.
        self.assertFalse((self.local / "b.txt").exists())

    def test_dry_run_without_upstream_reports_clearly(self):
        solo = Path(tempfile.mkdtemp())
        _init_repo(solo)
        _commit(solo, "a.txt", "1", "initial")
        self_update.REPO_ROOT = solo
        try:
            code = self_update.run_update(["--dry-run"])
            self.assertEqual(code, 1)
        finally:
            shutil.rmtree(solo, ignore_errors=True)

    def test_update_pulls_and_reinstalls(self):
        _commit(self.origin, "b.txt", "2", "second")
        code = self_update.run_update([])
        self.assertEqual(code, 0)
        self.assertTrue((self.local / "b.txt").exists())
        self.assertEqual(len(self.pip_calls), 1)
        self.assertEqual(self.pip_calls[0][:2], ("install", "-e"))

    def test_update_already_up_to_date_skips_reinstall(self):
        code = self_update.run_update([])
        self.assertEqual(code, 0)
        self.assertEqual(self.pip_calls, [])

    def test_reinstall_failure_is_reported(self):
        _commit(self.origin, "b.txt", "2", "second")

        def failing_pip(*args):
            self.pip_calls.append(args)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

        self_update._run_pip = failing_pip
        code = self_update.run_update([])
        self.assertEqual(code, 1)
        # The pull itself still happened — only reinstall failed.
        self.assertTrue((self.local / "b.txt").exists())


class TestRunUninstall(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(tempfile.mkdtemp())
        self.session_root = Path(tempfile.mkdtemp())

        self._original_repo_root = self_update.REPO_ROOT
        self._original_run_pip = self_update._run_pip
        self._original_session_root = self_update._session_root
        self._had_input_override = "input" in self_update.__dict__

        self_update.REPO_ROOT = self.repo_root
        self_update._session_root = lambda: self.session_root
        self.pip_calls: list[tuple] = []
        self_update._run_pip = self._fake_run_pip

    def tearDown(self):
        self_update.REPO_ROOT = self._original_repo_root
        self_update._run_pip = self._original_run_pip
        self_update._session_root = self._original_session_root
        if not self._had_input_override:
            self_update.__dict__.pop("input", None)
        shutil.rmtree(self.repo_root, ignore_errors=True)
        shutil.rmtree(self.session_root, ignore_errors=True)

    def _fake_run_pip(self, *args):
        self.pip_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def test_dry_run_does_not_call_pip_or_prompt(self):
        # No input() override at all — if this reached the confirmation
        # prompt it would hang waiting on stdin, so a passing test here
        # already proves dry-run returns before that point.
        code = self_update.run_uninstall(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pip_calls, [])

    def test_yes_flag_skips_prompt_and_uninstalls(self):
        code = self_update.run_uninstall(["--yes"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pip_calls, [("uninstall", "-y", "agent")])

    def test_default_keeps_local_data(self):
        rooms_dir = self.repo_root / "rooms"
        rooms_dir.mkdir()
        (rooms_dir / "x.json").write_text("{}")
        (self.session_root / "marker").write_text("x")

        code = self_update.run_uninstall(["--yes"])
        self.assertEqual(code, 0)
        self.assertTrue(rooms_dir.exists())
        self.assertTrue(self.session_root.exists())

    def test_confirmation_declined_aborts(self):
        self_update.input = lambda *a: "n"
        code = self_update.run_uninstall([])
        self.assertEqual(code, 1)
        self.assertEqual(self.pip_calls, [])

    def test_confirmation_accepted_proceeds(self):
        self_update.input = lambda *a: "y"
        code = self_update.run_uninstall([])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pip_calls), 1)

    def test_purge_removes_local_data(self):
        rooms_dir = self.repo_root / "rooms"
        rooms_dir.mkdir()
        (rooms_dir / "x.json").write_text("{}")
        (self.session_root / "marker").write_text("x")

        code = self_update.run_uninstall(["--yes", "--purge"])
        self.assertEqual(code, 0)
        self.assertFalse(rooms_dir.exists())
        self.assertFalse(self.session_root.exists())

    def test_purge_is_a_noop_when_nothing_exists(self):
        shutil.rmtree(self.session_root)
        code = self_update.run_uninstall(["--yes", "--purge"])
        self.assertEqual(code, 0)

    def test_pip_failure_skips_purge(self):
        rooms_dir = self.repo_root / "rooms"
        rooms_dir.mkdir()

        def failing_pip(*args):
            self.pip_calls.append(args)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

        self_update._run_pip = failing_pip
        code = self_update.run_uninstall(["--yes", "--purge"])
        self.assertEqual(code, 1)
        self.assertTrue(rooms_dir.exists())


class TestCliDispatch(unittest.TestCase):
    """cli.py's main() must route 'update'/'uninstall' to self_update
    before its normal path/--room argparse flow ever runs."""

    def test_update_dispatches_to_self_update(self):
        import cli

        original = self_update.run_update
        calls = []
        self_update.run_update = lambda argv: (calls.append(argv), 0)[1]
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["update", "--dry-run"])
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual(calls, [["--dry-run"]])
        finally:
            self_update.run_update = original

    def test_uninstall_dispatches_to_self_update(self):
        import cli

        original = self_update.run_uninstall
        calls = []
        self_update.run_uninstall = lambda argv: (calls.append(argv), 1)[1]
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["uninstall", "--yes"])
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(calls, [["--yes"]])
        finally:
            self_update.run_uninstall = original

    def test_plain_path_argument_is_not_treated_as_a_command(self):
        # Sanity check that dispatch only fires on an exact first-token
        # match — this doesn't run the TUI (would need a live server),
        # just confirms argparse, not the update/uninstall branch, is
        # what would handle it (SystemExit only comes from the missing
        # server discovery check, not from an update/uninstall path).
        original = self_update.run_update
        called = []
        self_update.run_update = lambda argv: called.append(argv) or 0
        try:
            import cli

            # A real path avoids cli.py's interactive input() prompt for
            # a missing one; port 1 fails fast (discovery's open_timeout=1
            # plus an immediate connection-refused), so this never blocks
            # on network or stdin.
            with self.assertRaises(SystemExit):
                cli.main([".", "--host", "127.0.0.1", "--port", "1"])
            self.assertEqual(called, [])
        finally:
            self_update.run_update = original


if __name__ == "__main__":
    unittest.main()

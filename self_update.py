"""`agent update` / `agent uninstall` — self-management for a git-checkout,
editable-install (`pip install -e .`) deployment of this project.

There's no packaged release/installer for this project today — "update"
really means "git pull, then reinstall so any new dependency is picked
up," and "uninstall" really means "unregister the agent/agent-server/
agent-session console scripts," not "delete a downloaded binary." Both
commands refuse to guess past that: a dirty working tree or a
non-fast-forward divergence stops `update` rather than risk overwriting
local changes (`git pull --ff-only`, never a merge or a reset); local
session data (`rooms/`, the workspace session cache) survives
`uninstall` unless `--purge` is explicitly given.

Deliberately importless of `ui`/`wire`/`langchain` — cli.py's normal
path already pays for those imports, but there's no reason this file
should need them, and keeping it light makes it independently testable
without dragging in the whole app.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# This file lives at the repo root, so its own location IS the repo root
# for a `pip install -e .` checkout. Tests monkeypatch this to a
# disposable temp git repo, the same seam convention tests/stubs.py uses
# for rooms.ROOMS_DIR / workspace_config.SESSION_ROOT.
REPO_ROOT = Path(__file__).resolve().parent

PACKAGE_NAME = "agent"


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _run_pip(*args: str) -> subprocess.CompletedProcess:
    """Runs pip via `sys.executable -m pip`, targeting whichever
    Python/venv is currently running this command rather than whatever
    `pip` happens to resolve to on PATH.

    Tests monkeypatch this function directly instead of letting a real
    pip install/uninstall run — actually uninstalling this package
    mid-test-suite would take the test run down with it.
    """
    return subprocess.run(
        [sys.executable, "-m", "pip", *args], capture_output=True, text=True
    )


def _is_git_checkout(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _working_tree_clean(repo_root: Path) -> bool:
    status = _git(repo_root, "status", "--porcelain")
    return status.returncode == 0 and not status.stdout.strip()


def _session_root() -> Path:
    # Lazy import: the only place this module reaches into the rest of
    # the app, and only when uninstall's --purge path actually needs it.
    from workspace.config import SESSION_ROOT

    return SESSION_ROOT


def run_update(argv: list[str]) -> int:
    """`agent update`: fast-forward pull, then reinstall. Returns a
    process exit code (0 success, 1 failure) rather than raising, so
    cli.py can pass it straight to sys.exit()."""
    parser = argparse.ArgumentParser(
        prog="agent update",
        description="Pull the latest changes from git and reinstall.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report whether an update is available without changing anything.",
    )
    args = parser.parse_args(argv)

    repo_root = REPO_ROOT
    if not _is_git_checkout(repo_root):
        print(
            f"Error: {repo_root} is not a git checkout — "
            "re-clone or reinstall manually to update.",
            file=sys.stderr,
        )
        return 1
    if not _working_tree_clean(repo_root):
        print(
            "Error: uncommitted changes in the repo — "
            "commit or stash them before updating.",
            file=sys.stderr,
        )
        return 1

    before = _git(repo_root, "rev-parse", "--short", "HEAD").stdout.strip()

    if args.dry_run:
        fetch = _git(repo_root, "fetch", "--quiet")
        if fetch.returncode != 0:
            print(f"Error: git fetch failed:\n{fetch.stderr}", file=sys.stderr)
            return 1
        behind = _git(repo_root, "rev-list", "--count", "HEAD..@{u}")
        if behind.returncode != 0:
            print(
                "No upstream tracking branch configured — cannot check for updates.",
                file=sys.stderr,
            )
            return 1
        count = behind.stdout.strip()
        if count == "0":
            print(f"Already up to date ({before}).")
        else:
            plural = "s" if count != "1" else ""
            print(
                f"{count} commit{plural} behind. "
                "Run `agent update` to pull and reinstall."
            )
        return 0

    pull = _git(repo_root, "pull", "--ff-only")
    sys.stdout.write(pull.stdout)
    if pull.returncode != 0:
        sys.stderr.write(pull.stderr)
        return 1

    after = _git(repo_root, "rev-parse", "--short", "HEAD").stdout.strip()
    if before == after:
        print(f"Already up to date ({before}).")
        return 0

    print(f"Updated {before} -> {after}. Reinstalling...")
    install = _run_pip("install", "-e", str(repo_root))
    sys.stdout.write(install.stdout)
    if install.returncode != 0:
        sys.stderr.write(install.stderr)
        return 1
    print("Done.")
    return 0


def run_uninstall(argv: list[str]) -> int:
    """`agent uninstall`: unregisters the package (`pip uninstall`).
    Local session data (rooms/, the workspace session cache) is kept by
    default — pass --purge to remove that too. Confirms interactively
    unless --yes is given."""
    parser = argparse.ArgumentParser(
        prog="agent uninstall",
        description="Uninstall the agent package.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without doing it.",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Also delete local session data (rooms/ and the workspace "
        "session cache). Kept by default.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Don't prompt for confirmation."
    )
    args = parser.parse_args(argv)

    rooms_dir = REPO_ROOT / "rooms"
    session_root = _session_root()
    purge_targets = [t for t in (rooms_dir, session_root) if t.exists()]

    print(f"This will uninstall the {PACKAGE_NAME!r} package (pip uninstall).")
    if args.purge:
        for target in purge_targets:
            print(f"  - also delete {target}")
    else:
        print(
            f"  (local data in {rooms_dir} and {session_root} is kept "
            "— pass --purge to remove it too)"
        )

    if args.dry_run:
        print("Dry run: nothing was changed.")
        return 0

    if not args.yes:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    result = _run_pip("uninstall", "-y", PACKAGE_NAME)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    if args.purge:
        for target in purge_targets:
            shutil.rmtree(target)
            print(f"Removed {target}.")

    print("Uninstalled.")
    return 0

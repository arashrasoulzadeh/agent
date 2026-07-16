"""What the indexer and watcher skip, and why.

Four independent reasons a path never enters a project's metadata index:

  1. It matches `.gitignore` or the session's configurable extra ignore
     list (both parsed as gitignore-style patterns via `pathspec`).
  2. It's a symlink (file or directory) — never followed, so a symlink
     pointing at an ancestor can't cause an infinite walk, and a dangling
     or out-of-root symlink can't leak metadata about something outside
     the project.
  3. It's larger than `max_file_size` (default 5MB) or has a known
     binary extension — "detected language" and content hashing aren't
     useful for either, and hashing a large binary on every reconcile is
     wasted work.
  4. It's an env file (`.env`/`.env.*`) — this one is hardcoded via
     `core.guard.is_secret()` and is NOT subject to `.gitignore` or the
     extra ignore list: a project without `.env` in its own `.gitignore`
     must still never have its secrets end up in metadata that gets
     serialized into an LLM prompt (see workspace/serialize.py).

Binary detection is extension-based only, not a content sniff — the
indexer walks every file in a project on every full reconcile, and
opening each one just to peek at its bytes would make that scan far more
expensive for a marginal accuracy gain. An unrecognized extension is
assumed to be text.
"""

from pathlib import Path

import pathspec

from core.guard import is_secret

BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".class", ".jar",
        ".pyc", ".pyo", ".whl",
        ".mp3", ".mp4", ".wav", ".flac", ".mov", ".avi", ".mkv",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".db", ".sqlite", ".sqlite3",
    }
)  # fmt: skip

DEFAULT_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def is_binary_extension(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_EXTENSIONS


class IgnoreRules:
    """What to skip when walking one project root."""

    def __init__(
        self,
        project_root: Path,
        extra_patterns: list[str] | None = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        # Resolved so this always agrees with ProjectIndexer's own
        # resolved project_root (see its docstring note) — both classes
        # are normally constructed with the same project_root.
        self.project_root = Path(project_root).resolve()
        self.max_file_size = max_file_size

        lines: list[str] = []
        gitignore = self.project_root / ".gitignore"
        if gitignore.is_file():
            text = gitignore.read_text(encoding="utf-8", errors="replace")
            lines.extend(text.splitlines())
        if extra_patterns:
            lines.extend(extra_patterns)
        self._spec = pathspec.PathSpec.from_lines("gitignore", lines)

    def should_skip_dir(self, rel_dir_path: str) -> bool:
        """Whether to prune this directory (and everything under it)
        from the walk entirely. `rel_dir_path` is relative to the
        project root, forward-slash normalized, no leading/trailing
        slash (e.g. "node_modules", "src/generated")."""
        if (self.project_root / rel_dir_path).is_symlink():
            return True
        return self._spec.match_file(rel_dir_path + "/")

    def should_skip(self, rel_path: str, size: int) -> bool:
        """Whether to exclude this file from the index entirely.
        `rel_path` is relative to the project root, forward-slash
        normalized."""
        full_path = self.project_root / rel_path
        if full_path.is_symlink():
            return True
        if is_secret(rel_path):
            return True
        if self._spec.match_file(rel_path):
            return True
        if size > self.max_file_size:
            return True
        return is_binary_extension(rel_path)

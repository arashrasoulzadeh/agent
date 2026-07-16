"""Builds and reconciles one project's metadata index.

`ProjectIndexer(project_name, project_root, ignore_rules)` takes the
root as a plain constructor argument — unlike core.guard's single
contextvar-based root (one root per request-context), a session may have
several `ProjectIndexer`s alive at once, one per attached project.

Three entry points:
  - `build()` — a full walk producing a fresh `ProjectIndex` from
    scratch. Used the first time a project is attached.
  - `reconcile(existing_index)` — a full walk compared against a
    previously stored index: the "catch up on everything that happened
    while nobody was watching" scan, run synchronously once whenever a
    session is loaded, before that project's watcher starts. This
    sequencing (not locking) is what keeps a fresh watcher from racing a
    concurrent startup scan.
  - `update_paths(index, changed_rel_paths)` — incremental, watcher-driven
    update of specific paths only, no full walk.

All three share one invariant: a file whose sha256 changes gets its
`derived` slot recomputed from scratch — structural signatures
(workspace/signatures.py: function/class/variable declarations and a
one-line docstring summary, never full source) for a language with a
registered extractor, else `None` — since stale derived data describing
old content must never survive a real content change. A file whose
mtime/size changed but sha256 is identical (e.g. `touch`, or an editor
rewriting identical bytes) keeps its existing derived data untouched,
including anything set via SessionManager.set_derived(). And all three
use a cheap mtime+size pre-check before ever recomputing a sha256 — a
file is only rehashed (and only then re-extracted) when the cheap check
says it might have actually changed.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from models.file_metadata import FileMetadata
from models.project_index import ProjectIndex
from workspace.ignore import IgnoreRules, is_binary_extension
from workspace.signatures import EXTRACTORS, extract_signatures

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".txt": "text",
}


def detect_language(rel_path: str) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(Path(rel_path).suffix.lower())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _synthesize_summary(signatures: dict) -> str | None:
    """A free, deterministic fallback description for a file with no
    module docstring — e.g. "2 functions, 1 class" — from declaration
    counts alone, empty categories omitted. None if there's nothing to
    describe (only called when there's no module docstring either, so a
    None here means the file gets no description at all).
    """
    counts = [
        (len(signatures.get("functions", [])), "function", "functions"),
        (len(signatures.get("classes", [])), "class", "classes"),
        (len(signatures.get("variables", [])), "variable", "variables"),
    ]
    parts = [
        f"{n} {singular if n == 1 else plural}" for n, singular, plural in counts if n
    ]
    return ", ".join(parts) if parts else None


def _extract_derived(full: Path, language: str | None) -> dict | None:
    """Structural signatures (function/class/variable declarations, a
    one-line docstring summary) for a file whose language has a
    registered extractor (workspace/signatures.py) — never full source.

    `derived["summary"]` is populated automatically here — preferring
    the file's own module docstring (workspace/signatures.py's
    `module_summary`), falling back to `_synthesize_summary()` when
    there's no docstring but there are declarations. This is the same
    slot workspace/serialize.py already reads and the same slot
    SessionManager.set_derived() can still overwrite manually (e.g. an
    LLM-generated summary) — that write path is unaffected, since a
    real content change re-runs this function and recomputes fresh,
    same as it always has for `signatures` itself.

    Skips the read entirely for a language with no extractor, so this
    costs nothing for the large majority of a typical project's files.
    Never raises: a read failure or an unparseable file just means no
    signatures, not a broken index.
    """
    if language not in EXTRACTORS:
        return None
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    signatures = extract_signatures(language, text)
    if not signatures:
        return None
    module_summary = signatures.pop("module_summary", None)
    summary = module_summary or _synthesize_summary(signatures)
    derived: dict = {"signatures": signatures}
    if summary:
        derived["summary"] = summary
    return derived


class ProjectIndexer:
    """Walks and re-walks one project root, producing/updating its
    ProjectIndex."""

    def __init__(
        self, project_name: str, project_root: Path, ignore_rules: IgnoreRules
    ) -> None:
        self.project_name = project_name
        # Resolved once, here, rather than trusting every caller to do it:
        # watchdog always reports fully-resolved event paths (symlinks in
        # the OS temp dir included — e.g. macOS's /var/folders/... is
        # itself a symlink to /private/var/folders/...), so an unresolved
        # project_root would make workspace/watcher.py's
        # Path.relative_to(project_root) silently fail on every event.
        self.project_root = Path(project_root).resolve()
        self.ignore_rules = ignore_rules

    def _walk_files(self, start_rel: str = ""):
        """Yield (rel_path, absolute Path, stat result) for every
        non-skipped file under `start_rel` (default: the whole project),
        pruning ignored/symlinked directories in place as we go."""
        root = self.project_root
        start = root / start_rel if start_rel else root
        if not start.is_dir() or start.is_symlink():
            return

        for dirpath, dirnames, filenames in os.walk(
            start, topdown=True, followlinks=False
        ):
            dir_path = Path(dirpath)
            dir_rel = dir_path.relative_to(root).as_posix()
            if dir_rel == ".":
                dir_rel = ""

            kept = []
            for d in dirnames:
                rel = f"{dir_rel}/{d}" if dir_rel else d
                if not self.ignore_rules.should_skip_dir(rel):
                    kept.append(d)
            dirnames[:] = kept

            for name in filenames:
                rel = f"{dir_rel}/{name}" if dir_rel else name
                full = dir_path / name
                try:
                    st = full.stat()
                except OSError:
                    continue  # vanished mid-walk, or a broken symlink
                if self.ignore_rules.should_skip(rel, st.st_size):
                    continue
                yield rel, full, st

    def _fresh_entry(self, rel: str, full: Path, st: os.stat_result) -> FileMetadata:
        language = detect_language(rel)
        return FileMetadata(
            path=rel,
            size=st.st_size,
            mtime=st.st_mtime,
            sha256=_hash_file(full),
            language=language,
            binary=is_binary_extension(rel),
            derived=_extract_derived(full, language),
        )

    def _reconciled_entry(
        self, rel: str, full: Path, st: os.stat_result, old: FileMetadata | None
    ) -> FileMetadata:
        """One file's entry, given its previous entry (if any) — the
        shared mtime/size-then-sha256 logic build()/reconcile()/
        update_paths() all rely on."""
        if old is not None and old.size == st.st_size and old.mtime == st.st_mtime:
            return old  # unchanged by the cheap check; don't touch the bytes

        sha256 = _hash_file(full)
        if old is not None and old.sha256 == sha256:
            # Same content (e.g. touch) — refresh the cheap fields, keep
            # everything derived from content, including `derived` itself.
            return FileMetadata(
                path=rel,
                size=st.st_size,
                mtime=st.st_mtime,
                sha256=sha256,
                language=old.language,
                binary=old.binary,
                derived=old.derived,
            )
        # New file, or content actually changed — stale derived data
        # must not survive a content change, but signatures are cheap
        # and deterministic, so they're simply recomputed fresh here
        # rather than left blank (a manually set summary from
        # SessionManager.set_derived() does NOT survive this, correctly
        # — it described the old content).
        language = detect_language(rel)
        return FileMetadata(
            path=rel,
            size=st.st_size,
            mtime=st.st_mtime,
            sha256=sha256,
            language=language,
            binary=is_binary_extension(rel),
            derived=_extract_derived(full, language),
        )

    def build(self) -> ProjectIndex:
        """A full walk, from scratch — no previous index to compare against."""
        files = {
            rel: self._fresh_entry(rel, full, st)
            for rel, full, st in self._walk_files()
        }
        return ProjectIndex(
            project_name=self.project_name,
            project_root=str(self.project_root),
            last_sync=_now_iso(),
            files=files,
        )

    def reconcile(self, existing_index: ProjectIndex | None) -> ProjectIndex:
        """A full walk, reusing whatever's still valid from `existing_index`."""
        old_files = existing_index.files if existing_index is not None else {}
        new_files = {}
        for rel, full, st in self._walk_files():
            new_files[rel] = self._reconciled_entry(rel, full, st, old_files.get(rel))
        return ProjectIndex(
            project_name=self.project_name,
            project_root=str(self.project_root),
            last_sync=_now_iso(),
            files=new_files,
        )

    def update_paths(self, index: ProjectIndex, changed_rel_paths) -> ProjectIndex:
        """Incrementally update just the named paths, in place, for a
        debounced batch of watcher events — no full walk. A path that no
        longer exists (or is now ignored) is removed from the index."""
        for rel in changed_rel_paths:
            full = self.project_root / rel
            try:
                st = full.stat()
            except OSError:
                index.files.pop(rel, None)
                continue
            if full.is_symlink() or self.ignore_rules.should_skip(rel, st.st_size):
                index.files.pop(rel, None)
                continue
            old = index.files.get(rel)
            index.files[rel] = self._reconciled_entry(rel, full, st, old)

        index.last_sync = _now_iso()
        return index

    def resync_subtree(self, index: ProjectIndex, rel_dir_path: str) -> ProjectIndex:
        """Re-walk one subtree (a directory created or deleted) rather
        than the whole project. Drops every existing entry under
        `rel_dir_path`, then re-adds whatever's actually there now
        (nothing, if the directory is gone)."""
        prefix = rel_dir_path.rstrip("/") + "/"
        for rel in list(index.files):
            if rel == rel_dir_path or rel.startswith(prefix):
                del index.files[rel]

        for rel, full, st in self._walk_files(start_rel=rel_dir_path):
            index.files[rel] = self._fresh_entry(rel, full, st)

        index.last_sync = _now_iso()
        return index

    def rename_subtree(
        self, index: ProjectIndex, old_rel_dir: str, new_rel_dir: str
    ) -> ProjectIndex:
        """A directory was moved/renamed — carry every entry under it
        (and its own entry, if present) over to the new prefix, keeping
        each FileMetadata's content-derived fields (including `derived`)
        intact, since the bytes didn't change."""
        old_prefix = old_rel_dir.rstrip("/") + "/"
        new_prefix = new_rel_dir.rstrip("/") + "/"
        for rel in list(index.files):
            if rel == old_rel_dir:
                new_rel = new_rel_dir
            elif rel.startswith(old_prefix):
                new_rel = new_prefix + rel[len(old_prefix) :]
            else:
                continue
            meta = index.files.pop(rel)
            meta.path = new_rel
            index.files[new_rel] = meta

        index.last_sync = _now_iso()
        return index

"""Keeps one project's ProjectIndex in sync with real filesystem changes.

`ProjectWatcher` wraps a `watchdog` Observer and debounces bursts of
events (trailing-edge: each new event during the window resets the
timer) before applying them and flushing to disk — a burst of edits
costs one flush, not one per event.

Assumes `index` is already a valid, fully-reconciled starting point:
`SessionManager.attach()`/`load()` run a full synchronous reconcile
*before* constructing/starting a watcher for a project (see
workspace/manager.py) — that sequencing, not locking, is what keeps a
fresh watcher from racing a concurrent startup scan. This class never
does its own initial reconcile.

Two separate locks, deliberately not one: `_lock` guards only the
pending-paths/pending-dir-ops sets and the debounce timer handle (cheap,
never held during I/O) — the timer reset itself (cancel-old +
start-new) happens under this lock, since doing it unlocked risks two
live timers racing. `_flush_lock` serializes the actual apply-and-save
work, since a timer firing and an explicit `stop()` can both reach
`_settle()` at nearly the same moment (`Timer.cancel()` only prevents a
*future* fire; it cannot un-fire a callback already running) — without
this second lock, two concurrent `IndexRepository.save()` calls could
step on each other's `.tmp` file.

index.json is a CACHE, never a ledger (see index_repository.py) — this
watcher is a liveness/performance optimization for a session that stays
loaded. Killing the process at any point leaves, at worst, an old-but-
valid index.json plus a harmless orphaned `.tmp`; the next `load()`'s
reconcile is what actually re-establishes correctness.
"""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from models.project_index import ProjectIndex
from workspace.index_repository import IndexRepository
from workspace.indexer import ProjectIndexer

logger = logging.getLogger("workspace.watcher")

DEFAULT_DEBOUNCE_SECONDS = 0.75


class ProjectWatcher:
    """Watches one project root, debounces bursts of changes, and keeps
    its ProjectIndex (in memory and on disk) up to date."""

    def __init__(
        self,
        indexer: ProjectIndexer,
        index: ProjectIndex,
        repository: IndexRepository,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self.indexer = indexer
        self.index = index
        self.repository = repository
        self.debounce_seconds = debounce_seconds

        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending_files: set[str] = set()
        # ("resync", rel_dir, None) or ("rename", old_rel_dir, new_rel_dir)
        self._pending_dir_ops: list[tuple[str, str, str | None]] = []
        self._timer: threading.Timer | None = None
        self._observer = Observer()
        self._handler = _Handler(self)
        self._started = False

    def start(self) -> None:
        self._observer.schedule(
            self._handler, str(self.indexer.project_root), recursive=True
        )
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            has_pending = bool(self._pending_files or self._pending_dir_ops)
        if has_pending:
            # A graceful stop must never silently drop the last debounce
            # window's changes.
            self._settle()
        if self._started:
            self._observer.stop()
            self._observer.join()
            self._started = False

    # ---- event intake (called from watchdog's dispatch thread) --------

    def _queue_file(self, rel_path: str) -> None:
        with self._lock:
            self._pending_files.add(rel_path)
            self._reset_timer()

    def _queue_dir_resync(self, rel_dir: str) -> None:
        with self._lock:
            self._pending_dir_ops.append(("resync", rel_dir, None))
            self._reset_timer()

    def _queue_dir_rename(self, old_rel_dir: str, new_rel_dir: str) -> None:
        with self._lock:
            self._pending_dir_ops.append(("rename", old_rel_dir, new_rel_dir))
            self._reset_timer()

    def _reset_timer(self) -> None:
        # Caller already holds self._lock.
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.debounce_seconds, self._on_timer_fire)
        self._timer.daemon = True
        self._timer.start()

    def _on_timer_fire(self) -> None:
        with self._lock:
            self._timer = None
        self._settle()

    def _settle(self) -> None:
        with self._lock:
            files = self._pending_files
            self._pending_files = set()
            dir_ops = self._pending_dir_ops
            self._pending_dir_ops = []

        if not files and not dir_ops:
            return

        with self._flush_lock:
            for op, a, b in dir_ops:
                if op == "resync":
                    self.indexer.resync_subtree(self.index, a)
                elif op == "rename":
                    self.indexer.rename_subtree(self.index, a, b)
            if files:
                self.indexer.update_paths(self.index, files)
            try:
                self.repository.save(self.index)
            except OSError:
                logger.exception(
                    "failed to flush index for %s", self.indexer.project_name
                )


class _Handler(FileSystemEventHandler):
    """Translates raw watchdog events into ProjectWatcher's queue calls."""

    def __init__(self, owner: ProjectWatcher) -> None:
        self.owner = owner

    def _rel(self, path: str) -> str | None:
        try:
            return Path(path).relative_to(
                self.owner.indexer.project_root
            ).as_posix()
        except ValueError:
            return None

    def on_created(self, event) -> None:
        rel = self._rel(event.src_path)
        if rel is None:
            return
        if event.is_directory:
            if self.owner.indexer.ignore_rules.should_skip_dir(rel):
                return
            self.owner._queue_dir_resync(rel)
        else:
            self.owner._queue_file(rel)

    def on_modified(self, event) -> None:
        # Directory "modified" events are noisy and uninteresting on most
        # platforms; the file events they accompany already cover real
        # changes.
        if event.is_directory:
            return
        rel = self._rel(event.src_path)
        if rel is not None:
            self.owner._queue_file(rel)

    def on_deleted(self, event) -> None:
        rel = self._rel(event.src_path)
        if rel is None:
            return
        if event.is_directory:
            if self.owner.indexer.ignore_rules.should_skip_dir(rel):
                return
            self.owner._queue_dir_resync(rel)
        else:
            self.owner._queue_file(rel)

    def on_moved(self, event) -> None:
        old_rel = self._rel(event.src_path)
        new_rel = self._rel(event.dest_path)
        if old_rel is None or new_rel is None:
            return

        if event.is_directory:
            rules = self.owner.indexer.ignore_rules
            old_ignored = rules.should_skip_dir(old_rel)
            new_ignored = rules.should_skip_dir(new_rel)
            if old_ignored and new_ignored:
                return
            if new_ignored:
                # Moved into an ignored location — drop what was there.
                self.owner._queue_dir_resync(old_rel)
            elif old_ignored:
                # Moved out of an ignored location — it's all new to us.
                self.owner._queue_dir_resync(new_rel)
            else:
                self.owner._queue_dir_rename(old_rel, new_rel)
        else:
            self.owner._queue_file(old_rel)
            self.owner._queue_file(new_rel)

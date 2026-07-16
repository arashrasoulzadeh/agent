"""Session and project metadata store.

A named session (a directory under `config.SESSION_ROOT`) tracks one or
more attached project roots. Each attached project gets its own
lightweight metadata mirror — path, size, mtime, sha256, detected
language, and a lazily-filled derived-data slot per file, never source
content — kept in sync by a background file watcher (watchdog). An LLM
prompt is built from this metadata (serialize.py), not from re-reading
files on every run.

    config.py               SESSION_ROOT resolution.
    ignore.py                gitignore/binary/size/symlink/.env exclusion.
    indexer.py                builds and reconciles a project's metadata.
    index_repository.py        atomic index.json read/write, one per project.
    manifest_repository.py      atomic manifest.json read/write, one per session.
    watcher.py                   debounced watchdog wrapper keeping a
                                project's index in sync incrementally.
    manager.py                    SessionManager: create/load/attach/
                                detach/list/status — the orchestration
                                layer everything above is wired through.
    serialize.py                    metadata -> compact LLM prompt context.
    cli.py                           the `agent-session` console script.

See docs/SESSIONS.md for the on-disk layout, the invariants the watcher
and indexer maintain, and what's deliberately out of scope.
"""

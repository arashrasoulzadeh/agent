"""SessionManager: create/load/attach/detach/list/status for sessions.

A session is a named subdirectory under workspace/config.py's
SESSION_ROOT, holding a manifest.json (workspace/manifest_repository.py)
of attached projects plus one subdirectory per attached project, each
holding that project's index.json (workspace/index_repository.py).

`attach()` always builds/reconciles a project's index synchronously and
persists it *before* `load()` can ever start a watcher against it — this
sequencing, not locking, is what keeps a fresh watcher from racing a
concurrent startup scan (see workspace/watcher.py's own docstring).
`load()` returns a `LoadedSession` whose start_watchers()/stop_watchers()
fan out to one `ProjectWatcher` per attached project, mirroring
wire/app.py's serve() shape: everything set up before anything starts
accepting/watching, a clean stop on the way out.
"""

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from models.session_manifest import ProjectAttachment, SessionManifest
from workspace import config
from workspace.ignore import IgnoreRules
from workspace.index_repository import IndexRepository
from workspace.indexer import ProjectIndexer
from workspace.manifest_repository import ManifestRepository
from workspace.watcher import ProjectWatcher


class SessionNotFound(Exception):
    """No session with the given name exists."""


class SessionAlreadyExists(Exception):
    """A session with the given name already exists."""


class ProjectNameConflict(Exception):
    """A different project is already attached under this name."""


class ProjectNotFound(Exception):
    """No project with the given name is attached to this session."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class LoadedSession:
    """A session with a live `ProjectWatcher` per attached project."""

    def __init__(
        self,
        name: str,
        manifest: SessionManifest,
        watchers: dict[str, ProjectWatcher],
    ) -> None:
        self.name = name
        self.manifest = manifest
        self.watchers = watchers

    def start_watchers(self) -> None:
        for watcher in self.watchers.values():
            watcher.start()

    def stop_watchers(self) -> None:
        for watcher in self.watchers.values():
            watcher.stop()


class SessionManager:
    def __init__(self, session_root: Path | None = None) -> None:
        self.session_root = (
            session_root if session_root is not None else config.SESSION_ROOT
        )

    def _session_dir(self, name: str) -> Path:
        return self.session_root / name

    def _manifest_repo(self, name: str) -> ManifestRepository:
        return ManifestRepository(self._session_dir(name))

    def _require_manifest(self, name: str) -> SessionManifest:
        manifest = self._manifest_repo(name).load()
        if manifest is None:
            raise SessionNotFound(f"session {name!r} not found")
        return manifest

    def _require_project(
        self, manifest: SessionManifest, project_name: str
    ) -> ProjectAttachment:
        attachment = manifest.projects.get(project_name)
        if attachment is None:
            raise ProjectNotFound(
                f"project {project_name!r} not attached to session {manifest.name!r}"
            )
        return attachment

    def _project_dir(self, session_name: str, project_name: str) -> Path:
        return self._session_dir(session_name) / project_name

    def _build_or_reconcile(
        self, session_name: str, attachment: ProjectAttachment
    ) -> tuple[ProjectIndexer, IndexRepository]:
        """Synchronously bring one project's index up to date and persist
        it. Called from both attach() and load(), always before any
        watcher exists for that project."""
        project_root = Path(attachment.root)
        project_dir = self._project_dir(session_name, attachment.name)
        ignore_rules = IgnoreRules(project_root, extra_patterns=attachment.ignore_extra)
        indexer = ProjectIndexer(attachment.name, project_root, ignore_rules)
        repo = IndexRepository(project_dir)

        existing = repo.load()
        index = indexer.reconcile(existing) if existing is not None else indexer.build()
        repo.save(index)
        return indexer, repo

    # ---- session lifecycle --------------------------------------------

    def create(self, name: str) -> SessionManifest:
        repo = self._manifest_repo(name)
        if repo.load() is not None:
            raise SessionAlreadyExists(f"session {name!r} already exists")
        manifest = SessionManifest(name=name, created_at=_now_iso(), projects={})
        repo.save(manifest)
        return manifest

    def load(self, name: str) -> LoadedSession:
        manifest = self._require_manifest(name)
        watchers = {}
        for project_name, attachment in manifest.projects.items():
            indexer, repo = self._build_or_reconcile(name, attachment)
            index = repo.load()
            watchers[project_name] = ProjectWatcher(indexer, index, repo)
        return LoadedSession(name, manifest, watchers)

    def list_sessions(self) -> list[str]:
        if not self.session_root.exists():
            return []
        return sorted(
            p.name
            for p in self.session_root.iterdir()
            if p.is_dir() and (p / "manifest.json").exists()
        )

    # ---- project attachment --------------------------------------------

    def attach(
        self,
        name: str,
        project_path: str | Path,
        project_name: str | None = None,
        ignore_extra: list[str] | None = None,
    ) -> ProjectAttachment:
        manifest = self._require_manifest(name)
        project_root = Path(project_path).expanduser().resolve()
        proj_name = project_name or project_root.name
        ignore_extra = list(ignore_extra) if ignore_extra else []

        existing = manifest.projects.get(proj_name)
        if existing is not None and Path(existing.root).resolve() != project_root:
            raise ProjectNameConflict(
                f"project name {proj_name!r} is already attached to "
                f"{existing.root!r}, not {str(project_root)!r}"
            )

        attachment = ProjectAttachment(
            name=proj_name,
            root=str(project_root),
            attached_at=existing.attached_at if existing is not None else _now_iso(),
            ignore_extra=ignore_extra,
        )
        manifest.projects[proj_name] = attachment
        self._manifest_repo(name).save(manifest)

        # Build/reconcile now, synchronously, before any watcher for this
        # project can exist.
        self._build_or_reconcile(name, attachment)
        return attachment

    def detach(self, name: str, project_name: str) -> None:
        manifest = self._require_manifest(name)
        self._require_project(manifest, project_name)
        del manifest.projects[project_name]
        self._manifest_repo(name).save(manifest)
        shutil.rmtree(self._project_dir(name, project_name), ignore_errors=True)

    def list_projects(self, name: str) -> list[ProjectAttachment]:
        manifest = self._require_manifest(name)
        return sorted(manifest.projects.values(), key=lambda a: a.name)

    # ---- status / derived-data write path ------------------------------

    def status(self, name: str, project_name: str | None = None) -> dict:
        manifest = self._require_manifest(name)
        if project_name is not None:
            attachments = [self._require_project(manifest, project_name)]
        else:
            attachments = list(manifest.projects.values())

        projects = []
        for attachment in attachments:
            repo = IndexRepository(self._project_dir(name, attachment.name))
            index = repo.load()
            projects.append(
                {
                    "name": attachment.name,
                    "root": attachment.root,
                    "file_count": len(index.files) if index is not None else 0,
                    "last_sync": index.last_sync if index is not None else None,
                }
            )
        return {"session": name, "projects": projects}

    def set_derived(
        self,
        name: str,
        project_name: str,
        rel_path: str,
        expected_sha256: str | None,
        value: dict,
    ) -> bool:
        """The one write path for a file's lazily-filled `derived` slot.

        A no-op (returns False) if the file's current sha256 no longer
        matches `expected_sha256` — a slow external write (e.g. a
        background summarizer) can never attach stale derived data to
        content that's since changed.
        """
        repo = IndexRepository(self._project_dir(name, project_name))
        index = repo.load()
        if index is None:
            return False
        meta = index.files.get(rel_path)
        if meta is None or meta.sha256 != expected_sha256:
            return False
        index.files[rel_path] = replace(meta, derived=value)
        repo.save(index)
        return True

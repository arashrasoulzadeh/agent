"""Serializes a session's project metadata into a compact prompt block.

Deliberately not JSON and not a pretty-printed metadata dump — this is
built for LLM context, where every token costs money and attention.
Content hashes aren't shown (useful for this system's own change
detection, meaningless to an LLM); a derived "summary," when present, is
shown inline, since that's exactly the enrichment the derived-data slot
exists for. A file's extracted structural signatures (workspace/
signatures.py: function/class/variable declarations, never bodies) are
rendered indented underneath it, in plain Python-like syntax rather than
JSON, since that's the most token-efficient representation an LLM
already understands natively.
"""

import fnmatch
from pathlib import Path

from models.project_index import ProjectIndex
from workspace import config
from workspace.index_repository import IndexRepository
from workspace.manager import ProjectNotFound, SessionNotFound
from workspace.manifest_repository import ManifestRepository


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _matches(rel_path: str, subtree: str | None, glob: str | None) -> bool:
    if subtree is not None:
        prefix = subtree.strip("/")
        if rel_path != prefix and not rel_path.startswith(prefix + "/"):
            return False
    if glob is not None and not fnmatch.fnmatch(rel_path, glob):
        return False
    return True


def _format_params(params: list[dict]) -> str:
    parts = []
    for p in params:
        part = p["name"]
        if p.get("annotation"):
            part += f": {p['annotation']}"
        if p.get("default") is not None:
            part += f" = {p['default']}"
        parts.append(part)
    return ", ".join(parts)


def _format_function(fn: dict, indent: str) -> list[str]:
    prefix = "async def" if fn.get("async") else "def"
    returns = f" -> {fn['returns']}" if fn.get("returns") else ""
    line = f"{indent}{prefix} {fn['name']}({_format_params(fn['params'])}){returns}"
    if fn.get("summary"):
        line += f"  # {fn['summary']}"
    return [line]


def _format_class(cls: dict, indent: str) -> list[str]:
    bases = f"({', '.join(cls['bases'])})" if cls.get("bases") else ""
    line = f"{indent}class {cls['name']}{bases}"
    if cls.get("summary"):
        line += f"  # {cls['summary']}"
    lines = [line]
    for method in cls.get("methods", []):
        lines.extend(_format_function(method, indent=indent + "    "))
    return lines


def _format_variable(var: dict, indent: str) -> str:
    line = f"{indent}{var['name']}"
    if var.get("annotation"):
        line += f": {var['annotation']}"
    if var.get("value") is not None:
        line += f" = {var['value']}"
    return line


def _render_signatures(signatures: dict, indent: str = "    ") -> list[str]:
    lines = []
    for var in signatures.get("variables", []):
        lines.append(_format_variable(var, indent))
    for fn in signatures.get("functions", []):
        lines.extend(_format_function(fn, indent))
    for cls in signatures.get("classes", []):
        lines.extend(_format_class(cls, indent))
    return lines


def _render_project(
    project_name: str,
    index: ProjectIndex,
    subtree: str | None,
    glob: str | None,
) -> list[str]:
    lines = [
        f"## Project: {project_name} ({index.project_root}) — "
        f"{len(index.files)} files, last synced {index.last_sync}"
    ]
    for rel_path in sorted(index.files):
        if not _matches(rel_path, subtree, glob):
            continue
        meta = index.files[rel_path]
        language = meta.language or "unknown"
        line = f"{rel_path} ({language}, {_human_size(meta.size)})"
        if meta.derived and meta.derived.get("summary"):
            line += f" — {meta.derived['summary']}"
        lines.append(line)
        if meta.derived and meta.derived.get("signatures"):
            lines.extend(_render_signatures(meta.derived["signatures"]))
    return lines


def to_prompt_context(
    session_name: str,
    project: str | None = None,
    subtree: str | None = None,
    glob: str | None = None,
    session_root: Path | None = None,
) -> str:
    """Build a compact, token-efficient LLM context block for a session.

    Filters by attached project name, a path subtree prefix, and/or an
    fnmatch glob — any combination, or none for the whole session. Reads
    directly from each project's index.json; never touches source files
    or starts a watcher.
    """
    root = session_root if session_root is not None else config.SESSION_ROOT
    session_dir = root / session_name
    manifest = ManifestRepository(session_dir).load()
    if manifest is None:
        raise SessionNotFound(f"session {session_name!r} not found")

    if project is not None:
        if project not in manifest.projects:
            raise ProjectNotFound(
                f"project {project!r} not attached to session {session_name!r}"
            )
        attachments = [manifest.projects[project]]
    else:
        attachments = list(manifest.projects.values())

    lines = [f"# Session: {session_name}"]
    for attachment in attachments:
        index = IndexRepository(session_dir / attachment.name).load()
        if index is None:
            continue
        lines.extend(_render_project(attachment.name, index, subtree, glob))
    return "\n".join(lines)

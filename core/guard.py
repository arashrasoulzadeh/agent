"""What the agent is allowed to touch.

Two restrictions, both enforced here so every tool inherits them:

1. Env files (.env and any .env.* variant) hold credentials — the GapGPT
   API key among them. A single read would put that key into an LLM
   request payload and into the log file.

2. Every path must resolve inside one of the room's attached projects.
   Relative paths are taken as relative to the project being addressed
   (the primary one, if a tool call doesn't say which), and `..` and
   symlinks are resolved before the check, so neither can be used to
   step outside it.

A room may have several attached projects (see service/rooms.py's
Room.projects) — the confinement set is a dict of named roots plus one
"primary" name (the room's own identity project, used whenever a tool
call omits `project`), not a single bare root. `set_project_root(path)`
still exists as a convenience for the single-project case (used by
agent/collector.py's ContextCollector, which only ever works with one
root and has no notion of "which project").

The confinement set is a contextvar, not a plain global: the server can
run several rooms concurrently, each analyzing different projects, each
turn on its own worker thread. `asyncio.to_thread` copies the calling
context into that new thread, so a set pinned *inside* one room's thread
is invisible to every other room's thread — the same isolation
`core/ask_context.py` uses for the `ask` tool.
"""

import re
from contextvars import ContextVar
from fnmatch import fnmatch
from pathlib import Path

SECRET_PATTERNS = (".env", ".env.*")

# The name set_project_root() pins its one root under — never exposed to
# callers, just an implementation detail of its single-project contract.
_SINGLE_PROJECT = "__project__"

_project_roots: ContextVar[dict[str, Path] | None] = ContextVar(
    "project_roots", default=None
)
_primary_project: ContextVar[str | None] = ContextVar("primary_project", default=None)


def set_project_root(path: str | Path) -> Path:
    """Pin a single folder as the only project — a convenience wrapper
    for a caller that only ever works with one root and has no notion of
    "which project" (agent/collector.py's ContextCollector). Equivalent
    to set_project_roots({"the one": path}, primary="the one")."""
    root = Path(path).expanduser().resolve()
    _project_roots.set({_SINGLE_PROJECT: root})
    _primary_project.set(_SINGLE_PROJECT)
    return root


def set_project_roots(roots: dict[str, str | Path], primary: str) -> dict[str, Path]:
    """Pin the full set of projects the agent is confined to for this
    worker thread, keyed by name. `primary` names the one a tool call
    resolves against when it omits `project` — service/rooms.py always
    passes its room's own WORKSPACE_PROJECT_NAME here.
    """
    if primary not in roots:
        raise ValueError(f"primary project {primary!r} not among roots {sorted(roots)}")
    resolved = {name: Path(p).expanduser().resolve() for name, p in roots.items()}
    _project_roots.set(resolved)
    _primary_project.set(primary)
    return resolved


def known_projects() -> list[str]:
    """Every project name currently confined to, sorted. Empty if
    nothing has been pinned yet."""
    roots = _project_roots.get()
    return sorted(roots) if roots else []


def primary_project() -> str | None:
    """The project an omitted `project` argument resolves to, or None
    if nothing has been pinned yet."""
    return _primary_project.get()


def project_root(project: str | None = None) -> Path:
    """`project`'s root, or the primary project's if omitted, or the cwd
    if nothing has been pinned at all.

    Falls back to the primary root if `project` names something
    unrecognized — this is a defensive fallback only, never reached in
    normal use: every real caller already checked `known_projects()` or
    got `None` back from `resolve_in_root()` first, and refused before
    ever reaching a path operation.
    """
    roots = _project_roots.get()
    if not roots:
        return Path.cwd().resolve()
    primary = _primary_project.get()
    name = project if project is not None else primary
    return roots.get(name, roots[primary])


def is_secret(path: str | Path) -> bool:
    """True if `path` names a credentials file the agent may not touch."""
    name = Path(path).name
    return any(fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def refusal(path: str | Path) -> str:
    return f"Error: {str(path)!r} is a protected env file and cannot be accessed."


def unknown_project_refusal(project: str) -> str:
    attached = known_projects()
    listed = ", ".join(attached) if attached else "(none)"
    return (
        f"Error: {project!r} is not an attached project. Attached projects: {listed}."
    )


def resolve_in_root(path: str | Path, project: str | None = None) -> Path | None:
    """Resolve `path` inside `project`'s root (or the primary project's),
    or None if it escapes or names an unattached project.

    Relative paths resolve against that root rather than the process
    cwd, so the agent's view of "." is always the project it's addressing.
    """
    if project is not None and project not in known_projects():
        return None
    root = project_root(project)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved = candidate.resolve()
    if resolved == root or resolved.is_relative_to(root):
        return resolved
    return None


def outside_refusal(path: str | Path, project: str | None = None) -> str:
    if project is not None and project not in known_projects():
        return unknown_project_refusal(project)
    return (
        f"Error: {str(path)!r} is outside the project folder "
        f"({project_root(project)}) and cannot be accessed."
    )


def resolve_file_or_refuse(path: str | Path, project: str | None = None) -> Path | str:
    """The shared confinement check for tools whose target is one
    specific file (cat, edit, write, delete, describe): refuse a secret
    (.env*) path, then resolve it inside `project`'s root (or the
    primary project's).

    Returns the resolved Path, or the exact refusal string the tool
    should return as-is — the call site is always
    `target = resolve_file_or_refuse(path, project=project)` followed by
    `if isinstance(target, str): return target`.

    Directory-targeting tools (ls, tree, create_directory) don't apply
    the secret check to their own target, so they call
    resolve_in_root()/outside_refusal() directly instead of this.
    """
    if is_secret(path):
        return refusal(path)
    target = resolve_in_root(path, project=project)
    if target is None:
        return outside_refusal(path, project=project)
    return target


def mentions_secret(command: str) -> bool:
    """Best-effort check for a shell command that touches an env file."""
    return ".env" in command


# A path-looking token: starts with ~, .., or / and runs to the next
# separator. The lookbehind keeps it from firing inside things like sed's
# s/a/b/ or a bare flag.
_PATHISH = re.compile(r"(?<![\w\-])(~|\.\.|/)[^\s'\";|&<>()]*")


def escapes_root(command: str, project: str | None = None) -> bool:
    """Best-effort check for a shell command reaching outside `project`'s
    root (or an unattached project name).

    A shell cannot be jailed the way the file tools can — command
    substitution, env vars and interpreters all offer ways around a static
    check. This catches the plain cases; the cwd pin does the rest.
    """
    if project is not None and project not in known_projects():
        return True
    for match in _PATHISH.finditer(command):
        token = match.group(0)
        if ".." in token or resolve_in_root(token, project) is None:
            return True
    return False


def escapes_refusal(project: str | None = None) -> str:
    """execute.py's own refusal text — mirrors outside_refusal()'s
    unknown-vs-escaped distinction with execute's own wording."""
    if project is not None and project not in known_projects():
        return unknown_project_refusal(project)
    return (
        f"Error: that command reaches outside the project folder "
        f"({project_root(project)})."
    )

"""What the agent is allowed to touch.

Two restrictions, both enforced here so every tool inherits them:

1. Env files (.env and any .env.* variant) hold credentials — the GapGPT
   API key among them. A single read would put that key into an LLM
   request payload and into the log file.

2. Every path must resolve inside the project root. Relative paths are
   taken as relative to that root, and `..` and symlinks are resolved
   before the check, so neither can be used to step outside it.

The root is a contextvar, not a plain global: the server can run several
rooms concurrently, each analyzing a different project, each turn on its
own worker thread. `asyncio.to_thread` copies the calling context into
that new thread, so a root set *inside* one room's thread is invisible to
every other room's thread — the same isolation `core/ask_context.py` uses
for the `ask` tool.
"""

import re
from contextvars import ContextVar
from fnmatch import fnmatch
from pathlib import Path

SECRET_PATTERNS = (".env", ".env.*")

_project_root: ContextVar[Path | None] = ContextVar("project_root", default=None)


def set_project_root(path: str | Path) -> Path:
    """Pin the folder the agent is confined to. Called by the collector."""
    root = Path(path).expanduser().resolve()
    _project_root.set(root)
    return root


def project_root() -> Path:
    """The folder the agent is confined to; the cwd until one is pinned."""
    root = _project_root.get()
    return root if root is not None else Path.cwd().resolve()


def is_secret(path: str | Path) -> bool:
    """True if `path` names a credentials file the agent may not touch."""
    name = Path(path).name
    return any(fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def refusal(path: str | Path) -> str:
    return f"Error: {str(path)!r} is a protected env file and cannot be accessed."


def resolve_in_root(path: str | Path) -> Path | None:
    """Resolve `path` inside the project root, or None if it escapes.

    Relative paths resolve against the root rather than the process cwd,
    so the agent's view of "." is always the project.
    """
    root = project_root()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved = candidate.resolve()
    if resolved == root or resolved.is_relative_to(root):
        return resolved
    return None


def outside_refusal(path: str | Path) -> str:
    return (
        f"Error: {str(path)!r} is outside the project folder "
        f"({project_root()}) and cannot be accessed."
    )


def mentions_secret(command: str) -> bool:
    """Best-effort check for a shell command that touches an env file."""
    return ".env" in command


# A path-looking token: starts with ~, .., or / and runs to the next
# separator. The lookbehind keeps it from firing inside things like sed's
# s/a/b/ or a bare flag.
_PATHISH = re.compile(r"(?<![\w\-])(~|\.\.|/)[^\s'\";|&<>()]*")


def escapes_root(command: str) -> bool:
    """Best-effort check for a shell command reaching outside the root.

    A shell cannot be jailed the way the file tools can — command
    substitution, env vars and interpreters all offer ways around a static
    check. This catches the plain cases; the cwd pin does the rest.
    """
    for match in _PATHISH.finditer(command):
        token = match.group(0)
        if ".." in token or resolve_in_root(token) is None:
            return True
    return False

"""What the agent is allowed to touch.

Two restrictions, both enforced here so every tool inherits them:

1. Env files (.env and any .env.* variant) hold credentials — the GapGPT
   API key among them. A single read would put that key into an LLM
   request payload and into the log file.

2. Every path must resolve inside the project root. Relative paths are
   taken as relative to that root, and `..` and symlinks are resolved
   before the check, so neither can be used to step outside it.
"""

import re
from fnmatch import fnmatch
from pathlib import Path

SECRET_PATTERNS = (".env", ".env.*")

_project_root: Path | None = None


def set_project_root(path: str | Path) -> Path:
    """Pin the folder the agent is confined to. Called by the collector."""
    global _project_root
    _project_root = Path(path).expanduser().resolve()
    return _project_root


def project_root() -> Path:
    """The folder the agent is confined to; the cwd until one is pinned."""
    return _project_root if _project_root is not None else Path.cwd().resolve()


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

"""Files the agent must never read, list, or write.

Env files hold credentials — the GapGPT API key among them. A single cat
would put that key into an LLM request payload and into the log file, so
they are filtered out at the tool layer: they never appear in a directory
listing, never reach the project map that seeds the prompt, and cannot be
read or overwritten.
"""

from fnmatch import fnmatch
from pathlib import Path

SECRET_PATTERNS = (".env", ".env.*")


def is_secret(path: str | Path) -> bool:
    """True if `path` names a file the agent is not allowed to touch."""
    name = Path(path).name
    return any(fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def refusal(path: str | Path) -> str:
    return f"Error: {str(path)!r} is a protected env file and cannot be accessed."

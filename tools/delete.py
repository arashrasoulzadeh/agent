"""Filesystem `delete` tool.

Not in AGENT_TOOLS: deletion is destructive and irreversible, so it stays
opt-in rather than something the agent can reach for on its own.
"""

from langchain_core.tools import tool

from tools.guard import (
    is_secret,
    outside_refusal,
    project_root,
    refusal,
    resolve_in_root,
)


@tool
def delete(path: str) -> str:
    """Delete a file, or an empty directory, inside the project.

    Args:
        path: Path to remove, inside the project.
    """
    if is_secret(path):
        return refusal(path)

    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if target == project_root():
        return "Error: refusing to delete the project root."

    try:
        if target.is_dir():
            target.rmdir()
            return f"Deleted directory {path}."
        if target.is_file():
            target.unlink()
            return f"Deleted file {path}."
    except OSError as exc:
        return f"Error deleting {path!r}: {exc}"

    return f"Error: {path!r} does not exist."

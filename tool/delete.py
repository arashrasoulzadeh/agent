"""Filesystem `delete` tool.

Deletion is destructive and irreversible, so it stays opt-in rather than
something the agent can reach for on its own: AGENT_TOOL = False keeps it
out of AGENT_TOOLS while still leaving it importable directly.
"""

from langchain_core.tools import tool

from core.guard import project_root, resolve_file_or_refuse

AGENT_TOOL = False


@tool
def delete(path: str, project: str | None = None) -> str:
    """Delete a file, or an empty directory, inside the project.

    Args:
        path: Path to remove, inside the project.
        project: Name of an attached project to delete from. Omit to use
            the room's primary project.
    """
    target = resolve_file_or_refuse(path, project=project)
    if isinstance(target, str):
        return target
    if target == project_root(project):
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

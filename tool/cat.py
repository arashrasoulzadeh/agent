"""Filesystem `cat` tool."""

from langchain_core.tools import tool

from core.guard import resolve_file_or_refuse


@tool
def cat(path: str, project: str | None = None) -> str:
    """Read and return the contents of a text file.

    Args:
        path: Path to the file to read, inside the project.
        project: Name of an attached project to read from. Omit to use
            the room's primary project.
    """
    target = resolve_file_or_refuse(path, project=project)
    if isinstance(target, str):
        return target
    if not target.is_file():
        return f"Error: {path!r} is not a file."

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: {path!r} is not a UTF-8 text file."

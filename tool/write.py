"""Filesystem `write` tool."""

from langchain_core.tools import tool

from core.guard import resolve_file_or_refuse


@tool
def write(path: str, content: str, project: str | None = None) -> str:
    """Write text to a file, creating it if needed and overwriting if it exists.

    Args:
        path: Path to the file to write, inside the project.
        content: The full text to write into the file.
        project: Name of an attached project to write into. Omit to use
            the room's primary project.
    """
    target = resolve_file_or_refuse(path, project=project)
    if isinstance(target, str):
        return target
    if target.is_dir():
        return f"Error: {path!r} is a directory."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path!r}: {exc}"

    return f"Wrote {len(content)} characters to {target}."

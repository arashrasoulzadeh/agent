"""Filesystem `edit` tool."""

from langchain_core.tools import tool

from core.guard import resolve_file_or_refuse


@tool
def edit(path: str, content: str, project: str | None = None) -> str:
    """Replace the full contents of a file that already exists.

    Args:
        path: Path to the file to edit, inside the project. It must exist.
        content: The new full contents of the file.
        project: Name of an attached project to edit in. Omit to use the
            room's primary project.
    """
    target = resolve_file_or_refuse(path, project=project)
    if isinstance(target, str):
        return target
    if not target.is_file():
        return f"Error: {path!r} is not an existing file."

    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error editing {path!r}: {exc}"

    return f"Updated {path} ({len(content)} characters)."

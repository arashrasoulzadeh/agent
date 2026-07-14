"""Filesystem `edit` tool."""

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, refusal, resolve_in_root


@tool
def edit(path: str, content: str) -> str:
    """Replace the full contents of a file that already exists.

    Args:
        path: Path to the file to edit, inside the project. It must exist.
        content: The new full contents of the file.
    """
    if is_secret(path):
        return refusal(path)

    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if not target.is_file():
        return f"Error: {path!r} is not an existing file."

    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error editing {path!r}: {exc}"

    return f"Updated {path} ({len(content)} characters)."

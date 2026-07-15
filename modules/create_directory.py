"""Filesystem `create_directory` tool."""

from langchain_core.tools import tool

from core.guard import outside_refusal, resolve_in_root


@tool
def create_directory(path: str) -> str:
    """Create a directory, and any missing parents, inside the project.

    Args:
        path: Directory to create, inside the project.
    """
    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if target.is_dir():
        return f"{path} already exists."

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Error creating {path!r}: {exc}"

    return f"Created directory {path}."

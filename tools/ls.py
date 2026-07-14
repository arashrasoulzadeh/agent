"""Filesystem `ls` tool."""

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, resolve_in_root


@tool
def ls(path: str = ".") -> str:
    """List the files and directories at the given path.

    Args:
        path: Directory to list, inside the project. Defaults to its root.
    """
    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if not target.is_dir():
        return f"Error: {path!r} is not a directory."

    entries = sorted(e.name for e in target.iterdir() if not is_secret(e.name))
    if not entries:
        return f"{path} is empty."
    return "\n".join(entries)

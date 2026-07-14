"""Filesystem `cat` tool."""

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, refusal, resolve_in_root


@tool
def cat(path: str) -> str:
    """Read and return the contents of a text file.

    Args:
        path: Path to the file to read, inside the project.
    """
    if is_secret(path):
        return refusal(path)

    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if not target.is_file():
        return f"Error: {path!r} is not a file."

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: {path!r} is not a UTF-8 text file."

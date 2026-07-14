"""Filesystem `ls` tool."""

import os

from langchain_core.tools import tool

from tools.guard import is_secret


@tool
def ls(path: str = ".") -> str:
    """List the files and directories at the given path.

    Args:
        path: Directory to list. Defaults to the current directory.
    """
    if not os.path.isdir(path):
        return f"Error: {path!r} is not a directory."
    entries = sorted(e for e in os.listdir(path) if not is_secret(e))
    if not entries:
        return f"{path} is empty."
    return "\n".join(entries)

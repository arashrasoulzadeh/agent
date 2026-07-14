"""Filesystem `cat` tool."""

import os

from langchain_core.tools import tool

from tools.guard import is_secret, refusal


@tool
def cat(path: str) -> str:
    """Read and return the contents of a text file.

    Args:
        path: Path to the file to read.
    """
    if is_secret(path):
        return refusal(path)
    if not os.path.isfile(path):
        return f"Error: {path!r} is not a file."
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        return f"Error: {path!r} is not a UTF-8 text file."

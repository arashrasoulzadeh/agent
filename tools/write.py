"""Filesystem `write` tool."""

from pathlib import Path

from langchain_core.tools import tool

from tools.guard import is_secret, refusal


@tool
def write(path: str, content: str) -> str:
    """Write text to a file, creating it if needed and overwriting if it exists.

    Args:
        path: Path to the file to write.
        content: The full text to write into the file.
    """
    if is_secret(path):
        return refusal(path)

    target = Path(path).expanduser()
    if target.is_dir():
        return f"Error: {path!r} is a directory."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path!r}: {exc}"

    return f"Wrote {len(content)} characters to {target}."

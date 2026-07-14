"""Filesystem `write` tool."""

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, refusal, resolve_in_root


@tool
def write(path: str, content: str) -> str:
    """Write text to a file, creating it if needed and overwriting if it exists.

    Args:
        path: Path to the file to write, inside the project.
        content: The full text to write into the file.
    """
    if is_secret(path):
        return refusal(path)

    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if target.is_dir():
        return f"Error: {path!r} is a directory."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path!r}: {exc}"

    return f"Wrote {len(content)} characters to {target}."

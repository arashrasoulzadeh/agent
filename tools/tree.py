"""Filesystem `tree` tool.

Return a simple ASCII tree representation of the directory at the given path.
This tool is read-only (no writes) and honors the same safety rules as other
file-system tools: it only operates inside the project root and ignores secret
(env) files.
"""

from pathlib import Path

from langchain_core.tools import tool

from tools.guard import is_secret, outside_refusal, resolve_in_root


@tool
def tree(path: str = ".") -> str:
    """Return a textual tree of the directory at the given path.

    Args:
        path: Directory to tree-list, inside the project. Defaults to root.
    """
    root = resolve_in_root(path)
    if root is None:
        return outside_refusal(path)
    if not root.exists():
        return f"Error: {path!r} does not exist."
    if not root.is_dir():
        return f"Error: {path!r} is not a directory."

    lines: list[str] = []

    def _build(dir_path: Path, prefix: str = "") -> None:
        try:
            entries = sorted(
                (e for e in dir_path.iterdir() if not is_secret(e.name)),
                key=lambda p: (not p.is_dir(), p.name),
            )
        except PermissionError:
            # If we somehow can't access a directory listing, skip it gracefully.
            return
        for i, entry in enumerate(entries):
            connector = "├── " if i < len(entries) - 1 else "└── "
            name = entry.name
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{name}/")
                extension = "│   " if i < len(entries) - 1 else "    "
                _build(entry, prefix + extension)
            else:
                lines.append(f"{prefix}{connector}{name}")

    # Show the root directory name as the top level
    lines.append(root.name + "/" if root.name else root.as_posix() + "/")
    _build(root)
    return "\n".join(lines)

"""On-demand structural-signature lookup tool — tier 2 of the two-tier
project metadata design (see workspace/serialize.py's module docstring
and docs/SESSIONS.md): cheaper than `cat`, richer than the one-line
description every bootstrap already carries for each file.

Needs to know which room's workspace project to look up — that's
core/room_context.py's job (set by service/rooms.py on the same worker
thread `core.guard.set_project_root()` already runs on), mirroring how
`cat`/`ls` already rely on core.guard for path confinement.
"""

from langchain_core.tools import tool

from core import room_context
from core.guard import (
    is_secret,
    outside_refusal,
    project_root,
    refusal,
    resolve_in_root,
)
from workspace import config as workspace_config
from workspace.index_repository import IndexRepository
from workspace.serialize import render_file_signatures


@tool
def describe(path: str) -> str:
    """Return one file's structural signatures (functions, classes,
    variables) without reading its full source.

    Cheaper than cat(); use it when the project map's one-line
    description isn't enough to judge whether a file matters, before
    falling back to cat for the actual code.

    Args:
        path: Path to the file, inside the project.
    """
    if is_secret(path):
        return refusal(path)

    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if not target.is_file():
        return f"Error: {path!r} is not a file."

    room_id = room_context.current_room_id()
    if room_id is None:
        return "Error: no active project session."

    rel_path = target.relative_to(project_root()).as_posix()
    project_dir = (
        workspace_config.SESSION_ROOT
        / room_id
        / workspace_config.WORKSPACE_PROJECT_NAME
    )
    index = IndexRepository(project_dir).load()
    if index is None:
        return "Error: no metadata index found for this project."

    meta = index.files.get(rel_path)
    if meta is None:
        return f"Error: {path!r} is not tracked in the project index."

    return render_file_signatures(rel_path, meta)

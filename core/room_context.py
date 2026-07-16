"""Which room's worker thread is currently running.

Mirrors core/guard.py's set_project_root/project_root shape — a plain
contextvar set once at the top of a worker function — not
core/ask_context.py's context-manager shape (that one exists for a
nested, bidirectional call: the `ask` tool blocking mid-turn for a
reply, which doesn't apply here).

Lets a tool (tool/describe.py) that needs "which room is this?" find out
without service/rooms.py injecting it as a parameter — tools are bare
`@tool`-decorated functions with no constructor to inject anything into.
A contextvar, not a plain global, for the same reason guard.py's project
root is one: asyncio.to_thread copies the calling context into the new
thread, so a room id set inside one room's worker thread stays invisible
to every other concurrently running room's thread.
"""

from contextvars import ContextVar

_current_room: ContextVar[str | None] = ContextVar("current_room_id", default=None)


def set_current_room(room_id: str) -> None:
    """Pin the room id the current worker thread is running for."""
    _current_room.set(room_id)


def current_room_id() -> str | None:
    """The active room's id, or None outside any room's worker thread."""
    return _current_room.get()

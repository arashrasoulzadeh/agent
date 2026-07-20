"""Routes the `show_ui` tool's request to whichever room is running it.

`tool/ui.py` is a plain `@tool` function: the LLM decides its arguments,
so there's no way to hand it "which room is calling me" as a parameter.
A contextvar solves that without `tool` depending on `service` (which
itself depends on `agent`, which depends on `tool` — a cycle if the
dependency ran the other way). Exactly `core/ask_context.py`'s own
pattern, for exactly the same reason — read that module's docstring
alongside this one.

`service/rooms.py` sets the presenter from *inside* the worker thread
that runs one room's turn (see its use of `asyncio.to_thread`, which
copies the calling context into the new thread): setting it there,
rather than before dispatching to the thread, keeps each concurrently
running room's presenter isolated from every other room's — same
isolation `core/ask_context.py` relies on for the same reason.
"""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ShowUiFn = Callable[[str | None, "list[Any]", "list[str] | None"], str]

_current: ContextVar[ShowUiFn | None] = ContextVar("current_ui_presenter", default=None)


@contextmanager
def presenter(fn: ShowUiFn):
    """Make `fn` the UI presenter for the duration of the `with` block."""
    token = _current.set(fn)
    try:
        yield
    finally:
        _current.reset(token)


def show(
    title: str | None, blocks: "list[Any]", quick_replies: "list[str] | None" = None
) -> str:
    """Presents `blocks` to whoever is listening, or says there's no
    surface to show them on if no one is — mirrors ask_context.ask()'s
    own "None if no one is" fallback, just with a string result instead
    of None, since show_ui always needs *something* to return to the LLM
    (unlike ask(), it has no reply to wait for)."""
    fn = _current.get()
    if fn is None:
        return "No UI surface is available right now."
    return fn(title, blocks, quick_replies)

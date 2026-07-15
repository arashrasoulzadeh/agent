"""Routes the `ask` tool's question to whichever room is running it.

`modules/ask.py` is a plain `@tool` function: the LLM decides its
arguments, so there's no way to hand it "which room is calling me" as a
parameter. A contextvar solves that without `modules` depending on
`server` (which itself depends on `pipeline`, which depends on `modules` —
a cycle if the dependency ran the other way).

`application/rooms.py` sets the asker from *inside* the worker thread that runs
one room's turn (see its use of `asyncio.to_thread`, which copies the
calling context into the new thread): setting it there, rather than
before dispatching to the thread, keeps each concurrently running room's
asker isolated from every other room's.
"""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

AskFn = Callable[[str], "str | None"]

_current: ContextVar[AskFn | None] = ContextVar("current_asker", default=None)


@contextmanager
def asker(fn: AskFn):
    """Make `fn` the asker for the duration of the `with` block."""
    token = _current.set(fn)
    try:
        yield
    finally:
        _current.reset(token)


def ask(question: str) -> str | None:
    """Put `question` to whoever is listening, or None if no one is."""
    fn = _current.get()
    return fn(question) if fn is not None else None

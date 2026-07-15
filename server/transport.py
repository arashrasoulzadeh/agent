"""The delivery interface: how a message reaches one connected client.

`server/rooms.py`, `server/events.py`, and `server/routes.py` — the
transport-agnostic core of the server — only ever talk to this
interface. None of them import `websockets`, construct a frame, or know
what "sending" actually involves. That's what makes adding a REST or
gRPC transport later a matter of writing one new adapter class here and
one new accept-loop in `server/app.py` (or a sibling module) — zero
changes to `rooms.py`/`events.py`/`routes.py`.

A `Transport` wraps exactly one client connection, however its wire
format works, and exposes the one operation the core needs: deliver a
message (any JSON-able dict — a response or an event) to that client.
Encoding is the transport's job, not the core's, precisely because a
future gRPC transport wants protobuf, not a JSON string.
"""

import json
from abc import ABC, abstractmethod
from typing import Any


class Transport(ABC):
    """One connected client, addressed generically.

    Room/events/routes hold these in a `set`, so identity (`==`/`hash`)
    is plain object identity — the default for any Transport subclass —
    matching one Transport instance per physical connection.
    """

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Deliver one message to this client. Raise if delivery fails
        (a closed connection, etc.) — callers (see server/events.py)
        treat that as "this client is gone" and drop it, they don't
        swallow the error silently."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether this client is still reachable. Best-effort — a
        connection can still drop between this check and the next
        `send()`, which is exactly why `send()` itself must still raise
        on failure rather than assume callers checked first."""


class WebSocketTransport(Transport):
    """Wraps one `websockets` server connection.

    This is the *only* file that imports `websockets` on the delivery
    side — a REST or gRPC transport would each get their own adapter
    here, none of them touching this class or anything upstream of it.
    """

    def __init__(self, connection) -> None:
        self._connection = connection

    async def send(self, message: dict[str, Any]) -> None:
        await self._connection.send(json.dumps(message))

    @property
    def is_open(self) -> bool:
        # websockets' State.OPEN == 1; comparing by value avoids a hard
        # import-time dependency on the exact enum location, which has
        # moved between websockets versions.
        state = getattr(self._connection, "state", None)
        return state is not None and getattr(state, "value", state) == 1

    def __repr__(self) -> str:
        addr = getattr(self._connection, "remote_address", "?")
        return f"WebSocketTransport({addr!r})"

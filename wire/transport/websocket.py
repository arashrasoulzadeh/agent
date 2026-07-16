"""The WebSocket `Transport` implementation.

This is the *only* file that imports `websockets` on the delivery side —
a REST or gRPC transport would each get their own adapter module next to
this one, none of them touching this class or anything upstream of it
(wire/transport/base.py's `Transport`).
"""

import json
from typing import Any

from wire.transport.base import Transport


class WebSocketTransport(Transport):
    """Wraps one `websockets` server connection."""

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

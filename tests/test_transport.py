"""Tests for the Transport interface (wire/transport/base.py) and
wire/events.py's broadcast().

The InMemoryTransport here is a second, deliberately non-WebSocket
implementation — proving events.broadcast()/Room only ever depend on the
Transport contract, never on `websockets` specifically, which is exactly
what would let a REST or gRPC transport plug in with zero core changes.
"""

import asyncio
import unittest
from typing import Any

from wire import events
from wire.transport.base import Transport
from wire.transport.websocket import WebSocketTransport


class InMemoryTransport(Transport):
    """A second Transport implementation with nothing to do with
    WebSockets — just appends whatever it's sent to a list."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict[str, Any]] = []
        self.fail = fail
        self._open = True

    async def send(self, message: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("simulated failure")
        self.sent.append(message)

    @property
    def is_open(self) -> bool:
        return self._open


class FakeWebSocketConnection:
    """Just enough of a websockets connection for WebSocketTransport."""

    def __init__(self, state_value: int = 1):
        self.sent: list[str] = []
        self.remote_address = ("127.0.0.1", 12345)

        class _State:
            def __init__(self, value):
                self.value = value

        self.state = _State(state_value)

    async def send(self, raw: str) -> None:
        self.sent.append(raw)


class TestWebSocketTransport(unittest.IsolatedAsyncioTestCase):
    async def test_send_encodes_to_json(self):
        connection = FakeWebSocketConnection()
        transport = WebSocketTransport(connection)
        await transport.send({"event": "answer", "data": {"text": "hi"}})

        self.assertEqual(len(connection.sent), 1)
        self.assertIn('"event": "answer"', connection.sent[0])
        self.assertIn('"text": "hi"', connection.sent[0])

    async def test_is_open_reflects_connection_state(self):
        open_transport = WebSocketTransport(FakeWebSocketConnection(state_value=1))
        closed_transport = WebSocketTransport(FakeWebSocketConnection(state_value=3))

        self.assertTrue(open_transport.is_open)
        self.assertFalse(closed_transport.is_open)


class TestBroadcastIsTransportAgnostic(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_delivers_to_any_transport_implementation(self):
        # Two entirely different Transport implementations subscribed to
        # the same room — events.broadcast() must not care which is which.
        ws_connection = FakeWebSocketConnection()
        clients: set[Transport] = {
            WebSocketTransport(ws_connection),
            InMemoryTransport(),
        }

        await events.broadcast(clients, "room-1", "answer", {"text": "hi"})

        self.assertEqual(len(ws_connection.sent), 1)
        in_memory = next(c for c in clients if isinstance(c, InMemoryTransport))
        self.assertEqual(len(in_memory.sent), 1)
        self.assertEqual(in_memory.sent[0]["event"], "answer")
        self.assertEqual(in_memory.sent[0]["data"], {"text": "hi"})

    async def test_broadcast_drops_a_failing_transport_without_failing_the_rest(self):
        good = InMemoryTransport()
        bad = InMemoryTransport(fail=True)
        clients: set[Transport] = {good, bad}

        await events.broadcast(clients, "room-1", "tokens", {"total": 5})

        self.assertEqual(len(good.sent), 1)
        self.assertNotIn(bad, clients)
        self.assertIn(good, clients)

    async def test_broadcast_is_a_no_op_with_no_clients(self):
        clients: set[Transport] = set()
        # Must not raise.
        await events.broadcast(clients, "room-1", "answer", {"text": "hi"})

    async def test_broadcast_survives_concurrent_subscription(self):
        # A client subscribing while broadcast() is mid-iteration must not
        # raise "Set changed size during iteration" (see events.py's note
        # on iterating a snapshot rather than the live set).
        clients: set[Transport] = {InMemoryTransport() for _ in range(3)}

        async def subscribe_more():
            await asyncio.sleep(0)  # yield once, mid-broadcast
            clients.add(InMemoryTransport())

        await asyncio.gather(
            events.broadcast(clients, "room-1", "answer", {"text": "hi"}),
            subscribe_more(),
        )
        # No assertion beyond "didn't raise" — that's the property under test.


if __name__ == "__main__":
    unittest.main()

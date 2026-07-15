"""Client -> server requests: the route catalog.

Kept separate from events.py on purpose (see that module's docstring).
Every handler takes the `Transport` issuing the request (server/transport.py
— never a raw websocket, HTTP request, or gRPC stream) and the request's
`data`, and returns the dict that becomes the response's `data` — or
raises `protocol.ProtocolError`, which server/app.py turns into an
`{"ok": false, "error": ...}` response without ever crashing the
connection. Nothing in this file is WebSocket-specific.
"""

import asyncio
import logging
from typing import Any

from server import rooms
from server.protocol import ProtocolError
from server.transport import Transport

logger = logging.getLogger("server.routes")


def _fire(coro) -> None:
    """Run `coro` in the background; log if it ever raises.

    Used for anything a route must not block on — see the note on
    Room's turn methods in server/rooms.py for why /prompt can't just
    `await` the whole turn.
    """
    task = asyncio.create_task(coro)

    def _report(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            logger.exception("background task failed", exc_info=t.exception())

    task.add_done_callback(_report)


async def session_create(transport: Transport, data: dict) -> dict:
    path = data.get("path")
    if not path:
        raise ProtocolError("/session/create needs 'path'")

    room = rooms.Room.create(path, asyncio.get_running_loop())
    room.subscribe(transport)
    return {"room": room.id}


async def session_resume(transport: Transport, data: dict) -> dict:
    room_id = data.get("room")
    if not room_id:
        raise ProtocolError("/session/resume needs 'room'")

    room = rooms.Room.get_or_load(room_id, asyncio.get_running_loop())
    if room is None:
        raise ProtocolError(f"no such room: {room_id!r}")

    room.subscribe(transport)
    return room.state_snapshot()


async def prompt(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    text = data.get("text")
    if not text:
        raise ProtocolError("/prompt needs 'text'")
    if not room.try_start_turn():
        raise ProtocolError("a turn is already running in this room")
    # Must not await the turn itself — see the note in server/rooms.py.
    _fire(room.run_prompt(text))
    return {"accepted": True}


async def reply(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    text = data.get("text", "")
    if not room.try_consume_reply():
        raise ProtocolError("this room isn't awaiting a reply")
    await room.deliver_reply(text)
    return {"accepted": True}


async def rooms_list(transport: Transport, data: dict) -> dict:
    return {"rooms": rooms.Room.list_saved()}


def _require_room(data: dict) -> "rooms.Room":
    room_id = data.get("room")
    if not room_id:
        raise ProtocolError("this route needs 'room'")
    room = rooms.ROOMS.get(room_id)
    if room is None:
        raise ProtocolError(
            f"room {room_id!r} isn't loaded — call /session/resume first"
        )
    return room


ROUTES: dict[str, Any] = {
    "/session/create": session_create,
    "/session/resume": session_resume,
    "/prompt": prompt,
    "/reply": reply,
    "/rooms/list": rooms_list,
}

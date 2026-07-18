"""Client -> server requests: the route catalog.

Kept separate from events.py on purpose (see that module's docstring).
Every handler takes the `Transport` issuing the request
(wire/transport/base.py — never a raw websocket, HTTP request, or gRPC
stream) and the request's `data`, and returns the dict that becomes the
response's `data` — or raises `protocol.ProtocolError`, which
wire/app.py turns into an `{"ok": false, "error": ...}` response without
ever crashing the connection. Nothing in this file is WebSocket-specific.
"""

import asyncio
import logging
from typing import Any

from service import rooms
from service.rooms import CannotRemovePrimaryProject
from wire.protocol import ProtocolError
from wire.transport.base import Transport
from workspace.manager import ProjectNameConflict, ProjectNotFound

logger = logging.getLogger("wire.routes")


def _fire(coro) -> None:
    """Run `coro` in the background; log if it ever raises.

    Used for anything a route must not block on — see the note on
    Room's turn methods in service/rooms.py for why /prompt can't just
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

    loop = asyncio.get_running_loop()

    # A room's id is stable per project path (room_id_for_path()), so a
    # path already analyzed before resumes that same room — full state
    # snapshot, like /session/resume — instead of starting a fresh one
    # and paying for another bootstrap turn.
    room_id = rooms.room_id_for_path(path)
    existing = rooms.Room.get_or_load(room_id, loop)
    if existing is not None:
        existing.subscribe(transport)
        # "room" alongside the full snapshot: cli.py's AgentApp reads
        # data["room"] from every /session/create response regardless of
        # whether it's a fresh room or (as here) a resumed one.
        return {"room": existing.id, **existing.state_snapshot()}

    room = rooms.Room.create(path, loop)
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
    # Must not await the turn itself — see the note in service/rooms.py.
    _fire(room.run_prompt(text))
    return {"accepted": True}


async def reply(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    text = data.get("text", "")
    if not room.try_consume_reply():
        raise ProtocolError("this room isn't awaiting a reply")
    await room.deliver_reply(text)
    return {"accepted": True}


async def resync(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    if not room.try_consume_resync():
        raise ProtocolError("no resync is pending for this room")
    if data.get("confirm"):
        if not room.try_start_turn():
            raise ProtocolError("a turn is already running in this room")
        # Same reason as /prompt: must not await the turn itself.
        _fire(room.run_resync())
    return {"accepted": True}


async def project_add(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    path = data.get("path")
    if not path:
        raise ProtocolError("/project/add needs 'path'")
    if not room.try_start_turn():
        raise ProtocolError("a turn is already running in this room")
    try:
        name = room.add_project(path, data.get("name"))
    except ProjectNameConflict as exc:
        room.turn_active = False
        raise ProtocolError(str(exc)) from exc
    # Update the client's project list right away, rather than making it
    # wait for the whole reanalysis turn run_resync() below fires off.
    await room.broadcast_state()
    _fire(room.run_resync())
    return {"name": name, "projects": room.project_list()}


async def project_remove(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    name = data.get("name")
    if not name:
        raise ProtocolError("/project/remove needs 'name'")
    if not room.try_start_turn():
        raise ProtocolError("a turn is already running in this room")
    try:
        room.remove_project(name)
    except (CannotRemovePrimaryProject, ProjectNotFound) as exc:
        room.turn_active = False
        raise ProtocolError(str(exc)) from exc
    await room.broadcast_state()
    _fire(room.run_resync())
    return {"projects": room.project_list()}


async def project_list(transport: Transport, data: dict) -> dict:
    room = _require_room(data)
    return {"projects": room.project_list()}


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
    "/resync": resync,
    "/project/add": project_add,
    "/project/remove": project_remove,
    "/project/list": project_list,
    "/rooms/list": rooms_list,
}

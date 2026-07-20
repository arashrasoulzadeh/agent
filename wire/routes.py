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
from dataclasses import asdict
from typing import Any

from actions import ACTIONS
from components import load_spec
from core import settings
from service import rooms, ui_builder
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


async def session_prompt(transport: Transport, data: dict) -> dict:
    """What cli.py should ask for before it has a path to call
    /session/create with — server-owned copy, not hardcoded client-side
    (see ui/app.py's own "generic server-driven UI renderer" framing).
    No room needed, same precedent as /session/create's own no-room
    request."""
    return {"text": "Project path", "default": "."}


async def ui_spec(transport: Transport, data: dict) -> dict:
    """The one config every generic renderer (ui/app.py,
    desktop/renderer.js) needs before it can draw anything client-local:
    style tokens, exit words, reply placeholders, the spinner frames, the
    connection-state labels, and the Rich color table a DOM renderer has
    to translate to CSS. Fetched fresh from components/spec.json on every
    call (not a module-level cache) rather than bundled into either
    client's own install — a style token added here, or an exit word
    changed, reaches every connected client on its next connect with no
    client code change, no client rebuild, and no client redeploy. No
    room needed, same precedent as /session/prompt and /settings/list:
    this isn't per-room state, it's process-wide.
    """
    return load_spec()


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
        # whether it's a fresh room or (as here) a resumed one. "tree" is
        # the full initial component tree (see Room.ui_tree()) — the
        # generic renderer mounts this once, then only ever applies
        # ui.update ops from then on.
        return {
            "room": existing.id,
            **existing.state_snapshot(),
            "tree": asdict(existing.ui_tree()),
        }

    room = rooms.Room.create(path, loop)
    room.subscribe(transport)
    return {"room": room.id, "tree": asdict(room.ui_tree())}


async def session_resume(transport: Transport, data: dict) -> dict:
    room_id = data.get("room")
    if not room_id:
        raise ProtocolError("/session/resume needs 'room'")

    room = rooms.Room.get_or_load(room_id, asyncio.get_running_loop())
    if room is None:
        raise ProtocolError(f"no such room: {room_id!r}")

    room.subscribe(transport)
    return {**room.state_snapshot(), "tree": asdict(room.ui_tree())}


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


async def settings_list(transport: Transport, data: dict) -> dict:
    return {"settings": settings.list_settings()}


async def settings_update(transport: Transport, data: dict) -> dict:
    key = data.get("key")
    value = data.get("value")
    if not key:
        raise ProtocolError("/settings/update needs 'key'")
    if value is None:
        raise ProtocolError("/settings/update needs 'value'")
    try:
        settings.update_setting(key, value)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc
    return {"settings": settings.list_settings()}


class _RouteActionContext:
    """The one concrete ActionContext (core/action.py) — wraps a
    (transport, room) pair and delegates to this module's own route
    functions (project_add, project_remove, ...) or Room methods, so a
    "/command" and its equivalent direct route/request stay
    behaviorally identical by construction, not a parallel copy of the
    same logic re-derived here."""

    def __init__(self, transport: Transport, room: "rooms.Room") -> None:
        self._transport = transport
        self._room = room

    async def add_project(self, path: str, name: str | None) -> None:
        data: dict[str, str] = {"room": self._room.id, "path": path}
        if name:
            data["name"] = name
        await project_add(self._transport, data)

    async def remove_project(self, name: str) -> None:
        await project_remove(self._transport, {"room": self._room.id, "name": name})

    async def show_settings(self) -> None:
        await self._room.push_modal(ui_builder.settings_modal_node(settings.list_settings()))

    async def show_panel(self, title: str | None, blocks: list[dict[str, Any]]) -> None:
        await self._room.append_content(
            "agent_ui", title=title, blocks=blocks, quick_replies=[]
        )

    async def info(self, text: str) -> None:
        await self._room.append_content("info", text=text)

    def project_list(self) -> list[dict[str, Any]]:
        return self._room.project_list()


async def ui_event(transport: Transport, data: dict) -> dict:
    """The one route every interaction with the server-driven UI goes
    through: a click, an Enter submit, a list selection. Dispatches by
    `component_id`, reusing the other routes' own logic (reply, resync,
    project_add, project_remove, settings_update) directly rather than
    duplicating it — see docs/PROTOCOL.md's "UI component protocol"
    section for the full id-naming convention this relies on.

    "exit"/"quit"/"q" are deliberately not handled here — whether to
    terminate the client process isn't room state, so the reference
    client (ui/app.py) intercepts those words itself before ever
    sending a footer-input submit for them. A hypothetical other client
    that sent one anyway would just have it treated as prompt text,
    which is a safe (if unhelpful) fallback, not a crash.
    """
    room = _require_room(data)
    component_id = data.get("component_id")
    event = data.get("event")
    if not component_id or not event:
        raise ProtocolError("/ui/event needs 'component_id' and 'event'")
    value = data.get("value") or ""

    if component_id == "footer-input" and event == "submit":
        await _dispatch_footer_submit(transport, room, value)
        return {"accepted": True}

    if component_id.startswith("opt-") and event == "click":
        await _dispatch_option_click(transport, room, component_id)
        return {"accepted": True}

    if component_id.startswith("quick-") and event == "click":
        await _dispatch_quick_reply(transport, room, component_id)
        return {"accepted": True}

    if component_id.startswith("setting-") and event == "submit":
        await _dispatch_setting_submit(transport, room, component_id, value)
        return {"accepted": True}

    raise ProtocolError(f"unknown component: {component_id!r}")


async def _dispatch_footer_submit(
    transport: Transport, room: "rooms.Room", value: str
) -> None:
    value = value.strip()

    if room.awaiting_reply:
        await reply(transport, {"room": room.id, "text": value})
        return

    if room.resync_suggested:
        confirm = value.lower() in ("y", "yes")
        await resync(transport, {"room": room.id, "confirm": confirm})
        return

    action = ACTIONS.get(value.split(" ", 1)[0]) if value.startswith("/") else None
    if action is not None and action.kind in ("ui", "action"):
        # "pre_prompt"/"post_prompt" actions never reach here — the
        # client resolves those itself the moment one's accepted from
        # the popup (see core/action.py's own docstring), so only
        # "ui"/"action" kinds ever have anything to `run`.
        args = value.split()[1:]
        assert action.run is not None  # guaranteed by Action.__post_init__
        await action.run(_RouteActionContext(transport, room), args)
        return

    if room.turn_active or not value:
        return

    await prompt(transport, {"room": room.id, "text": value})


async def _dispatch_option_click(
    transport: Transport, room: "rooms.Room", component_id: str
) -> None:
    if not room.awaiting_reply or room.pending_options is None:
        raise ProtocolError("no question is currently pending a click reply")
    try:
        index = int(component_id.removeprefix("opt-"))
        value = room.pending_options[index]
    except (ValueError, IndexError) as exc:
        raise ProtocolError(f"unknown option: {component_id!r}") from exc
    await reply(transport, {"room": room.id, "text": value})


async def _dispatch_quick_reply(
    transport: Transport, room: "rooms.Room", component_id: str
) -> None:
    """A show_ui quick-reply button click — unlike opt-N above (which
    only ever resolves against the *one* currently pending ask()
    question), quick_reply_context is never cleared, so a button from
    several turns back in the transcript's own scrollback stays
    clickable — see Room.quick_reply_context's own docstring for why.

    Calls Room.run_prompt() directly (not the /prompt route above) so it
    can pass a *different* `llm_text` than what's shown to the user —
    the chat bubble stays exactly the button's own label, indistinguishable
    from having typed and sent it, while the agent's next turn gets the
    originating panel's title and a compact content summary
    (_quick_reply_llm_text below) folded in as explicit context, since a
    bare label alone ("Option A") means nothing without knowing which
    panel it answered. Silently ignored while a turn is already running,
    same as an ordinary footer-input submit (_dispatch_footer_submit
    above) in that state — this is deliberately not exposed as a public
    `llm_text` field on /prompt's own wire schema; it's an internal
    detail of this one dispatch path, not a general client capability.
    """
    ctx = room.quick_reply_context.get(component_id)
    if ctx is None:
        raise ProtocolError(f"unknown quick reply: {component_id!r}")
    if not room.try_start_turn():
        return
    _fire(room.run_prompt(ctx["label"], llm_text=_quick_reply_llm_text(ctx)))


def _quick_reply_llm_text(ctx: dict[str, str]) -> str:
    title = ctx.get("title")
    summary = ctx.get("summary")
    where = f'the panel titled "{title}"' if title else "the panel shown above"
    context = f" ({summary})" if summary else ""
    return f'Regarding {where}{context}, the user chose: "{ctx["label"]}"'


async def _dispatch_setting_submit(
    transport: Transport, room: "rooms.Room", component_id: str, value: str
) -> None:
    key = component_id.removeprefix("setting-")
    spec = settings.get_spec(key)
    if spec is None:
        raise ProtocolError(f"unknown setting: {key!r}")
    if spec.secret and not value:
        return  # untouched — never overwrite a secret with blank
    result = await settings_update(transport, {"key": key, "value": value})
    await room.push_modal(ui_builder.settings_modal_node(result["settings"]))
    await room.append_content("info", text=f"Saved {spec.label}.")


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
    "/session/prompt": session_prompt,
    "/ui/spec": ui_spec,
    "/session/create": session_create,
    "/session/resume": session_resume,
    "/prompt": prompt,
    "/reply": reply,
    "/resync": resync,
    "/project/add": project_add,
    "/project/remove": project_remove,
    "/project/list": project_list,
    "/settings/list": settings_list,
    "/settings/update": settings_update,
    "/ui/event": ui_event,
    "/rooms/list": rooms_list,
}

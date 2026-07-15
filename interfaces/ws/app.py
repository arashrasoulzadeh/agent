"""The WebSocket transport adapter: accept raw connections, wrap each in
a WebSocketTransport, and dispatch its requests to routes.py.

This file — along with server/transport.py's WebSocketTransport — is the
*only* WebSocket-specific code in the delivery path. `routes.py`,
`rooms.py`, and `events.py` only ever see a `Transport`; a REST or gRPC
adapter would be a sibling module here (its own accept loop, its own
Transport subclass), never a change to any of those three.

A malformed request, an unknown route, or a route handler raising
`ProtocolError` all become an `{"ok": false, "error": ...}` response —
never a crashed connection. Anything else unexpected is also caught and
reported the same way, logged with a traceback, so one bad request can't
take the whole server (or even just that one connection) down.

`serve()` also runs every module's lifecycle hooks (core/module.py):
init() and start() before accepting connections, stop() on graceful
shutdown (SIGINT/SIGTERM) — see server/lifecycle.py.
"""

import asyncio
import logging
import signal

import websockets

from modules import LIFECYCLE_MODULES
from server import lifecycle, rooms
from server.config import HOST, PORT
from server.protocol import ProtocolError, Request, error_response, response
from server.routes import ROUTES
from server.transport import WebSocketTransport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("server.app")


async def handle(connection) -> None:
    transport = WebSocketTransport(connection)
    logger.info("client connected: %s", connection.remote_address)
    try:
        async for raw in connection:
            await _dispatch(transport, raw)
    finally:
        for room in rooms.ROOMS.values():
            room.unsubscribe(transport)
        logger.info("client disconnected: %s", connection.remote_address)


async def _dispatch(transport: WebSocketTransport, raw: str) -> None:
    try:
        request = Request.parse(raw)
    except ProtocolError as exc:
        await transport.send(error_response("unknown", str(exc)))
        return

    handler = ROUTES.get(request.route)
    if handler is None:
        await transport.send(
            error_response(request.id, f"unknown route: {request.route!r}")
        )
        return

    data = dict(request.data)
    if request.room is not None:
        data.setdefault("room", request.room)

    try:
        result = await handler(transport, data)
    except ProtocolError as exc:
        await transport.send(error_response(request.id, str(exc)))
        return
    except Exception as exc:
        logger.exception("route %s failed", request.route)
        await transport.send(error_response(request.id, f"internal error: {exc}"))
        return

    await transport.send(response(request.id, result))


async def serve() -> None:
    lifecycle.init_all(LIFECYCLE_MODULES, config={})
    lifecycle.start_all(LIFECYCLE_MODULES)

    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_requested.set)
        except NotImplementedError:
            # Some platforms/event loop policies (e.g. Windows' default)
            # don't support signal handlers; Ctrl+C still raises
            # KeyboardInterrupt there, which __main__.py catches instead.
            pass

    try:
        async with websockets.serve(handle, HOST, PORT):
            logger.info("listening on ws://%s:%s", HOST, PORT)
            await stop_requested.wait()
            logger.info("shutdown requested, closing...")
    finally:
        lifecycle.stop_all(LIFECYCLE_MODULES)

"""Server -> client pushes: the event catalog and how they're delivered.

Kept separate from routes.py on purpose — routes are what a client can
ask for; events are what the server reports, unprompted, to everyone
subscribed to a room. See docs/PROTOCOL.md for the full catalog with
example payloads.

`broadcast()` delivers through the `Transport` interface
(wire/transport/base.py) only — it has no idea whether a given client is
a websocket, a REST long-poll, or a gRPC stream, and it must never find
out.
"""

import logging

from wire import protocol
from wire.transport.base import Transport

logger = logging.getLogger("wire.events")

# Sent right after create/resume, and again whenever any of its fields
# change — the generic "something about this room's state changed" signal.
# data includes "projects": [{"name", "path", "primary"}, ...] — every
# project currently attached to the room, "primary" marking the one its
# id is derived from (service/rooms.py's WORKSPACE_PROJECT_NAME).
SESSION_STATE = "session.state"

# Echoes a submitted prompt/reply to every client in the room, including
# ones that didn't send it.
MESSAGE = "message"

TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOKENS = "tokens"

# The agent's own mid-turn question; the client should prompt the user
# and answer it with a /reply request.
# data: {"text": str, "options": list[str] | None} — options, when
# present, is a small set of known answers the client can offer as
# one-click choices (e.g. buttons) instead of free text; the reply is
# still just a string either way, so /reply is unchanged.
QUESTION = "question"

ANSWER = "answer"
ERROR = "error"

# The project has drifted too much (service/rooms.py's
# RESYNC_CHANGE_THRESHOLD) since its cached analysis was made for the
# room to keep trusting it silently — the client should ask the user
# whether to re-analyze and answer with a /resync request.
# data: {"changed": int, "total": int, "fraction": float}
RESYNC_SUGGESTED = "resync.suggested"


async def broadcast(
    clients: set[Transport], room_id: str, name: str, data: dict
) -> None:
    """Send one event to every client currently subscribed to a room.

    A client that fails to receive (already disconnected, etc.) is
    dropped from the set rather than taking the rest down with it. Each
    `await client.send(...)` yields control, and another client can
    subscribe or disconnect (mutating this same set) while this loop is
    suspended — so it iterates a snapshot, never the live set.
    """
    if not clients:
        return
    payload = protocol.event(name, room_id, data)
    dead = set()
    for client in list(clients):
        try:
            await client.send(payload)
        except Exception:
            logger.debug("dropping unreachable client from room %s", room_id)
            dead.add(client)
    clients.difference_update(dead)

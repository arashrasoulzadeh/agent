"""The wire envelope shared by every route and event.

Request  (client -> server): {"id": ..., "route": "/prompt", "room": ..., "data": {...}}
Response (server -> client): {"id": ..., "ok": true,  "data": {...}}
                           or {"id": ..., "ok": false, "error": "..."}
Event    (server -> client): {"event": "tool.call", "room": ..., "data": {...},
                              "ts": "..."}

`response()`/`error_response()`/`event()` build plain dicts, not encoded
JSON strings: encoding is a `Transport`'s job (see
wire/transport/base.py), not this module's — a future gRPC transport
wants protobuf, not JSON. Only `Request.parse()` decodes JSON, because
*decoding the request body* is the one wire-format detail that hasn't
moved behind Transport yet (each transport adapter parses its own
incoming frames before handing a plain dict to `wire/routes.py` —
WebSocketTransport's caller, wire/app.py, does that with
`Request.parse`).

See docs/PROTOCOL.md for the full route/event catalog with examples.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class ProtocolError(Exception):
    """A malformed or unsupported request — reported back as an error
    response, never allowed to crash the connection."""


@dataclass
class Request:
    id: str
    route: str
    room: str | None
    data: dict[str, Any]

    @classmethod
    def parse(cls, raw: str) -> "Request":
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ProtocolError("a request must be a JSON object")
        if "id" not in obj or "route" not in obj:
            raise ProtocolError("a request needs at least 'id' and 'route'")
        return cls(
            id=str(obj["id"]),
            route=str(obj["route"]),
            room=obj.get("room"),
            data=obj.get("data") or {},
        )


def response(request_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": request_id, "ok": True, "data": data}


def error_response(request_id: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": message}


def event(name: str, room: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": name,
        "room": room,
        "data": data,
        "ts": datetime.now(UTC).isoformat(),
    }

# Agent protocol

Every feature of the agent is served through one connection to the
server, addressed generically as a `Transport`
(`infrastructure/transport/base.py`) — today that's WebSocket, the only
transport implemented so far, but nothing about the protocol below or
the server's core (`application/rooms.py`, `interfaces/ws/routes.py`,
`interfaces/ws/events.py`) is WebSocket-specific. This document is the
contract: if you're writing a client (a GUI, a script, anything other
than `cli.py`), or a new transport adapter, this is everything you need.

## Running the server

```bash
agent-server
# or: python -m interfaces.ws
```

Listens on `ws://127.0.0.1:8765` by default. Override with the
`AGENT_WS_HOST` / `AGENT_WS_PORT` environment variables. The server is a
freestanding process, not tied to any client's lifetime: it keeps running
(and rooms stay live in memory) independent of whether a CLI or anything
else is currently connected, so a second client can attach to an
in-progress conversation, or a different client entirely can resume a
saved one later. `cli.py` only checks whether one is already listening
and tells you to start it if not (`interfaces/ws/discovery.py`) — it
never spawns the server itself.

## Transports: WebSocket today, more later without touching the protocol

`infrastructure/transport/base.py` defines one interface everything
above the wire format depends on:

```python
class Transport(ABC):
    async def send(self, message: dict) -> None: ...
    @property
    def is_open(self) -> bool: ...
```

`WebSocketTransport` (`infrastructure/transport/websocket.py`) is the
only implementation today, and `interfaces/ws/app.py` is the only file
that imports `websockets` on the delivery side — it accepts raw
connections, wraps each in a `WebSocketTransport`, and hands that to
`interfaces/ws/routes.py`. `Room` (`application/rooms.py`) holds a
`set[Transport]` of whoever's subscribed and broadcasts through
`interfaces/ws/events.py`'s `broadcast()`, which calls
`transport.send(...)` — never anything WebSocket-specific.

Adding REST or gRPC later means writing one new adapter next to
`websocket.py` — a `RestTransport`/`GrpcTransport` implementing
`send()`/`is_open`, plus its own accept loop (a sibling to
`interfaces/ws/app.py`'s `handle()`) that decodes its own wire format
into the same request envelope below and calls the same
`interfaces/ws/routes.py` handlers. Nothing in `rooms.py`, `routes.py`,
or `events.py` changes. `tests/test_transport.py` proves this
concretely: it runs `events.broadcast()` against a second, deliberately
non-WebSocket `Transport` implementation to confirm the core never
assumes which one it's talking to.

## Message shapes

Three shapes travel over the connection, all JSON, newline-free.

**Request** (client → server) — a route call, correlated by `id`:

```json
{"id": "3f1b...", "route": "/prompt", "room": "3c9e...", "data": {"text": "which package handles reservations?"}}
```

`room` is omitted for `/session/create` and `/rooms/list`, which don't
need one yet.

**Response** (server → client) — matched back to the request's `id`:

```json
{"id": "3f1b...", "ok": true, "data": {"accepted": true}}
```
```json
{"id": "3f1b...", "ok": false, "error": "room '...' isn't loaded — call /session/resume first"}
```

A response is *not* the turn's result — it just confirms the request was
accepted (or rejects it, e.g. "a turn is already running"). The actual
content streams as events, below. This matters for `/prompt` specifically:
the response comes back immediately, before the turn finishes, so a
client can still send other requests (like `/reply`, mid-turn) on the same
connection without waiting.

**Event** (server → client) — unprompted, room-scoped, not correlated to
any request:

```json
{"event": "tool.call", "room": "3c9e...", "data": {"name": "cat", "args": "path='go.mod'"}, "ts": "2026-07-15T13:29:44.997036+00:00"}
```

## Routes

| Route | `data` in | `data` out | Notes |
| --- | --- | --- | --- |
| `/session/create` | `{"path": "."}` | `{"room": "<uuid>"}` | Starts a new room and its bootstrap turn in the background; subscribes this connection to it. The bootstrap's progress (tool calls, the answer, `session.state`) arrives as events right after this response. |
| `/session/resume` | `{"room": "<uuid>"}` | The room's full `session.state` payload (see below), plus `"transcript"`: every past tool call/result/message/answer/question, in order, for repainting a client's view. | Subscribes this connection to the room — loading it from `rooms/{uuid}.json` first if it isn't already live in the server's memory (e.g. the server just started). Error if no such room exists. |
| `/prompt` | `{"room": "<uuid>", "text": "..."}` | `{"accepted": true}` | Submits a follow-up question. Error if a turn is already running in this room. |
| `/reply` | `{"room": "<uuid>", "text": "..."}` | `{"accepted": true}` | Answers the agent's own mid-turn `ask` question. Error if the room isn't currently awaiting one. |
| `/rooms/list` | `{}` | `{"rooms": [{"id", "path", "updated_at"}, ...]}` | Every room saved to disk, newest first — for a resume picker. |

## Events

All room-scoped: every client currently subscribed to that room gets
every event for it, including ones it didn't itself trigger (so two
clients open on the same room see the same conversation as it happens).

| Event | `data` | When |
| --- | --- | --- |
| `session.state` | `{path, model, base_url, tools, turn_active, status_label, awaiting_reply, active_tool, tokens}` | Right after `/session/create`/`/session/resume` land as the *response*'s data too — this event fires again any time any of these fields change. It's the one generic "something about this room changed" signal; a minimal client could ignore every other event and just re-render from this one. `status_label` is `"reading the project"`, `"thinking"`, or `null`. |
| `message` | `{role: "user", text}` | A prompt or reply was submitted — echoed to every client in the room, including the one that sent it, so all views append it in the same place in the transcript. |
| `tool.call` | `{name, args}` | A tool invocation starts. |
| `tool.result` | `{output}` | A tool invocation returns. |
| `tokens` | `{prompt, completion, total}` | Usage updated after an LLM call. |
| `question` | `{text}` | The agent's own mid-turn question (the `ask` tool). Answer it with `/reply`; `session.state.awaiting_reply` is `true` until then. |
| `answer` | `{text}` | The turn's final answer, markdown. |
| `error` | `{message}` | A turn failed. Already mapped from the exception type to a plain-English line (see `interfaces/ws/errors.py`) — nothing further to translate client-side. |

## Rooms and persistence

Every session is a room, identified by a UUID. A room owns one project's
conversation: its pipeline, its message history, its token totals. As
long as the server process is running, a room stays live in memory
whether or not any client is currently attached to it — that's what lets
a second client join an in-progress conversation with `/session/resume`
without anything being reloaded from disk.

Every room is also written to `rooms/{uuid}.json` — atomically (a temp
file, then a rename) — after every message, tool call/result, and token
update, not just at the end of a turn. That file is what makes a room
resumable even after the server itself has been restarted:

```json
{
  "id": "<uuid>",
  "path": "/abs/project/path",
  "created_at": "...", "updated_at": "...",
  "tokens": {"prompt": 0, "completion": 0, "total": 1234},
  "messages": [ ... the actual LangChain conversation, serialized ... ],
  "transcript": [ {"type": "tool_call", "name": "...", "args": "...", "ts": "..."}, ... ]
}
```

`messages` is what lets a resumed room's *agent* actually remember the
prior conversation (tool calls included) rather than just showing a log
of it. `transcript` is that log — everything a client needs to repaint
its view of the conversation on `/session/resume`, without touching the
agent at all.

## A minimal client, end to end

```
connect ws://127.0.0.1:8765
  -> {"id": "1", "route": "/session/create", "data": {"path": "."}}
  <- {"id": "1", "ok": true, "data": {"room": "3c9e..."}}
  <- {"event": "session.state", "room": "3c9e...", "data": {...}}
  <- {"event": "tool.call", "room": "3c9e...", "data": {"name": "cat", ...}}
  <- {"event": "tool.result", "room": "3c9e...", "data": {"output": "..."}}
  <- {"event": "answer", "room": "3c9e...", "data": {"text": "This is a ..."}}
  <- {"event": "session.state", "room": "3c9e...", "data": {"turn_active": false, ...}}

  -> {"id": "2", "route": "/prompt", "room": "3c9e...", "data": {"text": "which package handles reservations?"}}
  <- {"id": "2", "ok": true, "data": {"accepted": true}}
  <- {"event": "message", "room": "3c9e...", "data": {"role": "user", "text": "which package..."}}
  <- {"event": "tool.call", ...}
  ... eventually ...
  <- {"event": "answer", "room": "3c9e...", "data": {"text": "..."}}
```

`cli.py`/`interfaces/cli/app.py` is exactly this client, with a Textual
UI on top — read it alongside this document for a concrete
implementation.

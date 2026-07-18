# Agent protocol

Every feature of the agent is served through one connection to the
server, addressed generically as a `Transport`
(`wire/transport/base.py`) — today that's WebSocket, the only
transport implemented so far, but nothing about the protocol below or
the server's core (`service/rooms.py`, `wire/routes.py`,
`wire/events.py`) is WebSocket-specific. This document is the
contract: if you're writing a client (a GUI, a script, anything other
than `cli.py`), or a new transport adapter, this is everything you need.

## Running the server

```bash
agent-server
# or: python -m wire
```

Listens on `ws://127.0.0.1:8765` by default. Override with the
`AGENT_WS_HOST` / `AGENT_WS_PORT` environment variables. The server is a
freestanding process, not tied to any client's lifetime: it keeps running
(and rooms stay live in memory) independent of whether a CLI or anything
else is currently connected, so a second client can attach to an
in-progress conversation, or a different client entirely can resume a
saved one later. `cli.py` only checks whether one is already listening
and tells you to start it if not (`wire/discovery.py`) — it
never spawns the server itself.

## Transports: WebSocket today, more later without touching the protocol

`wire/transport/base.py` defines one interface everything
above the wire format depends on:

```python
class Transport(ABC):
    async def send(self, message: dict) -> None: ...
    @property
    def is_open(self) -> bool: ...
```

`WebSocketTransport` (`wire/transport/websocket.py`) is the
only implementation today, and `wire/app.py` is the only file
that imports `websockets` on the delivery side — it accepts raw
connections, wraps each in a `WebSocketTransport`, and hands that to
`wire/routes.py`. `Room` (`service/rooms.py`) holds a
`set[Transport]` of whoever's subscribed and broadcasts through
`wire/events.py`'s `broadcast()`, which calls
`transport.send(...)` — never anything WebSocket-specific.

Adding REST or gRPC later means writing one new adapter next to
`websocket.py` — a `RestTransport`/`GrpcTransport` implementing
`send()`/`is_open`, plus its own accept loop (a sibling to
`wire/app.py`'s `handle()`) that decodes its own wire format
into the same request envelope below and calls the same
`wire/routes.py` handlers. Nothing in `rooms.py`, `routes.py`,
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
need one yet, and for `/settings/list`/`/settings/update`, which never
need one at all — settings are process-global, not tied to any room.

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
| `/session/create` | `{"path": "."}` | For a path never analyzed before: `{"room": "<id>"}` — starts a new room and its bootstrap turn in the background; the bootstrap's progress (tool calls, the answer, `session.state`) arrives as events right after this response. For a path already analyzed (see below): `{"room": "<id>", ...same payload as /session/resume}` — the existing room is resumed instead, no new bootstrap turn. | Subscribes this connection to the room either way. A room's id is derived from the path itself (`service/rooms.py`'s `room_id_for_path()`, an md5 of the resolved absolute path) — analyzing the same project again always finds and resumes the same room rather than starting a new, empty one. Even a *new* room's bootstrap turn may skip the LLM entirely if `workspace/` already has a cached analysis of this project from an earlier room — see `docs/SESSIONS.md`'s "Room bootstrap integration"; a `resync.suggested` event (below) may follow if that cache looks stale. |
| `/session/resume` | `{"room": "<id>"}` | The room's full `session.state` payload (see below), plus `"transcript"`: every past tool call/result/message/answer/question, in order, for repainting a client's view. | Subscribes this connection to the room — loading it from `rooms/{id}.json` first if it isn't already live in the server's memory (e.g. the server just started). Error if no such room exists. |
| `/prompt` | `{"room": "<id>", "text": "..."}` | `{"accepted": true}` | Submits a follow-up question. Error if a turn is already running in this room. |
| `/reply` | `{"room": "<id>", "text": "..."}` | `{"accepted": true}` | Answers the agent's own mid-turn `ask` question. Error if the room isn't currently awaiting one. |
| `/resync` | `{"room": "<id>", "confirm": true\|false}` | `{"accepted": true}` | Responds to a `resync.suggested` event (below). `confirm: true` re-analyzes the project from scratch (a real LLM call) and refreshes the cached synthesis; `confirm: false` just clears the pending flag and leaves the existing (possibly stale) cached answer in place. Error if no resync is pending for this room, or a turn is already running. |
| `/project/add` | `{"room": "<id>", "path": "...", "name": "..."?}` | `{"name": "<attached-name>", "projects": [{"name", "path", "primary"}, ...]}` | Attaches an additional project to this room. `name` defaults to the path's basename. Error if `name` already names a *different* path in this room, or a turn is already running. Immediately re-analyzes the room (like a confirmed `/resync`) so the cached overview covers every attached project — a `session.state` broadcast lands right away with the updated project list, followed by the usual turn events as the reanalysis runs. |
| `/project/remove` | `{"room": "<id>", "name": "..."}` | `{"projects": [...]}` | Detaches a project and stops its background watcher. Error if `name` is the room's own primary project (its identity — never removable), `name` isn't attached, or a turn is already running. Also triggers an immediate reanalysis, same as `/project/add`. |
| `/project/list` | `{"room": "<id>"}` | `{"projects": [{"name", "path", "primary"}, ...]}` | Every project currently attached to the room — no mutation, no turn. |
| `/settings/list` | `{}` | `{"settings": [{"key", "label", "secret", "scope", "value", "set"}, ...]}` | Every known process-wide setting (`core/settings.py`'s `SETTINGS`) and its current effective value. No room needed — settings aren't per-room. `secret` settings' `value` is always masked (`"••••"`-style); the real value never round-trips over the wire. `scope` is `"immediate"` (takes effect on the very next use, e.g. `NOTION_API_KEY`) or `"new-rooms"` (only rooms created after the change pick it up, e.g. the GapGPT settings — the LLM client is built once per room and held for its whole life). |
| `/settings/update` | `{"key": "...", "value": "..."}` | Same shape as `/settings/list`'s `data`, refreshed. | Persists `value` for `key` to `settings.json` (gitignored, like `.env`) and applies it to the running process immediately. Error if `key` isn't a known setting. |
| `/rooms/list` | `{}` | `{"rooms": [{"id", "path", "updated_at"}, ...]}` | Every room saved to disk, newest first — for a resume picker. |

## Events

All room-scoped: every client currently subscribed to that room gets
every event for it, including ones it didn't itself trigger (so two
clients open on the same room see the same conversation as it happens).

| Event | `data` | When |
| --- | --- | --- |
| `session.state` | `{path, projects, model, base_url, tools, turn_active, status_label, awaiting_reply, resync_suggested, active_tool, tokens}` | Right after `/session/create`/`/session/resume` land as the *response*'s data too — this event fires again any time any of these fields change. It's the one generic "something about this room changed" signal; a minimal client could ignore every other event and just re-render from this one. `status_label` is `"reading the project"`, `"thinking"`, or `null`. `projects` is `[{"name", "path", "primary"}, ...]` — every project currently attached to the room (see `/project/add`/`/project/remove`/`/project/list` above); `path` stays the primary project's own path, unchanged. |
| `message` | `{role: "user", text}` | A prompt or reply was submitted — echoed to every client in the room, including the one that sent it, so all views append it in the same place in the transcript. |
| `tool.call` | `{name, args}` | A tool invocation starts. |
| `tool.result` | `{output}` | A tool invocation returns. |
| `tokens` | `{prompt, completion, total}` | Usage updated after an LLM call. |
| `question` | `{text, options}` | The agent's own mid-turn question (the `ask` tool). `options` is `null` for an open-ended question, or a small list of known answers (e.g. `["npm", "yarn", "pnpm"]`) the client can offer as one-click choices instead of free text. Either way, answer it with `/reply` — the reply is just a string, whether typed or a chosen option's own text; `session.state.awaiting_reply` is `true` until then. |
| `answer` | `{text}` | The turn's final answer, markdown. |
| `resync.suggested` | `{changed, total, fraction}` | The project has drifted (files added/removed/content-changed) past a threshold since its cached analysis was made — the bootstrap answer shown is that (possibly stale) cache. Answer with `/resync`; `session.state.resync_suggested` is `true` until then. |
| `error` | `{message}` | A turn failed. Already mapped from the exception type to a plain-English line (see `wire/errors.py`) — nothing further to translate client-side. |

## Rooms and persistence

Every session is a room, identified by an id derived from its primary
project's path (not a random one — `service/rooms.py`'s
`room_id_for_path()`, an md5 of the resolved absolute path), so
analyzing the same project again always finds the same room. A room owns
one conversation, which may span several attached projects (see
`/project/add`): one pipeline, one message history, one token total,
covering every project attached to it. The room's own id and primary
project never change once created — only added/removed projects are
mutable. As long as the server
process is running, a room stays live in memory whether or not any
client is currently attached to it — that's what lets a second client
join an in-progress conversation with `/session/resume` without
anything being reloaded from disk.

Every room is also written to `rooms/{id}.json` — atomically (a temp
file, then a rename) — after every message, tool call/result, and token
update, not just at the end of a turn. That file is what makes a room
resumable even after the server itself has been restarted:

```json
{
  "id": "<id>",
  "path": "/abs/project/path",
  "projects": {"project": "/abs/project/path", "backend": "/abs/other/path"},
  "created_at": "...", "updated_at": "...",
  "tokens": {"prompt": 0, "completion": 0, "total": 1234},
  "messages": [ ... the actual LangChain conversation, serialized ... ],
  "transcript": [ {"type": "tool_call", "name": "...", "args": "...", "ts": "..."}, ... ]
}
```

`projects` maps each attached project's name to its resolved path — the
room's primary project is always keyed `"project"`. An older
`rooms/{id}.json` written before multi-project support still loads
correctly with no migration: a missing `"projects"` key just falls back
to `{"project": path}`, exactly today's single-project shape.

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

`cli.py`/`ui/app.py` is exactly this client, with a Textual UI on top —
read it alongside this document for a concrete implementation.

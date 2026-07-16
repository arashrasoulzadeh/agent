# Writing a tool

A tool is one `.py` file under `tool/`. There is no registration
step: drop the file in, and `tool/registry.py` picks it up the next time
the process starts. This document covers both halves of the contract —
adding a tool, and (optionally) hooking into the server's lifecycle — and
how discovery actually works under the hood.

## The minimum: a tool

```python
# tool/word_count.py
"""Counts words in a file."""

from langchain_core.tools import tool

from core.guard import outside_refusal, resolve_in_root


@tool
def word_count(path: str) -> str:
    """Count the words in a file.

    Args:
        path: Path to the file, inside the project.
    """
    target = resolve_in_root(path)
    if target is None:
        return outside_refusal(path)
    if not target.is_file():
        return f"Error: {path!r} is not a file."

    text = target.read_text(encoding="utf-8", errors="replace")
    return f"{len(text.split())} words"
```

That's the whole thing. `tool/registry.py` imports every file under
`tool/` (except `__init__.py` and anything starting with `_`), finds
any LangChain `@tool` defined at module level, and includes it in
`AGENT_TOOLS` — the list `service/rooms.py`'s
`default_pipeline_factory` hands to the analyst. Nothing in
`tool/__init__.py`, `tool/registry.py`, or anywhere else needs to
change.

A few things worth matching from the existing tools (see `tool/ls.py`,
`tool/cat.py`, ...):

- Route filesystem access through `core/guard.py`'s `resolve_in_root()` /
  `is_secret()` — that's what confines every tool to the project folder
  and keeps `.env` files unreadable. Don't touch the filesystem directly.
- Return a string, always — errors included (`"Error: ..."`), never raise.
  A tool that raises breaks the agent's reasoning loop; a tool that
  returns an error string lets the agent read it and decide what to do.
- **Emit results only through what you're given.** A tool's return value
  already flows through the transport-agnostic path automatically (see
  below) — never construct a websocket message, print to a console, or
  reach for anything WebSocket/REST/gRPC-specific from inside a tool.

### Opting out of the default toolset

Set a module-level flag to keep a tool importable but out of
`AGENT_TOOLS` (see `tool/delete.py`, `tool/metadata.py`):

```python
AGENT_TOOL = False
```

## How a result reaches the client, without this file knowing

A tool's return value becomes a LangChain `ToolMessage`. `agent/analyst.py`
reports it to whatever `Sink` it was given (`agent/sink.py`) — in the
real server, that's `service/rooms.py`'s `RoomSink`, which turns it
into a `tool.result` protocol event and broadcasts it through every
subscribed `Transport` (`wire/transport/base.py`). A tool file
never imports `service/` or `wire/`, never touches a
`Transport`, and works identically whether the room is being watched
over WebSocket today or REST/gRPC later — that's the whole point of the
transport-agnostic boundary. See `docs/PROTOCOL.md` for the wire format
your tool's output ends up in.

Separately, `hooks/` + `extra/` (see [`docs/HOOKS.md`](HOOKS.md)) let
outside code observe or rewrite a tool call/result in flight, or add an
entirely new tool to `AGENT_TOOLS`, without editing `tool/` at all — a
different, more general extension point than the one this document
covers.

## The optional half: lifecycle hooks

Skip this section entirely unless your tool has state to set up or
tear down — a connection pool, a background task, a cache warmed from
disk. A plain `@tool` function needs none of it.

Expose a module-level `MODULE` object implementing any subset of three
methods (`core/module.py`):

```python
# tool/rate_limiter.py
"""A tool wrapped around a shared, rate-limited HTTP client."""

import httpx
from langchain_core.tools import tool


class _RateLimiterModule:
    def __init__(self) -> None:
        self.client: httpx.Client | None = None

    def init(self, config: dict) -> None:
        """Called once, before the server accepts its first connection."""
        self.timeout = config.get("timeout", 10)

    def start(self) -> None:
        """Called once, right after every module's init() has run."""
        self.client = httpx.Client(timeout=self.timeout)

    def stop(self) -> None:
        """Called once, during graceful shutdown."""
        if self.client is not None:
            self.client.close()


MODULE = _RateLimiterModule()


@tool
def fetch_url(url: str) -> str:
    """Fetch a URL through the shared rate-limited client.

    Args:
        url: The URL to fetch.
    """
    if MODULE.client is None:
        return "Error: the HTTP client isn't running yet."
    response = MODULE.client.get(url)
    return response.text
```

`tool/registry.py` discovers `MODULE` via `hasattr`, not `isinstance` —
implementing only `stop()` (say, to flush a cache on shutdown) is exactly
as valid as implementing all three. `wire/app.py` calls every
discovered module's `init(config)` then `start()` before accepting
connections, and `stop()` once on graceful shutdown (SIGINT/SIGTERM). One
module's hook raising is logged and skipped — it can't stop another
module's hook from running, or crash the server's own startup/shutdown.
`stop()` always runs, even for a module whose `init()`/`start()` never
completed (e.g. a *different* module's `init()` failed first) — write it
defensively (see the `if self.client is not None` check above).

## How discovery actually works

`tool/registry.discover()`:

1. Lists every `.py` file directly under `tool/` (via
   `core/discovery.py`'s generic `import_all()`, which does the
   `pkgutil.iter_modules` scan), skipping `__init__.py` and anything
   starting with `_`.
2. Imports each one exactly once (`importlib.import_module`).
3. Collects every module-level `BaseTool` instance into `all_tools`, and
   into `agent_tools` too unless the module set `AGENT_TOOL = False`.
4. Collects a module-level `MODULE` object into `lifecycle_modules` if it
   implements at least one of `init`/`start`/`stop`.

`tool/__init__.py` runs this once at import time, additionally routes
the collected tool list through the `on_tools_collected` hook (see
[`docs/HOOKS.md`](HOOKS.md)), and exposes the results as `AGENT_TOOLS`
and `LIFECYCLE_MODULES` — both plain module attributes, nothing to call.

## Testing a tool

Import it and call `.invoke({...})` directly — a LangChain tool doesn't
need a server or a room to run:

```python
from tool.word_count import word_count

def test_word_count():
    # assuming a fixture file exists inside the confined project root
    assert "3 words" in word_count.invoke({"path": "fixture.txt"})
```

See `tests/test_registry.py` for how discovery itself is tested (against
a temporary tool directory, so it never touches the real `tool/`).

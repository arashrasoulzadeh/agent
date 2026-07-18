# Agent

An interactive agent that reads a codebase and answers questions about it,
served over a WebSocket protocol with a full-screen terminal UI as its first
client.

Point it at a project and it builds a private map of the source, reads the
files that actually matter, and then holds a conversation about them —
remembering what it has already learned as you ask follow-ups. Every session
is a room that's saved to disk as it happens, so you can pick it back up
later, or open a second client onto the same conversation.

```
┌─ header ────────────────────────────────────────────────────┐
│  ⚡ AGENT                                     tokens 1,234   │
│   model gpt-4o-mini    url https://api.gapgpt.app/v1         │
│   tools  ask  cat  create_directory  edit  ▶execute  ls  ...  │
├───────────────────────────────────────────────────────────────┤
│ This is a Go microservice for party and discount management. │
│ It exposes two binaries — cmd/santa (the API) and             │
│ cmd/blitzen (background jobs) — and persists to Postgres,     │
│ with Redis for caching and RabbitMQ for events.                │
│                                                                 │
│ > which package handles reservations?                         │
│ → cat(path='internal/reservations/service.go')                │
│  … arrow keys / PageUp-PageDown / mouse wheel to scroll …      │
├───────────────────────────────────────────────────────────────┤
│ project ~/code/my-service   room 3c9e2f4a-...                 │
│ > _                                                            │
└───────────────────────────────────────────────────────────────┘
```

The header and footer are sized to exactly fit their own content
(`height: auto`) — the header grows by one line while a turn is running (for
the spinner) and shrinks back down when it's done; the content pane (`1fr`)
always absorbs whatever's left. All three reflow automatically on resize and
never grow past the terminal, and long transcripts scroll *inside* the
content pane rather than scrolling your terminal's own history. The tool
currently in flight is highlighted (`▶`) in the header's tool list.

## Install

Requires Python 3.11+ (uses `datetime.UTC` and `asyncio.timeout`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the project in editable mode and gives you two standalone
commands, `agent` and `agent-server`, in `.venv/bin/` — stable enough to
reference from a systemd unit, a launchd plist, or a Windows service
wrapper later, since neither depends on the current working directory or
being invoked as `python -m ...`.

Copy the example env file and add your key:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `GAPGPT_API_KEY` | — | **Required.** API key (read by the server process). |
| `GAPGPT_BASE_URL` | `https://api.gapgpt.app/v1` | Any OpenAI-compatible endpoint. |
| `GAPGPT_MODEL` | `gpt-4o-mini` | Model to use. |
| `GAPGPT_TIMEOUT` | `60` | Per-request timeout, in seconds. |
| `AGENT_WS_HOST` | `127.0.0.1` | Where the agent server listens, and where the CLI looks for one. |
| `AGENT_WS_PORT` | `8765` | Same, for the port. |
| `AGENT_VERBOSE` | unset | Also print (not just log) raw LLM request/response lines. |
| `NOTION_API_KEY` | unset | Optional. Enables the `notion_search`/`notion_read_page`/`notion_create_page`/`notion_append_text` tools. |

The backend is any OpenAI-compatible API, so `GAPGPT_BASE_URL` can point at
OpenAI, a local Ollama server, or anything else that speaks the same protocol.

### Notion tools (optional)

Setting `NOTION_API_KEY` gives the agent tools to search, read, and write
pages in a connected Notion workspace — a separate, external system, not
one of the local projects it's confined to. Create an internal
integration at [notion.so/my-integrations](https://www.notion.so/my-integrations),
copy its token into `NOTION_API_KEY`, then in Notion share whichever
pages the integration should see (it has no access to anything not
explicitly shared with it). Without a key set, the Notion tools are
still offered to the agent, but every call just returns a plain error
saying so — nothing else in the app is affected.

## Usage

Start the server first, in its own terminal (or as a background process,
or a service — whatever fits):

```bash
agent-server                    # or: python -m wire
```

Then, in another terminal:

```bash
agent .                # analyze the current directory
agent ~/code/project   # analyze somewhere else
agent                  # prompts for a path
agent --room <uuid>    # resume a previous session instead
```

`agent` (`cli.py`) is a thin client: it checks whether an agent server is
already listening on `ws://127.0.0.1:8765` and tells you to start one if
not — it never spawns the server itself, so the server's lifecycle is
never tied to any one client. The server keeps running independent of
any client, so a room stays live and reachable whether or not the CLI
that created it is still open.

Type follow-up questions into the input at the bottom. `exit`, `quit`, or `q`
ends the session. Scroll the transcript with arrow keys, PageUp/PageDown, or
the mouse wheel — it never scrolls your terminal itself.

### Settings

Type `/settings` to open an in-TUI screen for everything in the env-var
table above except `AGENT_WS_HOST`/`AGENT_WS_PORT` (those configure the
connection this screen lives behind, so they aren't editable through it).
One row per setting, `Enter` saves that row, `Escape` closes the screen:

```
/settings
```

Changes are read from and written to the server over the same WebSocket
protocol as everything else (`/settings/list`, `/settings/update` — see
`docs/PROTOCOL.md`) and persist to `settings.json` at the repo root
(gitignored, like `.env`), so they survive a server restart without
needing to hand-edit `.env`. `AGENT_VERBOSE` and `NOTION_API_KEY` take
effect immediately; the GapGPT settings (model/base URL/timeout/API key)
only affect rooms created *after* the change — an already-open
conversation's LLM client was already built and isn't hot-swapped.
Secret fields (`GAPGPT_API_KEY`, `NOTION_API_KEY`) are never shown in
cleartext: the screen shows a blank field you can type a new value into
(masked as you type), not the real stored value; leaving one blank and
pressing Enter is a no-op, so you can never overwrite a key by accident.

### Updating / uninstalling

There's no packaged release yet — this is a git checkout with an editable
install (`pip install -e .`), so "update" means pulling the latest commit and
reinstalling, and "uninstall" means unregistering the console scripts:

```bash
agent update              # git pull --ff-only, then pip install -e . again
agent update --dry-run    # just report how many commits behind you are
agent uninstall           # pip uninstall — keeps rooms/ and the session cache
agent uninstall --purge   # also deletes rooms/ and the workspace session cache
agent uninstall --yes     # skip the confirmation prompt
```

`agent update` refuses to run against a dirty working tree, and pulls with
`--ff-only` — it will never merge or overwrite local changes, just fail
loudly if history has diverged. `agent uninstall` never touches your saved
conversations or cached project indexes unless you pass `--purge`.

## Architecture

The project is organized by technical concern, one top-level package per
concern, rather than by framework layer. Each package takes what it
needs as injected constructor arguments instead of importing a concrete
implementation of another concern directly, so the dependency graph
stays a simple tree, not a web:

```
core/     shared, dependency-free kernel: path safety, the ask contextvar,
          the module-lifecycle contract, a generic dir-scan helper
llm/      the LLM client (get_llm) and its raw-IO logging callback
tool/     every capability the agent has, one file per tool, auto-discovered
agent/    the reasoning engine: ProjectPipeline, ProjectAnalyst, the
          Stage/Pipeline system, ContextSynthesizer — framework-free,
          takes its llm/tools/sink as constructor arguments
models/   pure data shapes threaded through agent/: ProjectContext, Turn
wire/     the WebSocket protocol: transport, routes, events, the accept loop
service/  use-case orchestration: Room, its persistence
ui/       the TUI: a thin WebSocket client
hooks/    lets files in extra/ hook into tool/ and agent/ without editing them
extra/    where you drop your own hook files (see Hooks, below)
```

```
┌──────────────┐  ws://127.0.0.1:8765  ┌───────────────────────────┐
│ ui/           │ ────────────────────► │ wire/ (standalone          │
│ (TUI, thin    │ ◄────────────────────  │ `agent-server` process)   │
│  client)      │   via a Transport      │ → service.Room             │
└──────────────┘   (wire/transport/)     │ → agent.ProjectPipeline    │
                                          │ → rooms/{uuid}.json       │
                                          └───────────────────────────┘
```

Every feature is served through one connection, addressed generically as
a `Transport` (`wire/transport/base.py`) — WebSocket is the only one
implemented so far, but nothing in `service/rooms.py`, `wire/routes.py`,
or `wire/events.py` is WebSocket-specific; adding REST or gRPC later is
one new adapter next to `wire/transport/websocket.py`, not a change to
any of those three. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the
full route/event catalog, message shapes, the transport boundary, and
the room persistence format. In short: requests use a `route` (e.g.
`/prompt`), the server reports everything — tool calls, token usage, the
final answer, any state change — as `event`s pushed to every client
subscribed to that room, and each room is saved via `RoomRepository`
(`service/room_repository.py`) to `rooms/{uuid}.json` after every change
so it can be resumed later, from any client, even after the server
itself restarts.

`ui/app.py` never touches an LLM or runs the pipeline itself; it only
ever sends requests and renders events. `agent/` and `tool/` (below)
don't know the server exists either — they're plain, reusable business
logic that reports through a `Sink` (`agent/sink.py`) and a context-var
based "ask" hook (`core/ask_context.py`), which `service/rooms.py` wires
up to the actual protocol; `agent/` doesn't import `tool/` or `llm/`
either — the concrete tool list, metadata source, and LLM client are all
injected by `service/rooms.py`'s `default_pipeline_factory`, not
hardcoded or built inside `agent/` itself. That's what keeps the CLI
genuinely thin: it imports `websockets`, `textual`, and `rich` — nothing
from `agent`, `tool`, or `langchain` ends up in that process at all.

Three patterns recur on purpose, each solving a specific decoupling
problem rather than for its own sake:

| Pattern | Where | Why |
| --- | --- | --- |
| Transport interface | `wire/transport/base.py` | The server's core never depends on WebSocket specifically. |
| Pipeline/stage | `agent/stage.py`, `agent/stages.py` | A query's processing is reorderable/composable from `PipelineConfig`, not a hardcoded method-call sequence. |
| Observer/event bus | `agent/events.py` | Stage lifecycle (started/completed/failed) can be watched by more than one thing (a logger today; a future metrics collector) without `Pipeline`/`Stage` knowing who's listening. |

Module lifecycle hooks (`core/module.py`) are a related but separate
concern — see [Tools](#tools) and [`docs/MODULES.md`](docs/MODULES.md).
A fourth, more general extension point — the hooks/extra system — lets
outside code rewrite text in flight or add a tool without editing
anything above; see [Hooks](#hooks) and [`docs/HOOKS.md`](docs/HOOKS.md).

## How it works

Three stages, run through the common `Stage` interface (`agent/stage.py`):

| Stage | Does |
| --- | --- |
| `CollectStage` (`ContextCollector`) | Walks the project and produces a private structural map. No LLM. Runs once per session, at `ProjectPipeline.start()`. |
| `AnalyzeStage` (`ProjectAnalyst`) | Holds the conversation. Reads files with tools and answers. Runs on every `.ask()`. |
| `SynthesizeStage` (`ContextSynthesizer`) | Compresses an answer into machine-readable context for another agent. |

`.ask(query)` (the interactive, stateful path — what the TUI calls on
every turn) always runs just `AnalyzeStage`, continuing the existing
session. `.run(query)` (the one-shot path) starts a *fresh* session and
runs whatever `PipelineConfig.stages` lists — `analyze` then `synthesize`
by default, but that list is what makes the sequence configurable rather
than hardcoded:

```python
from agent import PipelineConfig, ProjectPipeline
from agent.analyst import ProjectAnalyst
from agent.collector import ContextCollector
from agent.synthesizer import ContextSynthesizer

pipeline = ProjectPipeline(
    config=PipelineConfig(stages=["analyze"], synthesis_format="json"),
    # agent/ builds none of this itself — llm/tools/metadata are all
    # injected, never imported — see below.
    analyst=ProjectAnalyst(llm=my_llm, tools=my_tools),
    synthesizer=ContextSynthesizer(llm=my_llm),
    collector=my_collector,
)
pipeline.collect_context("~/code/project")
print(pipeline.run("What kind of project is this?"))  # no synthesize this time
```

Dropping `"synthesize"` from the list disables that step; a custom stage
registered with `agent.stages.register_stage()` can be inserted
anywhere in the list — `agent/__init__.py` never hardcodes the
sequence. Cancellation is checked *between* stages
(`PipelineContext.cancel()`); a stage already running can't be
interrupted mid-flight (Python can't forcibly stop a blocking call on
another thread), so a cancelled run simply won't reach the next stage.
An unhandled error in a stage stops the pipeline and propagates — it's
never swallowed.

`ProjectPipeline`/`ProjectAnalyst`/`ContextCollector` know nothing about
the server, the TUI, `tool/`, or `llm/`: the concrete tool list, metadata
source, and LLM client are all constructor parameters
(`service/rooms.py`'s `default_pipeline_factory` injects
`tool.AGENT_TOOLS`, `tool.metadata`, and `llm.get_llm(...)` — the only
place that wiring happens), and while a turn runs, the analyst reports
tool calls/results and token usage to an optional `Sink`
(`agent/sink.py`; a no-op if none is given) — that's how
`service/rooms.py` turns them into protocol events without `agent/`
depending on `service/`, `wire/`, or `tool/` at all. Stage lifecycle
(started/completed/failed) is reported separately to an optional
`StageEventBus` (`agent/events.py`) — `service/rooms.py` attaches a
logging observer per room, but nothing about `Pipeline`/`Stage`
requires it.

### Tools

Every capability the agent has lives in `tool/`, one file per tool, and
every call is reported live as it happens (a `tool.call`/`tool.result` event
pair — see [Architecture](#architecture)). Dropping a new `@tool`-decorated
function into `tool/` is enough to add a capability — `tool/registry.py`
auto-discovers it and includes it in `AGENT_TOOLS`. Nothing else needs to
change.

| Tool | Does |
| --- | --- |
| `ls` | List a directory. |
| `cat` | Read a file. |
| `write` | Create or overwrite a file. |
| `edit` | Replace the contents of an existing file. |
| `create_directory` | Create a directory. |
| `execute` | Run a shell command in the project. |
| `ask` | Put a question back to *you* when the project can't settle it — routed through `core/ask_context.py` rather than any specific transport, so this tool works the same whether it's a room asking a connected client, a test, or nothing at all. |
| `notion_search`, `notion_read_page`, `notion_create_page`, `notion_append_text` | Search/read/write pages in a connected Notion workspace via the real Notion API (`tool/notion.py`) — a separate external system, gated behind an optional `NOTION_API_KEY`. See [Notion tools](#notion-tools-optional). |

`delete` and `metadata` exist in `tool/` but set `AGENT_TOOL = False` at
module level, which keeps them out of `AGENT_TOOLS` while leaving them
directly importable (`from tool import delete`). `delete` is opt-out
because deletion is irreversible; `metadata` is the collector's internal
preprocessing tool. Set `AGENT_TOOL = True` (or drop the flag) on a module
to include it by default.

A module with setup/teardown state (a connection pool, a background
task) additionally exposes a module-level `MODULE` object implementing
part or all of the `init()`/`start()`/`stop()` lifecycle contract
(`core/module.py`) — `wire/app.py` calls those around the server's own
startup and graceful shutdown (SIGINT/SIGTERM). See
[`docs/MODULES.md`](docs/MODULES.md) for the full contract, a worked
example, and how to write, register, and test a new tool — third-party
tools need nothing beyond that document and `core/guard.py`/`core/module.py`.

### Service: rooms

`service/rooms.py` is the use-case layer: `Room` owns a
`ProjectPipeline`, its subscribed `Transport`s, turn/awaiting-reply
state, and persistence via `RoomRepository`
(`service/room_repository.py`) to `rooms/{uuid}.json`.
`default_pipeline_factory` is the one place the agent's concrete
toolset (`tool.AGENT_TOOLS`), metadata source (`tool.metadata`),
and LLM client (`llm.get_llm`) get wired into a
`ProjectPipeline` — the seam tests swap out to avoid a real LLM call.

A room's turn runs via `asyncio.to_thread`, same as the old Textual worker
thread did, just relocated server-side; `core.ask_context` and
`core.guard.set_project_root` are both set *inside* that thread (not before
dispatching to it), so concurrently running rooms — each potentially
analyzing a different project — stay isolated from one another.

### Server (wire)

`wire/` is the standalone WebSocket process everything runs behind,
started with the `agent-server` console script (or `python -m wire`) —
see [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the wire format itself.

| Module | Does |
| --- | --- |
| `config.py` | `AGENT_WS_HOST`/`AGENT_WS_PORT` — the one thing both the server and a thin client need, without a client importing the rest of the package. |
| `protocol.py` | The request/response/event envelope: builds the dicts a `Transport` sends; only `Request.parse()` decodes incoming JSON. |
| `routes.py` | Client → server requests (`/session/create`, `/prompt`, ...), addressed to a `Transport` — kept separate from... |
| `events.py` | ...server → client pushes (`tool.call`, `answer`, ...) delivered through `Transport.send()`, so "what a client can ask for" and "what the server reports" don't tangle into one file. |
| `errors.py` | Maps an exception type to the plain-English message the `error` event carries. |
| `lifecycle.py` | Runs every module's `init()`/`start()`/`stop()` hooks, isolating one module's failure from the rest. |
| `app.py` | The WebSocket accept loop: wraps each connection in a `WebSocketTransport` (`wire/transport/websocket.py`), dispatches requests, runs lifecycle hooks around startup/graceful shutdown (SIGINT/SIGTERM). |
| `discovery.py` | Is a server already listening? A client only checks — it never spawns one. |
| `__main__.py` | `main()` — the `agent-server` console-script entry point. |
| `transport/` | `Transport` ABC (`base.py`) + `WebSocketTransport` (`websocket.py`) — see [Architecture](#architecture). |

`Room` itself (`service/rooms.py`) is deliberately *not* here — see
[Architecture](#architecture) for why.

### CLI (ui)

`ui/app.py` is a single full-screen
[Textual](https://github.com/Textualize/textual) app and a thin
WebSocket client — it never runs the pipeline itself, it connects,
creates or resumes a room, and renders whatever protocol events arrive.
That's also what makes the fixed header/content/footer layout and
internal scrolling possible in the first place: Rich's `Console`/`Live`
only ever print into the terminal's own scrollback, but Textual redraws
the whole screen as a bounded region and reflows it on resize.

| Module | Does |
| --- | --- |
| `app.py` | The `AgentApp`: layout, the header/content/footer widgets, the connection and its receive loop, input routing. |
| `trace.py` | Renders `tool.call`/`tool.result`/`tokens` events into the header and content log. |
| `answer.py` | Renders the `answer` event: the turn's final answer, as markdown. |
| `error.py` | Renders the `error` event — the message is already friendly by the time it gets here (mapped server-side). |
| `style.py` | The Rich style strings the above share. |

The receive loop is a plain asyncio task on Textual's own event loop —
no `call_from_thread` anywhere in `ui/` anymore, since the boundary is
the network now, not a Python thread. `tests/test_app.py` and
`tests/test_server.py` drive a real server and a real client against
each other (`tests/stubs.py`'s pipeline never touches the network), so
the whole suite never spends a real API token.

### Hooks

`hooks/` + `extra/` let outside code interact with the agent without
editing it: drop a `.py` file into `extra/` that registers `@hook(...)`-
decorated callbacks, and it's auto-discovered the same way `tool/` files
are. Two flavors — `filter` hooks return a (possibly rewritten) value
(e.g. `before_prompt`, `after_answer`, `on_tool_result`, or
`on_tools_collected` to add a tool); `notify` hooks are side-effect only
(`on_tool_call`). One broken hook is logged and isolated, never able to
break another hook's turn or crash the caller. See
[`docs/HOOKS.md`](docs/HOOKS.md) for the full hook-point catalog, the
exact contract, and a worked example (`extra/_example_hook.py`,
`extra/_example_logging_hook.py` — both underscore-prefixed so they're
inspectable but never auto-loaded).

### Sessions

`workspace/` (console script `agent-session`) tracks one or more project
roots' metadata — path, size, mtime, content hash, detected language,
and (for Python today, via the stdlib `ast` module) automatically
extracted function/class/variable *signatures* plus a one-line docstring
summary per file, never source content or function bodies — inside a
named session, kept in sync by a background file watcher instead of
re-reading a project from scratch every run:

```bash
agent-session create test_session
agent-session attach test_session ~/code/my-project --name p1
agent-session load test_session      # foreground; watches p1 for changes
agent-session serialize test_session --project p1   # compact LLM-ready context
```

`service/rooms.py` also uses this store directly at room-bootstrap time:
every room attaches its project(s) here (keyed by the room's own id) and
checks for a cached prior analysis before ever calling the LLM — a
repeat run of an unchanged project answers instantly from cache, and a
project that's drifted too much since that cache was made prompts the
client for a `/resync` instead of silently trusting or discarding it. A
room isn't limited to one project: `/project/add`/`/project/remove` (or
the TUI's `/add`/`/remove` commands) attach or detach additional projects
mid-conversation, each addressed by name via a tool call's optional
`project` argument (omit it for the room's own primary project) — see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md)'s routes table. Every bootstrap —
first-ever, cached, a confirmed resync, or a project add/remove — seeds
the agent with only a lightweight, one-line-per-file map spanning every
attached project, never every file's full signatures up front; the agent
escalates to a `describe(path, project=...)` tool for one file's actual
structure, and to `cat` for real source, only when the question actually
needs it. See [`docs/SESSIONS.md`](docs/SESSIONS.md) for the on-disk
layout, the metadata schema, the room-bootstrap integration, the
two-tier metadata design, and the invariants that keep it crash-safe and
race-free.

### Safety

Two restrictions are enforced in `core/guard.py`, so every tool inherits them:

- **The agent is confined to the room's attached project(s).** Paths are
  resolved before they're checked, so `..`, absolute paths, and symlinks
  pointing outside are all refused; a tool call's optional `project`
  argument picks which attached project a path is resolved against
  (defaulting to the room's primary project), and an unrecognized project
  name is refused rather than silently falling back. The confinement set
  itself is a contextvar, not a plain global — the server can run several
  rooms concurrently, each on different project(s), so the roots set
  inside one room's worker thread are invisible to every other room's
  (the same isolation `core/ask_context.py` uses for the `ask` tool).
- **Env files are unreadable.** `.env` and `.env.*` are filtered out of
  listings, excluded from the project map, and cannot be read or written — a
  single `cat .env` would otherwise put your API key into an LLM request and
  into a log.

`execute` is the exception worth understanding. Its working directory is pinned
to the project and obvious escapes are rejected, but a shell cannot be truly
contained by a static check — command substitution and interpreters offer ways
around it. Treat anything with shell access as able to read what you can read,
and drop `execute` from `AGENT_TOOLS` if that isn't acceptable.

### Logging

The server logs through the standard `logging` module
(`wire/app.py` configures it for the whole process) — every
request, route failure, and raw LLM request/response line. Set
`AGENT_VERBOSE=1` to also print the LLM request/response lines rather
than only logging them. This is separate from room persistence:
`rooms/{uuid}.json` (see [Architecture](#architecture)) is what actually
lets a conversation be resumed, and is written regardless of log
verbosity.

## Development

```bash
pip install ruff pytest pre-commit
pre-commit install

ruff check . && ruff format .
python -m pytest tests/ -v
```

The test suite covers the offline guards, the transport interface (a
second, non-WebSocket implementation proves the core doesn't care which
one it's talking to), the pipeline stage system (composition, reordering,
cancellation, error propagation), the observer bus, tool discovery
(including lifecycle hooks), module lifecycle orchestration, the hooks
system (registration, filter/notify dispatch, isolation, extra/
discovery), the server/protocol (a real `websockets` server and client
against a stub pipeline), and the TUI (headlessly, against that same
server) — it makes no real API calls. Graceful shutdown (a real
`agent-server` process receiving SIGTERM, with a real module's
`init()`/`start()`/`stop()` hooks firing in order) was verified manually,
since it needs a real OS process and isn't repeated as an automated test.

## License

MIT

# Agent

[![CI](https://github.com/arashrasoulzadeh/agent/actions/workflows/ci.yml/badge.svg)](https://github.com/arashrasoulzadeh/agent/actions/workflows/ci.yml)
[![Release](https://github.com/arashrasoulzadeh/agent/actions/workflows/release.yml/badge.svg)](https://github.com/arashrasoulzadeh/agent/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

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

## Contents

- [Install](#install)
  - [Providers](#providers)
  - [Notion tools (optional)](#notion-tools-optional)
- [Usage](#usage)
  - [Settings](#settings)
  - [Updating / uninstalling](#updating--uninstalling)
- [Releases](#releases)
- [Architecture](#architecture)
- [How it works](#how-it-works)
  - [Tools](#tools)
  - [Slash commands (`actions/`)](#slash-commands-actions)
  - [Service: rooms](#service-rooms)
  - [Server (wire)](#server-wire)
  - [CLI (ui): a server-driven UI](#cli-ui-a-server-driven-ui)
  - [Desktop: a second generic renderer](#desktop-a-second-generic-renderer)
  - [Components: the vocabulary CLI, server, and desktop all share](#components-the-vocabulary-cli-server-and-desktop-all-share)
  - [Agent-driven UI (`show_ui`)](#agent-driven-ui-show_ui)
  - [Hooks](#hooks)
  - [Sessions](#sessions)
  - [Safety](#safety)
  - [Logging](#logging)
- [Development](#development)
- [License](#license)

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
| `LLM_PROVIDER` | `gapgpt` | Which provider `get_llm()` builds a client for: `gapgpt`, `anthropic`, or `ollama`. |
| `GAPGPT_API_KEY` | — | **Required** when `LLM_PROVIDER=gapgpt`. API key (read by the server process). |
| `GAPGPT_BASE_URL` | `https://api.gapgpt.app/v1` | Any OpenAI-compatible endpoint. |
| `GAPGPT_MODEL` | `gpt-4o-mini` | Model to use. |
| `GAPGPT_TIMEOUT` | `60` | Per-request timeout, in seconds. |
| `ANTHROPIC_API_KEY` | — | **Required** when `LLM_PROVIDER=anthropic`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Model to use. |
| `ANTHROPIC_TIMEOUT` | `60` | Per-request timeout, in seconds. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Only read when `LLM_PROVIDER=ollama`. |
| `OLLAMA_MODEL` | — | **Required** when `LLM_PROVIDER=ollama` — whatever you've pulled locally. |
| `AGENT_WS_HOST` | `127.0.0.1` | Where the agent server listens, and where the CLI looks for one. |
| `AGENT_WS_PORT` | `8765` | Same, for the port. |
| `AGENT_VERBOSE` | unset | Also print (not just log) raw LLM request/response lines. |
| `NOTION_API_KEY` | unset | Optional. Enables the `notion_search`/`notion_read_page`/`notion_create_page`/`notion_append_text` tools. |

### Providers

Three providers are supported (`llm/providers/`): `gapgpt` (any
OpenAI-compatible endpoint — OpenAI itself, or a local Ollama server via its
OpenAI-compat surface, work here too), `anthropic`, and native `ollama`.
`LLM_PROVIDER` picks which one `get_llm()` builds; it's process-wide, so it
applies to every room. Run `agent providers` to see which provider is active
and which of its env vars are actually set (a static readout of `.env` and
`settings.json`, not a live API call — accurate only when run from the same
checkout/host as `agent-server`).

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
that created it is still open. Even the `agent` (no path) prompt itself
is server-supplied (`/session/prompt`, see `docs/PROTOCOL.md`) rather
than hardcoded in the client — `cli.py` presents it, it doesn't decide it.

Type follow-up questions into the input at the bottom. `exit`, `quit`, or `q`
ends the session. Scroll the transcript with arrow keys, PageUp/PageDown, or
the mouse wheel — it never scrolls your terminal itself. As you type, a
small `~N tokens` line under the input gives a rough, live estimate of
what you're about to send — a fast client-local approximation (~4
characters/token, never a real tokenizer or a server round trip), not an
exact count; both clients compute it with the identical formula
(`ui/app.py`'s `_estimate_tokens()`, `desktop/renderer.js`'s
`estimateTokens()`) so the number reads the same either way.

Prefer a native window over the terminal? `desktop/` is a cross-platform
Electron app that connects to the same running `agent-server` and renders
the identical server-driven UI — same commands, same `/settings` screen,
every feature the CLI has. See [`desktop/README.md`](desktop/README.md).

### Settings

Type `/settings` to open an in-TUI screen for everything in the env-var
table above except `AGENT_WS_HOST`/`AGENT_WS_PORT` (those configure the
connection this screen lives behind, so they aren't editable through it).
One row per setting; `Tab`/`Shift+Tab` or the `Up`/`Down` arrows move
between fields (the currently focused one is highlighted so it's never
ambiguous which field a keystroke goes to), `Enter` saves the focused
row, `Escape` closes the screen. The screen scrolls if it doesn't fit
your terminal:

```
/settings
```

Changes are read from and written to the server over the same WebSocket
protocol as everything else (`/settings/list`, `/settings/update` — see
`docs/PROTOCOL.md`) and persist to `settings.json` at the repo root
(gitignored, like `.env`), so they survive a server restart without
needing to hand-edit `.env`. `AGENT_VERBOSE` and `NOTION_API_KEY` take
effect immediately; `LLM_PROVIDER` and every provider-specific setting
(GapGPT/Anthropic/Ollama's model/base URL/timeout/API key) only affect
rooms created *after* the change — an already-open conversation's LLM
client was already built and isn't hot-swapped.
Secret fields (`GAPGPT_API_KEY`, `ANTHROPIC_API_KEY`, `NOTION_API_KEY`)
are never shown in cleartext: the screen shows a blank field you can
type a new value into (masked as you type), not the real stored value;
leaving one blank and pressing Enter is a no-op, so you can never
overwrite a key by accident.

### Updating / uninstalling

If you installed from a git checkout (`pip install -e .`, the [Install](#install)
instructions above), "update" means pulling the latest commit and
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

If you installed a tagged release's wheel instead (see [Releases](#releases)
below), update by installing the newer wheel over it (`pip install --upgrade
agent-X.Y.Z-py3-none-any.whl`) — `agent update`'s git-based flow assumes a
git checkout and isn't the right tool there.

## Releases

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`, which
builds and attaches to a GitHub Release:

- **The Python package** — `agent-X.Y.Z-py3-none-any.whl` and the
  matching `.tar.gz` sdist, built with [`build`](https://pypi.org/project/build/)
  from `pyproject.toml`. Not published to PyPI — download the wheel
  from the release and `pip install` it, or just use the git-checkout
  install from [Install](#install) above; both end up running the same
  code.
- **The desktop app** — native installers for all three platforms,
  built with [electron-builder](https://www.electron.build/) from
  `desktop/`: a `.dmg` and `.zip` for macOS (x64 + arm64), an NSIS
  `.exe` for Windows, and an `.AppImage` + `.deb` for Linux.

None of these are code-signed — that needs a paid certificate this repo
doesn't have configured — so macOS will show a Gatekeeper warning on
first launch (right-click → Open bypasses it) and Windows SmartScreen
may warn too. See [`CONTRIBUTING.md`](CONTRIBUTING.md#releasing) for
how to cut a release.

## Architecture

The project is organized by technical concern, one top-level package per
concern, rather than by framework layer. Each package takes what it
needs as injected constructor arguments instead of importing a concrete
implementation of another concern directly, so the dependency graph
stays a simple tree, not a web:

```
core/       shared, dependency-free kernel: path safety, the ask contextvar,
            the module-lifecycle contract, a generic dir-scan helper
llm/        the LLM client (get_llm), dispatching to one of llm/providers/
            (gapgpt, anthropic, ollama) by LLM_PROVIDER, plus a shared
            raw-IO logging callback
tool/       every capability the agent has, one file per tool, auto-discovered
actions/    every `/`-command the command popup offers, one file per action,
            auto-discovered the same way as tool/ — see Slash commands, below
agent/      the reasoning engine: ProjectPipeline, ProjectAnalyst, the
            Stage/Pipeline system, ContextSynthesizer — framework-free,
            takes its llm/tools/sink as constructor arguments
models/     pure data shapes threaded through agent/: ProjectContext, Turn
wire/       the WebSocket protocol: transport, routes, events, the accept loop
service/    use-case orchestration: Room, its persistence
ui/         the TUI: a thin WebSocket client
desktop/    the Electron app: a second thin WebSocket client, same protocol
components/ server-side only: the UI vocabulary spec.json defines, read
            by core/style.py (and, over the wire via /ui/spec, by every
            client — see below; ui/ and desktop/ never import this)
hooks/      lets files in extra/ hook into tool/ and agent/ without editing them
extra/      where you drop your own hook files (see Hooks, below)
```

```
  ui/       (TUI, thin client)      ─┐
                                       │   ws://127.0.0.1:8765
  desktop/  (Electron, thin client) ─┴──►  wire/ (standalone `agent-server`
                                              process), via a Transport
                                              (wire/transport/)
                                            → service.Room
                                            → agent.ProjectPipeline
                                            → rooms/{uuid}.json
```

Both clients are *generic* renderers of the exact same server-driven
`Node`/`UIOp` tree (see "CLI (ui)" and "Desktop" below) — neither has
any built-in knowledge of a screen, so a feature added once
server-side (`service/ui_builder.py`) shows up in both without either
client's own code changing. `components/` (below) is what keeps their
two renderers from drifting apart on the small pieces that *are*
client-local (style tokens, exit words, the spinner/connection-state
constants).

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
| `show_ui` | Present a structured, styled panel (text/markdown/list/facts/table blocks, plus optional one-click quick-reply buttons) instead of plain prose — routed through `core/ui_context.py` the same way `ask` is, and rendered identically by `ui/app.py` and `desktop/renderer.js` since both are generic renderers of the same component vocabulary this compiles into (`service/ui_builder.py`'s `agent_ui_node()`). See [Agent-driven UI](#agent-driven-ui-show_ui). |
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

### Slash commands (`actions/`)

Every `/`-command the popup offers — `/add`, `/remove`, `/projects`,
`/settings`, `/explain`, `/tldr` — is one `Action` (`core/action.py`) in
one file under `actions/`, auto-discovered by
`core/action_registry.py`'s `discover_actions()` (`ACTIONS_DIR = actions/`,
same `import_all()` pattern `tool/registry.py` uses for `AGENT_TOOLS`).
Dropping a new `action = Action(...)`-defining module into `actions/` is
enough to add a command to both clients — `service/ui_builder.py`'s
`command_list_node()` iterates `actions.ACTIONS` to build the popup, so
nothing about `ui/app.py` or `desktop/renderer.js` changes either.

Each `Action` has a `kind`, and `kind` decides everything about what
accepting it does:

| Kind | Behavior | Examples |
| --- | --- | --- |
| `"action"` | Runs server-side against a narrow `ActionContext`, then reports back (usually via `info`). | `/add`, `/remove`, `/projects` |
| `"ui"` | Same as `"action"`, but expected to push a modal or full-screen UI rather than just report text. | `/settings` |
| `"pre_prompt"` | **Never reaches the server.** Accepting it replaces the footer input's value outright with the action's own `text`, in place of the bare command — from there it's ordinary, backspace-deletable input text. | `/explain` → `"Explain step by step: "` |
| `"post_prompt"` | Same client-local expansion mechanic as `pre_prompt`; the two only differ in the *convention* for where the inserted text is meant to sit relative to what you type next. | `/tldr` → `" Answer in one short sentence."` |

```python
# actions/tldr.py
from core.action import Action

action = Action(
    name="/tldr",
    usage="/tldr",
    description="Answer in one short sentence",
    kind="post_prompt",
    text=" Answer in one short sentence.",
)
```

`"action"`/`"ui"` commands instead define an async `run(ctx, args)` and
pass it as `Action(..., run=_run)`:

```python
# actions/remove.py
from core.action import Action, ActionContext

async def _run(ctx: ActionContext, args: list[str]) -> None:
    if not args:
        await ctx.info("Usage: /remove <name>")
        return
    await ctx.remove_project(args[0])

action = Action(name="/remove", usage="/remove <name>",
                 description="Detach a project", kind="action", run=_run)
```

`ActionContext` is a `Protocol` (`add_project`, `remove_project`,
`show_settings`, `show_panel`, `info`, `project_list`) — deliberately
narrow so `actions/` never needs to import `service/` or `wire/`
(`wire/routes.py`'s `_RouteActionContext` is the concrete adapter that
implements it; `make deps-check` enforces the boundary, same as
`agent/`'s and `workspace/`'s). Both clients apply the `pre_prompt`/
`post_prompt` expansion locally and identically —
`ui/app.py`'s `_apply_popup_selection()`, `desktop/renderer.js`'s
`applyPopupSelection()` — since it's client-typing-area state, not
room state, exactly like the connection-status/spinner/token-hint
pieces `docs/PROTOCOL.md` already calls out as client-local.

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

### CLI (ui): a server-driven UI

`ui/app.py` is a single full-screen
[Textual](https://github.com/Textualize/textual) app, and it's
*generic*: it has no built-in knowledge of any screen — no header
layout, no modal shapes, no command list. Everything drawable is a
`Node` (`models/ui.py`) built server-side by `service/ui_builder.py`
and delivered as a component tree, not as data for the client to
interpret and lay out itself. That's also what makes the fixed
header/content/footer layout and internal scrolling possible in the
first place: Rich's `Console`/`Live` only ever print into the
terminal's own scrollback, but Textual redraws the whole screen as a
bounded region and reflows it on resize.

`ui/app.py` mounts the full tree it gets back from `/session/create`/
`/session/resume` once, then applies each `ui.update` event's ops
(`replace`/`append`/`remove`) as they arrive — `AgentApp._build()`
turns a `Node` into a widget (`"container"` → `Vertical`/`Horizontal`,
`"text"` → `Static`, `"input"` → `Input`, `"button"` → `Button`,
`"list"` → an `OptionList` or a scrolling `Vertical`, depending on
`props.kind`), and `AgentApp.apply_ops()` walks the three op kinds.
Every click, submit, or selection becomes one `/ui/event` request; the
server decides what it means and pushes back whatever ops follow. See
[`docs/PROTOCOL.md`](docs/PROTOCOL.md)'s "UI component protocol"
section for the full schema, id-naming convention, and the two things
that stay client-local (connection status, the spinner glyph) because
neither is server-owned room state.

The receive loop is a plain asyncio task on Textual's own event loop —
no `call_from_thread` anywhere in `ui/`, since the boundary is the
network now, not a Python thread; incoming `ui.update` ops are applied
through a small internal queue so a fast turn's events can never be
applied out of order or dropped ahead of the initial tree mounting.
`tests/test_app.py` and `tests/test_server.py` drive a real server and
a real client against each other (`tests/stubs.py`'s pipeline never
touches the network), so the whole suite never spends a real API token.

### Desktop: a second generic renderer

`desktop/` is a cross-platform (macOS/Windows/Linux) Electron app that
plays the exact same role `ui/app.py` does, just DOM-based instead of
Textual-based: it's *also* generic (no header layout, no modal shapes,
no command list baked in), connects to the same `agent-server` over the
same protocol, mounts the same `tree`, and applies the same `ui.update`
ops (`desktop/renderer.js`'s `build()`/`replaceNode()`/`appendNode()`/
`removeNode()` mirror `ui/app.py`'s `_build()`/`_replace()`/`_append()`/
`_remove()` step for step). No framework, no bundler — plain DOM APIs
loaded as a classic script, so it stays close to Electron's own startup
and interaction-latency floor. See [`desktop/README.md`](desktop/README.md)
for how to run it.

Because both clients are generic renderers of the same server-built
tree, **a UI feature only needs one server-side change**
(`service/ui_builder.py`) to reach both — the only place a feature adds
client-side code in *both* `ui/app.py` and `desktop/renderer.js` is if
it touches one of the handful of things that are deliberately
client-local in both (connection status, the spinner glyph's animation,
command-popup filtering, `exit`/`quit`/`q` interception — see
docs/PROTOCOL.md's "UI component protocol" section). Add a node type,
style token, or client-local constant to `components/` (below) once,
and both pick it up.

### Components: the vocabulary CLI, server, and desktop all share

`components/spec.json` is the one file that defines every piece of the
UI vocabulary that isn't room state: the semantic style tokens
`core/style.py` re-exports (`THINK`, `TOOL`, `MESSAGE`, ...), the Rich
color names a DOM renderer has to translate to CSS (`ui/app.py` gets
this for free from Rich; `desktop/` doesn't), `exit`/`quit`/`q`, the
reply placeholders, the spinner frames, and the three connection-state
labels.

**Server-side only** — no client bundles a copy of `spec.json`.
`components/__init__.py` is Python's reader of it, and it's imported by
exactly one thing that's genuinely server-side: `core/style.py` (used by
`service/ui_builder.py` to build the literal style strings that end up
*inside* every Node's props, which is how the value actually reaches a
client — as ordinary room-state data, the same way everything else in a
Node does). Neither `ui/app.py` nor `desktop/renderer.js` ever imports
`components` or reads `spec.json` directly; both fetch it fresh over the
wire instead, as the very first request a generic renderer makes —
`/ui/spec` (`wire/routes.py`'s `ui_spec()`, see `docs/PROTOCOL.md`) —
before `/session/prompt`, `/rooms/list`, or anything else. That's the
concrete payoff of "one spec, one place, served, not shipped": add a
style token or change an exit word in `spec.json` and every connected
client picks it up on its next connect, with no client code change, no
client rebuild, and no client redeploy — the same principle that, later,
lets the agent itself introduce new client-local UI concepts purely by
changing what the server sends, never by shipping new client code.

`components/js/richStyle.js` is the one piece of this that *is* still
client-side code — necessarily: turning a Rich style string into CSS
needs a parser, and there's no way to "fetch" the ability to parse a
string. But the *data* it interprets against (the Rich color table)
comes from `/ui/spec`'s response via `setRichColors()`, not from a
bundled file — `desktop/preload.js` exposes the parser function, never
spec data, to `desktop/renderer.js`.

### Agent-driven UI (`show_ui`)

The payoff of everything above: `tool/ui.py`'s `show_ui` tool lets the
*agent itself* decide to draw a bordered panel — not just write prose —
and it renders identically on both clients. The LLM never sees a
`Node`; it calls `show_ui` with a small, forgiving vocabulary of block
kinds (`text`, `markdown`, `list`, `facts`, `table`) plus an optional
`title` and up to 6 `quick_replies`, and `service/ui_builder.py`'s
`agent_ui_node()` compiles that into a bordered/titled `container` Node
(`props.panel`, `panel_title`, `border_style`) holding one child per
block plus a row of quick-reply buttons.

Two genuinely new pieces make that panel richer than the rest of the
app's usual monospace-text furniture, both deliberate uses of "change
the struct as much as you want":

- `props.panel` now works on a `container` node, not just a `text`
  node — `ui/app.py`'s `_build()` sets the *widget's own* Textual
  border/`border_title` (a different color vocabulary than Rich's own
  `Panel`, hence `_textual_color()`'s small translation table there),
  and `desktop/renderer.js`'s `build()` reuses its existing text-panel
  CSS classes (`applyPanelChrome()`) on a container instead.
- `"table"` is a real sixth `Node` type (`props.headers`/`rows`), not
  formatted text — `ui/app.py` builds a genuine `rich.table.Table`,
  `desktop/renderer.js` a real `<table>`. `list` and `facts` blocks stay
  text (styled `spans` — colored bullets, bold-accent labels — rather
  than a new node type each), since a bullet list or a label/value pair
  doesn't need a grid the way tabular data does.

Everything else — routing the tool call to whichever room is running it
without a dependency cycle (`core/ui_context.py`, mirroring
`core/ask_context.py`'s own contextvar pattern exactly), generating and
persisting each quick-reply button's id (`Room.show_ui()`,
`service/rooms.py`), and resolving a click back to a prompt
(`wire/routes.py`'s `/ui/event` dispatch, a `quick-` id prefix alongside
the existing `opt-`/`setting-` ones) — is just this project's existing
patterns applied once more, not new mechanisms.

A quick-reply click submits its label as an ordinary `/prompt`, exactly
as if typed and sent — and unlike an `ask()` question's option buttons
(cleared the moment it's answered), a `show_ui` quick-reply stays
clickable for as long as it's visible in the transcript's own
scrollback, even turns later.

**What the user sees and what the agent gets are deliberately split.**
The chat bubble a clicked quick-reply produces is exactly the button's
own label — indistinguishable from having typed and sent that text —
but the agent's *next turn* receives more: `Room.run_prompt()` takes an
optional `llm_text` that, when given, is what actually reaches the
agent while `text` is still what's shown and persisted. `_dispatch_quick_reply`
(`wire/routes.py`) builds that `llm_text` from the originating panel's
title and a compact one-line synopsis of its blocks
(`service/ui_builder.py`'s `summarize_blocks()`) — e.g. `Regarding the
panel titled "Comparison" (npm vs pnpm...), the user chose: "Use npm"`
— so a bare, ambiguous label like "Use npm" always arrives with the
context of which panel it answered, without cluttering the transcript
the human actually reads.

Every panel's own title also gets a `✦` prefix (`agent_ui_node()`), the
one visual cue distinguishing agent-built structured content from a
plain answer panel (grey border, no title) or an error panel (red,
titled "error") at a glance, since all three share the exact same
underlying primitive. A brand-new room additionally gets a one-time
"Tip: I can also show interactive panels..." line right after its first
bootstrap answer (`Room._show_capability_tip()`) — the one place someone
who's never used this feature would otherwise have no way to discover
it exists; it's an ephemeral "info" entry, so it never repeats on a
later `/session/resume`.

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
request, route failure, raw LLM request/response line, and outgoing
`ui.update` op (`service.rooms ui.update replace header (type=container)`,
`... append content (type=text)`, etc. — what's actually being
created/replaced/removed on a client's screen, and when). All of this
runs unconditionally, independent of `AGENT_VERBOSE`. Set
`AGENT_VERBOSE=1` to also print the LLM request/response lines rather
than only logging them. Those `AGENT_VERBOSE` print lines are always
flushed immediately, so they still show up promptly when stdout isn't a
real terminal — piped through `make server`, redirected to a file, run
under a process manager — where Python would otherwise block-buffer
them. This is separate from room persistence:
`rooms/{uuid}.json` (see [Architecture](#architecture)) is what actually
lets a conversation be resumed, and is written regardless of log
verbosity.

## Development

```bash
make install       # .venv + pip install -e . + ruff/pytest/pre-commit
make pre-commit     # installs the git hook (ruff check + format on commit)
make check          # lint + compile + deps-check + full test suite — same gates CI runs
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full dev-setup, testing,
and release process, [`docs/README.md`](docs/README.md) for the deeper
per-subsystem docs (protocol, tools, hooks, sessions), and
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) for exactly what
runs on every push and PR.

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

[MIT](LICENSE)

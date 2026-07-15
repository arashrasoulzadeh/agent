# Agent

An interactive CLI agent that reads a codebase and answers questions about it,
in a full-screen terminal UI.

Point it at a project and it builds a private map of the source, reads the
files that actually matter, and then holds a conversation about them —
remembering what it has already learned as you ask follow-ups.

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
│ project ~/code/my-service   log agent.log                     │
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

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the example env file and add your key:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `GAPGPT_API_KEY` | — | **Required.** API key. |
| `GAPGPT_BASE_URL` | `https://api.gapgpt.app/v1` | Any OpenAI-compatible endpoint. |
| `GAPGPT_MODEL` | `gpt-4o-mini` | Model to use. |
| `GAPGPT_TIMEOUT` | `60` | Per-request timeout, in seconds. |
| `AGENT_LOG` | `agent.log` | Where the session log is written. |

The backend is any OpenAI-compatible API, so `GAPGPT_BASE_URL` can point at
OpenAI, a local Ollama server, or anything else that speaks the same protocol.

## Usage

```bash
python main.py .                # analyze the current directory
python main.py ~/code/project   # analyze somewhere else
python main.py                  # prompts for a path
```

Type follow-up questions into the input at the bottom. `exit`, `quit`, or `q`
ends the session. Scroll the transcript with arrow keys, PageUp/PageDown, or
the mouse wheel — it never scrolls your terminal itself.

## How it works

Three separated stages, wired together by `ProjectPipeline`:

| Stage | Does |
| --- | --- |
| `ContextCollector` | Walks the project and produces a private structural map. No LLM. |
| `ProjectAnalyst` | Holds the conversation. Reads files with tools and answers. |
| `ContextSynthesizer` | Optional: compresses an answer into machine-readable context for another agent. |

Each stage is swappable, and `PipelineConfig` decides which run:

```python
from pipeline import PipelineConfig, ProjectPipeline

pipeline = ProjectPipeline(
    config=PipelineConfig(synthesize=True, synthesis_format="json")
)
pipeline.collect_context("~/code/project")
print(pipeline.run("What kind of project is this?"))
```

### Modules

Every capability the agent has lives in `modules/`, one file per tool, and
every call is printed live as it happens. Dropping a new `@tool`-decorated
function into `modules/` is enough to add a capability — `core/registry.py`
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
| `ask` | Put a question back to *you* when the project can't settle it. |

`delete` and `metadata` exist in `modules/` but set `AGENT_TOOL = False` at
module level, which keeps them out of `AGENT_TOOLS` while leaving them
directly importable (`from modules import delete`). `delete` is opt-out
because deletion is irreversible; `metadata` is the collector's internal
preprocessing tool. Set `AGENT_TOOL = True` (or drop the flag) on a module
to include it by default.

### UI

The CLI is a single full-screen [Textual](https://github.com/Textualize/textual)
app (`ui/app.py`), not a sequence of printed lines — that's what makes the
fixed header/content/footer layout and internal scrolling possible in the
first place. Rich's `Console`/`Live` only ever print into the terminal's own
scrollback; Textual redraws the whole screen as a bounded region and reflows
it on resize.

| Module | Does |
| --- | --- |
| `app.py` | The `AgentApp`: layout, the header/content/footer widgets, input routing, and the worker thread that runs the (blocking, network-calling) pipeline without freezing the UI. |
| `trace.py` | Tool-call/tool-result trace lines, the token count, and the currently-active tool highlight, pushed into the header and content log. |
| `prompts.py` | Routes the agent's own `ask` tool question through the footer input, blocking the worker thread until it's answered. |
| `answer.py` | Appends the agent's final answer, as markdown, to the content log. |
| `error.py` | A friendly, plain-English line instead of a raw traceback. |
| `engine.py` | File logging shared by all of the above. |
| `state.py` | Holds the one running `AgentApp` instance so the modules above can reach it without importing `app.py` directly. |

The pipeline's blocking calls run via `self.run_worker(..., thread=True)`; any
widget mutation from that thread goes through `self.call_from_thread(...)`.
`tests/test_app.py` drives the app headlessly (Textual's `run_test()`/`Pilot`)
against a stub pipeline, so it never spends real API tokens.

### Safety

Two restrictions are enforced in `core/guard.py`, so every tool inherits them:

- **The agent is confined to the project folder.** Paths are resolved before
  they're checked, so `..`, absolute paths, and symlinks pointing outside are
  all refused.
- **Env files are unreadable.** `.env` and `.env.*` are filtered out of
  listings, excluded from the project map, and cannot be read or written — a
  single `cat .env` would otherwise put your API key into an LLM request and
  into the log.

`execute` is the exception worth understanding. Its working directory is pinned
to the project and obvious escapes are rejected, but a shell cannot be truly
contained by a static check — command substitution and interpreters offer ways
around it. Treat anything with shell access as able to read what you can read,
and drop `execute` from `AGENT_TOOLS` if that isn't acceptable.

### Logging

Every event — tool calls, results, questions, answers, errors — is mirrored,
without color codes, to `agent.log`, regardless of whether it's shown in the
app. Raw LLM requests/responses are always logged but only appended to the
content log when you pass `-v`/`--verbose`.

## Development

```bash
pip install ruff pytest pre-commit
pre-commit install

ruff check . && ruff format .
python -m pytest tests/ -v
```

The test suite covers the offline guards and the TUI (headlessly, against a
stub pipeline) — it makes no real API calls.

## License

MIT

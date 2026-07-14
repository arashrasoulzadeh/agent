# Agent

An interactive CLI agent that reads a codebase and answers questions about it.

Point it at a project and it builds a private map of the source, reads the files
that actually matter, and then holds a conversation about them — remembering
what it has already learned as you ask follow-ups.

```
$ python main.py ~/code/my-service

  → cat(path='go.mod')
  → cat(path='README.md')
  ← module github.com/acme/my-service ...

This is a Go microservice for party and discount management. It exposes two
binaries — `cmd/santa` (the API) and `cmd/blitzen` (background jobs) — and
persists to Postgres, with Redis for caching and RabbitMQ for events.

Ask follow-up questions about the project ('exit' to quit).

> which package handles reservations?
```

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
| `NO_COLOR` | — | Set to disable colored output. |

The backend is any OpenAI-compatible API, so `GAPGPT_BASE_URL` can point at
OpenAI, a local Ollama server, or anything else that speaks the same protocol.

## Usage

```bash
python main.py .                # analyze the current directory
python main.py ~/code/project   # analyze somewhere else
python main.py                  # prompts for a path
```

Then ask questions until you're done. `exit`, `quit`, `q`, or Ctrl-D ends the
session.

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

### Tools

The agent works by calling tools, and every call is printed as it happens.

| Tool | Does |
| --- | --- |
| `ls` | List a directory. |
| `cat` | Read a file. |
| `write` | Create or overwrite a file. |
| `edit` | Replace the contents of an existing file. |
| `create_directory` | Create a directory. |
| `execute` | Run a shell command in the project. |
| `ask` | Put a question back to *you* when the project can't settle it. |

`delete` exists but is deliberately **not** registered — deletion is
irreversible, so it is opt-in. Add it to `AGENT_TOOLS` if you want it.

### Safety

Two restrictions are enforced in `tools/guard.py`, so every tool inherits them:

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

Every event is color-coded on the terminal and mirrored, without color codes,
to `agent.log`.

| Channel | Color | Carries |
| --- | --- | --- |
| `think` | gray | Tool calls and their results |
| `message` | cyan | A question going to the agent |
| `output` | green | The agent's answer |
| `request` | blue | Raw LLM request |
| `response` | magenta | Raw LLM response |
| `question` | yellow | The agent asking you something |

## Development

```bash
pip install ruff pytest pre-commit
pre-commit install

ruff check . && ruff format .
python -m pytest tests/ -v
```

The test suite covers the offline guards and makes no API calls.

## License

MIT

# Contributing

## Setup

```bash
make install       # creates .venv, pip install -e . plus ruff/pytest/pre-commit
make pre-commit     # installs the git hook (ruff check + format on commit)
```

For the desktop client too:

```bash
make desktop-install   # cd desktop && npm install
```

## Before opening a PR

```bash
make check
```

This runs the same four gates CI runs, in order: `ruff check` (lint),
`compile` (byte-compiles every tracked `.py` file — a fast syntax-error
net), `deps-check` (the dependency-direction boundaries — see below),
and the full pytest suite. Run `make lint-fix && make format` first if
`ruff` complains about anything auto-fixable.

If you touched anything under `desktop/` or `components/js/`, also run:

```bash
node --test desktop/markdown.test.js components/js/richStyle.test.js
node --check desktop/main.js desktop/preload.js desktop/renderer.js
```

`.github/workflows/ci.yml` runs all of the above (lint, the Python
matrix across 3.11–3.13, `deps-check`, and the JS suite) on every push
and PR against `main` — a green `make check` locally should mean a
green CI run.

## Dependency-direction boundaries

`make deps-check` enforces three rules the architecture depends on
(see the root [README](README.md#architecture)):

- `agent/` never imports `tool/`, `llm/`, or `workspace/` — the
  reasoning engine takes its tools/LLM as constructor arguments, it
  never builds them itself.
- `workspace/` never imports `tool/` or `service/`.
- `actions/` never imports `service/` or `wire/` — same reasoning as
  `tool/`: it stays a `core/`-only leaf so a slash-command's `run()`
  only ever sees the narrow `ActionContext` protocol, not the whole
  server.

Import in the direction the check expects (through an injected
constructor argument or a narrow `Protocol`, not a direct import) if
you're adding a new cross-package call.

## Adding a capability

- **A new tool** the agent can call: drop a `@tool`-decorated function
  into `tool/` — auto-discovered, no registration step. See
  [`docs/MODULES.md`](docs/MODULES.md) for the full contract (including
  the optional `MODULE` lifecycle hook for anything with setup/teardown
  state).
- **A new `/`-command**: drop an `Action(...)`-defining module into
  `actions/` — see the root README's
  [Slash commands](README.md#slash-commands-actions) section for the
  four `kind`s and two worked examples.
- **A hook that rewrites text or adds a tool from outside the repo**:
  see [`docs/HOOKS.md`](docs/HOOKS.md).
- **A UI feature**: change `service/ui_builder.py` once — both
  `ui/app.py` (CLI) and `desktop/renderer.js` (desktop) are generic
  renderers of the same server-built tree, so neither needs its own
  change unless you're touching one of the handful of things that are
  deliberately client-local (see
  [`docs/PROTOCOL.md`](docs/PROTOCOL.md)'s "UI component protocol"
  section). If you do touch one of those, change both clients
  identically in the same PR — see [README](README.md) for why the two
  are meant to be functionally interchangeable, not merely similar.

## Tests

No mocked LLM calls, ever — `tests/stubs.py`'s pipeline stands in for
a real one, so the whole suite runs offline and spends no API tokens.
Write tests that assert on real behavior through the actual protocol
(a real `websockets` server + client, a real headless Textual app)
rather than on internal call counts. See `tests/test_action.py` or
`tests/test_ui_builder.py` for the current shape of a well-scoped unit
test in this codebase, and `tests/test_app.py`/`tests/test_server.py`
for end-to-end examples.

## Releasing

Releases are tag-triggered (`.github/workflows/release.yml`) — pushing
a `vX.Y.Z` tag builds the Python sdist/wheel and the desktop app's
macOS/Windows/Linux installers, then attaches all of them to a GitHub
Release for that tag. Two version numbers have to agree with the tag
before it'll build anything:

1. `pyproject.toml`'s `[project].version`
2. `desktop/package.json`'s `version`

Bump both to the same new version, commit, then:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow fails fast (before building anything) if either file's
version doesn't match the tag — that's on purpose, so a forgotten
version bump never ships a release mislabeled with the wrong version.

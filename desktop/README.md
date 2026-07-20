# Agent Desktop

A cross-platform (macOS/Windows/Linux) desktop client for the agent
server — the same server-driven UI `ui/app.py` renders as a terminal
app, rendered here as a native window instead. Connects to the same
`agent-server` over the same WebSocket protocol (see
[`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)), so anything you can do in
the CLI (ask questions, answer a mid-turn question, `/add`/`/remove`
projects, `/settings`) you can do here too — and any new feature added
server-side (`service/ui_builder.py`) shows up in both automatically,
since neither client has any built-in knowledge of a screen; both are
generic renderers of the same `Node`/`UIOp` tree.

No framework, no bundler — plain Electron + vanilla DOM APIs, so
startup and interaction latency stay close to Electron's own floor.
`components/js/` (see [`../components/`](../components/)) is the one
piece shared with the CLI/server side: the UI vocabulary (style tokens,
exit words, the spinner/connection-state constants) both sides read
from the same `components/spec.json`.

## Run

Start the agent server first, same as for the CLI:

```bash
agent-server                    # or: python -m wire, from the repo root
```

Then, in `desktop/`:

```bash
npm install     # once
npm start
```

`AGENT_WS_HOST`/`AGENT_WS_PORT` (same env vars `wire/config.py` reads)
control where it connects — default `127.0.0.1:8765`.

On launch it checks the server is reachable, then shows a start screen:
enter (or browse to) a project path to open, or resume one of your
saved rooms (`rooms/*.json`) from the list. From there it's the same
header/content/footer layout as the CLI — type a follow-up at the
bottom, `/settings` for the settings screen, `exit`/`quit`/`q` to close.

## Layout

```
main.js       Electron main process — one window, a folder-picker IPC
              handler, and safe external-link opening. Never talks to
              the agent server itself.
preload.js    contextBridge: exposes components/js/'s shared vocabulary,
              AGENT_WS_HOST/PORT, and the folder picker to the renderer.
              (sandbox: false — see main.js's comment for why.)
index.html    The page shell: a start screen, the mount point for the
              server's root tree, and a reserved modal overlay.
renderer.js   The generic renderer itself — connects, requests/applies
              ui.update ops, forwards clicks/submits to /ui/event.
              Read alongside ui/app.py when adding a feature to both.
styles.css    Theme-aware (light/dark via prefers-color-scheme), no
              build step.
```

## Packaging

There's no packaged installer yet (`npm start` runs straight from
source, same "no packaged release" state the root README describes for
the CLI). `electron-builder`/`electron-forge` would be the next step
when that's actually needed.

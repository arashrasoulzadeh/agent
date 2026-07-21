# Docs

Deep-dive references for extending or integrating with the agent. Start
at the root [README](../README.md) for the overview, architecture, and
how the pieces fit together — these documents assume that context and
go one level deeper into a single subsystem each.

| Doc | Covers |
| --- | --- |
| [PROTOCOL.md](PROTOCOL.md) | The full WebSocket wire protocol: every route and event, the `Node`/`UIOp` component-tree shape a client renders, id-naming conventions, what stays client-local vs. server-owned, and the room persistence format. Read this before writing a new client or touching `wire/`/`service/ui_builder.py`. |
| [MODULES.md](MODULES.md) | How to write a tool: the `@tool` contract, auto-discovery, the optional `MODULE` lifecycle hook (`init`/`start`/`stop`) for a tool with setup/teardown state, and a full worked example. Read this before adding anything to `tool/`. |
| [HOOKS.md](HOOKS.md) | How to write a hook: the `@hook(...)` contract, the two flavors (`filter` vs `notify`), the full hook-point catalog, and isolation guarantees (one broken hook never breaks another's turn). Read this before adding a file to `extra/`. |
| [SESSIONS.md](SESSIONS.md) | The on-disk session/project-metadata layout `workspace/` maintains, the two-tier context design (a lightweight always-on map vs. on-demand file signatures), the room-bootstrap integration, and the crash-safety/race-freedom invariants. Read this before touching `workspace/` or the room-bootstrap path in `service/rooms.py`. |

See also [CONTRIBUTING.md](../CONTRIBUTING.md) for the dev-setup,
testing, and release process, and the root README's
[Architecture](../README.md#architecture) section for how these
subsystems relate to each other.

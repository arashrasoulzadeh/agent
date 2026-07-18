"""Command-line interface: a thin WebSocket client.

Connects to an agent server that is already running as its own process
(`agent-server`, or `python -m wire`). If nothing answers on the
configured host:port, it prints how to start one and exits — it never
spawns the server itself, so the server's lifecycle stays independent of
any client.

Once connected, it hands off to the full-screen TUI (ui/app.py), which
creates or resumes a room and renders whatever the server reports.

`agent update` / `agent uninstall` are a different mode entirely — self-
management, not a project session — dispatched to self_update.py before
any of the argument parsing below runs. Reserving those two words means
a project literally named "update" or "uninstall" can't be opened as
`agent update`/`agent uninstall` (it still works via `agent ./update`
or `agent --room <id>`); an acceptable trade for not needing a
subcommand framework here.
"""

import argparse
import asyncio
import sys

from ui.app import AgentApp
from wire import discovery
from wire.config import HOST, PORT


def main(argv: list[str] | None = None) -> None:
    raw = list(sys.argv[1:]) if argv is None else list(argv)
    if raw[:1] == ["update"]:
        import self_update

        sys.exit(self_update.run_update(raw[1:]))
    if raw[:1] == ["uninstall"]:
        import self_update

        sys.exit(self_update.run_uninstall(raw[1:]))

    parser = argparse.ArgumentParser(
        prog="agent",
        description="Ask questions about a codebase.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help=(
            "Project to analyze. Prompts for one if omitted. Ignored with "
            "--room. ('update'/'uninstall' are reserved; see `agent update "
            "--help`.)"
        ),
    )
    parser.add_argument(
        "--room",
        help="Resume a previous session (see rooms/) instead of starting a new one.",
    )
    parser.add_argument(
        "--host", default=HOST, help=f"Agent server host (default: {HOST})."
    )
    parser.add_argument(
        "--port", type=int, default=PORT, help=f"Agent server port (default: {PORT})."
    )
    args = parser.parse_args(argv)

    path = args.path
    if not args.room and not path:
        # This happens before the TUI takes the screen, so a plain
        # blocking prompt is fine here.
        path = input("Project path [.]: ").strip() or "."

    try:
        asyncio.run(discovery.require_running(args.host, args.port))
    except discovery.ServerNotRunning as exc:
        sys.exit(str(exc))

    server_url = f"ws://{args.host}:{args.port}"
    AgentApp(server_url, path or ".", room=args.room).run()


if __name__ == "__main__":
    main()

"""Run the server standalone: the `agent-server` console script, or
`python -m wire` directly.

A normal, freestanding process: nothing about it depends on being
started by any particular client. `cli.py` just checks whether one is
already listening and tells you to run this if not (see
wire/discovery.py) — the server's lifecycle is never tied to a client's,
which is what lets it later be wrapped as a systemd/launchd/Windows
service pointed at the `agent-server` executable.

Shuts down gracefully on SIGINT/SIGTERM (wire/app.py's serve() handles
that and runs every module's stop() hook); KeyboardInterrupt here is
only a fallback for platforms where signal handlers aren't supported.
"""

import asyncio

from dotenv import load_dotenv

from wire.app import serve


def main() -> None:
    load_dotenv()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

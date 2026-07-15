"""Run the server standalone: `python -m server`.

A normal, freestanding process: nothing about it depends on being
started by any particular client. `cli.py` just checks whether one is
already listening and tells you to run this if not (see
server/discovery.py) — the server's lifecycle is never tied to a client's.

Shuts down gracefully on SIGINT/SIGTERM (server/app.py's serve() handles
that and runs every module's stop() hook); KeyboardInterrupt here is only
a fallback for platforms where signal handlers aren't supported.
"""

import asyncio

from dotenv import load_dotenv

from server.app import serve

if __name__ == "__main__":
    load_dotenv()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass

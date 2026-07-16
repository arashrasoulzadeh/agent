"""Is a server already listening? The server is a separate process, run
on its own (`agent-server`, or `python -m wire`); a client just needs the
same host:port to attach to the exact same rooms. Clients only check —
they never start it, so the server's lifecycle is never tied to any one
client.
"""

import websockets

from wire.config import HOST, PORT


class ServerNotRunning(RuntimeError):
    """No server is listening on the given host:port."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        super().__init__(
            f"No agent server is listening on ws://{host}:{port}.\n"
            f"Start one in another terminal first:\n\n"
            f"    agent-server\n"
        )


async def is_running(host: str = HOST, port: int = PORT) -> bool:
    try:
        async with websockets.connect(f"ws://{host}:{port}", open_timeout=1):
            return True
    except OSError:
        return False


async def require_running(host: str = HOST, port: int = PORT) -> None:
    """Raise ServerNotRunning unless a server is already up. Never spawns."""
    if not await is_running(host, port):
        raise ServerNotRunning(host, port)

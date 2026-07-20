"""Is a server already listening? The server is a separate process, run
on its own (`agent-server`, or `python -m wire`); a client just needs the
same host:port to attach to the exact same rooms. Clients only check —
they never start it, so the server's lifecycle is never tied to any one
client.
"""

import json
import uuid

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


async def fetch_session_prompt(host: str = HOST, port: int = PORT) -> dict:
    """The {"text", "default"} for cli.py's startup path prompt —
    server-owned copy (wire/routes.py's session_prompt), not hardcoded
    client-side. Raises ServerNotRunning on the same connection failure
    require_running() checks for.
    """
    try:
        async with websockets.connect(f"ws://{host}:{port}", open_timeout=1) as ws:
            request_id = str(uuid.uuid4())
            await ws.send(
                json.dumps({"id": request_id, "route": "/session/prompt", "data": {}})
            )
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == request_id:
                    if not msg["ok"]:
                        raise RuntimeError(msg["error"])
                    return msg["data"]
    except OSError:
        raise ServerNotRunning(host, port) from None

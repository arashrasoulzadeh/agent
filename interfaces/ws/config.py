"""Where the server listens — the one thing both the server and a thin
client (cli.py) need, without a client having to import the rest of the
server package (routes, rooms, the pipeline, langchain, ...) just to find
out the default port.
"""

import os

HOST = os.getenv("AGENT_WS_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_WS_PORT", "8765"))

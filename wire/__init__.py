"""The agent's WebSocket server.

Every feature the agent has is served through one WebSocket connection per
client: requests use a `route` (e.g. `/prompt`), pushes use an `event`
(e.g. `tool.call`). See docs/PROTOCOL.md for the full catalog.

    protocol.py   the wire envelope: requests, responses, events
    routes.py     client -> server requests (a room's actions)
    events.py     server -> client pushes (kept separate from routes, so
                  "things a client can ask for" and "things the server
                  reports" don't tangle into one file)
    app.py        the actual server loop: accepts connections, dispatches
                  requests to routes.py, keeps clients subscribed to rooms
    discovery.py  is a server already listening? `cli.py` only checks —
                  it never spawns one itself.

`Room` — one project session, its pipeline, and its persistence to
rooms/{uuid}.json — lives in service/rooms.py, not here: it's the
use-case layer this package's routes/events talk to, not part of the
delivery mechanism itself.

Run standalone with the `agent-server` console script, or
`python -m wire` directly.
"""

"""The agent's WebSocket server.

Every feature the agent has is served through one WebSocket connection per
client: requests use a `route` (e.g. `/prompt`), pushes use an `event`
(e.g. `tool.call`). See docs/PROTOCOL.md for the full catalog.

    protocol.py   the wire envelope: requests, responses, events
    routes.py     client -> server requests (a room's actions)
    events.py     server -> client pushes (kept separate from routes, so
                  "things a client can ask for" and "things the server
                  reports" don't tangle into one file)
    rooms.py      Room: one project session, its pipeline, and its
                  persistence to rooms/{uuid}.json
    app.py        the actual server loop: accepts connections, dispatches
                  requests to routes.py, keeps clients subscribed to rooms
    discovery.py  is a server already listening? spawn one if not.

Run standalone with `python -m server`; `cli.py` spawns it automatically
if nothing is listening on the configured host:port yet.
"""

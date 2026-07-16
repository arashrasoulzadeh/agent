"""Tests for the wire/ package: a real websockets server and a real
websockets client, talking the actual wire protocol (see docs/PROTOCOL.md)
end to end. The pipeline is always a stub (tests/stubs.py) — this suite
never touches the network or spends a real API token.
"""

import asyncio
import json
import unittest
import uuid

import websockets

from service import rooms
from tests.stubs import (
    AskToolPipeline,
    FailingPipeline,
    StubPipeline,
    ToolCallingPipeline,
    running_server,
)


async def recv_until(ws, predicate, timeout=5):
    """Read messages until one matches, discarding the rest. Callers must
    know the emission order well enough that what they skip past is
    never something they'll need to check later."""
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if predicate(msg):
                return msg


async def send_request(ws, route, data=None, room=None):
    request_id = str(uuid.uuid4())
    payload = {"id": request_id, "route": route, "data": data or {}}
    if room is not None:
        payload["room"] = room
    await ws.send(json.dumps(payload))
    resp = await recv_until(ws, lambda m: m.get("id") == request_id)
    if not resp["ok"]:
        raise AssertionError(resp["error"])
    return resp["data"]


class TestSessionLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_create_bootstraps_and_broadcasts_events(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            self.assertTrue(room_id)

            answer = await recv_until(ws, lambda m: m.get("event") == "answer")
            self.assertIn("stub answer to", answer["data"]["text"])

            state = await recv_until(ws, lambda m: m.get("event") == "session.state")
            self.assertFalse(state["data"]["turn_active"])
            self.assertEqual(state["data"]["path"], ".")

    async def test_prompt_runs_a_tool_and_answers(self):
        async with running_server(ToolCallingPipeline) as uri, websockets.connect(
            uri
        ) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(ws, "/prompt", {"text": "use-tool"}, room=room_id)

            call = await recv_until(ws, lambda m: m.get("event") == "tool.call")
            self.assertEqual(call["data"], {"name": "cat", "args": "path='README.md'"})
            result = await recv_until(ws, lambda m: m.get("event") == "tool.result")
            self.assertEqual(result["data"], {"output": "# hi"})
            answer = await recv_until(
                ws, lambda m: m.get("event") == "answer" and m["data"]["text"] == "done"
            )
            self.assertEqual(answer["data"]["text"], "done")

    async def test_prompt_rejected_while_a_turn_is_active(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")  # bootstrap done

            live_room = rooms.ROOMS[room_id]
            self.assertTrue(live_room.try_start_turn())
            try:
                request_id = str(uuid.uuid4())
                await ws.send(
                    json.dumps(
                        {
                            "id": request_id,
                            "route": "/prompt",
                            "room": room_id,
                            "data": {"text": "should be rejected"},
                        }
                    )
                )
                resp = await recv_until(ws, lambda m: m.get("id") == request_id)
                self.assertFalse(resp["ok"])
            finally:
                live_room.turn_active = False

    async def test_ask_tool_round_trips_through_question_and_reply(self):
        async with running_server(AskToolPipeline) as uri, websockets.connect(
            uri
        ) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(ws, "/prompt", {"text": "ask-me"}, room=room_id)

            question = await recv_until(ws, lambda m: m.get("event") == "question")
            self.assertEqual(question["data"]["text"], "what should I call this?")

            await send_request(ws, "/reply", {"text": "Widget"}, room=room_id)

            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "Widget" in m["data"]["text"],
            )
            self.assertEqual(answer["data"]["text"], "got: Widget")

    async def test_reply_rejected_when_not_awaiting_one(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            with self.assertRaises(AssertionError):
                await send_request(ws, "/reply", {"text": "nope"}, room=room_id)

    async def test_startup_failure_reports_a_friendly_error(self):
        async with running_server(FailingPipeline) as uri, websockets.connect(
            uri
        ) as ws:
            await send_request(ws, "/session/create", {"path": "/nonexistent"})
            error = await recv_until(ws, lambda m: m.get("event") == "error")
            self.assertIn("bad project path", error["data"]["message"])


class TestPersistenceAndResume(unittest.IsolatedAsyncioTestCase):
    async def test_room_is_persisted_and_resumable(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "some/project"})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            room_file = rooms.ROOMS_DIR / f"{room_id}.json"
            self.assertTrue(room_file.exists())
            payload = json.loads(room_file.read_text())
            self.assertEqual(payload["id"], room_id)
            self.assertEqual(payload["path"], "some/project")
            self.assertTrue(payload["messages"])

            async with websockets.connect(uri) as ws2:
                resumed = await send_request(ws2, "/session/resume", {"room": room_id})
                self.assertEqual(resumed["path"], "some/project")
                self.assertTrue(resumed["transcript"])

    async def test_resume_loads_from_disk_when_not_live(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            saved_json = (rooms.ROOMS_DIR / f"{room_id}.json").read_text()

        # That server (and its in-memory ROOMS) is gone; only the saved
        # file would remain. A *fresh* server, with that same file placed
        # in its rooms/ dir, should still be able to resume it — proving
        # get_or_load() actually reads from disk, not just from ROOMS.
        async with running_server(StubPipeline) as uri:
            rooms.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
            (rooms.ROOMS_DIR / f"{room_id}.json").write_text(saved_json)
            async with websockets.connect(uri) as ws2:
                resumed = await send_request(
                    ws2, "/session/resume", {"room": room_id}
                )
                self.assertEqual(resumed["id"], room_id)

    async def test_rooms_list_includes_saved_rooms(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            listing = await send_request(ws, "/rooms/list")
            self.assertTrue(any(r["id"] == room_id for r in listing["rooms"]))


if __name__ == "__main__":
    unittest.main()

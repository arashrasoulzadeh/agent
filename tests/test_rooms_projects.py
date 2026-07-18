"""End-to-end tests for the multi-project rooms feature: /project/add,
/project/remove, /project/list, and the backward-compat load path for a
room saved before this feature existed. A real websockets server + a
real websockets client, always backed by a stub pipeline (tests/stubs.py)
— never a real LLM call, matching the rest of this suite.
"""

import asyncio
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import websockets

from service import rooms
from tests.stubs import SlowPipeline, StubPipeline, running_server
from workspace import config as workspace_config
from workspace.config import WORKSPACE_PROJECT_NAME


async def recv_until(ws, predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if predicate(msg):
                return msg


async def wait_until(predicate, timeout=2.0, interval=0.02):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(interval)


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


class TestProjectAddRemove(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base_dir = Path(tempfile.mkdtemp())
        self.primary_dir = Path(tempfile.mkdtemp())
        (self.primary_dir / "main.py").write_text("x = 1\n")
        self.backend_dir = Path(tempfile.mkdtemp())
        (self.backend_dir / "app.py").write_text("y = 2\n")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)
        shutil.rmtree(self.primary_dir, ignore_errors=True)
        shutil.rmtree(self.backend_dir, ignore_errors=True)

    async def _create_room(self, ws, pipeline_started_count=1):
        data = await send_request(
            ws, "/session/create", {"path": str(self.primary_dir)}
        )
        room_id = data["room"]
        for _ in range(pipeline_started_count):
            await recv_until(ws, lambda m: m.get("event") == "answer")
        return room_id

    async def test_add_project_happy_path(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)

            resp = await send_request(
                ws,
                "/project/add",
                {"path": str(self.backend_dir), "name": "backend"},
                room=room_id,
            )
            self.assertEqual(resp["name"], "backend")
            names = {p["name"] for p in resp["projects"]}
            self.assertEqual(names, {WORKSPACE_PROJECT_NAME, "backend"})

            # Two session.state broadcasts land around this response: one
            # from the route handler's immediate broadcast_state() (may
            # race the response itself), one from the reanalysis turn
            # completing. Wait for the reanalysis's own answer first,
            # then the session.state that follows it, matching how
            # test_server.py's own tests order these two checks.
            await recv_until(ws, lambda m: m.get("event") == "answer")
            state = await recv_until(ws, lambda m: m.get("event") == "session.state")
            state_names = {p["name"] for p in state["data"]["projects"]}
            self.assertEqual(state_names, {WORKSPACE_PROJECT_NAME, "backend"})

            room_file = self.base_dir / "rooms" / f"{room_id}.json"
            payload = json.loads(room_file.read_text())
            self.assertEqual(
                set(payload["projects"]), {WORKSPACE_PROJECT_NAME, "backend"}
            )
            self.assertEqual(
                Path(payload["projects"]["backend"]).resolve(),
                self.backend_dir.resolve(),
            )

    async def test_add_project_defaults_name_to_basename(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)

            resp = await send_request(
                ws, "/project/add", {"path": str(self.backend_dir)}, room=room_id
            )
            self.assertEqual(resp["name"], self.backend_dir.name)

    async def test_add_project_name_conflict_rolls_back_turn_active(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)
            await send_request(
                ws,
                "/project/add",
                {"path": str(self.backend_dir), "name": "backend"},
                room=room_id,
            )
            await recv_until(ws, lambda m: m.get("event") == "answer")

            other_dir = Path(tempfile.mkdtemp())
            try:
                with self.assertRaises(AssertionError) as cm:
                    await send_request(
                        ws,
                        "/project/add",
                        {"path": str(other_dir), "name": "backend"},
                        room=room_id,
                    )
                self.assertIn("already attached", str(cm.exception))
            finally:
                shutil.rmtree(other_dir, ignore_errors=True)

            # turn_active must have been rolled back by the failed add —
            # a normal prompt still works right after.
            await send_request(ws, "/prompt", {"text": "hi"}, room=room_id)
            await recv_until(
                ws,
                lambda m: (
                    m.get("event") == "answer"
                    and "stub answer to: hi" in m["data"]["text"]
                ),
            )

    async def test_add_rejected_while_a_turn_is_active(self):
        async with (
            running_server(SlowPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.primary_dir)}
            )
            room_id = data["room"]
            # The bootstrap turn is still running (SlowPipeline.ask()
            # sleeps) — try to add mid-turn.
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/project/add",
                    {"path": str(self.backend_dir), "name": "backend"},
                    room=room_id,
                )
            self.assertIn("already running", str(cm.exception))
            await recv_until(ws, lambda m: m.get("event") == "answer")

    async def test_remove_primary_project_rejected(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/project/remove",
                    {"name": WORKSPACE_PROJECT_NAME},
                    room=room_id,
                )
            self.assertIn("primary project", str(cm.exception))

    async def test_remove_unattached_project_rejected(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws, "/project/remove", {"name": "mobile"}, room=room_id
                )
            self.assertIn("not attached", str(cm.exception))

    async def test_remove_secondary_project_reanalyzes_and_stops_watcher(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)
            await send_request(
                ws,
                "/project/add",
                {"path": str(self.backend_dir), "name": "backend"},
                room=room_id,
            )
            await recv_until(ws, lambda m: m.get("event") == "answer")

            self.assertIn((room_id, "backend"), rooms.ROOM_WATCHERS)
            live_room = rooms.ROOMS[room_id]
            questions_before = len(live_room.pipeline.questions)

            resp = await send_request(
                ws, "/project/remove", {"name": "backend"}, room=room_id
            )
            self.assertEqual(
                {p["name"] for p in resp["projects"]}, {WORKSPACE_PROJECT_NAME}
            )
            await recv_until(ws, lambda m: m.get("event") == "answer")

            self.assertNotIn((room_id, "backend"), rooms.ROOM_WATCHERS)
            self.assertNotIn("backend", live_room.projects)
            # A real reanalysis ran — proof it's not just a silent state
            # edit (mirrors test_server.py's own "did the LLM actually
            # get called" signal).
            self.assertGreater(len(live_room.pipeline.questions), questions_before)

    async def test_project_list_marks_primary(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            room_id = await self._create_room(ws)
            await send_request(
                ws,
                "/project/add",
                {"path": str(self.backend_dir), "name": "backend"},
                room=room_id,
            )
            await recv_until(ws, lambda m: m.get("event") == "answer")

            resp = await send_request(ws, "/project/list", {}, room=room_id)
            by_name = {p["name"]: p for p in resp["projects"]}
            self.assertTrue(by_name[WORKSPACE_PROJECT_NAME]["primary"])
            self.assertFalse(by_name["backend"]["primary"])


class TestBackwardCompatNoProjectsKey(unittest.IsolatedAsyncioTestCase):
    """A room saved by a pre-multi-project version of this app has no
    "projects" key in rooms/{id}.json at all — Room.get_or_load() must
    still load it, falling back to a single-project shape."""

    def setUp(self):
        self.base_dir = Path(tempfile.mkdtemp())
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "main.py").write_text("x = 1\n")
        self.rooms_dir = self.base_dir / "rooms"
        self.rooms_dir.mkdir(parents=True)
        self.session_root = self.base_dir / "sessions"

        self.room_id = rooms.room_id_for_path(str(self.project_dir))
        (self.rooms_dir / f"{self.room_id}.json").write_text(
            json.dumps(
                {
                    "id": self.room_id,
                    "path": str(self.project_dir),
                    "created_at": "2020-01-01T00:00:00",
                    "updated_at": "2020-01-01T00:00:00",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0},
                    "messages": [],
                    "transcript": [],
                }
            )
        )

        self.original_rooms_dir = rooms.ROOMS_DIR
        self.original_session_root = workspace_config.SESSION_ROOT
        self.original_factory = rooms.pipeline_factory
        rooms.ROOMS.clear()
        rooms.ROOMS_DIR = self.rooms_dir
        workspace_config.SESSION_ROOT = self.session_root
        rooms.pipeline_factory = lambda config, events, room: StubPipeline(
            rooms.RoomSink(room)
        )

    def tearDown(self):
        rooms.stop_all_room_watchers()
        rooms.ROOMS.clear()
        rooms.ROOMS_DIR = self.original_rooms_dir
        workspace_config.SESSION_ROOT = self.original_session_root
        rooms.pipeline_factory = self.original_factory
        shutil.rmtree(self.base_dir, ignore_errors=True)
        shutil.rmtree(self.project_dir, ignore_errors=True)

    async def test_missing_projects_key_falls_back_to_single_project(self):
        loop = asyncio.get_running_loop()
        room = rooms.Room.get_or_load(self.room_id, loop)
        self.assertIsNotNone(room)
        self.assertEqual(room.projects, {WORKSPACE_PROJECT_NAME: str(self.project_dir)})


if __name__ == "__main__":
    unittest.main()

"""Tests for the wire/ package: a real websockets server and a real
websockets client, talking the actual wire protocol (see docs/PROTOCOL.md)
end to end. The pipeline is always a stub (tests/stubs.py) — this suite
never touches the network or spends a real API token.
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import websockets

from service import rooms
from tests.stubs import (
    AskToolPipeline,
    FailingPipeline,
    StubPipeline,
    ToolCallingPipeline,
    running_server,
)
from workspace.index_repository import IndexRepository
from workspace.synthesis_repository import SynthesisRepository


async def recv_until(ws, predicate, timeout=5):
    """Read messages until one matches, discarding the rest. Callers must
    know the emission order well enough that what they skip past is
    never something they'll need to check later."""
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if predicate(msg):
                return msg


async def wait_until(predicate, timeout=2.0, interval=0.02):
    """Poll `predicate` until it's truthy. Room._cache_synthesis() runs
    as a fire-and-forget tail step after a turn's `answer` event is
    already sent (service/rooms.py's _run_turn()) — deliberately, so
    caching never blocks turn_active from clearing — so a test that
    needs the cache write to have landed can't just react to the
    `answer` event; it has to wait for the write itself."""
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

    async def test_create_with_same_path_resumes_instead_of_creating_new(self):
        # Room ids are derived from the (resolved) path (service/rooms.py's
        # room_id_for_path()), not random - creating "twice" for the same
        # path must resume the first room, not spend a second bootstrap.
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            first = await send_request(ws, "/session/create", {"path": "some/project"})
            await recv_until(ws, lambda m: m.get("event") == "answer")

            second = await send_request(ws, "/session/create", {"path": "some/project"})

        self.assertEqual(first["room"], second["room"])
        # A resumed room's /session/create response looks like a full
        # state snapshot (matching /session/resume), not just {"room": id}.
        self.assertIn("transcript", second)
        self.assertTrue(second["transcript"])

    async def test_create_with_different_paths_gets_different_rooms(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            first = await send_request(ws, "/session/create", {"path": "project-a"})
            await recv_until(ws, lambda m: m.get("event") == "answer")

            second = await send_request(ws, "/session/create", {"path": "project-b"})
            await recv_until(ws, lambda m: m.get("event") == "answer")

        self.assertNotEqual(first["room"], second["room"])

    async def test_prompt_runs_a_tool_and_answers(self):
        async with (
            running_server(ToolCallingPipeline) as uri,
            websockets.connect(uri) as ws,
        ):
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
        async with (
            running_server(AskToolPipeline) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(ws, "/prompt", {"text": "ask-me"}, room=room_id)

            question = await recv_until(ws, lambda m: m.get("event") == "question")
            self.assertEqual(question["data"]["text"], "what should I call this?")
            self.assertIsNone(question["data"]["options"])

            await send_request(ws, "/reply", {"text": "Widget"}, room=room_id)

            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "Widget" in m["data"]["text"],
            )
            self.assertEqual(answer["data"]["text"], "got: Widget")

    async def test_ask_tool_with_options_round_trips_through_question_and_reply(self):
        async with (
            running_server(AskToolPipeline) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws, "/prompt", {"text": "ask-with-options"}, room=room_id
            )

            question = await recv_until(ws, lambda m: m.get("event") == "question")
            self.assertEqual(question["data"]["text"], "pick one")
            self.assertEqual(question["data"]["options"], ["a", "b", "c"])

            await send_request(ws, "/reply", {"text": "b"}, room=room_id)

            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "got: b" in m["data"]["text"],
            )
            self.assertEqual(answer["data"]["text"], "got: b")

    async def test_reply_rejected_when_not_awaiting_one(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            with self.assertRaises(AssertionError):
                await send_request(ws, "/reply", {"text": "nope"}, room=room_id)

    async def test_startup_failure_reports_a_friendly_error(self):
        async with (
            running_server(FailingPipeline) as uri,
            websockets.connect(uri) as ws,
        ):
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
                resumed = await send_request(ws2, "/session/resume", {"room": room_id})
                self.assertEqual(resumed["id"], room_id)

    async def test_prompt_succeeds_after_resuming_a_room_from_disk(self):
        """Regression test: Room.get_or_load() restores the analyst's
        conversation via resume(), but a follow-up /prompt goes through
        ProjectPipeline.ask(), which has its own separate precondition —
        self.context must be set, or it raises "Call start() before
        ask()." get_or_load() used to never set it (nothing does, on a
        resume, unless something explicitly fixes this), so the very
        first /prompt after a resume would fail on the real pipeline.
        StubPipeline.ask() now enforces the same precondition (see
        tests/stubs.py), so this test fails loudly without the fix in
        Room.get_or_load()."""
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            saved_json = (rooms.ROOMS_DIR / f"{room_id}.json").read_text()

        async with running_server(StubPipeline) as uri:
            rooms.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
            (rooms.ROOMS_DIR / f"{room_id}.json").write_text(saved_json)
            async with websockets.connect(uri) as ws2:
                await send_request(ws2, "/session/resume", {"room": room_id})

                await send_request(
                    ws2, "/prompt", {"text": "a follow-up"}, room=room_id
                )
                answer = await recv_until(ws2, lambda m: m.get("event") == "answer")
                self.assertEqual(answer["data"]["text"], "stub answer to: a follow-up")

    async def test_rooms_list_includes_saved_rooms(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            listing = await send_request(ws, "/rooms/list")
            self.assertTrue(any(r["id"] == room_id for r in listing["rooms"]))


class TestRoomIdForPath(unittest.TestCase):
    def test_same_path_produces_the_same_id(self):
        self.assertEqual(
            rooms.room_id_for_path("some/project"),
            rooms.room_id_for_path("some/project"),
        )

    def test_different_paths_produce_different_ids(self):
        self.assertNotEqual(
            rooms.room_id_for_path("project-a"),
            rooms.room_id_for_path("project-b"),
        )

    def test_relative_and_equivalent_absolute_path_produce_the_same_id(self):
        self.assertEqual(
            rooms.room_id_for_path("."),
            rooms.room_id_for_path(os.getcwd()),
        )


class TestWorkspaceCacheIntegration(unittest.IsolatedAsyncioTestCase):
    """The room-bootstrap <-> workspace/ integration: a cached
    ProjectSynthesis lets a brand-new room skip the LLM entirely, and a
    project that's drifted too much since that cache was made gets a
    resync.suggested event instead of a silent stale answer (see
    service/rooms.py's _collect_and_start()/RESYNC_CHANGE_THRESHOLD)."""

    def setUp(self):
        self.base_dir = Path(tempfile.mkdtemp())
        self.project_dir = Path(tempfile.mkdtemp())
        for i in range(5):
            (self.project_dir / f"mod{i}.py").write_text(
                f"def f{i}():\n    return {i}\n"
            )

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _cache_dir(self, room_id: str) -> Path:
        return self.base_dir / "sessions" / room_id / rooms.WORKSPACE_PROJECT_NAME

    def _drop_room_file(self, room_id: str) -> None:
        """Simulates a room whose own conversation was reset (e.g.
        rooms/{id}.json deleted) while the workspace cache survives —
        the scenario Room._cache_synthesis()'s docstring describes as
        the payoff case: the *next* room for this same path can skip the
        LLM even though it's a brand-new Room object."""
        (self.base_dir / "rooms" / f"{room_id}.json").unlink()

    async def test_bootstrap_populates_index_and_synthesis_cache(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            answer = await recv_until(ws, lambda m: m.get("event") == "answer")

            cache_dir = self._cache_dir(room_id)
            index = IndexRepository(cache_dir).load()
            self.assertIsNotNone(index)
            self.assertEqual(len(index.files), 5)
            self.assertTrue(
                all(
                    f.derived and f.derived.get("signatures")
                    for f in index.files.values()
                )
            )

            await wait_until(lambda: SynthesisRepository(cache_dir).load() is not None)
            synthesis = SynthesisRepository(cache_dir).load()
            self.assertEqual(synthesis.answer, answer["data"]["text"])
            self.assertEqual(synthesis.file_count, 5)

    async def test_first_bootstrap_seeds_from_lightweight_index_not_full_signatures(
        self,
    ):
        """Proves the very first-ever analysis of a project (no cached
        synthesis yet) is seeded from workspace/'s tier-1 lightweight
        index (service/rooms.py's _workspace_context(), built on
        to_lightweight_context()) and not the old shallow tool.metadata
        listing or the full-signature to_prompt_context() — neither of
        which distinguishes itself from the other in any assertion above.
        A rendered function signature line for one of the fixture files
        would only appear if the full-signature tier had leaked in.
        """
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            live_room = rooms.ROOMS[room_id]
            raw = live_room.pipeline.context.raw
            self.assertIn("mod0.py", raw)
            self.assertIn("1 function", raw)
            self.assertNotIn("def f0()", raw)

    async def test_second_bootstrap_after_reset_uses_cache_without_llm(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            first_answer = await recv_until(ws, lambda m: m.get("event") == "answer")
            await wait_until(
                lambda: SynthesisRepository(self._cache_dir(room_id)).load() is not None
            )

        self._drop_room_file(room_id)

        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            self.assertEqual(data["room"], room_id)
            second_answer = await recv_until(ws, lambda m: m.get("event") == "answer")
            self.assertEqual(
                second_answer["data"]["text"], first_answer["data"]["text"]
            )

            state = await recv_until(ws, lambda m: m.get("event") == "session.state")
            self.assertFalse(state["data"]["resync_suggested"])

            live_room = rooms.ROOMS[room_id]
            # No real analysis ran at all — proof the cache hit skipped
            # the LLM entirely (Room._collect_and_start() no longer
            # calls pipeline.start() for any bootstrap, cached or not,
            # so .questions is the one signal left that .ask() ran).
            self.assertEqual(live_room.pipeline.questions, [])

    async def test_resync_suggested_when_project_has_drifted_past_threshold(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            await wait_until(
                lambda: SynthesisRepository(self._cache_dir(room_id)).load() is not None
            )

        self._drop_room_file(room_id)
        # 2 of 5 tracked files changed content -> 40% drift, above the
        # default 20% RESYNC_CHANGE_THRESHOLD.
        (self.project_dir / "mod0.py").write_text("def f0():\n    return 999\n")
        (self.project_dir / "mod1.py").write_text("def f1():\n    return 999\n")

        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            self.assertEqual(data["room"], room_id)
            await recv_until(ws, lambda m: m.get("event") == "answer")
            resync_event = await recv_until(
                ws, lambda m: m.get("event") == "resync.suggested"
            )
            self.assertEqual(resync_event["data"]["changed"], 2)
            self.assertEqual(resync_event["data"]["total"], 5)
            self.assertAlmostEqual(resync_event["data"]["fraction"], 0.4)

            live_room = rooms.ROOMS[room_id]
            self.assertTrue(live_room.resync_suggested)
            # Still cache-only: the drift is flagged, but the LLM hasn't
            # been called yet — that's what a confirmed /resync is for.
            self.assertEqual(live_room.pipeline.questions, [])

    async def test_resync_confirm_reruns_analysis_and_updates_cache(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            await wait_until(
                lambda: SynthesisRepository(self._cache_dir(room_id)).load() is not None
            )

        self._drop_room_file(room_id)
        (self.project_dir / "mod0.py").write_text("def f0():\n    return 999\n")
        (self.project_dir / "mod1.py").write_text("def f1():\n    return 999\n")

        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            await recv_until(ws, lambda m: m.get("event") == "resync.suggested")

            await send_request(ws, "/resync", {"confirm": True}, room=room_id)
            await recv_until(ws, lambda m: m.get("event") == "answer")

            live_room = rooms.ROOMS[room_id]
            # A confirmed resync runs a real ask() — Room._collect_and_
            # start_for_resync() seeds context itself (no more
            # pipeline.start() to observe), so .questions is the proof
            # a fresh analysis actually happened this time.
            self.assertEqual(live_room.pipeline.questions, [rooms.BOOTSTRAP_QUERY])
            self.assertFalse(live_room.resync_suggested)

            synthesis = SynthesisRepository(self._cache_dir(room_id)).load()
            self.assertEqual(synthesis.file_count, 5)

    async def test_resync_declined_leaves_cache_untouched_and_clears_flag(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            await wait_until(
                lambda: SynthesisRepository(self._cache_dir(room_id)).load() is not None
            )

        self._drop_room_file(room_id)
        (self.project_dir / "mod0.py").write_text("def f0():\n    return 999\n")
        (self.project_dir / "mod1.py").write_text("def f1():\n    return 999\n")

        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            await recv_until(ws, lambda m: m.get("event") == "resync.suggested")

            await send_request(ws, "/resync", {"confirm": False}, room=room_id)

            live_room = rooms.ROOMS[room_id]
            self.assertFalse(live_room.resync_suggested)
            self.assertEqual(live_room.pipeline.questions, [])

    async def test_resync_rejected_when_none_is_pending(self):
        async with (
            running_server(StubPipeline, base_dir=self.base_dir) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(
                ws, "/session/create", {"path": str(self.project_dir)}
            )
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            with self.assertRaises(AssertionError):
                await send_request(ws, "/resync", {"confirm": True}, room=room_id)


if __name__ == "__main__":
    unittest.main()

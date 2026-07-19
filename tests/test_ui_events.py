"""Wire-level tests for the /ui/event route and the ui.update event it
drives (see wire/routes.py's ui_event/_dispatch_* and
service/rooms.py's append_content/push_modal/dismiss_modal) — a real
websockets server + a real websockets client, backed by a stub
pipeline, exercising the actual dispatch table before ui/app.py (the
generic renderer) is ever involved.
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

from core import settings
from tests.stubs import AskToolPipeline, SlowPipeline, StubPipeline, running_server


async def recv_until(ws, predicate, timeout=5):
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


def _ops_of(msg) -> list[dict]:
    return msg["data"]["ops"]


def _find_op(ops: list[dict], op: str, target: str) -> dict | None:
    for entry in reversed(ops):
        if entry["op"] == op and entry["target"] == target:
            return entry
    return None


async def _wait_for_op(ws, op: str, target: str, node_predicate=None, timeout=5):
    """Waits for a ui.update event containing an op matching (op,
    target) — and, if given, whose node also satisfies node_predicate —
    skipping any earlier/unrelated ui.update events. broadcast_state()
    fires its own header/footer replace ops on every state change, so
    "the next ui.update" alone isn't a reliable enough match; this
    keeps polling until the SPECIFIC op being waited for shows up."""

    def predicate(msg):
        if msg.get("event") != "ui.update":
            return False
        found = _find_op(_ops_of(msg), op, target)
        if found is None:
            return False
        return node_predicate is None or node_predicate(found["node"])

    msg = await recv_until(ws, predicate, timeout)
    return _find_op(_ops_of(msg), op, target)


class SettingsIsolatedTestCase(unittest.IsolatedAsyncioTestCase):
    """Same isolation as tests/test_settings_routes.py — only the tests
    that actually touch settings need this, but it's harmless for the
    others too."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self._original_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = self.tmp_dir / "settings.json"
        self._original_env = {}
        for spec in settings.SETTINGS:
            self._original_env[spec.key] = os.environ.pop(spec.key, None)

    def tearDown(self):
        settings.SETTINGS_FILE = self._original_file
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestUiEventValidation(SettingsIsolatedTestCase):
    async def test_missing_component_id_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws, "/ui/event", {"event": "submit", "value": "x"}, room=room_id
                )
            self.assertIn("needs 'component_id'", str(cm.exception))

    async def test_unknown_component_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/ui/event",
                    {"component_id": "something-random", "event": "submit"},
                    room=room_id,
                )
            self.assertIn("unknown component", str(cm.exception))


class TestFooterSubmitDispatch(SettingsIsolatedTestCase):
    async def test_submits_as_a_prompt_when_idle(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {"component_id": "footer-input", "event": "submit", "value": "hello"},
                room=room_id,
            )
            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "hello" in m["data"]["text"],
            )
            self.assertIn("stub answer to: hello", answer["data"]["text"])

    async def test_unmatched_slash_text_still_falls_through_to_a_prompt(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {"component_id": "footer-input", "event": "submit", "value": "/xyz"},
                room=room_id,
            )
            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "/xyz" in m["data"]["text"],
            )
            self.assertIn("stub answer to: /xyz", answer["data"]["text"])

    async def test_blocked_while_a_turn_is_active(self):
        async with running_server(SlowPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            # Bootstrap's own turn is still running (SlowPipeline sleeps).
            result = await send_request(
                ws,
                "/ui/event",
                {"component_id": "footer-input", "event": "submit", "value": "hi"},
                room=room_id,
            )
            self.assertEqual(result, {"accepted": True})
            await recv_until(ws, lambda m: m.get("event") == "answer")
            # The blocked-while-busy text must never have reached the
            # pipeline as a real question.
            final = await recv_until(ws, lambda m: m.get("event") == "session.state")
            self.assertFalse(final["data"]["turn_active"])

    async def test_projects_command_appends_info_without_a_turn(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {
                    "component_id": "footer-input",
                    "event": "submit",
                    "value": "/projects",
                },
                room=room_id,
            )
            append = await _wait_for_op(
                ws,
                "append",
                "content",
                lambda node: "No projects attached" in node["props"].get("text", ""),
            )
            self.assertIn("No projects attached", append["node"]["props"]["text"])

    async def test_add_without_args_shows_usage_locally(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {"component_id": "footer-input", "event": "submit", "value": "/add"},
                room=room_id,
            )
            append = await _wait_for_op(
                ws,
                "append",
                "content",
                lambda node: "Usage: /add" in node["props"].get("text", ""),
            )
            self.assertIn("Usage: /add", append["node"]["props"]["text"])

    async def test_add_command_attaches_a_project(self):
        base_dir = Path(tempfile.mkdtemp())
        primary_dir = Path(tempfile.mkdtemp())
        (primary_dir / "main.py").write_text("x = 1\n")
        backend_dir = Path(tempfile.mkdtemp())
        (backend_dir / "app.py").write_text("y = 2\n")
        try:
            async with (
                running_server(StubPipeline, base_dir=base_dir) as uri,
                websockets.connect(uri) as ws,
            ):
                data = await send_request(
                    ws, "/session/create", {"path": str(primary_dir)}
                )
                room_id = data["room"]
                await recv_until(ws, lambda m: m.get("event") == "answer")

                await send_request(
                    ws,
                    "/ui/event",
                    {
                        "component_id": "footer-input",
                        "event": "submit",
                        "value": f"/add {backend_dir} backend",
                    },
                    room=room_id,
                )
                state = await recv_until(
                    ws,
                    lambda m: (
                        m.get("event") == "session.state"
                        and any(p["name"] == "backend" for p in m["data"]["projects"])
                    ),
                )
                self.assertTrue(
                    any(p["name"] == "backend" for p in state["data"]["projects"])
                )
        finally:
            shutil.rmtree(base_dir, ignore_errors=True)
            shutil.rmtree(primary_dir, ignore_errors=True)
            shutil.rmtree(backend_dir, ignore_errors=True)

    async def test_settings_command_pushes_the_settings_modal(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {
                    "component_id": "footer-input",
                    "event": "submit",
                    "value": "/settings",
                },
                room=room_id,
            )
            modal = await _wait_for_op(
                ws,
                "replace",
                "modal",
                lambda node: node["props"].get("kind") == "settings",
            )
            self.assertEqual(modal["node"]["props"]["kind"], "settings")


class TestReplyDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_footer_submit_delivers_a_reply_while_awaiting_one(self):
        async with (
            running_server(AskToolPipeline) as uri,
            websockets.connect(uri) as ws,
        ):
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(ws, "/prompt", {"text": "ask-me"}, room=room_id)
            await recv_until(ws, lambda m: m.get("event") == "question")

            await send_request(
                ws,
                "/ui/event",
                {"component_id": "footer-input", "event": "submit", "value": "Widget"},
                room=room_id,
            )
            answer = await recv_until(
                ws,
                lambda m: (
                    m.get("event") == "answer" and "got: Widget" in m["data"]["text"]
                ),
            )
            self.assertIn("got: Widget", answer["data"]["text"])

    async def test_option_click_resolves_the_pending_option_by_index(self):
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
            await recv_until(ws, lambda m: m.get("event") == "question")

            await send_request(
                ws,
                "/ui/event",
                {"component_id": "opt-1", "event": "click"},
                room=room_id,
            )
            answer = await recv_until(
                ws,
                lambda m: m.get("event") == "answer" and "got: b" in m["data"]["text"],
            )
            self.assertIn("got: b", answer["data"]["text"])

    async def test_option_click_rejected_without_a_pending_question(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/ui/event",
                    {"component_id": "opt-0", "event": "click"},
                    room=room_id,
                )
            self.assertIn("no question is currently pending", str(cm.exception))

    async def test_option_click_out_of_range_is_rejected(self):
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
            await recv_until(ws, lambda m: m.get("event") == "question")

            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/ui/event",
                    {"component_id": "opt-99", "event": "click"},
                    room=room_id,
                )
            self.assertIn("unknown option", str(cm.exception))

            # The rejected click never resolved the pending ask() — answer
            # it for real so the server-side worker thread blocked on
            # reply_queue.get() doesn't hang this test's teardown (the
            # same class of bug fixed in tests/test_app.py earlier).
            await send_request(
                ws,
                "/ui/event",
                {"component_id": "opt-0", "event": "click"},
                room=room_id,
            )
            await recv_until(ws, lambda m: m.get("event") == "answer")


class TestSettingSubmitDispatch(SettingsIsolatedTestCase):
    async def test_updates_the_setting_and_confirms(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {
                    "component_id": "setting-GAPGPT_MODEL",
                    "event": "submit",
                    "value": "gpt-5",
                },
                room=room_id,
            )
            self.assertEqual(os.environ.get("GAPGPT_MODEL"), "gpt-5")

            modal = await _wait_for_op(ws, "replace", "modal")
            self.assertIsNotNone(modal)

    async def test_blank_secret_submit_is_a_noop(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            await send_request(
                ws,
                "/ui/event",
                {
                    "component_id": "setting-NOTION_API_KEY",
                    "event": "submit",
                    "value": "",
                },
                room=room_id,
            )
            self.assertFalse(settings.SETTINGS_FILE.exists())
            self.assertNotIn("NOTION_API_KEY", os.environ)

    async def test_unknown_setting_key_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/session/create", {"path": "."})
            room_id = data["room"]
            await recv_until(ws, lambda m: m.get("event") == "answer")

            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws,
                    "/ui/event",
                    {
                        "component_id": "setting-NOT_A_REAL_SETTING",
                        "event": "submit",
                        "value": "x",
                    },
                    room=room_id,
                )
            self.assertIn("unknown setting", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

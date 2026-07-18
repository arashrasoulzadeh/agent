"""Tests for the /settings/list and /settings/update wire routes: a real
websockets server + a real websockets client, backed by a stub pipeline
(irrelevant here — these routes never touch a Room), matching
tests/test_server.py's conventions.
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
from tests.stubs import StubPipeline, running_server


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


class TestSettingsRoutes(unittest.IsolatedAsyncioTestCase):
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

    async def test_list_returns_every_known_setting(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/settings/list")
            keys = {s["key"] for s in data["settings"]}
            self.assertEqual(keys, {spec.key for spec in settings.SETTINGS})

    async def test_no_room_is_required(self):
        # Sanity: these routes work with zero rooms ever created — no
        # /session/create call anywhere in this test.
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(ws, "/settings/list")
            self.assertIsInstance(data["settings"], list)

    async def test_update_persists_and_reflects_in_next_list(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(
                ws, "/settings/update", {"key": "GAPGPT_MODEL", "value": "gpt-5"}
            )
            by_key = {s["key"]: s for s in data["settings"]}
            self.assertEqual(by_key["GAPGPT_MODEL"]["value"], "gpt-5")

            second = await send_request(ws, "/settings/list")
            by_key2 = {s["key"]: s for s in second["settings"]}
            self.assertEqual(by_key2["GAPGPT_MODEL"]["value"], "gpt-5")

    async def test_update_persists_to_disk_across_a_fresh_apply(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            await send_request(
                ws, "/settings/update", {"key": "GAPGPT_TIMEOUT", "value": "120"}
            )

        payload = json.loads(settings.SETTINGS_FILE.read_text())
        self.assertEqual(payload["GAPGPT_TIMEOUT"], "120")

    async def test_update_secret_never_returns_the_raw_value(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            data = await send_request(
                ws,
                "/settings/update",
                {"key": "GAPGPT_API_KEY", "value": "sk-real-secret-value"},
            )
            by_key = {s["key"]: s for s in data["settings"]}
            value = by_key["GAPGPT_API_KEY"]["value"]
            self.assertNotIn("real-secret", value)
            self.assertNotEqual(value, "sk-real-secret-value")

    async def test_unknown_key_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            with self.assertRaises(AssertionError) as cm:
                await send_request(
                    ws, "/settings/update", {"key": "NOT_A_REAL_SETTING", "value": "x"}
                )
            self.assertIn("unknown setting", str(cm.exception))

    async def test_missing_key_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            with self.assertRaises(AssertionError) as cm:
                await send_request(ws, "/settings/update", {"value": "x"})
            self.assertIn("needs 'key'", str(cm.exception))

    async def test_missing_value_is_rejected(self):
        async with running_server(StubPipeline) as uri, websockets.connect(uri) as ws:
            with self.assertRaises(AssertionError) as cm:
                await send_request(ws, "/settings/update", {"key": "GAPGPT_MODEL"})
            self.assertIn("needs 'value'", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

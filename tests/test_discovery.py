"""Tests for wire/discovery.py — is a server listening, and (for
fetch_session_prompt) the actual round trip to /session/prompt cli.py
uses to source its startup prompt's text/default from the server.
"""

import unittest

from tests.stubs import StubPipeline, running_server
from wire import discovery


class TestFetchSessionPrompt(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_server_supplied_prompt(self):
        async with running_server(StubPipeline) as uri:
            host, port = uri.removeprefix("ws://").split(":")
            result = await discovery.fetch_session_prompt(host, int(port))
        self.assertEqual(result, {"text": "Project path", "default": "."})

    async def test_raises_server_not_running_when_nothing_is_listening(self):
        # Port 1 is a privileged port nothing is listening on; connection
        # is refused immediately, well under discovery's open_timeout=1 —
        # matches tests/test_self_update.py's own "port 1 fails fast" trick.
        with self.assertRaises(discovery.ServerNotRunning):
            await discovery.fetch_session_prompt("127.0.0.1", 1)


if __name__ == "__main__":
    unittest.main()

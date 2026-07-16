"""Tests for core/room_context.py: the per-worker-thread "which room is
this?" contextvar tool/describe.py relies on.
"""

import asyncio
import unittest

from core import room_context


class TestRoomContext(unittest.TestCase):
    def tearDown(self):
        # Contextvars set in the main thread would otherwise leak into
        # later tests running on it.
        room_context._current_room.set(None)

    def test_default_is_none(self):
        self.assertIsNone(room_context.current_room_id())

    def test_set_then_get_roundtrips(self):
        room_context.set_current_room("room-1")
        self.assertEqual(room_context.current_room_id(), "room-1")

    def test_set_overwrites_previous_value(self):
        room_context.set_current_room("room-1")
        room_context.set_current_room("room-2")
        self.assertEqual(room_context.current_room_id(), "room-2")


class TestRoomContextThreadIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_a_room_id_set_in_one_worker_thread_is_invisible_to_another(self):
        """Mirrors how core/guard.py's project root stays isolated
        between concurrently running rooms — each Room._ask_blocking()
        runs on its own asyncio.to_thread worker, which copies the
        calling context into the new thread, so a value set *inside*
        one worker is never visible to another."""

        def _set_and_read(room_id: str) -> str | None:
            room_context.set_current_room(room_id)
            return room_context.current_room_id()

        results = await asyncio.gather(
            asyncio.to_thread(_set_and_read, "room-a"),
            asyncio.to_thread(_set_and_read, "room-b"),
        )
        self.assertEqual(set(results), {"room-a", "room-b"})
        # Neither worker thread's set() leaked back into this thread.
        self.assertIsNone(room_context.current_room_id())


if __name__ == "__main__":
    unittest.main()

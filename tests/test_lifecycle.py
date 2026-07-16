"""Tests for wire/lifecycle.py: init/start/stop hook orchestration — one
module's failing hook must not stop another module's from running.

The real subprocess-level behavior (a running server actually calling
these on startup and on SIGINT/SIGTERM) was also verified manually by
spawning `agent-server` and sending it a real SIGTERM; that's not
repeated here as an automated test since it needs a real OS process.
"""

import unittest

from wire import lifecycle


class RecordingModule:
    def __init__(self, name, fail_hook=None):
        self.name = name
        self.fail_hook = fail_hook
        self.calls: list[str] = []

    def init(self, config):
        self.calls.append(f"init({config})")
        if self.fail_hook == "init":
            raise RuntimeError(f"{self.name} init failed")

    def start(self):
        self.calls.append("start")
        if self.fail_hook == "start":
            raise RuntimeError(f"{self.name} start failed")

    def stop(self):
        self.calls.append("stop")
        if self.fail_hook == "stop":
            raise RuntimeError(f"{self.name} stop failed")


class PartialModule:
    """Only implements stop() — the common case for a module that just
    needs to flush something on shutdown."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class TestLifecycleOrchestration(unittest.TestCase):
    def test_init_start_stop_called_on_every_module(self):
        a = RecordingModule("a")
        b = RecordingModule("b")

        lifecycle.init_all([a, b], config={"x": 1})
        lifecycle.start_all([a, b])
        lifecycle.stop_all([a, b])

        self.assertEqual(a.calls, ["init({'x': 1})", "start", "stop"])
        self.assertEqual(b.calls, ["init({'x': 1})", "start", "stop"])

    def test_a_failing_init_does_not_stop_the_next_modules_init(self):
        broken = RecordingModule("broken", fail_hook="init")
        fine = RecordingModule("fine")

        lifecycle.init_all([broken, fine], config={})

        self.assertIn("init({})", broken.calls)
        self.assertIn("init({})", fine.calls)

    def test_a_failing_start_does_not_stop_the_next_modules_start(self):
        broken = RecordingModule("broken", fail_hook="start")
        fine = RecordingModule("fine")

        lifecycle.start_all([broken, fine])

        self.assertIn("start", broken.calls)
        self.assertIn("start", fine.calls)

    def test_stop_runs_for_every_module_even_if_one_fails(self):
        broken = RecordingModule("broken", fail_hook="stop")
        fine = RecordingModule("fine")

        lifecycle.stop_all([broken, fine])

        self.assertIn("stop", broken.calls)
        self.assertIn("stop", fine.calls)

    def test_stop_runs_even_for_a_module_that_never_had_init_or_start_called(self):
        # Mirrors wire/app.py's finally block: stop() must tolerate a
        # module whose init()/start() never ran (e.g. a sibling module's
        # init() raised before this one's start() was reached).
        module = RecordingModule("never-started")
        lifecycle.stop_all([module])
        self.assertEqual(module.calls, ["stop"])

    def test_a_module_implementing_only_stop_is_handled_correctly(self):
        partial = PartialModule()
        lifecycle.init_all([partial], config={})  # no init() — must not raise
        lifecycle.start_all([partial])  # no start() — must not raise
        lifecycle.stop_all([partial])
        self.assertTrue(partial.stopped)


if __name__ == "__main__":
    unittest.main()

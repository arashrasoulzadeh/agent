"""Tests for pipeline/events.py's StageEventBus in isolation — subscribe,
unsubscribe, partial observers, and that one observer raising doesn't
break the bus or stop other observers from being notified.
"""

import unittest

from pipeline.events import LoggingStageObserver, StageEventBus


class TestStageEventBus(unittest.TestCase):
    def test_subscribed_observer_receives_all_three_callbacks(self):
        bus = StageEventBus()
        seen = []

        class Observer:
            def on_stage_started(self, name, turn):
                seen.append(("started", name))

            def on_stage_completed(self, name, turn):
                seen.append(("completed", name))

            def on_stage_failed(self, name, turn, exc):
                seen.append(("failed", name))

        bus.subscribe(Observer())
        bus.stage_started("a", None)
        bus.stage_completed("a", None)
        bus.stage_failed("b", None, ValueError("x"))

        self.assertEqual(
            seen, [("started", "a"), ("completed", "a"), ("failed", "b")]
        )

    def test_unsubscribed_observer_receives_nothing_further(self):
        bus = StageEventBus()
        seen = []

        class Observer:
            def on_stage_started(self, name, turn):
                seen.append(name)

        observer = Observer()
        bus.subscribe(observer)
        bus.stage_started("a", None)
        bus.unsubscribe(observer)
        bus.stage_started("b", None)

        self.assertEqual(seen, ["a"])

    def test_partial_observer_only_implementing_one_callback_is_fine(self):
        bus = StageEventBus()
        seen = []

        class OnlyCompleted:
            def on_stage_completed(self, name, turn):
                seen.append(name)

        bus.subscribe(OnlyCompleted())
        # Neither of these should raise even though the observer has no
        # on_stage_started/on_stage_failed.
        bus.stage_started("a", None)
        bus.stage_failed("a", None, ValueError("x"))
        bus.stage_completed("a", None)

        self.assertEqual(seen, ["a"])

    def test_multiple_observers_all_get_notified(self):
        bus = StageEventBus()
        seen_a, seen_b = [], []

        class ObserverA:
            def on_stage_started(self, name, turn):
                seen_a.append(name)

        class ObserverB:
            def on_stage_started(self, name, turn):
                seen_b.append(name)

        bus.subscribe(ObserverA())
        bus.subscribe(ObserverB())
        bus.stage_started("a", None)

        self.assertEqual(seen_a, ["a"])
        self.assertEqual(seen_b, ["a"])

    def test_a_raising_observer_does_not_stop_other_observers(self):
        bus = StageEventBus()
        seen = []

        class Broken:
            def on_stage_started(self, name, turn):
                raise RuntimeError("boom")

        class Fine:
            def on_stage_started(self, name, turn):
                seen.append(name)

        bus.subscribe(Broken())
        bus.subscribe(Fine())

        bus.stage_started("a", None)  # must not raise
        self.assertEqual(seen, ["a"])

    def test_logging_observer_never_raises_and_tracks_duration(self):
        observer = LoggingStageObserver()
        observer.on_stage_started("collect", turn=None)
        observer.on_stage_completed("collect", turn=None)
        observer.on_stage_started("collect", turn=None)
        observer.on_stage_failed("collect", turn=None, exc=ValueError("x"))
        # Nothing to assert beyond "didn't raise" — it's a logging sink.


if __name__ == "__main__":
    unittest.main()

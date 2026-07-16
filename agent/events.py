"""Observer pattern for pipeline stage lifecycle.

This decouples stage execution from whoever needs to react to it — a
structured logger, a future metrics collector, anything else — without
`Pipeline`/`Stage` (agent/stage.py) knowing any of them exist. It is
deliberately *not* used for the high-frequency, fine-grained tool
call/result/token reporting that happens *inside* one stage (the
analyze stage's tool-calling loop) — that already has a simpler, more
direct channel in `Sink` (agent/sink.py), and forcing the observer
pattern on top of it would just be indirection with no one new listening.
Stage-level lifecycle is different: several independent things
plausibly want to know "a stage started/finished" (a broadcaster, a
logger, a metrics counter), which is exactly the situation an observer
list is for.
"""

import logging
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent.stage import Turn


class StageObserver(Protocol):
    """Implement only the callbacks you need — StageEventBus calls
    whatever's present via getattr, so a partial observer is fine."""

    def on_stage_started(self, stage_name: str, turn: "Turn") -> None: ...
    def on_stage_completed(self, stage_name: str, turn: "Turn") -> None: ...
    def on_stage_failed(
        self, stage_name: str, turn: "Turn", exc: Exception
    ) -> None: ...


class StageEventBus:
    """A tiny pub/sub hub: observers subscribe, the pipeline publishes.

    An observer that raises is logged and dropped from this call, never
    propagated — a broken listener must not be able to fail someone
    else's turn.
    """

    def __init__(self) -> None:
        self._observers: list[StageObserver] = []
        self._logger = logging.getLogger("agent.events")

    def subscribe(self, observer: StageObserver) -> None:
        self._observers.append(observer)

    def unsubscribe(self, observer: StageObserver) -> None:
        self._observers.remove(observer)

    def stage_started(self, stage_name: str, turn: "Turn") -> None:
        self._notify("on_stage_started", stage_name, turn)

    def stage_completed(self, stage_name: str, turn: "Turn") -> None:
        self._notify("on_stage_completed", stage_name, turn)

    def stage_failed(self, stage_name: str, turn: "Turn", exc: Exception) -> None:
        self._notify("on_stage_failed", stage_name, turn, exc)

    def _notify(self, callback_name: str, *args) -> None:
        for observer in list(self._observers):
            callback = getattr(observer, callback_name, None)
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception:
                self._logger.exception(
                    "observer %r's %s raised", observer, callback_name
                )


class LoggingStageObserver:
    """Logs each stage's start, completion (with duration), and failure —
    a concrete, genuinely decoupled use of the bus: Pipeline and Stage
    never call `logging` themselves."""

    def __init__(self, logger_name: str = "agent.stage") -> None:
        self._logger = logging.getLogger(logger_name)
        self._started_at: dict[int, float] = {}

    def on_stage_started(self, stage_name: str, turn: "Turn") -> None:
        self._started_at[id(turn)] = time.monotonic()
        self._logger.info("stage %s started", stage_name)

    def on_stage_completed(self, stage_name: str, turn: "Turn") -> None:
        elapsed = self._elapsed(turn)
        self._logger.info("stage %s completed in %.3fs", stage_name, elapsed)

    def on_stage_failed(self, stage_name: str, turn: "Turn", exc: Exception) -> None:
        elapsed = self._elapsed(turn)
        self._logger.warning(
            "stage %s failed after %.3fs: %s", stage_name, elapsed, exc
        )

    def _elapsed(self, turn: "Turn") -> float:
        started = self._started_at.pop(id(turn), None)
        return time.monotonic() - started if started is not None else 0.0

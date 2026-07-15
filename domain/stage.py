"""The common stage interface, and the runner that composes stages, in
whatever order `PipelineConfig` specifies, into one flow.

Stages are async by contract — a uniform interface that leaves room for
a future stage doing real async I/O — even though today's concrete
stages (pipeline/stages.py) wrap synchronous work: collection is a
filesystem walk, analysis and synthesis are synchronous LangChain calls.
They run inside the one worker thread server/rooms.py already dedicates
to a turn (via `asyncio.run()` inside that thread — see
ProjectPipeline.ask() in pipeline/__init__.py), so blocking here does not
stall the server's own event loop.

Cancellation is checked *between* stages, not inside one. Python cannot
forcibly interrupt a blocking call already in flight on another thread —
that limitation exists for any thread-per-turn design, not just this one
— so once a stage has started, it runs to completion; a cancelled run
simply won't advance to the *next* stage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pipeline.context import ProjectContext

if TYPE_CHECKING:
    from pipeline.events import StageEventBus


@dataclass
class Turn:
    """The unit of work threaded through the pipeline: what's known so
    far about one query. Stages read what they need off it and write
    their result back onto it — the same instance, mutated, flows to the
    next stage."""

    path: str
    query: str
    context: ProjectContext | None = None
    answer: str | None = None
    synthesized: str | None = None


class PipelineCancelled(Exception):
    """Raised when a run is cancelled between stages."""


@dataclass
class PipelineContext:
    """Per-run state: cancellation, and an optional event bus stage
    lifecycle is reported to (see pipeline/events.py). Both are opt-in —
    a bare `PipelineContext()` has no observers and cannot be cancelled,
    so using the observer pattern costs nothing when nobody needs it."""

    events: "StageEventBus | None" = None
    _cancelled: bool = field(default=False, repr=False)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class Stage(ABC):
    """One self-contained processing step: a Turn in, the same Turn
    (mutated) out. Errors propagate — a stage must never swallow one;
    let it raise and the Pipeline stops and re-raises to its caller."""

    name: str = "stage"

    @abstractmethod
    async def run(self, turn: Turn, ctx: PipelineContext) -> Turn: ...


class Pipeline:
    """Runs an ordered list of stages over one Turn, firing lifecycle
    events around each (if `ctx.events` is set) and stopping on the
    first error rather than continuing with a partial result."""

    def __init__(self, stages: list[Stage]):
        self.stages = stages

    async def run(self, turn: Turn, ctx: PipelineContext | None = None) -> Turn:
        ctx = ctx or PipelineContext()
        for stage in self.stages:
            if ctx.cancelled:
                raise PipelineCancelled(f"cancelled before stage {stage.name!r}")

            if ctx.events:
                ctx.events.stage_started(stage.name, turn)
            try:
                turn = await stage.run(turn, ctx)
            except Exception as exc:
                if ctx.events:
                    ctx.events.stage_failed(stage.name, turn, exc)
                raise
            if ctx.events:
                ctx.events.stage_completed(stage.name, turn)
        return turn

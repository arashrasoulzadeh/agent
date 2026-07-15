"""Concrete stages, and the registry that lets `PipelineConfig.stages`
select and order them by name instead of `pipeline/__init__.py`
hardcoding a fixed sequence.

`register_stage()` is the extension point: anyone can add a new stage
type (a redaction pass, a cache lookup, ...) and make it available to
`PipelineConfig.stages` without touching this file or `ProjectPipeline`.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from pipeline.analyst import ProjectAnalyst
from pipeline.context import ContextCollector
from pipeline.stage import PipelineContext, Stage, Turn
from pipeline.synthesizer import ContextSynthesizer

if TYPE_CHECKING:
    from pipeline import ProjectPipeline


class CollectStage(Stage):
    """Gathers the private project map. No LLM."""

    name = "collect"

    def __init__(self, collector: ContextCollector):
        self.collector = collector

    async def run(self, turn: Turn, ctx: PipelineContext) -> Turn:
        turn.context = self.collector.collect(turn.path)
        return turn


class AnalyzeStage(Stage):
    """Reasons over the collected context and answers the query.

    Assumes a session has already been started (ProjectPipeline.start())
    when used in the interactive, stateful flow; ProjectPipeline.run()
    (the one-shot flow) starts a fresh session immediately before running
    this stage instead.
    """

    name = "analyze"

    def __init__(self, analyst: ProjectAnalyst):
        self.analyst = analyst

    async def run(self, turn: Turn, ctx: PipelineContext) -> Turn:
        turn.answer = self.analyst.ask(turn.query)
        return turn


class SynthesizeStage(Stage):
    """Converts the analyst's answer into compact, AI-ready context.

    A no-op if there's no answer yet — lets this stage sit in a pipeline
    that also handles configurations where analyze didn't run.
    """

    name = "synthesize"

    def __init__(self, synthesizer: ContextSynthesizer):
        self.synthesizer = synthesizer

    async def run(self, turn: Turn, ctx: PipelineContext) -> Turn:
        if turn.answer is None or turn.context is None:
            return turn
        turn.synthesized = self.synthesizer.synthesize(turn.answer, turn.context)
        return turn


StageFactory = Callable[["ProjectPipeline"], Stage]

STAGE_FACTORIES: dict[str, StageFactory] = {
    "collect": lambda pipeline: CollectStage(pipeline.collector),
    "analyze": lambda pipeline: AnalyzeStage(pipeline.analyst),
    "synthesize": lambda pipeline: SynthesizeStage(pipeline.synthesizer),
}


def register_stage(name: str, factory: StageFactory) -> None:
    """Make a new stage type available to `PipelineConfig.stages` by name."""
    STAGE_FACTORIES[name] = factory

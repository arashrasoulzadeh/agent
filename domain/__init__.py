"""Project-analysis pipeline.

Orchestrates three stages (pipeline/stage.py, pipeline/stages.py):
    1. collect    — gather a private project map. No LLM.
    2. analyze    — reason over that map and answer questions.
    3. synthesize — convert an answer into AI-ready context.

Two ways to drive it:
    - start(path) + ask(query): interactive — collect once, then each
      ask() remembers prior turns (the analyst's own session), running
      just the analyze stage. Use this for a Q&A session.
    - run(query): one-shot — collect_context() first, then a *fresh*
      session runs through `PipelineConfig.stages` (analyze, then
      synthesize by default). Reorder, drop, or add to that list via
      config — nothing here hardcodes the sequence.

Every stage can also be swapped for a custom implementation via the
constructor, and an optional `events` bus (pipeline/events.py) reports
stage lifecycle to anyone listening, so the pipeline can be reshaped or
observed without editing its wiring.
"""

import asyncio

from langchain_core.tools import BaseTool

from pipeline.analyst import ProjectAnalyst
from pipeline.config import PipelineConfig
from pipeline.context import ContextCollector, ProjectContext
from pipeline.events import StageEventBus
from pipeline.sink import Sink
from pipeline.stage import Pipeline, PipelineContext, Turn
from pipeline.stages import STAGE_FACTORIES
from pipeline.synthesizer import ContextSynthesizer


class ProjectPipeline:
    def __init__(
        self,
        config: PipelineConfig | None = None,
        collector: ContextCollector | None = None,
        analyst: ProjectAnalyst | None = None,
        synthesizer: ContextSynthesizer | None = None,
        sink: Sink | None = None,
        events: StageEventBus | None = None,
        tools: list[BaseTool] | None = None,
    ):
        self.config = config or PipelineConfig()
        # No library-level default: unlike an analyst with no tools (still
        # functional, just limited), a collector with no metadata source
        # can't do anything — see collect_context()'s check below.
        self.collector = collector
        self.analyst = analyst or ProjectAnalyst(
            temperature=self.config.analysis_temperature, sink=sink, tools=tools
        )
        self.synthesizer = synthesizer or ContextSynthesizer(
            temperature=self.config.synthesis_temperature,
            fmt=self.config.synthesis_format,
        )
        self.events = events
        self.context: ProjectContext | None = None

        self._collect_pipeline = Pipeline([STAGE_FACTORIES["collect"](self)])
        self._ask_pipeline = Pipeline([STAGE_FACTORIES["analyze"](self)])
        self._run_pipeline = self._build_configured_pipeline()

    def _build_configured_pipeline(self) -> Pipeline:
        stages = []
        for name in self.config.stages:
            factory = STAGE_FACTORIES.get(name)
            if factory is None:
                raise ValueError(
                    f"unknown pipeline stage {name!r}; register it first "
                    "with pipeline.stages.register_stage()"
                )
            stages.append(factory(self))
        return Pipeline(stages)

    def _run_sync(self, pipeline: Pipeline, turn: Turn) -> Turn:
        """Drive an async Pipeline from this (synchronous) call site.

        Meant to be called from the one worker thread server/rooms.py
        already dedicates to a turn (via `asyncio.to_thread`), so a fresh
        event loop here is exactly the standard, safe way to run async
        code with no loop of its own — see pipeline/stage.py's module
        docstring for why stages are async at all despite today's
        concrete stages doing blocking work.

        Raises a clear error instead of asyncio's cryptic one if called
        from a thread that already has a running loop (e.g. directly
        from async code, instead of through `asyncio.to_thread`) — doing
        the blocking work here would stall that loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # good: no loop running on this thread
        else:
            raise RuntimeError(
                "ProjectPipeline.start()/ask()/run() block and must run on "
                "a thread with no event loop of its own — call them via "
                "asyncio.to_thread(...) from async code, not directly."
            )
        return asyncio.run(pipeline.run(turn, PipelineContext(events=self.events)))

    def collect_context(self, path: str = ".") -> ProjectContext:
        """Stage 1 — collect and cache the private project map."""
        if not self.config.collect:
            raise RuntimeError("collect step is disabled in config.")
        if self.collector is None:
            raise RuntimeError(
                "No ContextCollector configured — pass "
                "collector=ContextCollector(metadata_fn=...) to ProjectPipeline()."
            )
        turn = self._run_sync(self._collect_pipeline, Turn(path=path, query=""))
        self.context = turn.context
        return self.context

    def start(self, path: str = ".") -> ProjectContext:
        """Collect context and open an interactive analysis session."""
        context = self.collect_context(path)
        self.analyst.start_session(context)
        return context

    def ask(self, query: str) -> str:
        """Stage 2 only — ask a question in the current session.

        Remembers prior turns, so follow-up questions can build on
        earlier answers. Call start() first.
        """
        if self.context is None:
            raise RuntimeError("Call start() before ask().")
        turn = Turn(path=self.context.path, query=query, context=self.context)
        turn = self._run_sync(self._ask_pipeline, turn)
        return turn.answer

    def run(self, query: str) -> str:
        """Stages 2-3 — answer the query in a fresh session, then run
        whatever `PipelineConfig.stages` configures after analysis
        (synthesis by default).

        Returns the synthesized context when the synthesize stage ran,
        otherwise the analyst's raw answer.
        """
        if self.context is None:
            raise RuntimeError("Call collect_context() before run().")
        if "analyze" not in self.config.stages:
            raise RuntimeError("analyze step is disabled in config.")

        self.analyst.start_session(self.context)  # a fresh, one-shot session
        turn = Turn(path=self.context.path, query=query, context=self.context)
        turn = self._run_sync(self._run_pipeline, turn)
        return turn.synthesized if turn.synthesized is not None else turn.answer


__all__ = ["ProjectPipeline", "PipelineConfig", "ProjectContext"]

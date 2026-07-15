"""Project-analysis pipeline.

Orchestrates three stages (domain/stage.py, domain/stages.py):
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
constructor, and an optional `events` bus (domain/events.py) reports
stage lifecycle to anyone listening, so the pipeline can be reshaped or
observed without editing its wiring.
"""

import asyncio

from domain.analyst import ProjectAnalyst
from domain.config import PipelineConfig
from domain.context import ContextCollector, ProjectContext
from domain.events import StageEventBus
from domain.stage import Pipeline, PipelineContext, Turn
from domain.stages import STAGE_FACTORIES
from domain.synthesizer import ContextSynthesizer


class ProjectPipeline:
    def __init__(
        self,
        config: PipelineConfig | None = None,
        collector: ContextCollector | None = None,
        analyst: ProjectAnalyst | None = None,
        synthesizer: ContextSynthesizer | None = None,
        events: StageEventBus | None = None,
    ):
        self.config = config or PipelineConfig()
        # None of these have a library-level default: a collector needs a
        # real metadata source, an analyst/synthesizer need a real LLM —
        # domain/ doesn't import infrastructure/ or modules/ to build any
        # of that itself (that would point the dependency arrow the wrong
        # way). The caller (application/rooms.py, in the real app) builds
        # the concrete objects — including calling
        # infrastructure.llm.get_llm(...) — and passes them in. Missing
        # ones surface as a clear error the first time they're needed
        # (collect_context()/start()/ask()/run()), not a confusing
        # AttributeError on None.
        self.collector = collector
        self.analyst = analyst
        self.synthesizer = synthesizer
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
                    "with domain.stages.register_stage()"
                )
            stages.append(factory(self))
        return Pipeline(stages)

    def _run_sync(self, pipeline: Pipeline, turn: Turn) -> Turn:
        """Drive an async Pipeline from this (synchronous) call site.

        Meant to be called from the one worker thread application/rooms.py
        already dedicates to a turn (via `asyncio.to_thread`), so a fresh
        event loop here is exactly the standard, safe way to run async
        code with no loop of its own — see domain/stage.py's module
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

    def _require(self, obj, name: str, example: str) -> None:
        if obj is None:
            raise RuntimeError(
                f"No {name} configured — pass {example} to ProjectPipeline()."
            )

    def collect_context(self, path: str = ".") -> ProjectContext:
        """Stage 1 — collect and cache the private project map."""
        if not self.config.collect:
            raise RuntimeError("collect step is disabled in config.")
        self._require(
            self.collector,
            "ContextCollector",
            "collector=ContextCollector(metadata_fn=...)",
        )
        turn = self._run_sync(self._collect_pipeline, Turn(path=path, query=""))
        self.context = turn.context
        return self.context

    def start(self, path: str = ".") -> ProjectContext:
        """Collect context and open an interactive analysis session."""
        self._require(self.analyst, "ProjectAnalyst", "analyst=ProjectAnalyst(llm=...)")
        context = self.collect_context(path)
        self.analyst.start_session(context)
        return context

    def ask(self, query: str) -> str:
        """Stage 2 only — ask a question in the current session.

        Remembers prior turns, so follow-up questions can build on
        earlier answers. Call start() first.
        """
        self._require(self.analyst, "ProjectAnalyst", "analyst=ProjectAnalyst(llm=...)")
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
        self._require(self.analyst, "ProjectAnalyst", "analyst=ProjectAnalyst(llm=...)")
        if self.context is None:
            raise RuntimeError("Call collect_context() before run().")
        if "analyze" not in self.config.stages:
            raise RuntimeError("analyze step is disabled in config.")
        if "synthesize" in self.config.stages:
            self._require(
                self.synthesizer,
                "ContextSynthesizer",
                "synthesizer=ContextSynthesizer(llm=...)",
            )

        self.analyst.start_session(self.context)  # a fresh, one-shot session
        turn = Turn(path=self.context.path, query=query, context=self.context)
        turn = self._run_sync(self._run_pipeline, turn)
        return turn.synthesized if turn.synthesized is not None else turn.answer


__all__ = ["ProjectPipeline", "PipelineConfig", "ProjectContext"]

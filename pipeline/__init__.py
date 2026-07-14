"""Project-analysis pipeline.

Orchestrates the separated, configurable stages:
    1. ContextCollector   — gather a private project map.
    2. ProjectAnalyst     — reason over that map and answer questions.
    3. ContextSynthesizer — convert an answer into AI-ready context.

Two ways to drive it:
    - start(path) + ask(query): interactive stage 1 + 2, with the analyst
      remembering prior turns. Use this for a Q&A session.
    - run(query): one-shot stage 2 + 3, producing a compact synthesized
      answer for machine consumption. Requires collect_context() first.

Which stages run, and how each behaves, is driven by PipelineConfig. Any
stage can also be swapped for a custom implementation via the
constructor, so the pipeline can be reshaped without editing its wiring.
"""

from pipeline.analyst import ProjectAnalyst
from pipeline.config import PipelineConfig
from pipeline.context import ContextCollector, ProjectContext
from pipeline.synthesizer import ContextSynthesizer


class ProjectPipeline:
    def __init__(
        self,
        config: PipelineConfig | None = None,
        collector: ContextCollector | None = None,
        analyst: ProjectAnalyst | None = None,
        synthesizer: ContextSynthesizer | None = None,
    ):
        self.config = config or PipelineConfig()
        self.collector = collector or ContextCollector()
        self.analyst = analyst or ProjectAnalyst(
            temperature=self.config.analysis_temperature
        )
        self.synthesizer = synthesizer or ContextSynthesizer(
            temperature=self.config.synthesis_temperature,
            fmt=self.config.synthesis_format,
        )
        self.context: ProjectContext | None = None

    def collect_context(self, path: str = ".") -> ProjectContext:
        """Stage 1 — collect and cache the private project map."""
        if not self.config.collect:
            raise RuntimeError("collect step is disabled in config.")
        self.context = self.collector.collect(path)
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
        return self.analyst.ask(query)

    def run(self, query: str) -> str:
        """Stages 2–3 — answer the query, then synthesize AI-ready context.

        Returns the synthesized context when the synthesize step is
        enabled, otherwise the analyst's raw answer.
        """
        if self.context is None:
            raise RuntimeError("Call collect_context() before run().")
        if not self.config.analyze:
            raise RuntimeError("analyze step is disabled in config.")

        answer = self.analyst.analyze(query, self.context)

        if not self.config.synthesize:
            return answer
        return self.synthesizer.synthesize(answer, self.context)


__all__ = ["ProjectPipeline", "PipelineConfig", "ProjectContext"]

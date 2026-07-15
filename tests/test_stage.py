"""Tests for the pipeline Stage/Pipeline system (pipeline/stage.py) and
its concrete stages (pipeline/stages.py) — composability, reordering via
configuration, cancellation, and error propagation.
"""

import unittest

from pipeline import PipelineConfig, ProjectPipeline
from pipeline.context import ProjectContext
from pipeline.stage import Pipeline, PipelineCancelled, PipelineContext, Stage, Turn


class RecordingStage(Stage):
    """Appends its name to a shared log; optionally raises."""

    def __init__(self, name: str, log: list[str], fail: bool = False):
        self.name = name
        self.log = log
        self.fail = fail

    async def run(self, turn: Turn, ctx: PipelineContext) -> Turn:
        self.log.append(self.name)
        if self.fail:
            raise ValueError(f"{self.name} exploded")
        return turn


class TestPipelineComposition(unittest.IsolatedAsyncioTestCase):
    async def test_stages_run_in_the_given_order(self):
        log: list[str] = []
        pipeline = Pipeline([RecordingStage("a", log), RecordingStage("b", log)])
        await pipeline.run(Turn(path=".", query="q"))
        self.assertEqual(log, ["a", "b"])

    async def test_reordering_the_stage_list_reorders_execution(self):
        log: list[str] = []
        pipeline = Pipeline([RecordingStage("b", log), RecordingStage("a", log)])
        await pipeline.run(Turn(path=".", query="q"))
        self.assertEqual(log, ["b", "a"])

    async def test_turn_flows_through_and_accumulates_stage_output(self):
        class SetAnswer(Stage):
            name = "set_answer"

            async def run(self, turn, ctx):
                turn.answer = "the answer"
                return turn

        class SetSynthesized(Stage):
            name = "set_synthesized"

            async def run(self, turn, ctx):
                turn.synthesized = f"synth({turn.answer})"
                return turn

        pipeline = Pipeline([SetAnswer(), SetSynthesized()])
        turn = await pipeline.run(Turn(path=".", query="q"))
        self.assertEqual(turn.answer, "the answer")
        self.assertEqual(turn.synthesized, "synth(the answer)")


class TestPipelineCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_before_run_stops_every_stage(self):
        log: list[str] = []
        ctx = PipelineContext()
        ctx.cancel()
        pipeline = Pipeline([RecordingStage("a", log)])

        with self.assertRaises(PipelineCancelled):
            await pipeline.run(Turn(path=".", query="q"), ctx)
        self.assertEqual(log, [])

    async def test_cancelling_mid_run_stops_the_next_stage(self):
        log: list[str] = []
        ctx = PipelineContext()

        class CancellingStage(Stage):
            name = "canceller"

            async def run(self, turn, ctx):
                log.append(self.name)
                ctx.cancel()
                return turn

        pipeline = Pipeline([CancellingStage(), RecordingStage("never", log)])
        with self.assertRaises(PipelineCancelled):
            await pipeline.run(Turn(path=".", query="q"), ctx)
        self.assertEqual(log, ["canceller"])


class TestPipelineErrorPropagation(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_stage_stops_the_pipeline_and_reraises(self):
        log: list[str] = []
        pipeline = Pipeline(
            [
                RecordingStage("a", log),
                RecordingStage("b", log, fail=True),
                RecordingStage("c", log),
            ]
        )
        with self.assertRaises(ValueError):
            await pipeline.run(Turn(path=".", query="q"))
        self.assertEqual(log, ["a", "b"])  # c never ran


class RecordingObserver:
    def __init__(self):
        self.events: list[tuple] = []

    def on_stage_started(self, stage_name, turn):
        self.events.append(("started", stage_name))

    def on_stage_completed(self, stage_name, turn):
        self.events.append(("completed", stage_name))

    def on_stage_failed(self, stage_name, turn, exc):
        self.events.append(("failed", stage_name))


class TestPipelineLifecycleEvents(unittest.IsolatedAsyncioTestCase):
    async def test_events_fire_around_each_stage(self):
        from pipeline.events import StageEventBus

        bus = StageEventBus()
        observer = RecordingObserver()
        bus.subscribe(observer)

        pipeline = Pipeline([RecordingStage("a", []), RecordingStage("b", [])])
        await pipeline.run(Turn(path=".", query="q"), PipelineContext(events=bus))

        self.assertEqual(
            observer.events,
            [
                ("started", "a"),
                ("completed", "a"),
                ("started", "b"),
                ("completed", "b"),
            ],
        )

    async def test_failure_event_fires_instead_of_completed(self):
        from pipeline.events import StageEventBus

        bus = StageEventBus()
        observer = RecordingObserver()
        bus.subscribe(observer)

        pipeline = Pipeline([RecordingStage("a", [], fail=True)])
        with self.assertRaises(ValueError):
            await pipeline.run(Turn(path=".", query="q"), PipelineContext(events=bus))

        self.assertEqual(observer.events, [("started", "a"), ("failed", "a")])


class FakeCollector:
    def collect(self, path):
        return ProjectContext(path=path, raw="{}")


class FakeAnalyst:
    def __init__(self):
        self._messages: list = []

    def start_session(self, context):
        self._messages = [{"role": "system", "content": "ctx"}]

    def ask(self, query):
        return f"answer to: {query}"

    def resume(self, messages):
        self._messages = messages

    @property
    def messages(self):
        return self._messages


class FakeSynthesizer:
    def synthesize(self, answer, context):
        return f"SYNTH[{answer}]"


class TestProjectPipelineIntegration(unittest.TestCase):
    """ProjectPipeline as a whole: correct external behavior for
    .start()/.ask()/.run(), and PipelineConfig.stages actually driving
    what .run() does — the "configuration, not hardcoded" requirement."""

    def _pipeline(self, stages=None):
        if stages is None:
            stages = ["analyze", "synthesize"]
        config = PipelineConfig(stages=stages)
        return ProjectPipeline(
            config=config,
            collector=FakeCollector(),
            analyst=FakeAnalyst(),
            synthesizer=FakeSynthesizer(),
        )

    def test_ask_returns_prose_never_synthesized(self):
        pipeline = self._pipeline()
        pipeline.start("/some/path")
        self.assertEqual(pipeline.ask("hi"), "answer to: hi")

    def test_run_returns_synthesized_by_default(self):
        pipeline = self._pipeline()
        pipeline.collect_context("/some/path")
        self.assertEqual(pipeline.run("hi"), "SYNTH[answer to: hi]")

    def test_run_skips_synthesis_when_dropped_from_config(self):
        pipeline = self._pipeline(stages=["analyze"])
        pipeline.collect_context("/some/path")
        self.assertEqual(pipeline.run("hi"), "answer to: hi")

    def test_unknown_stage_name_raises_at_construction(self):
        with self.assertRaises(ValueError):
            self._pipeline(stages=["not-a-real-stage"])

    def test_collector_missing_raises_a_clear_error(self):
        pipeline = ProjectPipeline(analyst=FakeAnalyst(), synthesizer=FakeSynthesizer())
        with self.assertRaisesRegex(RuntimeError, "No ContextCollector configured"):
            pipeline.collect_context(".")


if __name__ == "__main__":
    unittest.main()

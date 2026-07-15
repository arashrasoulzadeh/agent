"""Headless tests for the AgentApp TUI.

These never construct a real LLM or touch the network — the pipeline is a
stub that returns canned text instantly — so running this suite never
spends real API tokens.
"""

import time
import unittest

from modules.ask import ask as ask_tool
from ui import trace
from ui.app import AgentApp


class StubPipeline:
    """A pipeline that never touches the network."""

    def __init__(self):
        self.started_with = None
        self.questions: list[str] = []

    def start(self, path: str = ".") -> None:
        self.started_with = path

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if question == "trigger-ask-tool":
            reply = ask_tool.invoke({"question": "What should I call this?"})
            return f"got: {reply}"
        return f"stub answer to: {question}"


class FailingPipeline:
    """A pipeline whose project load always fails."""

    def start(self, path: str = ".") -> None:
        raise ValueError("bad project path")

    def ask(self, question: str) -> str:
        raise AssertionError("should never be reached")


class TestAgentAppLayout(unittest.IsolatedAsyncioTestCase):
    async def test_regions_always_sum_to_the_full_screen(self):
        # .region is the widget's full allocated box (including its
        # border); .size is just the inner content area, which is what
        # actually matters for "never exceeds the terminal".
        app = AgentApp(StubPipeline(), ".")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.2)
            header = app.query_one("#header").region
            content = app.query_one("#content").region
            footer = app.query_one("#footer").region
            self.assertEqual(header.height + content.height + footer.height, 40)
            self.assertLess(header.height, content.height)
            self.assertLess(footer.height, content.height)

    async def test_resize_reflows_without_exceeding_the_terminal(self):
        app = AgentApp(StubPipeline(), ".")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.resize_terminal(120, 60)
            await pilot.pause(0.1)
            header = app.query_one("#header").region
            content = app.query_one("#content").region
            footer = app.query_one("#footer").region
            self.assertEqual(header.height + content.height + footer.height, 60)
            self.assertEqual(header.width, 120)

    async def test_header_and_footer_are_sized_to_their_own_content(self):
        # height: auto, not a fixed quota — idle, the header is exactly
        # banner/tokens + model/url + tools (3 lines + border), and the
        # footer is exactly info + input (2 lines + border).
        app = AgentApp(StubPipeline(), ".")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            self.assertEqual(app.query_one("#header").region.height, 4)
            self.assertEqual(app.query_one("#footer").region.height, 3)

    async def test_header_grows_while_working_then_shrinks_back(self):
        app = AgentApp(StubPipeline(), ".")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            idle_height = app.query_one("#header").region.height

            def _do_work():
                with trace.working("thinking"):
                    time.sleep(0.2)

            worker = app.run_worker(_do_work, thread=True)
            await pilot.pause(0.1)
            self.assertEqual(app.query_one("#header").region.height, idle_height + 1)

            await worker.wait()
            await pilot.pause(0.05)
            self.assertEqual(app.query_one("#header").region.height, idle_height)

    async def test_footer_input_has_no_background(self):
        app = AgentApp(StubPipeline(), ".")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            self.assertEqual(app.query_one("#footer-input").styles.background.a, 0)


class TestAgentAppFlow(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_runs_then_follow_up_reaches_the_pipeline(self):
        pipeline = StubPipeline()
        app = AgentApp(pipeline, "/some/project")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual(pipeline.started_with, "/some/project")
            self.assertTrue(pipeline.questions)
            self.assertIn("clear overview", pipeline.questions[0])
            self.assertFalse(app._turn_active)

            app.query_one("#footer-input").focus()
            await pilot.press(*"hello there")
            await pilot.press("enter")
            await pilot.pause(0.2)

            self.assertIn("hello there", pipeline.questions)

    async def test_ask_tool_blocks_the_worker_until_answered(self):
        pipeline = StubPipeline()
        app = AgentApp(pipeline, ".")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.3)  # let the bootstrap turn finish

            app.query_one("#footer-input").focus()
            await pilot.press(*"trigger-ask-tool")
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertTrue(app._awaiting_reply)

            await pilot.press(*"Widget")
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertFalse(app._awaiting_reply)
            self.assertFalse(app._turn_active)

    async def test_active_tool_highlights_during_its_call_then_clears(self):
        # `pilot.press()` itself takes some wall time to settle, so the
        # tool call needs to hold long enough that polling can reliably
        # catch it mid-flight rather than assuming a fixed delay lands
        # inside that window.
        class ToolCallingPipeline(StubPipeline):
            def ask(self, question: str) -> str:
                if question == "use-tool":
                    trace.tool_call("cat", "path='README.md'")
                    time.sleep(0.5)
                    trace.tool_result("# hi")
                    return "done"
                return super().ask(question)

        app = AgentApp(ToolCallingPipeline(), ".")
        async with app.run_test(size=(100, 40)) as pilot:
            for _ in range(20):
                await pilot.pause(0.05)
                if not app._turn_active:
                    break
            self.assertFalse(app._turn_active)
            self.assertIsNone(app._active_tool)

            app.query_one("#footer-input").focus()
            await pilot.press(*"use-tool")
            await pilot.press("enter")

            for _ in range(20):
                await pilot.pause(0.05)
                if app._active_tool is not None:
                    break
            self.assertEqual(app._active_tool, "cat")

            for _ in range(20):
                await pilot.pause(0.05)
                if app._active_tool is None:
                    break
            self.assertIsNone(app._active_tool)

    async def test_startup_failure_exits_cleanly_instead_of_hanging(self):
        app = AgentApp(FailingPipeline(), "/nonexistent")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.5)
            self.assertFalse(app.is_running)
            self.assertEqual(app.return_code, 1)


if __name__ == "__main__":
    unittest.main()

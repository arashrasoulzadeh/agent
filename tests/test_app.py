"""Headless tests for the AgentApp TUI client.

AgentApp is a thin WebSocket client (see ui/app.py) — these tests run a
real server (tests/stubs.py's running_server) with a stubbed pipeline and
point a real AgentApp at it, exercising the actual wire protocol end to
end. Nothing here touches the network or spends a real API token.
"""

import asyncio
import unittest

from interfaces.cli.app import AgentApp
from tests.stubs import (
    AskToolPipeline,
    FailingPipeline,
    SlowPipeline,
    StubPipeline,
    ToolCallingPipeline,
    running_server,
)


async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(interval)


def log_text(app: AgentApp) -> str:
    content = app.query_one("#content")
    return "\n".join(strip.text for strip in content.lines)


class TestAgentAppLayout(unittest.IsolatedAsyncioTestCase):
    async def test_regions_always_sum_to_the_full_screen(self):
        # .region is the widget's full allocated box (including its
        # border); .size is just the inner content area, which is what
        # actually matters for "never exceeds the terminal".
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                await pilot.pause(0.1)
                header = app.query_one("#header").region
                content = app.query_one("#content").region
                footer = app.query_one("#footer").region
                self.assertEqual(header.height + content.height + footer.height, 40)
                self.assertLess(header.height, content.height)
                self.assertLess(footer.height, content.height)

    async def test_resize_reflows_without_exceeding_the_terminal(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                await pilot.resize_terminal(120, 60)
                await pilot.pause(0.1)
                header = app.query_one("#header").region
                content = app.query_one("#content").region
                footer = app.query_one("#footer").region
                self.assertEqual(header.height + content.height + footer.height, 60)
                self.assertEqual(header.width, 120)

    async def test_header_grows_while_working_then_shrinks_back(self):
        async with running_server(SlowPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 30)) as pilot:
                await wait_until(lambda: not app.turn_active)
                await pilot.pause(0.1)
                idle_height = app.query_one("#header").region.height

                app.query_one("#footer-input").focus()
                await pilot.press(*"hello")
                await pilot.press("enter")
                await wait_until(lambda: app.turn_active)
                self.assertEqual(
                    app.query_one("#header").region.height, idle_height + 1
                )

                await wait_until(lambda: not app.turn_active)
                await pilot.pause(0.05)
                self.assertEqual(app.query_one("#header").region.height, idle_height)

    async def test_footer_input_has_no_background(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 30)):
                self.assertEqual(app.query_one("#footer-input").styles.background.a, 0)


class TestAgentAppFlow(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_runs_then_follow_up_reaches_the_pipeline(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, "/some/project")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                self.assertIn("clear overview", log_text(app))

                app.query_one("#footer-input").focus()
                await pilot.press(*"hello there")
                await pilot.press("enter")
                await wait_until(lambda: "hello there" in log_text(app))
                await wait_until(lambda: not app.turn_active)
                self.assertIn("stub answer to: hello there", log_text(app))

    async def test_ask_tool_round_trips_through_question_and_reply(self):
        async with running_server(AskToolPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"ask-me")
                await pilot.press("enter")
                await wait_until(lambda: app.awaiting_reply)
                self.assertIn("what should I call this?", log_text(app))

                await pilot.press(*"Widget")
                await pilot.press("enter")
                await wait_until(lambda: not app.awaiting_reply and not app.turn_active)
                self.assertIn("got: Widget", log_text(app))

    async def test_tool_call_and_result_land_in_the_transcript(self):
        # The stub resolves near instantly, so this checks the durable
        # outcome (the trace + answer landed in the log) rather than
        # racing to observe the transient active_tool="cat" state.
        async with running_server(ToolCallingPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"use-tool")
                await pilot.press("enter")
                await wait_until(
                    lambda: not app.turn_active and app.active_tool is None
                )

                text = log_text(app)
                self.assertIn("cat", text)
                self.assertIn("README.md", text)
                self.assertIn("# hi", text)
                self.assertIn("done", text)

    async def test_startup_failure_shows_an_error_without_crashing(self):
        async with running_server(FailingPipeline) as uri:
            app = AgentApp(uri, "/nonexistent")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: "bad project path" in log_text(app))
                await pilot.pause(0.05)
                self.assertTrue(app.is_running)


if __name__ == "__main__":
    unittest.main()

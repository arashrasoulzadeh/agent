"""Headless tests for the AgentApp TUI client.

AgentApp is a thin WebSocket client (see ui/app.py) — these tests run a
real server (tests/stubs.py's running_server) with a stubbed pipeline and
point a real AgentApp at it, exercising the actual wire protocol end to
end. Nothing here touches the network or spends a real API token.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from textual.widgets import Button, Input, OptionList, Static

from core import settings
from tests.stubs import (
    AskToolPipeline,
    FailingPipeline,
    SlowPipeline,
    StubPipeline,
    ToolCallingPipeline,
    running_server,
)
from ui.app import (
    _COMMAND_HELP,
    AgentApp,
    QuestionModal,
    SettingsModal,
    _parse_command,
)


async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(interval)


def log_text(app: AgentApp) -> str:
    content = app.query_one("#content")
    return "\n".join(strip.text for strip in content.lines)


def footer_info_text(app: AgentApp) -> str:
    # Static has no public renderable-string accessor; the content it was
    # last update()'d with is stashed under this name-mangled attribute.
    return app.query_one("#footer-info", Static)._Static__content


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

    async def test_ask_tool_with_options_shows_modal_and_click_replies(self):
        async with running_server(AskToolPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"ask-with-options")
                await pilot.press("enter")
                await wait_until(lambda: app.awaiting_reply)
                await wait_until(lambda: isinstance(app.screen, QuestionModal))
                self.assertIn("pick one", log_text(app))
                # app.query() only searches the base screen — a pushed
                # modal's widgets live on app.screen (the top of the
                # screen stack) instead.
                self.assertEqual(
                    [b.label.plain for b in app.screen.query(Button)],
                    ["a", "b", "c"],
                )

                await pilot.click("#opt-1")  # "b"
                await wait_until(lambda: not app.awaiting_reply and not app.turn_active)
                self.assertIn("got: b", log_text(app))

    async def test_question_modal_escape_falls_back_to_free_text(self):
        async with running_server(AskToolPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"ask-with-options")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, QuestionModal))

                await pilot.press("escape")
                await wait_until(lambda: not isinstance(app.screen, QuestionModal))
                self.assertTrue(app.awaiting_reply)  # still pending, free text works

                app.query_one("#footer-input").focus()
                await pilot.press(*"c")
                await pilot.press("enter")
                await wait_until(lambda: not app.awaiting_reply and not app.turn_active)
                self.assertIn("got: c", log_text(app))

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


class TestParseCommand(unittest.TestCase):
    def test_recognizes_add(self):
        self.assertEqual(_parse_command("/add ../backend"), ("/add", ["../backend"]))

    def test_recognizes_add_with_name(self):
        self.assertEqual(
            _parse_command("/add ../backend backend"),
            ("/add", ["../backend", "backend"]),
        )

    def test_recognizes_remove(self):
        self.assertEqual(_parse_command("/remove backend"), ("/remove", ["backend"]))

    def test_recognizes_projects_with_no_args(self):
        self.assertEqual(_parse_command("/projects"), ("/projects", []))

    def test_recognizes_settings_with_no_args(self):
        self.assertEqual(_parse_command("/settings"), ("/settings", []))

    def test_plain_text_is_not_a_command(self):
        self.assertIsNone(_parse_command("hello there"))

    def test_bare_y_is_never_a_command(self):
        # Must never be misparsed while a resync confirmation is pending.
        self.assertIsNone(_parse_command("y"))

    def test_bare_n_is_never_a_command(self):
        self.assertIsNone(_parse_command("n"))

    def test_unrecognized_slash_command_is_not_a_command(self):
        self.assertIsNone(_parse_command("/help"))

    def test_empty_string_is_not_a_command(self):
        self.assertIsNone(_parse_command(""))


class TestAgentAppProjectCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base_dir = Path(tempfile.mkdtemp())
        self.primary_dir = Path(tempfile.mkdtemp())
        (self.primary_dir / "main.py").write_text("x = 1\n")
        self.backend_dir = Path(tempfile.mkdtemp())
        (self.backend_dir / "app.py").write_text("y = 2\n")

    def tearDown(self):
        shutil.rmtree(self.base_dir, ignore_errors=True)
        shutil.rmtree(self.primary_dir, ignore_errors=True)
        shutil.rmtree(self.backend_dir, ignore_errors=True)

    async def test_add_command_attaches_project_and_updates_state(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.primary_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*f"/add {self.backend_dir} backend")
                await pilot.press("enter")

                await wait_until(
                    lambda: any(p["name"] == "backend" for p in app.projects)
                )
                await wait_until(lambda: not app.turn_active)
                self.assertIn("projects backend", footer_info_text(app))

    async def test_remove_command_detaches_project(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.primary_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*f"/add {self.backend_dir} backend")
                await pilot.press("enter")
                await wait_until(
                    lambda: any(p["name"] == "backend" for p in app.projects)
                )
                await wait_until(lambda: not app.turn_active)

                await pilot.press(*"/remove backend")
                await pilot.press("enter")
                await wait_until(
                    lambda: all(p["name"] != "backend" for p in app.projects)
                )
                await wait_until(lambda: not app.turn_active)
                self.assertNotIn("backend", footer_info_text(app))

    async def test_projects_command_renders_locally_without_a_request(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.primary_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/projects")
                await pilot.press("enter")
                await wait_until(lambda: "Attached projects" in log_text(app))

                # No request was ever sent for this command — nothing left
                # pending, and the turn state never toggled.
                self.assertEqual(app._pending, {})
                self.assertFalse(app.turn_active)
                self.assertIn("project (primary)", log_text(app))

    async def test_add_usage_shown_locally_when_path_missing(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.primary_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/add")
                await pilot.press("enter")
                await wait_until(lambda: "Usage: /add" in log_text(app))
                self.assertEqual(app._pending, {})


class TestAgentAppSettings(unittest.IsolatedAsyncioTestCase):
    """Same running_server + AgentApp + pilot pattern as
    TestAgentAppProjectCommands, plus isolating core.settings'
    SETTINGS_FILE and every known setting's env var — this class is the
    one place in the suite that actually writes real settings, so
    nothing here may leak into another test or the real environment."""

    def setUp(self):
        self.base_dir = Path(tempfile.mkdtemp())
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "main.py").write_text("x = 1\n")

        self.settings_dir = Path(tempfile.mkdtemp())
        self._original_settings_file = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = self.settings_dir / "settings.json"

        self._original_env = {}
        for spec in settings.SETTINGS:
            self._original_env[spec.key] = os.environ.pop(spec.key, None)

    def tearDown(self):
        settings.SETTINGS_FILE = self._original_settings_file
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.base_dir, ignore_errors=True)
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.settings_dir, ignore_errors=True)

    async def test_settings_command_opens_modal_with_every_setting(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                inputs = app.screen.query(Input)
                self.assertEqual(
                    {i.id for i in inputs},
                    {f"setting-{spec.key}" for spec in settings.SETTINGS},
                )

    async def test_non_secret_field_shows_its_default_value(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                model_input = app.screen.query_one("#setting-GAPGPT_MODEL", Input)
                self.assertEqual(model_input.value, "gpt-4o-mini")

    async def test_secret_field_starts_blank_even_when_set(self):
        settings.update_setting("NOTION_API_KEY", "sk-already-set")
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                notion_input = app.screen.query_one("#setting-NOTION_API_KEY", Input)
                self.assertEqual(notion_input.value, "")
                self.assertTrue(notion_input.password)

    async def test_editing_a_non_secret_field_sends_update(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                # AGENT_VERBOSE has no default, so its Input starts empty —
                # no need to clear existing text before typing.
                app.screen.query_one("#setting-AGENT_VERBOSE", Input).focus()
                await pilot.press(*"1")
                await pilot.press("enter")

                await wait_until(lambda: "Saved" in log_text(app))
                self.assertEqual(os.environ.get("AGENT_VERBOSE"), "1")

    async def test_blank_secret_field_submit_sends_nothing(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                app.screen.query_one("#setting-GAPGPT_API_KEY", Input).focus()
                await pilot.press("enter")
                await pilot.pause(0.1)

                self.assertEqual(app._pending, {})
                self.assertNotIn("Saved", log_text(app))
                self.assertFalse(settings.SETTINGS_FILE.exists())

    async def test_typed_secret_field_submit_saves_and_clears_the_field(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                key_input = app.screen.query_one("#setting-NOTION_API_KEY", Input)
                key_input.focus()
                await pilot.press(*"sk-typed-secret")
                await pilot.press("enter")

                await wait_until(lambda: "Saved" in log_text(app))
                self.assertEqual(os.environ.get("NOTION_API_KEY"), "sk-typed-secret")
                self.assertEqual(key_input.value, "")

    async def test_escape_closes_the_modal(self):
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                await pilot.press("escape")
                await wait_until(lambda: not isinstance(app.screen, SettingsModal))

    async def test_settings_field_submit_never_reaches_prompt_handling(self):
        # A settings Input's Submitted event must be stopped before it
        # bubbles to AgentApp's own on_input_submitted — otherwise the
        # exact same keystroke would also be treated as chat text (or a
        # resync/reply answer), sent as a stray /prompt.
        async with running_server(StubPipeline, base_dir=self.base_dir) as uri:
            app = AgentApp(uri, str(self.project_dir))
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)

                app.query_one("#footer-input").focus()
                await pilot.press(*"/settings")
                await pilot.press("enter")
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

                app.screen.query_one("#setting-AGENT_VERBOSE", Input).focus()
                await pilot.press(*"SETTINGVALUE123")
                await pilot.press("enter")

                await wait_until(lambda: "Saved" in log_text(app))
                await pilot.pause(0.1)
                self.assertNotIn("stub answer to: SETTINGVALUE123", log_text(app))
                self.assertFalse(app.turn_active)


class TestCommandPopup(unittest.IsolatedAsyncioTestCase):
    """The '/' command popup (see AgentApp._update_command_popup):
    shown while the footer input's first token is an ambiguous or
    incomplete command prefix, navigable with the keyboard or mouse,
    and never confused with a real chat message or command submission."""

    async def test_shows_every_command_on_bare_slash(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)
                self.assertFalse(popup.display)

                await pilot.press("/")
                await pilot.pause(0.05)
                self.assertTrue(popup.display)
                ids = [
                    popup.get_option_at_index(i).id for i in range(popup.option_count)
                ]
                self.assertEqual(ids, [c[0] for c in _COMMAND_HELP])
                self.assertEqual(popup.highlighted, 0)

    async def test_filters_to_matching_prefix(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press(*"/s")
                await pilot.pause(0.05)
                self.assertTrue(popup.display)
                self.assertEqual(popup.option_count, 1)
                self.assertEqual(popup.get_option_at_index(0).id, "/settings")

    async def test_hides_when_nothing_matches(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press(*"/xyz")
                await pilot.pause(0.05)
                self.assertFalse(popup.display)

    async def test_stays_visible_on_exact_match_before_a_space(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press(*"/settings")
                await pilot.pause(0.05)
                self.assertTrue(popup.display)

    async def test_hides_once_typing_moves_past_an_exact_match(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press(*"/add path")
                await pilot.pause(0.05)
                self.assertFalse(popup.display)

    async def test_up_down_navigation_wraps(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press("/")
                await pilot.pause(0.05)
                self.assertEqual(popup.highlighted, 0)

                await pilot.press("up")
                await pilot.pause(0.05)
                self.assertEqual(popup.highlighted, popup.option_count - 1)

                await pilot.press("down")
                await pilot.pause(0.05)
                self.assertEqual(popup.highlighted, 0)

    async def test_escape_dismisses_without_touching_input(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press("/")
                await pilot.pause(0.05)
                self.assertTrue(popup.display)

                await pilot.press("escape")
                await pilot.pause(0.05)
                self.assertFalse(popup.display)
                self.assertEqual(app.query_one("#footer-input", Input).value, "/")

    async def test_enter_accepts_highlighted_suggestion_without_submitting(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()

                await pilot.press(*"/s")
                await pilot.pause(0.05)
                await pilot.press("enter")
                await pilot.pause(0.05)

                self.assertEqual(
                    app.query_one("#footer-input", Input).value, "/settings "
                )
                self.assertFalse(app.turn_active)
                self.assertEqual(app._pending, {})
                self.assertFalse(app.query_one("#command-popup", OptionList).display)

    async def test_second_enter_after_accepting_submits_the_command(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()

                await pilot.press(*"/s")
                await pilot.pause(0.05)
                await pilot.press("enter")  # accept -> "/settings "
                await pilot.pause(0.05)
                await pilot.press("enter")  # submit
                await wait_until(lambda: isinstance(app.screen, SettingsModal))

    async def test_click_selects_and_fills_the_input(self):
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                popup = app.query_one("#command-popup", OptionList)

                await pilot.press("/")
                await pilot.pause(0.05)
                popup.highlighted = 2  # "/projects"
                popup.action_select()
                await pilot.pause(0.05)

                self.assertEqual(
                    app.query_one("#footer-input", Input).value, "/projects "
                )
                self.assertFalse(popup.display)

    async def test_popup_stays_hidden_while_awaiting_reply(self):
        async with running_server(AskToolPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()
                await pilot.press(*"ask-me")
                await pilot.press("enter")
                await wait_until(lambda: app.awaiting_reply)

                await pilot.press("/")
                await pilot.pause(0.05)
                self.assertFalse(app.query_one("#command-popup", OptionList).display)

                # Answer the pending ask() before the test ends — leaving
                # it dangling would leave the server-side worker thread
                # blocked forever on the reply queue (Room._ask_blocking),
                # hanging the whole test run at teardown.
                await pilot.press("backspace")
                await pilot.press(*"done")
                await pilot.press("enter")
                await wait_until(lambda: not app.awaiting_reply and not app.turn_active)

    async def test_unmatched_slash_text_still_falls_through_to_a_prompt(self):
        # Regression guard: _accept_command_popup must return False (and
        # therefore change nothing) when nothing matches, so this old
        # behavior — an unrecognized slash-prefixed line is just sent as
        # a normal chat message — is unaffected by the popup feature.
        async with running_server(StubPipeline) as uri:
            app = AgentApp(uri, ".")
            async with app.run_test(size=(100, 40)) as pilot:
                await wait_until(lambda: not app.turn_active)
                app.query_one("#footer-input", Input).focus()

                await pilot.press(*"/xyz")
                await pilot.press("enter")
                await wait_until(lambda: "stub answer to: /xyz" in log_text(app))


if __name__ == "__main__":
    unittest.main()

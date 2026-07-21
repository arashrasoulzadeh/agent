"""Tests for core/action.py's Action validation, core/action_registry.py's
auto-discovery, and each actions/*.py module's own `_run` — a fake
ActionContext stands in for wire/routes.py's real _RouteActionContext,
matching this project's own precedent of testing against the narrow
interface a module actually depends on, not the concrete class behind it.
"""

import unittest

from actions import ACTIONS
from core.action import Action
from core.action_registry import discover_actions


class FakeActionContext:
    """Records every call instead of doing anything — enough surface to
    satisfy core.action.ActionContext structurally."""

    def __init__(self, projects: list[dict] | None = None):
        self.calls: list[tuple[str, tuple]] = []
        self._projects = projects or []

    async def add_project(self, path, name):
        self.calls.append(("add_project", (path, name)))

    async def remove_project(self, name):
        self.calls.append(("remove_project", (name,)))

    async def show_settings(self):
        self.calls.append(("show_settings", ()))

    async def show_panel(self, title, blocks):
        self.calls.append(("show_panel", (title, blocks)))

    async def info(self, text):
        self.calls.append(("info", (text,)))

    def project_list(self):
        return self._projects


class TestActionValidation(unittest.TestCase):
    def test_name_must_start_with_a_slash(self):
        with self.assertRaises(ValueError):
            Action(name="add", usage="add", description="x", kind="action", run=_noop)

    def test_pre_prompt_without_text_is_rejected(self):
        with self.assertRaises(ValueError):
            Action(name="/x", usage="/x", description="x", kind="pre_prompt")

    def test_post_prompt_without_text_is_rejected(self):
        with self.assertRaises(ValueError):
            Action(name="/x", usage="/x", description="x", kind="post_prompt")

    def test_action_kind_without_run_is_rejected(self):
        with self.assertRaises(ValueError):
            Action(name="/x", usage="/x", description="x", kind="action")

    def test_ui_kind_without_run_is_rejected(self):
        with self.assertRaises(ValueError):
            Action(name="/x", usage="/x", description="x", kind="ui")

    def test_well_formed_pre_prompt_action_constructs_cleanly(self):
        action = Action(
            name="/x", usage="/x", description="x", kind="pre_prompt", text="hi "
        )
        self.assertEqual(action.text, "hi ")

    def test_well_formed_action_kind_constructs_cleanly(self):
        action = Action(
            name="/x", usage="/x", description="x", kind="action", run=_noop
        )
        self.assertIs(action.run, _noop)


async def _noop(ctx, args) -> None:
    return None


class TestActionRegistryDiscovery(unittest.TestCase):
    def test_discovers_every_action_file_under_actions(self):
        found = discover_actions()
        self.assertEqual(
            set(found.keys()),
            {"/add", "/remove", "/projects", "/settings", "/explain", "/tldr"},
        )

    def test_actions_package_exposes_the_same_registry(self):
        # actions/__init__.py runs discovery once at import time — this
        # just confirms it isn't a second, independently-stale copy.
        self.assertEqual(set(ACTIONS.keys()), set(discover_actions().keys()))

    def test_iteration_order_is_deterministic_alphabetical_by_filename(self):
        self.assertEqual(
            list(discover_actions().keys()),
            ["/add", "/explain", "/projects", "/remove", "/settings", "/tldr"],
        )


class TestAddAction(unittest.IsolatedAsyncioTestCase):
    async def test_missing_path_shows_usage(self):
        ctx = FakeActionContext()
        await ACTIONS["/add"].run(ctx, [])
        self.assertEqual(ctx.calls, [("info", ("Usage: /add <path> [name]",))])

    async def test_path_only(self):
        ctx = FakeActionContext()
        await ACTIONS["/add"].run(ctx, ["../other"])
        self.assertEqual(ctx.calls, [("add_project", ("../other", None))])

    async def test_path_and_name(self):
        ctx = FakeActionContext()
        await ACTIONS["/add"].run(ctx, ["../other", "backend"])
        self.assertEqual(ctx.calls, [("add_project", ("../other", "backend"))])


class TestRemoveAction(unittest.IsolatedAsyncioTestCase):
    async def test_missing_name_shows_usage(self):
        ctx = FakeActionContext()
        await ACTIONS["/remove"].run(ctx, [])
        self.assertEqual(ctx.calls, [("info", ("Usage: /remove <name>",))])

    async def test_name_given(self):
        ctx = FakeActionContext()
        await ACTIONS["/remove"].run(ctx, ["backend"])
        self.assertEqual(ctx.calls, [("remove_project", ("backend",))])


class TestProjectsAction(unittest.IsolatedAsyncioTestCase):
    async def test_no_projects_attached(self):
        ctx = FakeActionContext(projects=[])
        await ACTIONS["/projects"].run(ctx, [])
        self.assertEqual(ctx.calls, [("info", ("No projects attached.",))])

    async def test_lists_projects_sorted_with_primary_marked(self):
        ctx = FakeActionContext(
            projects=[
                {"name": "backend", "path": "/b", "primary": False},
                {"name": "project", "path": "/p", "primary": True},
            ]
        )
        await ACTIONS["/projects"].run(ctx, [])
        [(kind, (text,))] = ctx.calls
        self.assertEqual(kind, "info")
        self.assertIn("project (primary)  /p", text)
        self.assertIn("backend (secondary)  /b", text)
        # sorted by name: "backend" before "project"
        self.assertLess(text.index("backend"), text.index("project (primary)"))


class TestSettingsAction(unittest.IsolatedAsyncioTestCase):
    async def test_opens_settings(self):
        ctx = FakeActionContext()
        await ACTIONS["/settings"].run(ctx, [])
        self.assertEqual(ctx.calls, [("show_settings", ())])


class TestExplainAndTldrActions(unittest.TestCase):
    """No `run` for these — see core/action.py's own docstring: they
    never reach the server at all."""

    def test_explain_is_a_pre_prompt_with_no_run(self):
        action = ACTIONS["/explain"]
        self.assertEqual(action.kind, "pre_prompt")
        self.assertIsNone(action.run)
        self.assertTrue(action.text)

    def test_tldr_is_a_post_prompt_with_no_run(self):
        action = ACTIONS["/tldr"]
        self.assertEqual(action.kind, "post_prompt")
        self.assertIsNone(action.run)
        self.assertTrue(action.text)


if __name__ == "__main__":
    unittest.main()

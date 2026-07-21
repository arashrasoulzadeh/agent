"""Tests for service/ui_builder.py: pure Room-state -> Node functions.

No server, no room, no client — every function here takes plain values
and returns a Node, so these are ordinary unit tests asserting on the
returned shape.
"""

import unittest

from actions import ACTIONS
from service import ui_builder


def _ids(node):
    return [c.id for c in node.children]


def _find(node, node_id):
    """Recursively finds a node by id anywhere in the tree — settings
    rows nest their label/input a level deeper than the modal itself."""
    if node.id == node_id:
        return node
    for child in node.children:
        found = _find(child, node_id)
        if found is not None:
            return found
    return None


class TestHeaderNode(unittest.TestCase):
    def test_basic_shape(self):
        node = ui_builder.header_node(
            model="gpt-4o-mini",
            base_url="https://api.gapgpt.app/v1",
            tool_names=["cat", "ls"],
            active_tool=None,
            tokens={"prompt": 0, "completion": 0, "total": 0},
        )
        self.assertEqual(node.type, "container")
        self.assertEqual(node.id, "header")
        ids = _ids(node)
        # Turn status lives in footer_status_node now, not here — see
        # TestFooterStatusNode.
        self.assertNotIn("header-status", ids)
        self.assertIn("connection-status", ids)

    def test_connection_status_slot_is_always_empty(self):
        node = ui_builder.header_node(
            model="m",
            base_url="u",
            tool_names=[],
            active_tool=None,
            tokens={"total": 0},
        )
        slot = next(c for c in node.children if c.id == "connection-status")
        self.assertEqual(slot.props, {})
        self.assertEqual(slot.children, [])

    def test_active_tool_gets_highlighted_span(self):
        node = ui_builder.header_node(
            model="m",
            base_url="u",
            tool_names=["cat", "ls"],
            active_tool="cat",
            tokens={"total": 0},
        )
        tools = next(c for c in node.children if c.id == "header-tools")
        spans = tools.props["spans"]
        # spans[0] is the "  tools  " label prefix — skip it. "ls" is a
        # substring of "tools" itself, so a bare `"ls" in text` match
        # would wrongly hit that prefix span instead of the ls tool's
        # own span; matching on the padded, marker-prefixed form avoids
        # that.
        cat_span = next(s for s in spans if s["text"].strip("▶ ") == "cat")
        ls_span = next(s for s in spans if s["text"].strip("▶ ") == "ls")
        self.assertIn("▶", cat_span["text"])
        self.assertEqual(cat_span["style"], "bold bright_green")
        self.assertNotIn("▶", ls_span["text"])
        self.assertEqual(ls_span["style"], "grey50")

    def test_token_total_is_comma_formatted(self):
        node = ui_builder.header_node(
            model="m",
            base_url="u",
            tool_names=[],
            active_tool=None,
            tokens={"total": 12345},
        )
        tokens_node = next(c for c in node.children if c.id == "header-tokens")
        self.assertIn("12,345", tokens_node.props["text"])


class TestFooterStatusNode(unittest.TestCase):
    def test_idle_when_no_status_label(self):
        node = ui_builder.footer_status_node(None)
        self.assertEqual(node.id, "footer-status")
        self.assertEqual(node.props["text"], "Idle")
        self.assertFalse(node.props["active"])

    def test_shows_the_active_label_while_a_turn_is_running(self):
        node = ui_builder.footer_status_node("thinking")
        self.assertEqual(node.props["text"], "thinking…")
        self.assertTrue(node.props["active"])

    def test_a_different_label_is_not_confused_with_idle(self):
        node = ui_builder.footer_status_node("reading the project")
        self.assertEqual(node.props["text"], "reading the project…")
        self.assertNotEqual(node.props["text"], "Idle")
        self.assertTrue(node.props["active"])


class TestFooterNodes(unittest.TestCase):
    def test_single_project_info_text(self):
        node = ui_builder.footer_info_node(
            "/some/project", [{"name": "project", "primary": True}], "room-1"
        )
        self.assertEqual(node.props["text"], "project /some/project   room room-1")

    def test_multi_project_info_text_lists_sorted_names(self):
        projects = [
            {"name": "backend", "primary": False},
            {"name": "project", "primary": True},
        ]
        node = ui_builder.footer_info_node("/p", projects, "room-1")
        self.assertEqual(node.props["text"], "projects backend, project   room room-1")

    def test_default_placeholder(self):
        node = ui_builder.footer_input_node(awaiting_reply=False, awaiting_resync=False)
        self.assertEqual(node.type, "input")
        self.assertEqual(node.id, "footer-input")
        self.assertIn("follow-up", node.props["placeholder"])
        self.assertFalse(node.props["password"])

    def test_awaiting_reply_placeholder(self):
        node = ui_builder.footer_input_node(awaiting_reply=True, awaiting_resync=False)
        self.assertEqual(node.props["placeholder"], "Your answer…")

    def test_awaiting_resync_placeholder(self):
        node = ui_builder.footer_input_node(awaiting_reply=False, awaiting_resync=True)
        self.assertEqual(node.props["placeholder"], "y/n")

    def test_awaiting_reply_takes_priority_over_resync(self):
        # Mirrors Room's own invariant: these two flags are never both
        # meaningfully true at once, but reply wins if they were.
        node = ui_builder.footer_input_node(awaiting_reply=True, awaiting_resync=True)
        self.assertEqual(node.props["placeholder"], "Your answer…")


class TestCommandListNode(unittest.TestCase):
    def test_contains_every_auto_discovered_action_hidden_by_default(self):
        node = ui_builder.command_list_node()
        self.assertEqual(node.type, "list")
        self.assertFalse(node.props["display"])
        values = [c.props["value"] for c in node.children]
        self.assertEqual(values, list(ACTIONS.keys()))

    def test_every_child_carries_its_action_kind(self):
        node = ui_builder.command_list_node()
        kinds = {c.props["value"]: c.props["kind"] for c in node.children}
        self.assertEqual(kinds["/add"], "action")
        self.assertEqual(kinds["/settings"], "ui")
        self.assertEqual(kinds["/explain"], "pre_prompt")
        self.assertEqual(kinds["/tldr"], "post_prompt")

    def test_only_pre_and_post_prompt_actions_carry_an_expansion(self):
        node = ui_builder.command_list_node()
        by_value = {c.props["value"]: c.props for c in node.children}
        self.assertEqual(by_value["/explain"]["expansion"], "Explain step by step: ")
        self.assertNotIn("expansion", by_value["/add"])
        self.assertNotIn("expansion", by_value["/settings"])


class TestQuestionModalNode(unittest.TestCase):
    def test_none_without_options(self):
        self.assertIsNone(ui_builder.question_modal_node("open ended?", None))
        self.assertIsNone(ui_builder.question_modal_node("open ended?", []))

    def test_one_button_per_option_in_order(self):
        node = ui_builder.question_modal_node("pick one", ["a", "b", "c"])
        options_row = next(c for c in node.children if c.id == "modal-options")
        self.assertEqual(
            [c.id for c in options_row.children], ["opt-0", "opt-1", "opt-2"]
        )
        self.assertEqual(
            [c.props["label"] for c in options_row.children], ["a", "b", "c"]
        )

    def test_question_text_included(self):
        node = ui_builder.question_modal_node("pick one", ["a"])
        question = next(c for c in node.children if c.id == "modal-question")
        self.assertEqual(question.props["text"], "pick one")


class TestSettingsModalNode(unittest.TestCase):
    def _settings(self):
        return [
            {
                "key": "GAPGPT_MODEL",
                "label": "Model",
                "secret": False,
                "scope": "new-rooms",
                "value": "gpt-5",
                "set": True,
            },
            {
                "key": "NOTION_API_KEY",
                "label": "Notion API key",
                "secret": True,
                "scope": "immediate",
                "value": "••••••••",
                "set": True,
            },
        ]

    def test_non_secret_input_prefilled_with_real_value(self):
        node = ui_builder.settings_modal_node(self._settings())
        model_input = _find(node, "setting-GAPGPT_MODEL")
        self.assertEqual(model_input.props["value"], "gpt-5")
        self.assertFalse(model_input.props["password"])

    def test_secret_input_starts_blank_never_the_masked_value(self):
        node = ui_builder.settings_modal_node(self._settings())
        key_input = _find(node, "setting-NOTION_API_KEY")
        self.assertEqual(key_input.props["value"], "")
        self.assertTrue(key_input.props["password"])

    def test_new_rooms_scope_label_gets_suffix(self):
        node = ui_builder.settings_modal_node(self._settings())
        label = _find(node, "setting-GAPGPT_MODEL-label")
        self.assertIn("(new rooms)", label.props["text"])

    def test_immediate_scope_label_has_no_suffix(self):
        node = ui_builder.settings_modal_node(self._settings())
        label = _find(node, "setting-NOTION_API_KEY-label")
        self.assertNotIn("(new rooms)", label.props["text"])

    def test_rows_are_nested_containers_with_label_and_input(self):
        node = ui_builder.settings_modal_node(self._settings())
        row = next(c for c in node.children if c.id == "setting-GAPGPT_MODEL-row")
        self.assertEqual(row.type, "container")
        child_ids = {c.id for c in row.children}
        self.assertEqual(
            child_ids, {"setting-GAPGPT_MODEL-label", "setting-GAPGPT_MODEL"}
        )


class TestContentEntryNode(unittest.TestCase):
    def test_message(self):
        node = ui_builder.content_entry_node("message", "n1", text="hello", role="user")
        self.assertEqual(node.props["text"], "> hello")

    def test_tool_call_has_three_styled_spans(self):
        node = ui_builder.content_entry_node(
            "tool_call", "n1", name="cat", args="path='README.md'"
        )
        spans = node.props["spans"]
        self.assertEqual(len(spans), 3)
        self.assertEqual(spans[1]["text"], "cat")

    def test_tool_call_args_are_truncated(self):
        long_args = "x" * 500
        node = ui_builder.content_entry_node(
            "tool_call", "n1", name="cat", args=long_args
        )
        args_span = node.props["spans"][2]["text"]
        self.assertLess(len(args_span), 200)

    def test_tool_result(self):
        node = ui_builder.content_entry_node("tool_result", "n1", output="# hi")
        self.assertIn("# hi", node.props["text"])
        self.assertTrue(node.props["text"].startswith("← "))

    def test_question(self):
        node = ui_builder.content_entry_node("question", "n1", text="pick one")
        self.assertEqual(node.props["text"], "? pick one")

    def test_answer_is_a_markdown_panel(self):
        node = ui_builder.content_entry_node("answer", "n1", text="**bold**")
        self.assertTrue(node.props["panel"])
        self.assertEqual(node.props["format"], "markdown")
        self.assertEqual(node.props["text"], "**bold**")

    def test_error_is_a_titled_panel(self):
        node = ui_builder.content_entry_node("error", "n1", message="bad project path")
        self.assertTrue(node.props["panel"])
        self.assertEqual(node.props["panel_title"], "error")
        self.assertEqual(node.props["border_style"], "red")

    def test_resync_suggested_mentions_counts(self):
        node = ui_builder.content_entry_node(
            "resync_suggested", "n1", changed=3, total=10, fraction=0.3
        )
        self.assertIn("3 of 10", node.props["text"])

    def test_info(self):
        node = ui_builder.content_entry_node("info", "n1", text="Saved Model.")
        self.assertEqual(node.props["text"], "Saved Model.")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            ui_builder.content_entry_node("nonsense", "n1")

    def test_every_entry_carries_the_given_node_id(self):
        for kind, fields in [
            ("message", {"text": "x", "role": "user"}),
            ("tool_call", {"name": "cat", "args": ""}),
            ("tool_result", {"output": "x"}),
            ("question", {"text": "x"}),
            ("answer", {"text": "x"}),
            ("error", {"message": "x"}),
            ("resync_suggested", {"changed": 1, "total": 2}),
            ("info", {"text": "x"}),
        ]:
            node = ui_builder.content_entry_node(kind, "fixed-id", **fields)
            self.assertEqual(node.id, "fixed-id", msg=kind)


class TestRootTree(unittest.TestCase):
    def _kwargs(self, **overrides):
        base = dict(
            path="/p",
            projects=[{"name": "project", "primary": True}],
            room_id="room-1",
            model="gpt-4o-mini",
            base_url="https://api.gapgpt.app/v1",
            tool_names=["cat"],
            active_tool=None,
            tokens={"total": 0},
            status_label=None,
            awaiting_reply=False,
            awaiting_resync=False,
        )
        base.update(overrides)
        return base

    def test_top_level_shape(self):
        tree = ui_builder.root_tree(**self._kwargs())
        self.assertEqual(tree.id, "root")
        self.assertEqual(_ids(tree), ["header", "content", "footer"])

    def test_content_defaults_to_empty_list(self):
        tree = ui_builder.root_tree(**self._kwargs())
        content = next(c for c in tree.children if c.id == "content")
        self.assertEqual(content.props["kind"], "log")
        self.assertEqual(content.children, [])

    def test_transcript_nodes_are_embedded_in_content(self):
        entry = ui_builder.content_entry_node("message", "n1", text="hi", role="user")
        tree = ui_builder.root_tree(**self._kwargs(transcript_nodes=[entry]))
        content = next(c for c in tree.children if c.id == "content")
        self.assertEqual(content.children, [entry])

    def test_footer_contains_status_info_commands_and_input_in_that_order(self):
        # footer-status leads — readable "idle vs in progress" right
        # above the prompt, before the projects line.
        tree = ui_builder.root_tree(**self._kwargs())
        footer = next(c for c in tree.children if c.id == "footer")
        self.assertEqual(
            _ids(footer),
            ["footer-status", "footer-info", "command-popup", "footer-input"],
        )


class TestAgentUiNode(unittest.TestCase):
    """show_ui's compiler (tool/ui.py -> Room.show_ui -> here). One
    Node in, no I/O — the same "pure function" contract every other
    builder in this module follows, so a malformed LLM-authored block
    is exercised directly, not just through a real tool call."""

    def test_basic_shape_is_a_panel_container(self):
        node = ui_builder.agent_ui_node(
            "n1", "Comparison", [{"kind": "text", "text": "hi"}], []
        )
        self.assertEqual(node.type, "container")
        self.assertTrue(node.props["panel"])
        self.assertEqual(node.props["panel_title"], "✦ Comparison")
        self.assertEqual(node.props["border_style"], "bright_cyan")

    def test_no_title_falls_back_to_the_bare_icon(self):
        node = ui_builder.agent_ui_node("n1", None, [], [])
        self.assertEqual(node.props["panel_title"], "✦")

    def test_one_child_per_block_in_order(self):
        blocks = [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}]
        node = ui_builder.agent_ui_node("n1", None, blocks, [])
        self.assertEqual(_ids(node), ["n1-block-0", "n1-block-1"])

    def test_no_quick_replies_means_no_button_row(self):
        node = ui_builder.agent_ui_node("n1", None, [], [])
        self.assertEqual(node.children, [])

    def test_quick_replies_become_a_trailing_horizontal_button_row(self):
        replies = [{"id": "quick-1", "label": "Yes"}, {"id": "quick-2", "label": "No"}]
        node = ui_builder.agent_ui_node("n1", None, [], replies)
        row = node.children[-1]
        self.assertEqual(row.id, "n1-replies")
        self.assertEqual(row.props["direction"], "horizontal")
        self.assertEqual([c.type for c in row.children], ["button", "button"])
        self.assertEqual([c.id for c in row.children], ["quick-1", "quick-2"])
        self.assertEqual([c.props["label"] for c in row.children], ["Yes", "No"])

    def test_block_kind_text(self):
        node = ui_builder.agent_ui_node(
            "n1", None, [{"kind": "text", "text": "hi"}], []
        )
        block = node.children[0]
        self.assertEqual(block.type, "text")
        self.assertEqual(block.props["text"], "hi")
        self.assertNotIn("format", block.props)

    def test_block_kind_markdown(self):
        blocks = [{"kind": "markdown", "text": "**b**"}]
        block = ui_builder.agent_ui_node("n1", None, blocks, []).children[0]
        self.assertEqual(block.props["format"], "markdown")
        self.assertEqual(block.props["text"], "**b**")

    def test_block_kind_table_is_a_real_table_node_not_formatted_text(self):
        blocks = [
            {"kind": "table", "headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]}
        ]
        block = ui_builder.agent_ui_node("n1", None, blocks, []).children[0]
        self.assertEqual(block.type, "table")
        self.assertEqual(block.props["headers"], ["A", "B"])
        self.assertEqual(block.props["rows"], [["1", "2"], ["3", "4"]])

    def test_block_kind_table_coerces_every_cell_to_a_string(self):
        blocks = [{"kind": "table", "headers": ["N"], "rows": [[1], [2.5]]}]
        block = ui_builder.agent_ui_node("n1", None, blocks, []).children[0]
        self.assertEqual(block.props["rows"], [["1"], ["2.5"]])

    def test_block_kind_table_skips_malformed_rows_instead_of_crashing(self):
        blocks = [
            {"kind": "table", "headers": ["A"], "rows": [["ok"], "not-a-list", None]}
        ]
        block = ui_builder.agent_ui_node("n1", None, blocks, []).children[0]
        self.assertEqual(block.props["rows"], [["ok"]])

    def test_block_kind_list_uses_colored_bullet_spans(self):
        blocks = [{"kind": "list", "items": ["x", "y"]}]
        spans = (
            ui_builder.agent_ui_node("n1", None, blocks, []).children[0].props["spans"]
        )
        bullets = [s for s in spans if s["text"] == "• "]
        self.assertEqual(len(bullets), 2)
        self.assertEqual(bullets[0]["style"], "bright_cyan")
        self.assertEqual(
            [s["text"] for s in spans if s["text"] in ("x", "y")], ["x", "y"]
        )

    def test_block_kind_list_empty_degrades_to_a_placeholder(self):
        blocks = [{"kind": "list", "items": []}]
        spans = (
            ui_builder.agent_ui_node("n1", None, blocks, []).children[0].props["spans"]
        )
        self.assertEqual(spans[0]["text"], "(empty list)")

    def test_block_kind_facts_bolds_labels_and_right_aligns_to_the_longest(self):
        blocks = [{"kind": "facts", "pairs": {"Rec": "pnpm", "Reason": "fast"}}]
        spans = (
            ui_builder.agent_ui_node("n1", None, blocks, []).children[0].props["spans"]
        )
        labels = [s for s in spans if s["style"] == "bold bright_cyan"]
        self.assertEqual(len(labels), 2)
        self.assertTrue(
            labels[0]["text"].startswith("   Rec:")
        )  # padded to "Reason"'s 6 chars

    def test_block_kind_facts_empty_degrades_to_a_placeholder(self):
        blocks = [{"kind": "facts", "pairs": {}}]
        spans = (
            ui_builder.agent_ui_node("n1", None, blocks, []).children[0].props["spans"]
        )
        self.assertEqual(spans[0]["text"], "(no facts given)")

    def test_unrecognized_block_kind_degrades_to_a_visible_placeholder(self):
        blocks = [{"kind": "nonsense", "x": 1}]
        block = ui_builder.agent_ui_node("n1", None, blocks, []).children[0]
        self.assertIn("nonsense", block.props["text"])

    def test_non_dict_block_renders_as_plain_text_instead_of_crashing(self):
        block = ui_builder.agent_ui_node("n1", None, ["just a string"], []).children[0]
        self.assertEqual(block.props["text"], "just a string")

    def test_content_entry_node_delegates_the_agent_ui_kind(self):
        node = ui_builder.content_entry_node(
            "agent_ui",
            "n1",
            title="T",
            blocks=[{"kind": "text", "text": "x"}],
            quick_replies=[],
        )
        self.assertEqual(node.id, "n1")
        self.assertEqual(node.props["panel_title"], "✦ T")


class TestSummarizeBlocks(unittest.TestCase):
    """The compact synopsis a clicked quick-reply folds into the agent's
    next turn (wire/routes.py's _dispatch_quick_reply) — never shown to
    the user, so what matters is that it stays short and never leaks raw
    tabular data (a table's a row count away from blowing the token
    budget this whole function exists to protect)."""

    def test_joins_text_and_markdown_blocks(self):
        summary = ui_builder.summarize_blocks(
            [
                {"kind": "text", "text": "intro"},
                {"kind": "markdown", "text": "**bold**"},
            ]
        )
        self.assertIn("intro", summary)
        self.assertIn("**bold**", summary)

    def test_includes_list_items_inline(self):
        summary = ui_builder.summarize_blocks([{"kind": "list", "items": ["a", "b"]}])
        self.assertIn("a, b", summary)

    def test_includes_facts_as_key_value_pairs(self):
        summary = ui_builder.summarize_blocks(
            [{"kind": "facts", "pairs": {"Rec": "pnpm"}}]
        )
        self.assertIn("Rec: pnpm", summary)

    def test_table_mentions_headers_only_never_row_data(self):
        summary = ui_builder.summarize_blocks(
            [{"kind": "table", "headers": ["A", "B"], "rows": [["1", "2"]] * 50}]
        )
        self.assertIn("a table (A, B)", summary)
        self.assertNotIn("1", summary)

    def test_truncates_to_the_given_limit(self):
        summary = ui_builder.summarize_blocks(
            [{"kind": "text", "text": "x" * 500}], limit=50
        )
        self.assertLessEqual(len(summary), 50)

    def test_empty_blocks_gives_an_empty_summary(self):
        self.assertEqual(ui_builder.summarize_blocks([]), "")

    def test_malformed_blocks_are_skipped_not_crashed_on(self):
        summary = ui_builder.summarize_blocks(
            ["not-a-dict", {"kind": "text", "text": "ok"}]
        )
        self.assertEqual(summary, "ok")


if __name__ == "__main__":
    unittest.main()

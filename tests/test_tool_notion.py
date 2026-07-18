"""Tests for tool/notion.py — every Notion API call goes through
httpx.MockTransport (tool.notion._transport), so this suite never opens a
real socket, matching every other tool's zero-network-in-tests rule.
"""

import json
import os
import unittest

import httpx

from tool import notion
from tool.notion import (
    notion_append_text,
    notion_create_page,
    notion_read_page,
    notion_search,
)


class NotionToolTestCase(unittest.TestCase):
    """Sets NOTION_API_KEY and a mock transport per test; restores both."""

    def setUp(self):
        self._original_key = os.environ.get("NOTION_API_KEY")
        self._original_transport = notion._transport
        os.environ["NOTION_API_KEY"] = "test-key"

    def tearDown(self):
        if self._original_key is None:
            os.environ.pop("NOTION_API_KEY", None)
        else:
            os.environ["NOTION_API_KEY"] = self._original_key
        notion._transport = self._original_transport

    def _mock(self, handler) -> None:
        notion._transport = httpx.MockTransport(handler)


def _json_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=body)


class TestMissingApiKey(unittest.TestCase):
    def setUp(self):
        self._original_key = os.environ.get("NOTION_API_KEY")
        os.environ.pop("NOTION_API_KEY", None)

    def tearDown(self):
        if self._original_key is not None:
            os.environ["NOTION_API_KEY"] = self._original_key

    def test_search_without_key_errors_and_never_touches_the_network(self):
        # No transport configured at all — if the tool tried to make a
        # real request, this would hang or hit the real network instead
        # of failing fast, so a wrong short-circuit would show up here.
        result = notion_search.invoke({"query": "anything"})
        self.assertIn("NOTION_API_KEY", result)

    def test_append_without_key_errors(self):
        result = notion_append_text.invoke({"page_id": "p1", "text": "hi"})
        self.assertIn("NOTION_API_KEY", result)


class TestNotionSearch(NotionToolTestCase):
    def test_lists_pages_and_databases(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/search")
            body = json.loads(request.content)
            self.assertEqual(body["query"], "roadmap")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertEqual(request.headers["Notion-Version"], "2022-06-28")
            return _json_response(
                200,
                {
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "properties": {
                                "Name": {
                                    "type": "title",
                                    "title": [{"plain_text": "Q3 Roadmap"}],
                                }
                            },
                        },
                        {
                            "object": "database",
                            "id": "db-1",
                            "title": [{"plain_text": "Roadmap Tracker"}],
                        },
                    ]
                },
            )

        self._mock(handler)
        result = notion_search.invoke({"query": "roadmap"})
        self.assertIn("[page] Q3 Roadmap (id: page-1)", result)
        self.assertIn("[database] Roadmap Tracker (id: db-1)", result)

    def test_no_results(self):
        self._mock(lambda request: _json_response(200, {"results": []}))
        result = notion_search.invoke({"query": "nonexistent"})
        self.assertIn("No Notion pages or databases match", result)

    def test_untitled_item_falls_back(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                200,
                {"results": [{"object": "page", "id": "page-2", "properties": {}}]},
            )

        self._mock(handler)
        result = notion_search.invoke({"query": "x"})
        self.assertIn("(untitled)", result)


class TestNotionReadPage(NotionToolTestCase):
    def test_renders_title_and_block_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/pages/page-1":
                return _json_response(
                    200,
                    {
                        "object": "page",
                        "id": "page-1",
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"plain_text": "Meeting Notes"}],
                            }
                        },
                    },
                )
            self.assertEqual(request.url.path, "/v1/blocks/page-1/children")
            return _json_response(
                200,
                {
                    "results": [
                        {
                            "type": "heading_1",
                            "heading_1": {"rich_text": [{"plain_text": "Agenda"}]},
                        },
                        {
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"plain_text": "Discuss Q3 plans."}]
                            },
                        },
                        {
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [{"plain_text": "Ship the roadmap"}]
                            },
                        },
                        {
                            "type": "divider",
                            "divider": {},
                        },
                    ]
                },
            )

        self._mock(handler)
        result = notion_read_page.invoke({"page_id": "page-1"})
        self.assertIn("# Meeting Notes", result)
        self.assertIn("# Agenda", result)
        self.assertIn("Discuss Q3 plans.", result)
        self.assertIn("- Ship the roadmap", result)

    def test_page_with_no_text_blocks_reports_that_clearly(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/pages/page-2":
                return _json_response(
                    200,
                    {
                        "object": "page",
                        "id": "page-2",
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"plain_text": "Empty"}],
                            }
                        },
                    },
                )
            return _json_response(
                200, {"results": [{"type": "divider", "divider": {}}]}
            )

        self._mock(handler)
        result = notion_read_page.invoke({"page_id": "page-2"})
        self.assertIn("# Empty", result)
        self.assertIn("(no text content)", result)


class TestNotionCreatePage(NotionToolTestCase):
    def test_creates_page_with_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/pages")
            body = json.loads(request.content)
            self.assertEqual(body["parent"], {"page_id": "parent-1"})
            self.assertEqual(
                body["properties"]["title"]["title"][0]["text"]["content"],
                "New Page",
            )
            self.assertEqual(len(body["children"]), 2)
            return _json_response(200, {"id": "new-page-1"})

        self._mock(handler)
        result = notion_create_page.invoke(
            {
                "parent_page_id": "parent-1",
                "title": "New Page",
                "content": "line one\n\nline two",
            }
        )
        self.assertIn("Created page 'New Page'", result)
        self.assertIn("new-page-1", result)

    def test_creates_page_with_no_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["children"], [])
            return _json_response(200, {"id": "new-page-2"})

        self._mock(handler)
        result = notion_create_page.invoke(
            {"parent_page_id": "parent-1", "title": "Blank Page"}
        )
        self.assertIn("Created page 'Blank Page'", result)


class TestNotionAppendText(NotionToolTestCase):
    def test_appends_paragraphs(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(request.url.path, "/v1/blocks/page-1/children")
            body = json.loads(request.content)
            self.assertEqual(len(body["children"]), 2)
            return _json_response(200, {})

        self._mock(handler)
        result = notion_append_text.invoke(
            {"page_id": "page-1", "text": "first\nsecond"}
        )
        self.assertIn("Appended 2 paragraph(s)", result)

    def test_blank_text_is_refused_without_a_network_call(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should never make a request for blank text")

        self._mock(handler)
        result = notion_append_text.invoke({"page_id": "page-1", "text": "   \n  "})
        self.assertIn("no text given", result)


class TestNotionApiErrors(NotionToolTestCase):
    def test_unauthorized_reports_status_and_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(401, {"message": "API token is invalid."})

        self._mock(handler)
        result = notion_search.invoke({"query": "x"})
        self.assertIn("401", result)
        self.assertIn("API token is invalid.", result)

    def test_connection_failure_reports_a_clear_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        self._mock(handler)
        result = notion_search.invoke({"query": "x"})
        self.assertIn("could not reach the Notion API", result)


if __name__ == "__main__":
    unittest.main()

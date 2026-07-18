"""Notion content tools: search, read, create, and append to pages in a
Notion workspace, via Notion's real REST API (https://api.notion.com/v1)
authenticated with an integration API key.

Notion has no public chat/completions API — "Notion AI" is a UI feature
of the Notion product itself, not something a third party can call. What
IS public is the content API (pages, blocks, databases, search), so
that's what these tools wrap: the agent can look things up in and write
to a connected Notion workspace, the same way it reads/writes local files
with cat/write/edit.

Every call needs NOTION_API_KEY (an internal integration token, created
at https://www.notion.so/my-integrations) in the environment, and the
integration must be explicitly shared with whatever pages it should see
— Notion's API has no workspace-wide access by default. Missing or
rejected credentials come back as a plain error string, like every other
tool's error convention here (core.guard's refusals, cat's "not a file",
etc.) rather than a raised exception.
"""

import os

import httpx
from langchain_core.tools import tool

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

_MISSING_KEY_ERROR = (
    "Error: NOTION_API_KEY is not set. Add it to your environment "
    "(an internal integration token from https://www.notion.so/my-integrations) "
    "to use the Notion tools."
)

# Tests monkeypatch this to an httpx.MockTransport — never a real socket in
# this suite, same constraint every other tool's tests already follow.
_transport: httpx.BaseTransport | None = None


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, transport=_transport)


def _headers() -> dict[str, str] | None:
    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        return None
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, json_body: dict | None = None) -> dict | str:
    """Returns the parsed JSON body, or a plain error string."""
    headers = _headers()
    if headers is None:
        return _MISSING_KEY_ERROR

    try:
        with _client() as client:
            response = client.request(
                method, f"{NOTION_API_BASE}{path}", headers=headers, json=json_body
            )
    except httpx.HTTPError as exc:
        return f"Error: could not reach the Notion API ({exc})."

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        message = data.get("message") or response.text
        return f"Error: Notion API returned {response.status_code}: {message}"
    return data


def _plain_text(rich_text: list[dict]) -> str:
    return "".join(segment.get("plain_text", "") for segment in rich_text)


def _title_of(item: dict) -> str:
    if item.get("object") == "database":
        return _plain_text(item.get("title", [])) or "(untitled)"
    for prop in item.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain_text(prop.get("title", [])) or "(untitled)"
    return "(untitled)"


_BLOCK_PREFIXES = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "to_do": "[ ] ",
    "quote": "> ",
}
_TEXT_BLOCK_TYPES = frozenset(_BLOCK_PREFIXES) | {"paragraph", "callout"}


def _render_block(block: dict) -> str | None:
    block_type = block.get("type")
    if block_type not in _TEXT_BLOCK_TYPES:
        return None
    text = _plain_text(block.get(block_type, {}).get("rich_text", []))
    if not text:
        return None
    return f"{_BLOCK_PREFIXES.get(block_type, '')}{text}"


def _paragraphs(content: str) -> list[dict]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]},
        }
        for line in content.splitlines()
        if line.strip()
    ]


@tool
def notion_search(query: str) -> str:
    """Search this Notion workspace for pages and databases by title.

    Only sees pages/databases the integration has been explicitly shared
    with in Notion, not the whole workspace. Returns each match's type,
    title, and id — pass a result's id to notion_read_page,
    notion_create_page, or notion_append_text.

    Args:
        query: Text to match against page/database titles.
    """
    result = _request("POST", "/search", {"query": query, "page_size": 20})
    if isinstance(result, str):
        return result

    results = result.get("results", [])
    if not results:
        return f"No Notion pages or databases match {query!r}."

    lines = [
        f"- [{item.get('object', '?')}] {_title_of(item)} (id: {item.get('id', '?')})"
        for item in results
    ]
    return "\n".join(lines)


@tool
def notion_read_page(page_id: str) -> str:
    """Read a Notion page's title and text content.

    Args:
        page_id: The page's id (from notion_search, or the trailing id
            segment of its Notion URL).
    """
    page = _request("GET", f"/pages/{page_id}")
    if isinstance(page, str):
        return page
    title = _title_of(page)

    children = _request("GET", f"/blocks/{page_id}/children?page_size=100")
    if isinstance(children, str):
        return children

    lines = [_render_block(block) for block in children.get("results", [])]
    body = "\n".join(line for line in lines if line)
    return f"# {title}\n\n{body}" if body else f"# {title}\n\n(no text content)"


@tool
def notion_create_page(parent_page_id: str, title: str, content: str = "") -> str:
    """Create a new Notion page nested under an existing page.

    Args:
        parent_page_id: Id of the Notion page the new page is created
            inside (from notion_search).
        title: Title for the new page.
        content: Optional body text. Each non-blank line becomes its own
            paragraph.
    """
    body = {
        "parent": {"page_id": parent_page_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
        "children": _paragraphs(content),
    }
    result = _request("POST", "/pages", body)
    if isinstance(result, str):
        return result
    return f"Created page {title!r} (id: {result.get('id')})."


@tool
def notion_append_text(page_id: str, text: str) -> str:
    """Append text to the end of an existing Notion page.

    Args:
        page_id: The Notion page id to append to.
        text: Text to add. Each non-blank line becomes its own paragraph.
    """
    children = _paragraphs(text)
    if not children:
        return "Error: no text given to append."
    result = _request("PATCH", f"/blocks/{page_id}/children", {"children": children})
    if isinstance(result, str):
        return result
    return f"Appended {len(children)} paragraph(s) to page {page_id}."

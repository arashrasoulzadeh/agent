"""Agent-driven UI tool.

Lets the agent present structured, styled content — not just prose — to
whichever client is attached, rendered identically by the terminal (
ui/app.py) and the desktop app (desktop/renderer.js) since both are
generic renderers of the same component vocabulary this compiles into,
server-side, by service/ui_builder.py's agent_ui_node(). Routed through
core.ui_context rather than a specific transport, room, or client,
mirroring tool/ask.py's own ask_context pattern exactly and for the same
reason: this tool works the same whether it's a websocket room's client
receiving it, a test, or nothing at all.
"""

from typing import Any

from langchain_core.tools import tool

from core import ui_context


@tool
def show_ui(
    blocks: list[dict[str, Any]],
    title: str | None = None,
    quick_replies: list[str] | None = None,
) -> str:
    """Show the user a bordered panel of structured content instead of
    plain prose — renders the same way on the terminal and desktop
    clients. Use it when structure genuinely helps: a comparison table,
    a checklist, labeled facts, or quick-reply follow-ups. Skip it for
    an ordinary explanation — that's still just your reply text.

    Args:
        blocks: dicts, each one a "kind":
            text {"text"} — one paragraph.
            markdown {"text"} — full markdown: headings, **bold**,
                `code`, links, lists.
            list {"items": [...]} — a bullet list.
            facts {"pairs": {"label": "value", ...}} — aligned
                label/value lines.
            table {"headers": [...], "rows": [[...], ...]} — a data
                table.
            A malformed block renders as-is rather than failing your turn.
        title: Optional heading.
        quick_replies: Up to 6 click-to-send labels. Omit for none.

    Returns: a short confirmation; doesn't end your turn.
    """
    return ui_context.show(title, blocks, quick_replies)

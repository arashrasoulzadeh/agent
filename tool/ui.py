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
def show_ui(blocks: list[dict[str, Any]], title: str | None = None,
            quick_replies: list[str] | None = None) -> str:
    """Present structured, styled content to the user — rendered as a
    bordered panel in both the terminal client and the desktop app,
    instead of (or alongside) an ordinary prose reply.

    Use this when structure genuinely helps: a short comparison table, a
    checklist, a set of labeled facts, or a small set of one-click
    follow-up prompts. Don't reach for it for an ordinary explanation —
    that's still just your normal reply text. Overusing this for
    everything turns it into noise instead of signal.

    Args:
        blocks: One or more content blocks, each a dict with a "kind":
            {"kind": "text", "text": "..."} — one plain paragraph.
            {"kind": "markdown", "text": "..."} — full markdown (the
                same renderer your normal answers already use:
                headings, **bold**, `code`, links, lists).
            {"kind": "list", "items": ["...", "..."]} — a bullet list.
            {"kind": "facts", "pairs": {"label": "value", ...}} — a
                short list of labeled facts, aligned.
            {"kind": "table", "headers": ["...", ...], "rows": [["...",
                ...], ...]} — a small data table, column-aligned.
            An unrecognized or malformed block is shown as-is rather
            than failing your turn, so a small mistake here costs you a
            slightly ugly block, not the whole response.
        title: An optional heading shown at the top of the panel.
        quick_replies: Up to 6 short labels the user can click instead
            of typing — clicking one submits it as their next message,
            exactly as if they'd typed and sent it themselves. Good for
            "which of these?" follow-ups. Omit for none.

    Returns:
        A short confirmation. This does not end your turn — keep going
        if there's more to say, or use it again for another panel.
    """
    return ui_context.show(title, blocks, quick_replies)

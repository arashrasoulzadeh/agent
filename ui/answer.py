"""Appends the agent's final answer, rendered as markdown, to the
transcript log.

The content log scrolls internally (arrows/PageUp-PageDown/mouse wheel),
so there's no separate pager path for long answers — they just scroll
like everything else in the transcript.
"""

from rich.markdown import Markdown
from rich.panel import Panel

from ui import state
from ui.engine import record


def show(text: object) -> None:
    """The agent's final answer, rendered as markdown."""
    record("output", text)
    app = state.get_app()
    if app is None:
        return
    panel = Panel(Markdown(str(text)), border_style="grey35", padding=(1, 2))
    app.call_from_thread(app.write, panel)

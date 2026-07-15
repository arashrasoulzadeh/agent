"""Renders the `error` event.

The message has already been mapped from an exception type to plain
English server-side (see server/errors.py) — the client just displays it.
"""

from rich.panel import Panel
from rich.text import Text

from ui import style


def show(app, message: str) -> None:
    app.write(
        Panel(
            Text(message, style=style.ERROR),
            title="error",
            title_align="left",
            border_style="red",
            padding=(0, 2),
        )
    )

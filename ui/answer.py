"""Renders the `answer` event: the turn's final markdown answer."""

from rich.markdown import Markdown
from rich.panel import Panel


def show(app, text: str) -> None:
    app.write(Panel(Markdown(text), border_style="grey35", padding=(1, 2)))

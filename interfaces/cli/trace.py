"""Renders the tool-call trace and token count events from the server.

Called directly from `ui/app.py`'s receive loop with the event's `data`
and the app itself — there's no separate worker thread anymore (the
network is the boundary now, not a Python thread), so no thread-hop is
needed to touch a widget.
"""

from rich.text import Text

from core.text import preview
from ui import style


def tool_call(app, name: str, args: str) -> None:
    app.active_tool = name
    line = Text("→ ", style=style.THINK)
    line.append(name, style=style.TOOL)
    line.append(f"({preview(args, 90)})", style=style.THINK)
    app.write(line)
    app.refresh_header()


def tool_result(app, output: str) -> None:
    app.active_tool = None
    app.write(Text(f"← {preview(output, 90)}", style=style.THINK))
    app.refresh_header()


def set_tokens(app, prompt: int, completion: int, total: int) -> None:
    app.tokens = {"prompt": prompt, "completion": completion, "total": total}
    app.refresh_header()

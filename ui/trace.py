"""Tool-call trace + token bookkeeping, pushed into the running AgentApp.

Same public surface as before (`tool_call`, `tool_result`, `tokens`,
`working()`), so pipeline/analyst.py and modules/ask.py need no changes —
only the backend changed, from a Rich Live panel to the app's header and
scrollable content log. Every call here runs on the worker thread that's
executing the agent's tool-calling loop, so touching the app always goes
through `call_from_thread`.
"""

from contextlib import contextmanager

from rich.text import Text

from ui import state, style
from ui.engine import preview, record


class Tokens:
    """Token usage: the prompt just sent, and the session running total."""

    def __init__(self) -> None:
        self.prompt = 0  # prompt tokens of the most recent call
        self.completion = 0  # completion tokens of the most recent call
        self.total = 0  # every token this session

    def add(self, prompt: int, completion: int, total: int) -> None:
        self.prompt = prompt
        self.completion = completion
        self.total += total or (prompt + completion)
        app = state.get_app()
        if app is not None:
            app.call_from_thread(app.refresh_header)

    def reset(self) -> None:
        self.__init__()


tokens = Tokens()


def _push(line: Text) -> None:
    app = state.get_app()
    if app is not None:
        app.call_from_thread(app.write, line)


def tool_call(name: str, args: str) -> None:
    """A tool the agent is calling, with its arguments."""
    record("think", f"→ {name}({args})")
    line = Text("→ ", style=style.THINK)
    line.append(name, style=style.TOOL)
    line.append(f"({preview(args, 90)})", style=style.THINK)
    app = state.get_app()
    if app is not None:
        app.call_from_thread(app.set_active_tool, name)
    _push(line)


def tool_result(text: object) -> None:
    """What a tool handed back."""
    record("think", f"← {preview(text)}")
    _push(Text(f"← {preview(text, 90)}", style=style.THINK))
    app = state.get_app()
    if app is not None:
        app.call_from_thread(app.set_active_tool, None)


@contextmanager
def working(label: str = "thinking"):
    """Show `label` (with a spinner) in the header while the agent works."""
    app = state.get_app()
    if app is not None:
        app.call_from_thread(app.set_status, label)
    try:
        yield
    finally:
        if app is not None:
            app.call_from_thread(app.set_status, None)

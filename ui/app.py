"""The full-screen agent TUI.

A single Textual app owns the whole terminal for the session: a header
(banner, model/endpoint, tools with the active one highlighted, token
count, and turn status), a scrollable content log (the whole conversation
transcript), and a footer (project/log info + the input line). The header
and footer are sized to exactly match their own content (`height: auto`)
rather than a fixed quota; `#content` is `1fr` and absorbs whatever's left.
Textual reflows all three on resize — nothing here ever grows past the
terminal, and the content log scrolls internally (arrows/PageUp-PageDown/
mouse wheel) rather than the terminal's own scrollback.

The pipeline's blocking, network-calling work (`pipeline.start()` and
`pipeline.ask()`) runs in a thread worker so the event loop — the header's
spinner, the content log's scrolling — stays responsive. Everything that
touches a widget from that thread goes through `call_from_thread`.
"""

import os
import queue

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from core.guard import project_root
from modules import AGENT_TOOLS
from pipeline import ProjectPipeline
from ui import answer, error, state, style, trace
from ui.engine import LOG_FILE, record

EXIT_COMMANDS = {"exit", "quit", "q"}

BOOTSTRAP_QUERY = (
    "Give me a clear overview of this project: what it is, its purpose, "
    "its tech stack, and how it's organized. Read whatever files you need "
    "to be confident in your answer."
)

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class AgentApp(App):
    """Header (sized to its own content) / content (fills the rest) /
    footer (sized to its own content, incl. the input line).

    Both bands use `height: auto` — they take exactly as many rows as their
    current content needs (the header grows by one line while a turn is
    running, for the spinner) and nothing more. `#content` is `1fr`, so it
    always absorbs whatever's left; the three regions still sum to exactly
    the terminal's height at any size, but neither band ever reserves
    empty space or gets a fixed quota it doesn't need.
    """

    TITLE = "agent"

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: auto;
        border-bottom: heavy $primary;
        padding: 0 1;
    }

    #content {
        height: 1fr;
    }

    #footer {
        height: auto;
        border-top: solid $primary;
    }

    #footer-info {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }

    #footer-input {
        height: 1;
        border: none;
        background: transparent;
        color: $text;
    }
    """

    def __init__(self, pipeline: ProjectPipeline, path: str):
        super().__init__()
        self.pipeline = pipeline
        self.path = path
        self._model = os.getenv("GAPGPT_MODEL", "gpt-4o-mini")
        self._base_url = os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1")
        self._tool_names = [t.name for t in AGENT_TOOLS]
        self._active_tool: str | None = None
        self._status_label: str | None = None
        self._spinner_frame = 0
        self._awaiting_reply = False
        # Startup (project load + the bootstrap question) owns the first
        # turn; set before the worker starts so there's no race with an
        # eager keystroke.
        self._turn_active = True
        self._reply_queue: queue.Queue[str | None] = queue.Queue()

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield RichLog(id="content", wrap=True, markup=False, highlight=False)
        with Vertical(id="footer"):
            yield Static(id="footer-info")
            yield Input(
                placeholder="Ask a follow-up, or 'exit' to quit.",
                id="footer-input",
            )

    def on_mount(self) -> None:
        state.set_app(self)
        self.refresh_header()
        self.set_interval(0.1, self._tick)
        self.query_one("#footer-input", Input).focus()
        self.run_worker(self._startup, thread=True)

    def on_unmount(self) -> None:
        # Unblocks a worker thread that's mid-`ask` if the app is closing.
        self._reply_queue.put(None)
        state.clear_app()

    # ---- rendering: only ever called on the main thread ----------------

    def write(self, renderable) -> None:
        self.query_one("#content", RichLog).write(renderable)

    def refresh_header(self) -> None:
        top = Table.grid(expand=True)
        top.add_column(ratio=1)
        top.add_column(justify="right")
        top.add_row(
            Text(" ⚡ AGENT", style="bold bright_cyan"),
            Text(f"tokens {trace.tokens.total:,} ", style="bold bright_white"),
        )

        config = Text(f"  model {self._model}    url {self._base_url}", style="grey62")

        tools_line = Text("  tools  ")
        for name in self._tool_names:
            active = name == self._active_tool
            tool_style = "bold bright_green" if active else "grey50"
            tools_line.append(("▶" if active else " ") + name + "  ", style=tool_style)

        lines = [top, config, tools_line]
        if self._status_label is not None:
            frame = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
            lines.append(
                Text(f"  {frame} {self._status_label}…", style="bold bright_yellow")
            )

        self.query_one("#header", Static).update(Group(*lines))

    def set_status(self, label: str | None) -> None:
        self._status_label = label
        if label is None:
            self._active_tool = None
        self.refresh_header()

    def set_active_tool(self, name: str | None) -> None:
        self._active_tool = name
        self.refresh_header()

    def _tick(self) -> None:
        if self._status_label is not None:
            self._spinner_frame += 1
            self.refresh_header()

    def _render_footer_info(self) -> None:
        info = self.query_one("#footer-info", Static)
        info.update(f"project {project_root()}   log {LOG_FILE}")
        self.sub_title = str(project_root())

    def _show_hint(self) -> None:
        self.write(Text("Ask a follow-up, or 'exit' to quit.", style=style.INFO))

    # ---- the agent's own mid-turn question ------------------------------

    def ask_and_wait(self, question: str) -> str | None:
        """Called from the worker thread inside the `ask` tool."""
        self.call_from_thread(self._begin_question, question)
        return self._reply_queue.get()

    def _begin_question(self, question: str) -> None:
        self.write(Text(f"? {question}", style=style.QUESTION))
        self._awaiting_reply = True
        self.query_one("#footer-input", Input).placeholder = "Your answer…"

    # ---- input handling --------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        event.input.value = ""

        if self._awaiting_reply:
            self._awaiting_reply = False
            event.input.placeholder = "Ask a follow-up, or 'exit' to quit."
            self._reply_queue.put(value)
            return

        if self._turn_active:
            return

        if not value or value.lower() in EXIT_COMMANDS:
            self.exit()
            return

        self.write(Text(f"> {value}", style=style.MESSAGE))
        record("message", value)
        self._turn_active = True
        self.run_worker(lambda: self._run_turn(value), thread=True)

    # ---- turns ----------------------------------------------------------

    def _startup(self) -> None:
        try:
            with trace.working("reading the project"):
                self.pipeline.start(self.path)
        except Exception as exc:
            error.show(exc)
            self.call_from_thread(self.exit, None, 1)
            return

        self.call_from_thread(self._render_footer_info)
        record("message", BOOTSTRAP_QUERY)
        self._run_turn(BOOTSTRAP_QUERY)
        self.call_from_thread(self._show_hint)

    def _run_turn(self, question: str) -> None:
        try:
            with trace.working("thinking"):
                reply = self.pipeline.ask(question)
        except Exception as exc:
            error.show(exc)
        else:
            answer.show(reply)
        finally:
            self._turn_active = False

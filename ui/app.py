"""The full-screen agent TUI — a thin WebSocket client.

Connects to the agent server (see wire/), creates or resumes a room,
and renders whatever the server reports. Every piece of the agent's
activity — a tool call, a token update, the final answer, any state
change — arrives as a protocol event over that one connection (see
docs/PROTOCOL.md); this app never runs a pipeline or touches an LLM
itself, and never needs `call_from_thread` — the network is the boundary
now, not a Python thread, so the receive loop already runs on the same
asyncio event loop Textual does.

Layout is unchanged from before: header/content/footer, header and
footer sized to their own content (`height: auto`), content (`1fr`)
scrolling internally.
"""

import asyncio
import json
import uuid
from typing import Any

import websockets
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Static

from ui import answer, error, style, trace

EXIT_COMMANDS = {"exit", "quit", "q"}
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _resync_prompt_text(data: dict[str, Any]) -> str:
    changed = data.get("changed", 0)
    total = data.get("total", 0)
    return (
        f"? {changed} of {total} files have changed since this project was "
        "last analyzed. Re-analyze? (y/n)"
    )


_COMMANDS = {"/add", "/remove", "/projects"}


def _parse_command(value: str) -> tuple[str, list[str]] | None:
    """Recognizes only `/add`, `/remove`, `/projects` — anything else
    (ordinary chat text, a bare "y"/"n" resync reply, an unrelated
    slash-prefixed typo) returns None and falls through to normal
    handling."""
    if not value.startswith("/"):
        return None
    parts = value.split()
    command = parts[0]
    if command not in _COMMANDS:
        return None
    return command, parts[1:]


class ServerError(Exception):
    """The server responded with `{"ok": false}` to a request."""


class QuestionModal(ModalScreen[str | None]):
    """One button per option for the agent's `ask(question, options=...)`.

    Dismisses with the clicked option's own label, or None on Escape —
    closing this without a choice leaves free-text entry as the
    fallback (the footer input's placeholder is already switched to
    "Your answer…" by the caller before this is pushed, so nothing is
    lost by backing out).
    """

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    QuestionModal {
        align: center middle;
    }
    QuestionModal #question-box {
        width: auto;
        max-width: 80%;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    QuestionModal #question-text {
        margin-bottom: 1;
    }
    QuestionModal #question-buttons {
        align: center middle;
        height: auto;
    }
    QuestionModal #question-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, question: str, options: list[str]):
        super().__init__()
        self.question = question
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="question-box"):
            yield Label(self.question, id="question-text")
            with Horizontal(id="question-buttons"):
                for i, option in enumerate(self.options):
                    yield Button(option, id=f"opt-{i}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(str(event.button.label))


class AgentApp(App):
    """Header (sized to its own content) / content (fills the rest) /
    footer (sized to its own content, incl. the input line).

    See ui/app.py's previous revisions for why both bands use
    `height: auto` rather than a fixed quota, and why the footer input
    has no distinct background — both are unchanged from before.
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

    def __init__(self, server_url: str, path: str, room: str | None = None):
        super().__init__()
        self.server_url = server_url
        self.path = path
        self.room: str | None = room
        self.ws: websockets.ClientConnection | None = None

        self.model = "loading"
        self.base_url = "loading"
        self.projects: list[dict[str, Any]] = []
        self.tool_names: list[str] = []
        self.active_tool: str | None = None
        self.status_label: str | None = "connecting"
        self.tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.turn_active = True
        self.awaiting_reply = False
        self.awaiting_resync = False

        self._spinner_frame = 0
        self._shown_hint = False
        self._pending: dict[str, asyncio.Future] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield RichLog(id="content", wrap=True, markup=False, highlight=False)
        with Vertical(id="footer"):
            yield Static(id="footer-info")
            yield Input(placeholder="Connecting…", id="footer-input")

    def on_mount(self) -> None:
        self.refresh_header()
        self.set_interval(0.1, self._tick)
        self.query_one("#footer-input", Input).focus()
        asyncio.create_task(self._connect())

    async def on_unmount(self) -> None:
        if self.ws is not None:
            await self.ws.close()

    # ---- connection -------------------------------------------------------

    async def _connect(self) -> None:
        try:
            self.ws = await websockets.connect(self.server_url)
        except OSError as exc:
            self._fatal(f"Could not reach the agent server at {self.server_url}: {exc}")
            return

        asyncio.create_task(self._receive_loop())

        try:
            if self.room:
                data = await self._request("/session/resume", {"room": self.room})
                self._apply_state(data)
                for entry in data.get("transcript", []):
                    self._replay(entry)
                if not self.turn_active:
                    self._show_hint()
            else:
                data = await self._request("/session/create", {"path": self.path})
                self.room = data["room"]
                if "transcript" in data:
                    # A room already existed for this path (room ids are
                    # derived from the path — see service/rooms.py's
                    # room_id_for_path()) and was resumed instead of a
                    # fresh one being created: repaint its history exactly
                    # like /session/resume does above.
                    self._apply_state(data)
                    for entry in data.get("transcript", []):
                        self._replay(entry)
                    if not self.turn_active:
                        self._show_hint()
                # Otherwise, a genuinely new room: the rest (model/tools/
                # tokens/the bootstrap answer) arrives as events —
                # session.state lands right after this.
        except ServerError as exc:
            self._fatal(str(exc))

    async def _request(self, route: str, data: dict) -> dict:
        if self.ws is None:
            raise ServerError("not connected")
        request_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"id": request_id, "route": route, "data": data}
        if self.room is not None:
            payload["room"] = self.room
        await self.ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def _receive_loop(self) -> None:
        try:
            async for raw in self.ws:
                if not self.is_running:
                    return
                self._handle(json.loads(raw))
        except websockets.ConnectionClosed:
            self._fatal("Lost connection to the agent server.")

    def _handle(self, msg: dict[str, Any]) -> None:
        if "route" not in msg and "id" in msg:
            future = self._pending.get(msg["id"])
            if future is None or future.done():
                return
            if msg.get("ok"):
                future.set_result(msg.get("data", {}))
            else:
                future.set_exception(ServerError(msg.get("error", "request failed")))
            return

        name = msg.get("event")
        data = msg.get("data", {})
        if name == "session.state":
            was_active = self.turn_active
            self._apply_state(data)
            if was_active and not self.turn_active:
                self._show_hint()
        elif name == "message":
            self.write(Text(f"> {data['text']}", style=style.MESSAGE))
        elif name == "tool.call":
            trace.tool_call(self, data["name"], data["args"])
        elif name == "tool.result":
            trace.tool_result(self, data["output"])
        elif name == "tokens":
            trace.set_tokens(self, data["prompt"], data["completion"], data["total"])
        elif name == "question":
            self.write(Text(f"? {data['text']}", style=style.QUESTION))
            self.query_one("#footer-input", Input).placeholder = "Your answer…"
            options = data.get("options")
            if options:
                self.push_screen(
                    QuestionModal(data["text"], options), self._on_question_answered
                )
        elif name == "answer":
            answer.show(self, data["text"])
        elif name == "error":
            error.show(self, data["message"])
        elif name == "resync.suggested":
            self.awaiting_resync = True
            self.write(Text(_resync_prompt_text(data), style=style.QUESTION))
            self.query_one("#footer-input", Input).placeholder = "y/n"

    def _replay(self, entry: dict[str, Any]) -> None:
        kind = entry.get("type")
        if kind == "message":
            self.write(Text(f"> {entry['text']}", style=style.MESSAGE))
        elif kind == "tool_call":
            trace.tool_call(self, entry["name"], entry["args"])
        elif kind == "tool_result":
            trace.tool_result(self, entry["output"])
        elif kind == "question":
            self.write(Text(f"? {entry['text']}", style=style.QUESTION))
        elif kind == "answer":
            answer.show(self, entry["text"])
        elif kind == "resync_suggested":
            self.write(Text(_resync_prompt_text(entry), style=style.QUESTION))
        self.active_tool = None  # replay never leaves a tool "in flight"

    def _fatal(self, message: str) -> None:
        error.show(self, message)
        self.status_label = None
        self.refresh_header()

    # ---- rendering ----------------------------------------------------

    def write(self, renderable) -> None:
        # An event can still be in flight when the app is torn down (the
        # receive loop is a background task, not awaited by shutdown) —
        # there's nothing to render into anymore, so just drop it.
        if not self.is_running:
            return
        self.query_one("#content", RichLog).write(renderable)

    def _apply_state(self, data: dict[str, Any]) -> None:
        self.model = data.get("model", self.model)
        self.base_url = data.get("base_url", self.base_url)
        self.projects = data.get("projects", self.projects)
        self.tool_names = data.get("tools", self.tool_names)
        self.turn_active = data.get("turn_active", self.turn_active)
        self.status_label = data.get("status_label")
        self.awaiting_reply = data.get("awaiting_reply", self.awaiting_reply)
        self.awaiting_resync = data.get("resync_suggested", self.awaiting_resync)
        self.active_tool = data.get("active_tool")
        self.tokens = data.get("tokens", self.tokens)

        footer_input = self.query_one("#footer-input", Input)
        if self.awaiting_reply:
            footer_input.placeholder = "Your answer…"
        elif self.awaiting_resync:
            footer_input.placeholder = "y/n"
        else:
            footer_input.placeholder = "Ask a follow-up, or 'exit' to quit."
        if len(self.projects) > 1:
            names = ", ".join(
                p["name"] for p in sorted(self.projects, key=lambda p: p["name"])
            )
            info_text = f"projects {names}   room {self.room}"
        else:
            info_text = f"project {data.get('path', self.path)}   room {self.room}"
        self.query_one("#footer-info", Static).update(info_text)
        self.refresh_header()

    def refresh_header(self) -> None:
        if not self.is_running:
            return
        top = Table.grid(expand=True)
        top.add_column(ratio=1)
        top.add_column(justify="right")
        top.add_row(
            Text(" ⚡ AGENT", style="bold bright_cyan"),
            Text(f"tokens {self.tokens['total']:,} ", style="bold bright_white"),
        )

        config = Text(f"  model {self.model}    url {self.base_url}", style="grey62")

        tools_line = Text("  tools  ")
        for name in self.tool_names:
            active = name == self.active_tool
            tool_style = "bold bright_green" if active else "grey50"
            tools_line.append(("▶" if active else " ") + name + "  ", style=tool_style)

        lines = [top, config, tools_line]
        if self.status_label is not None:
            frame = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
            lines.append(
                Text(f"  {frame} {self.status_label}…", style="bold bright_yellow")
            )

        self.query_one("#header", Static).update(Group(*lines))

    def _tick(self) -> None:
        if self.status_label is not None:
            self._spinner_frame += 1
            self.refresh_header()

    def _show_hint(self) -> None:
        if not self._shown_hint:
            self._shown_hint = True
            self.write(Text("Ask a follow-up, or 'exit' to quit.", style=style.INFO))

    # ---- input handling -------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        event.input.value = ""

        if self.awaiting_reply:
            await self._safe_request("/reply", {"text": value})
            return

        if self.awaiting_resync:
            self.awaiting_resync = False
            confirm = value.lower() in ("y", "yes")
            await self._safe_request("/resync", {"confirm": confirm})
            return

        parsed = _parse_command(value)
        if parsed is not None:
            command, args = parsed
            await self._handle_command(command, args)
            return

        if self.turn_active:
            return

        if not value or value.lower() in EXIT_COMMANDS:
            self.exit()
            return

        await self._safe_request("/prompt", {"text": value})

    async def _handle_command(self, command: str, args: list[str]) -> None:
        if command == "/projects":
            self._show_projects()
            return
        if command == "/add":
            if not args:
                self.write(Text("Usage: /add <path> [name]", style=style.INFO))
                return
            data: dict[str, str] = {"path": args[0]}
            if len(args) > 1:
                data["name"] = args[1]
            await self._safe_request("/project/add", data)
            return
        if command == "/remove":
            if not args:
                self.write(Text("Usage: /remove <name>", style=style.INFO))
                return
            await self._safe_request("/project/remove", {"name": args[0]})
            return

    def _show_projects(self) -> None:
        if not self.projects:
            self.write(Text("No projects attached.", style=style.INFO))
            return
        lines = ["Attached projects:"]
        for p in sorted(self.projects, key=lambda p: p["name"]):
            marker = "primary" if p.get("primary") else "secondary"
            lines.append(f"  {p['name']} ({marker})  {p['path']}")
        self.write(Text("\n".join(lines), style=style.INFO))

    def _on_question_answered(self, value: str | None) -> None:
        """QuestionModal's dismiss callback. None means Escape — the
        free-text footer input (already switched to "Your answer…"
        when the question arrived) is still live as a fallback, so
        there's nothing to do here."""
        if value is None:
            return
        asyncio.create_task(self._deliver_reply(value))

    async def _deliver_reply(self, value: str) -> None:
        self.write(Text(f"> {value}", style=style.MESSAGE))
        await self._safe_request("/reply", {"text": value})

    async def _safe_request(self, route: str, data: dict) -> None:
        try:
            await self._request(route, data)
        except ServerError as exc:
            error.show(self, str(exc))

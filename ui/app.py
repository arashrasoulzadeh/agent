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
import logging
import uuid
from typing import Any

import websockets
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

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


_COMMANDS = {"/add", "/remove", "/projects", "/settings"}

# (command, usage, description) — drives the command popup (see
# AgentApp._update_command_popup) and stays in sync with _COMMANDS above
# manually; there's no dynamic discovery here since the command set is
# small and fixed.
_COMMAND_HELP = [
    ("/add", "/add <path> [name]", "Attach another project to this room"),
    ("/remove", "/remove <name>", "Detach a project"),
    ("/projects", "/projects", "List attached projects"),
    ("/settings", "/settings", "Open the settings screen"),
]


def _parse_command(value: str) -> tuple[str, list[str]] | None:
    """Recognizes only `/add`, `/remove`, `/projects`, `/settings` — anything else
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


class SettingsModal(ModalScreen[None]):
    """One Label+Input row per setting from a `/settings/list` response;
    each Input saves independently on Enter, Escape closes the whole
    screen.

    A secret setting's Input starts blank (never pre-filled with the
    masked dots `/settings/list` sends back — submitting those literally
    would overwrite the real value with garbage), typed in
    `password=True` mode, and an empty submit is a no-op: the field was
    never touched, so nothing is sent. A non-secret setting's Input
    starts pre-filled with its real current value; any submit (even
    unchanged) is a harmless write.

    `Input.Submitted` bubbles up through this screen to the App the same
    way it would for the footer's own input — `on_input_submitted` here
    must call `event.stop()` or AgentApp's own handler would also fire
    for the same keystroke, misreading a saved setting's value as a
    chat message or command.
    """

    BINDINGS = [("escape", "dismiss(None)", "Close")]

    DEFAULT_CSS = """
    SettingsModal {
        align: center middle;
    }
    SettingsModal #settings-box {
        width: 74;
        max-width: 92%;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    SettingsModal #settings-title {
        margin-bottom: 1;
    }
    SettingsModal .settings-row {
        height: auto;
        margin-bottom: 1;
    }
    SettingsModal .settings-label {
        width: 30;
        color: $text-muted;
    }
    SettingsModal .settings-row Input {
        width: 1fr;
    }
    SettingsModal #settings-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, app_ref: "AgentApp", settings: list[dict[str, Any]]):
        super().__init__()
        self._app_ref = app_ref
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Label("Settings", id="settings-title")
            for s in self.settings:
                label = s["label"]
                if s["scope"] == "new-rooms":
                    label += " (new rooms)"
                with Horizontal(classes="settings-row"):
                    yield Label(label, classes="settings-label")
                    yield Input(
                        value="" if s["secret"] else s["value"],
                        placeholder=(
                            "unchanged — type to replace" if s["secret"] else ""
                        ),
                        password=s["secret"],
                        id=f"setting-{s['key']}",
                    )
            yield Label("Enter to save a field, Escape to close.", id="settings-hint")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        key = (event.input.id or "").removeprefix("setting-")
        spec = next((s for s in self.settings if s["key"] == key), None)
        if spec is None:
            return
        value = event.value
        if spec["secret"] and not value:
            return  # untouched — never overwrite a secret with blank
        result = await self._app_ref._safe_request_data(
            "/settings/update", {"key": key, "value": value}
        )
        if result is None:
            return
        if spec["secret"]:
            event.input.value = ""
        self._app_ref.write(Text(f"Saved {spec['label']}.", style=style.INFO))


class AgentApp(App):
    """Header (sized to its own content) / content (fills the rest) /
    footer (sized to its own content, incl. the input line).

    See ui/app.py's previous revisions for why both bands use
    `height: auto` rather than a fixed quota, and why the footer input
    has no distinct background — both are unchanged from before.
    """

    TITLE = "agent"

    # Up/Down/Escape only do anything while the command popup is showing
    # (each action checks that itself) — Input doesn't bind any of these
    # three, so they always bubble here uninterrupted regardless of
    # whether the popup is visible, with no effect on existing behavior
    # when it isn't.
    BINDINGS = [
        Binding("up", "popup_prev", show=False),
        Binding("down", "popup_next", show=False),
        Binding("escape", "popup_dismiss", show=False),
    ]

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

    #command-popup {
        height: auto;
        max-height: 6;
        display: none;
        border-top: solid $primary;
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
            yield OptionList(id="command-popup")
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
                try:
                    self._handle(json.loads(raw))
                except Exception:
                    # An unhandled exception here would otherwise kill this
                    # background task silently (nothing awaits it) — the
                    # connection looks alive but no further event ever gets
                    # processed again. Logging and continuing means one bad
                    # message can't take the rest of the session down with
                    # it, matching wire/app.py's own per-request isolation.
                    logging.getLogger("ui.app").exception(
                        "failed to handle an incoming message"
                    )
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "footer-input":
            return
        self._update_command_popup(event.value)

    def _update_command_popup(self, value: str) -> None:
        """Shows every command whose name starts with the input's first
        token while that token is still ambiguous or incomplete; hides
        once it's an exact, unambiguous match and the user has moved on
        to typing arguments (a space after it), or once awaiting_reply/
        awaiting_resync means "/"-prefixed text isn't treated as a
        command at all (see on_input_submitted's early returns)."""
        popup = self.query_one("#command-popup", OptionList)
        first_token = value.split(" ", 1)[0]
        matches = [c for c in _COMMAND_HELP if c[0].startswith(first_token)]
        exact_and_past_it = (
            len(matches) == 1 and matches[0][0] == first_token and " " in value
        )
        if (
            self.awaiting_reply
            or self.awaiting_resync
            or not value.startswith("/")
            or not matches
            or exact_and_past_it
        ):
            popup.display = False
            return

        popup.display = True
        popup.clear_options()
        for command, usage, description in matches:
            popup.add_option(Option(f"{usage}  —  {description}", id=command))
        popup.highlighted = 0

    def _accept_command_popup(self, input_widget: Input, value: str) -> bool:
        """If the popup is showing a suggestion and `value` isn't
        already a complete, recognized command, Enter completes the
        input to the highlighted suggestion instead of submitting —
        returns True in that case. Returns False for every other case
        (already-valid command, popup not showing, no matches), leaving
        on_input_submitted's normal handling completely untouched —
        including the existing "unrecognized slash text falls through
        to being sent as chat" behavior when there's no match at all.
        """
        if self.awaiting_reply or self.awaiting_resync:
            return False
        if _parse_command(value) is not None:
            return False
        popup = self.query_one("#command-popup", OptionList)
        if not popup.display or popup.option_count == 0:
            return False
        index = popup.highlighted or 0
        option = popup.get_option_at_index(index)
        input_widget.value = f"{option.id} "
        input_widget.cursor_position = len(input_widget.value)
        return True

    def action_popup_prev(self) -> None:
        popup = self.query_one("#command-popup", OptionList)
        if not popup.display or popup.option_count == 0:
            return
        popup.highlighted = ((popup.highlighted or 0) - 1) % popup.option_count

    def action_popup_next(self) -> None:
        popup = self.query_one("#command-popup", OptionList)
        if not popup.display or popup.option_count == 0:
            return
        popup.highlighted = ((popup.highlighted or 0) + 1) % popup.option_count

    def action_popup_dismiss(self) -> None:
        popup = self.query_one("#command-popup", OptionList)
        popup.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-popup":
            return
        input_widget = self.query_one("#footer-input", Input)
        input_widget.value = f"{event.option.id} "
        input_widget.cursor_position = len(input_widget.value)
        input_widget.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()

        if self._accept_command_popup(event.input, value):
            return

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
        if command == "/settings":
            data = await self._safe_request_data("/settings/list", {})
            if data is not None:
                self.push_screen(SettingsModal(self, data["settings"]))
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

    async def _safe_request_data(self, route: str, data: dict) -> dict | None:
        """Like _safe_request, but returns the response payload on
        success instead of discarding it — for callers (the /settings
        command, SettingsModal's per-field saves) that need to read the
        result rather than just fire-and-report-errors."""
        try:
            return await self._request(route, data)
        except ServerError as exc:
            error.show(self, str(exc))
            return None

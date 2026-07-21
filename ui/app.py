"""The full-screen agent TUI — a generic server-driven UI renderer.

Connects to the agent server (see wire/), creates or resumes a room, and
renders whatever component tree the server sends. This module has zero
built-in knowledge of any screen (no header layout, no modal shapes, no
command list) — everything drawable is a `Node` (models/ui.py's shape,
mirrored here as plain dicts) built server-side by service/ui_builder.py
and delivered two ways: once as a full tree in `/session/create`'s or
`/session/resume`'s response (`data["tree"]`), and from then on as
incremental `ui.update` ops (replace/append/remove — see
docs/PROTOCOL.md's "UI component protocol" section).

Three things are deliberately still client-local, none of them room
state:
  - **Connection status**: a client that's disconnected can't be told
    by the server that it's disconnected. Rendered into the reserved,
    always-empty `connection-status` node the server's header leaves
    for exactly this purpose.
  - **Spinner animation**: the server only says whether a status is
    active and its label (`footer-status`'s text/active props); the
    glyph frame is animated locally on a timer, never resent per-frame
    over the wire.
  - **Command-popup filtering and "exit"/"quit"/"q"**: the popup's data
    (the 4 commands) is sent once and filtered locally as the user
    types — only a completed submit round-trips. Terminating the
    client process isn't room state either, so those three words are
    intercepted here, before a footer-input submit for them is ever
    sent.

Every other interaction — a click, a submit, a selection — becomes one
`/ui/event` request (`_send_ui_event`); the server decides what it means
and pushes back whatever ui.update ops follow.
"""

import asyncio
import json
import logging
import math
import re
import uuid
from typing import Any, NamedTuple

import websockets
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

# No `from components import ...` here on purpose: this client's local
# UI vocabulary (exit words, spinner frames, reply placeholders,
# connection-state labels) is fetched fresh from the server (/ui/spec —
# see wire/routes.py's ui_spec()) as the first thing _connect() does,
# not bundled into this install. A style token added or an exit word
# changed server-side reaches this client on its next connect with no
# code change here — see components/__init__.py's own docstring for the
# full "one spec, one place" rationale this mirrors on the wire instead
# of at import time.


class _CommandOption(NamedTuple):
    """One command-popup row's data — actions.ACTIONS, over the wire
    via service/ui_builder.py's command_list_node(), one per attached
    "command-<name>" text node's props. `expansion` is only present for
    a "pre_prompt"/"post_prompt" kind action (core/action.py); None for
    "ui"/"action"."""

    value: str
    text: str
    kind: str
    expansion: str | None


def _estimate_tokens(text: str) -> int:
    """A fast, dependency-free approximation shown to the user before
    they send — not a real tokenizer, ~4 characters per token (the
    commonly-cited rule of thumb for English text), rounded up. Good
    enough for "roughly how much will this cost", not for anything
    metering/billing needs to be exact about. desktop/renderer.js's
    estimateTokens() uses the identical formula so the number reads the
    same on both clients; duplicated rather than shared via components/
    since it's a trivial, stateless algorithm, not server-owned UI data
    — unlike the vocabulary this module's own top-of-file note explains
    fetching from the server instead of importing directly."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


_GREY_RAMP = re.compile(r"^gr[ae]y\d+$")


def _textual_color(rich_color: str | None, default: str = "grey") -> str:
    """A Node's `border_style` is a Rich color string (server-built by
    service/ui_builder.py, meant for Rich's own Panel(border_style=...) —
    see _render_text's panel branch below, which needs no translation
    since it *is* Rich rendering). A **widget's own** border (Textual's
    `styles.border`, used for a panel-wrapped *container* — see _build()'s
    container branch below) goes through Textual's own `Color.parse()`
    instead, which knows a different, smaller vocabulary: no "greyNN"
    ramp at all (just a flat "grey"), and "bright_x" wants an "ansi_"
    prefix Rich doesn't use. Approximates just the handful of forms
    service/ui_builder.py actually sends (grey35, red, bright_cyan, ...)
    rather than a full Rich-color compatibility layer; the desktop
    equivalent of this problem is components/js/richStyle.js's
    colorToHex(), same reasoning, different target vocabulary."""
    if not rich_color:
        return default
    name = rich_color.strip().split()[-1]  # drop a leading "bold "/"italic " etc.
    if _GREY_RAMP.match(name):
        return "grey"
    if name.startswith("bright_"):
        return f"ansi_{name}"
    return name


def _render_text(props: dict[str, Any]):
    """One Node(type="text") -> a Rich renderable. Covers every shape
    service/ui_builder.py's content_entry_node/_text/_spans produce:
    plain text+style, multi-span text, markdown, and either wrapped in
    a Panel or not."""
    if props.get("format") == "markdown":
        # "dracula" pairs the code-fence syntax highlighting this app
        # already got for free from Rich/Pygments with the same
        # pink/green/amber/cyan token palette desktop/'s own hand-rolled
        # highlighter uses (desktop/styles.css's .tok-* rules) — one
        # color language for code across both clients, not two that
        # happen to clash.
        renderable = Markdown(
            props.get("text", ""), code_theme="dracula", inline_code_theme="dracula"
        )
    elif "spans" in props:
        renderable = Text()
        for span in props["spans"]:
            renderable.append(span.get("text", ""), style=span.get("style"))
    else:
        renderable = Text(props.get("text", ""), style=props.get("style"))
    if props.get("panel"):
        padding = props.get("padding", [0, 0])
        renderable = Panel(
            renderable,
            title=props.get("panel_title"),
            title_align="left",
            border_style=props.get("border_style", ""),
            padding=tuple(padding),
        )
    return renderable


class ServerError(Exception):
    """The server responded with `{"ok": false}` to a request."""


class AgentApp(App):
    """Header (sized to its own content) / content (fills the rest) /
    footer (sized to its own content, incl. the input line) — the exact
    layout is whatever the server's root_tree() sends; this class only
    fixes the CSS slots those node ids render into.
    """

    TITLE = "agent"

    BINDINGS = [
        Binding("up", "popup_prev", show=False),
        Binding("down", "popup_next", show=False),
        Binding("escape", "dismiss_overlay", show=False),
    ]

    CSS = """
    Screen {
        layout: vertical;
        layers: base overlay;
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

    #footer-status {
        height: 1;
        padding: 0 1;
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

    #modal-slot {
        layer: overlay;
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #modal {
        width: auto;
        max-width: 90%;
        max-height: 90%;
        overflow-y: auto;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }

    /* Settings rows only ("#modal-options" — the question modal's button
    row — is also a Horizontal, but has no Static/Input children, so it's
    untouched). Without an explicit width, the label Static defaults to
    filling the whole row, pushing Input entirely outside it (and
    Input's default 3-row border was being clipped to the row's 1-row
    height) — invisible fields, not just a layout quirk. */
    #modal Horizontal {
        height: auto;
    }

    #modal Horizontal > Static {
        width: auto;
        padding: 0 2 0 0;
    }

    #modal Horizontal > Input {
        width: 1fr;
        height: 1;
        border: none;
        background: $boost;
    }

    /* select_on_focus (Textual's Input default) only highlights existing
    text — an empty field (e.g. Ollama model with nothing typed yet)
    shows no visual difference at all when focused. This makes the
    active row obvious regardless of content. */
    #modal Horizontal > Input:focus {
        background: $accent 40%;
    }
    """

    def __init__(self, server_url: str, path: str, room: str | None = None):
        super().__init__()
        self.server_url = server_url
        self.path = path
        self.room: str | None = room
        self.ws: websockets.ClientConnection | None = None

        self._widgets: dict[str, Widget] = {}
        self._node_type: dict[str, str] = {}
        self._modal_slot: Vertical | None = None
        self._token_hint: Static | None = None
        self._command_options: list[_CommandOption] = []
        self._connection_state = "connecting"
        # Populated by _connect()'s /ui/spec fetch, before anything else
        # runs — see this module's top-of-file comment.
        self._ui_spec: dict[str, Any] = {}
        self._footer_status_props: dict[str, Any] | None = None
        self._spinner_frame = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._ui_queue: asyncio.Queue = asyncio.Queue()
        self._root_mounted = asyncio.Event()

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        asyncio.create_task(self._ui_apply_loop())
        asyncio.create_task(self._connect())

    async def on_unmount(self) -> None:
        if self.ws is not None:
            await self.ws.close()

    # ---- connection -------------------------------------------------------

    async def _connect(self) -> None:
        try:
            self.ws = await websockets.connect(self.server_url)
        except OSError as exc:
            await self._fatal(
                f"Could not reach the agent server at {self.server_url}: {exc}"
            )
            return

        asyncio.create_task(self._receive_loop())

        try:
            # Before anything else — every other request below assumes
            # exit words, spinner frames, reply placeholders, and
            # connection-state labels are already known.
            self._ui_spec = await self._request("/ui/spec", {})
            if self.room:
                data = await self._request("/session/resume", {"room": self.room})
            else:
                data = await self._request("/session/create", {"path": self.path})
                self.room = data["room"]
            await self._mount_root(data["tree"])
        except ServerError as exc:
            await self._fatal(str(exc))

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
            pass
        # `async for` above also ends silently, with no exception, on a
        # *clean* close (websockets' own __aiter__ swallows
        # ConnectionClosedOK internally) — a graceful server shutdown
        # looks exactly like this, not like the abnormal-close case the
        # `except` above alone would catch. Either way the loop ending
        # means the connection is gone, unless it's this app's own
        # on_unmount closing self.ws as part of a normal exit.
        if self.is_running:
            await self._fatal("Lost connection to the agent server.")

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

        # Every other event (session.state, message, tool.call, ...) still
        # fires server-side for any other purpose, but this renderer only
        # ever draws from ui.update — see this module's docstring.
        if msg.get("event") == "ui.update":
            self._ui_queue.put_nowait(msg.get("data", {}).get("ops", []))

    async def _fatal(self, message: str) -> None:
        if "content" in self._widgets:
            await self._append_error(message)
            self._set_connection_status("disconnected")
        else:
            # Nothing has ever been mounted (e.g. the initial connection
            # itself was refused) — there's no screen to show this on.
            self.exit(message=f"Error: {message}")

    # ---- applying server-driven UI ops -------------------------------------

    async def _ui_apply_loop(self) -> None:
        """Applies queued ui.update ops one batch at a time, in arrival
        order. Waits for the initial tree to be mounted first — a fast
        bootstrap turn can otherwise push ops before /session/create's
        own response (carrying that initial tree) has come back, since
        both travel over the same connection."""
        while True:
            ops = await self._ui_queue.get()
            await self._root_mounted.wait()
            try:
                await self.apply_ops(ops)
            except Exception:
                logging.getLogger("ui.app").exception("failed to apply ui.update ops")

    async def apply_ops(self, ops: list[dict]) -> None:
        for op in ops:
            kind = op["op"]
            target = op["target"]
            if kind == "replace":
                await self._replace(target, op["node"])
            elif kind == "append":
                await self._append(target, op["node"])
            elif kind == "remove":
                await self._remove(target)

    async def _replace(self, target: str, node: dict) -> None:
        # footer-input's live, not-yet-submitted typed value must survive
        # a replace that only changed the placeholder/password mode — see
        # service/ui_builder.py's module docstring for why.
        if target == "footer-input":
            widget = self._widgets.get("footer-input")
            if isinstance(widget, Input):
                props = node.get("props", {})
                widget.placeholder = props.get("placeholder", "")
                widget.password = props.get("password", False)
                return

        # footer-status rebuilds on *every* state broadcast — every tool
        # call, every token update, not just when it actually changes
        # (see footer_status_node's own docstring). Unmounting/
        # remounting it that often churns layout for no visible reason
        # and, worse, races anything that resolves screen coordinates
        # against a currently-mounted widget (e.g. a test's pilot.click
        # on a button elsewhere in the tree) — updating in place avoids
        # both.
        if target == "footer-status":
            widget = self._widgets.get("footer-status")
            if isinstance(widget, Static):
                self._footer_status_props = node.get("props", {})
                widget.update(self._render_footer_status())
                return

        existing = self._widgets.pop(target, None)

        # A replace reuses the same node id (e.g. "header", "modal"), so
        # the old and new widgets always collide on id — Textual checks
        # id uniqueness at mount time, so the old widget must be removed
        # *before* the new one is mounted, not after. Position is
        # preserved by index, captured before removal shifts it.
        parent = existing.parent if existing is not None else None
        index = parent.children.index(existing) if parent is not None else None

        # _forget_children then _build, with no `await` between them, so
        # self._widgets goes straight from the old subtree's ids to the
        # new one's — never a state where a child both sides share (e.g.
        # "connection-status", present in every header rebuild) is
        # observably missing. That gap is real: `await existing.remove()`
        # is a genuine yield point, and anything polling self._widgets
        # (a #footer-status check, _tick's spinner, another client
        # request) can run during it.
        if existing is not None:
            self._forget_children(existing)
        new_widget = self._build(node)
        if existing is not None:
            await existing.remove()

        if target == "modal":
            await self._modal_slot.mount(new_widget)
            self._modal_slot.display = True
            return

        if parent is None:
            return
        await parent.mount(new_widget, before=index)

    async def _append(self, target: str, node: dict) -> None:
        container = self._widgets.get(target)
        if container is None:
            return
        widget = self._build(node)
        await container.mount(widget)
        if target == "content":
            container.scroll_end(animate=False)

    async def _remove(self, target: str) -> None:
        existing = self._widgets.pop(target, None)
        self._node_type.pop(target, None)
        if existing is None:
            return
        self._forget_children(existing)
        await existing.remove()
        if target == "modal":
            self._modal_slot.display = False

    def _forget_children(self, widget: Widget) -> None:
        """Purges every descendant's id from self._widgets/_node_type.

        _build() registers an id for every node it constructs, including
        nested children (e.g. "header"'s "connection-status"). A replace
        or remove only pops the top-level target's own id — without
        this, a child that disappears when a container's shape changes
        leaves a stale entry behind forever, pointing at a widget that's
        no longer mounted.
        """
        for child in list(widget.children):
            self._forget_children(child)
            node_id = child.id
            if node_id is not None:
                self._widgets.pop(node_id, None)
                self._node_type.pop(node_id, None)

    async def _mount_root(self, tree: dict) -> None:
        self._modal_slot = Vertical(id="modal-slot")
        root_widget = self._build(tree)
        await self.mount(root_widget, self._modal_slot)
        # A client-owned status line, mounted once as an extra sibling
        # inside the server's own "footer" container — safe because
        # nothing in the protocol ever replaces "footer" itself, only
        # "footer-info"/"footer-input" individually by their own id (see
        # service/rooms.py's _state_ops()), so this widget is never
        # touched by an incoming ui.update op. Same trick as
        # connection-status/modal-slot: client-local cosmetics live
        # outside the tree the server actually rebuilds. Must happen
        # *before* _root_mounted.set() below: that call wakes the
        # ui.update apply loop, and a concurrent op replacing
        # footer-info/footer-input mounts into this same "footer"
        # parent — interleaving that mount with this one corrupts
        # Textual's node bookkeeping (DuplicateIds), since both race on
        # the same parent's children list.
        footer = self._widgets.get("footer")
        if footer is not None:
            self._token_hint = Static(Text(""), id="token-hint")
            await footer.mount(self._token_hint)
        self._modal_slot.display = False
        self._set_connection_status("connected")
        self._root_mounted.set()
        # A resumed session's tree arrives with its whole transcript
        # already replayed as "content"'s children (unlike a fresh
        # session's near-empty one) — without this it opens scrolled to
        # the top, showing the oldest turn instead of the most recent one.
        # Called twice: scroll_end's own post-refresh deferral accounts
        # for most of the transcript's layout, but a tall/rich reply
        # (markdown, a panel) can still shift height afterward — e.g. a
        # vertical scrollbar appearing narrows the content width, which
        # rewraps text taller — so a second pass shortly after catches
        # any such late reflow the first one landed just before.
        content = self._widgets.get("content")
        if content is not None:
            content.scroll_end(animate=False)
            self.set_timer(0.2, lambda: content.scroll_end(animate=False))
        footer_input = self._widgets.get("footer-input")
        if footer_input is not None:
            footer_input.focus()

    async def _append_error(self, message: str) -> None:
        container = self._widgets.get("content")
        if container is None:
            return
        node_id = f"local-error-{uuid.uuid4().hex}"
        widget = Static(
            Panel(
                Text(message, style="bold red"),
                title="error",
                title_align="left",
                border_style="red",
                padding=(0, 2),
            ),
            id=node_id,
        )
        self._widgets[node_id] = widget
        await container.mount(widget)
        container.scroll_end(animate=False)

    # ---- building widgets from nodes ---------------------------------------

    def _build(self, node: dict) -> Widget:
        """Constructs a fresh widget (and, for a container/list, its
        whole subtree) from one Node dict. Never mutates an
        already-mounted widget — that's `_replace`'s footer-input
        special case only; everything else in this app is cheap enough
        to fully rebuild on every change, matching this project's
        existing "resend the whole thing, don't diff" precedent.
        """
        node_id = node["id"]
        node_type = node["type"]
        props = node.get("props", {})
        self._node_type[node_id] = node_type

        if node_id == "connection-status":
            # Reserved, client-owned slot — the server always sends this
            # empty; only _set_connection_status ever writes its content.
            widget = Static(self._render_connection_status(), id=node_id)
        elif node_type == "container":
            children = [self._build(c) for c in node.get("children", [])]
            cls = Horizontal if props.get("direction") == "horizontal" else Vertical
            widget = cls(*children, id=node_id)
            if props.get("panel"):
                # A bordered/titled container — e.g. service/ui_builder.py's
                # agent_ui_node(), the show_ui tool's rendering. Unlike a
                # text node's panel (a Rich Panel wrapping a renderable,
                # _render_text above), this is the *widget's own* border
                # (Textual styles, not Rich), so its color needs
                # _textual_color's translation, not the raw border_style.
                widget.styles.border = (
                    "round",
                    _textual_color(props.get("border_style")),
                )
                if props.get("panel_title"):
                    widget.border_title = props["panel_title"]
                padding = props.get("padding")
                if padding:
                    widget.styles.padding = tuple(padding)
        elif node_type == "text":
            if node_id == "footer-status":
                self._footer_status_props = props
                widget = Static(self._render_footer_status(), id=node_id)
            else:
                widget = Static(_render_text(props), id=node_id)
        elif node_type == "input":
            widget = Input(
                value=props.get("value", ""),
                placeholder=props.get("placeholder", ""),
                password=props.get("password", False),
                id=node_id,
            )
        elif node_type == "button":
            widget = Button(props.get("label", ""), id=node_id)
        elif node_type == "list" and props.get("kind") == "options":
            # The command popup: its data is downloaded once here, then
            # filtered locally as the user types (_update_command_popup) —
            # never re-fetched or re-sent per keystroke.
            widget = OptionList(id=node_id)
            self._command_options = [
                _CommandOption(
                    c["props"]["value"],
                    c["props"]["text"],
                    c["props"].get("kind", "action"),
                    c["props"].get("expansion"),
                )
                for c in node.get("children", [])
            ]
            for option in self._command_options:
                widget.add_option(Option(option.text, id=option.value))
            widget.display = props.get("display", True)
        elif node_type == "list":  # kind == "log" — the content transcript
            children = [self._build(c) for c in node.get("children", [])]
            widget = VerticalScroll(*children, id=node_id)
        elif node_type == "table":
            # A show_ui table block (service/ui_builder.py's
            # _table_node()) — a real Rich Table, not formatted text;
            # the desktop equivalent is a real HTML <table>
            # (desktop/renderer.js's buildTable()).
            headers = props.get("headers") or []
            table = Table(
                show_header=bool(headers),
                header_style="bold bright_cyan",
                border_style="grey50",
                box=box.SIMPLE_HEAD if headers else box.SIMPLE,
                expand=False,
                pad_edge=False,
            )
            for header in headers:
                table.add_column(str(header))
            for row in props.get("rows") or []:
                table.add_row(*[str(cell) for cell in row])
            widget = Static(table, id=node_id)
        else:
            raise ValueError(f"unknown node: {node!r}")

        self._widgets[node_id] = widget
        return widget

    # ---- client-local cosmetics: connection status + spinner --------------

    def _render_connection_status(self) -> Text:
        label, style_name = self._ui_spec["connectionStates"][self._connection_state]
        return Text(f"  {label}", style=style_name)

    def _set_connection_status(self, state: str) -> None:
        self._connection_state = state
        widget = self._widgets.get("connection-status")
        if widget is not None:
            widget.update(self._render_connection_status())

    def _render_footer_status(self) -> Text:
        props = self._footer_status_props or {}
        label = props.get("text", "").strip()
        style_name = props.get("style")
        if not props.get("active"):
            return Text(f"○ {label}", style=style_name)
        frames = self._ui_spec["spinnerFrames"]
        frame = frames[self._spinner_frame % len(frames)]
        return Text(f"{frame} {label}", style=style_name)

    def _tick(self) -> None:
        if not (self._footer_status_props or {}).get("active"):
            return
        self._spinner_frame += 1
        widget = self._widgets.get("footer-status")
        if widget is not None:
            widget.update(self._render_footer_status())

    # ---- token estimate (client-local cosmetic, like the spinner) ---------

    def _update_token_hint(self, value: str) -> None:
        if self._token_hint is None:
            return
        count = _estimate_tokens(value)
        if not count:
            self._token_hint.update(Text(""))
            return
        label = "token" if count == 1 else "tokens"
        self._token_hint.update(Text(f"  ~{count} {label}", style="grey62"))

    # ---- command popup (client-local filtering only) -----------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "footer-input":
            return
        self._update_command_popup(event.value)
        self._update_token_hint(event.value)

    def _update_command_popup(self, value: str) -> None:
        popup = self._widgets.get("command-popup")
        footer_input = self._widgets.get("footer-input")
        if popup is None or footer_input is None or not isinstance(popup, OptionList):
            return
        reply_mode = footer_input.placeholder in self._ui_spec["replyPlaceholders"]
        first_token = value.split(" ", 1)[0]
        matches = [c for c in self._command_options if c.value.startswith(first_token)]
        exact_and_past_it = (
            len(matches) == 1 and matches[0].value == first_token and " " in value
        )
        if reply_mode or not value.startswith("/") or not matches or exact_and_past_it:
            popup.display = False
            return
        popup.display = True
        popup.clear_options()
        for option in matches:
            popup.add_option(Option(option.text, id=option.value))
        popup.highlighted = 0

    def _apply_popup_selection(self, input_widget: Input, command_value: str) -> None:
        """Completes footer-input for an accepted popup selection —
        shared by both acceptance paths (Enter, a mouse click). A
        "pre_prompt"/"post_prompt" action's `expansion` text is spliced
        in directly, in place of the "/command" token, and nothing more
        happens here: it's now just live-typed text like anything else
        in the box, still editable, still backspace-deletable character
        by character — never a hidden marker riding alongside the real
        input. A "ui"/"action" kind keeps the previous behavior: the
        bare "/command " token, left for the user to optionally add
        arguments to before Enter sends it on to the server."""
        entry = next(
            (c for c in self._command_options if c.value == command_value), None
        )
        if (
            entry is not None
            and entry.kind in ("pre_prompt", "post_prompt")
            and entry.expansion
        ):
            input_widget.value = entry.expansion
        else:
            input_widget.value = f"{command_value} "
        input_widget.cursor_position = len(input_widget.value)

    def _accept_command_popup(self, input_widget: Input, value: str) -> bool:
        """If the popup is showing a suggestion and `value` isn't
        already a complete, recognized command, Enter completes the
        input to the highlighted suggestion instead of submitting."""
        popup = self._widgets.get("command-popup")
        if popup is None or not isinstance(popup, OptionList):
            return False
        if input_widget.placeholder in self._ui_spec["replyPlaceholders"]:
            return False
        if any(c.value == value for c in self._command_options):
            return False
        if not popup.display or popup.option_count == 0:
            return False
        index = popup.highlighted or 0
        option = popup.get_option_at_index(index)
        self._apply_popup_selection(input_widget, option.id)
        return True

    def action_popup_prev(self) -> None:
        popup = self._widgets.get("command-popup")
        if isinstance(popup, OptionList) and popup.display and popup.option_count:
            popup.highlighted = ((popup.highlighted or 0) - 1) % popup.option_count
            return
        # Not the footer's command popup — up/down inside an open modal
        # (settings, a question with options) moves focus between its
        # fields instead, the same as Tab/Shift-Tab, since a modal's
        # Input doesn't otherwise do anything with up/down itself.
        if self._modal_slot is not None and self._modal_slot.display:
            self.action_focus_previous()

    def action_popup_next(self) -> None:
        popup = self._widgets.get("command-popup")
        if isinstance(popup, OptionList) and popup.display and popup.option_count:
            popup.highlighted = ((popup.highlighted or 0) + 1) % popup.option_count
            return
        if self._modal_slot is not None and self._modal_slot.display:
            self.action_focus_next()

    def action_dismiss_overlay(self) -> None:
        """Escape: hides the command popup if it's showing; otherwise
        hides a visible modal. Purely a local visibility toggle — no
        request is sent, so a dismissed question modal correctly leaves
        free-text entry live as a fallback (awaiting_reply is unchanged,
        server-side), and a re-replaced modal (e.g. a later question)
        shows normally regardless of whether this ever ran."""
        popup = self._widgets.get("command-popup")
        if isinstance(popup, OptionList) and popup.display:
            popup.display = False
            return
        if self._modal_slot is not None and self._modal_slot.display:
            self._modal_slot.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-popup":
            return
        footer_input = self._widgets.get("footer-input")
        if not isinstance(footer_input, Input):
            return
        self._apply_popup_selection(footer_input, event.option.id)
        footer_input.focus()

    # ---- forwarding interactions to the server -----------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_widget = event.input
        component_id = input_widget.id
        if component_id is None:
            return
        value = event.value.strip()

        if component_id == "footer-input":
            if self._accept_command_popup(input_widget, value):
                return
            if value.lower() in self._ui_spec["exitCommands"]:
                self.exit()
                return
            input_widget.value = ""
        elif component_id.startswith("setting-") and input_widget.password:
            # Optimistic local clear for a just-submitted secret field —
            # the server's next modal replace also sends it back blank,
            # but this avoids a stale-looking value in the meantime.
            input_widget.value = ""

        await self._send_ui_event(component_id, "submit", value)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id is None:
            return
        await self._send_ui_event(event.button.id, "click")

    async def _send_ui_event(
        self, component_id: str, event: str, value: str | None = None
    ) -> None:
        data: dict[str, str] = {"component_id": component_id, "event": event}
        if value is not None:
            data["value"] = value
        await self._safe_request("/ui/event", data)

    async def _safe_request(self, route: str, data: dict) -> None:
        try:
            await self._request(route, data)
        except ServerError as exc:
            await self._append_error(str(exc))

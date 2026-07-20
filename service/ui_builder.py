"""Pure Room-state -> Node functions: what ui/app.py's `refresh_header()`,
`_apply_state()`'s widget-touching half, `_handle()`'s per-event
`Text()` construction (ui/trace.py, ui/answer.py, ui/error.py),
`QuestionModal`, `SettingsModal`, and `_COMMAND_HELP` used to do
client-side now lives here, server-side, as plain functions with no I/O
and no Room mutation — cheap to unit-test exhaustively (see
tests/test_ui_builder.py) without a real server, room, or client.

Every function takes plain values, not a `Room`, on purpose: it keeps
this module a leaf (service/rooms.py calls into it, never the reverse)
and keeps tests from needing to construct a full Room just to check a
header's shape.

**Idempotency matters more than efficiency here.** These functions may
be called and their result sent as a "replace" op even when nothing
visible actually changed (e.g. the header rebuilds on every token
update, even though most of it — the tools row, the model line — didn't
change). That's fine *only* because the client's own apply_ops updates
an already-mounted widget's props in place rather than unmounting and
remounting it — critical for `footer-input` specifically, whose live,
not-yet-submitted typed value must survive a "replace" that only
changed the placeholder text. A node that always remounted on replace
would corrupt in-progress typing every time any part of the header
changed. This module just needs to build the right props each time;
preserving live client-local state across a replace is the renderer's
job (ui/app.py), not this module's.
"""

from typing import Any

from core.style import ERROR, INFO, MESSAGE, QUESTION, THINK, TOOL
from core.text import preview
from models.ui import Node

# The four slash commands' data — moved here from ui/app.py's
# _COMMAND_HELP. Sent once (see command_list_node/root_tree); the
# client filters this already-downloaded list locally as the user
# types, never re-fetching per keystroke (typing stays local — see
# docs/PROTOCOL.md's UI component protocol section).
COMMANDS = [
    ("/add", "/add <path> [name]", "Attach another project to this room"),
    ("/remove", "/remove <name>", "Detach a project"),
    ("/projects", "/projects", "List attached projects"),
    ("/settings", "/settings", "Open the settings screen"),
]


def _text(node_id: str, text: str, style: str) -> Node:
    return Node(type="text", id=node_id, props={"text": text, "style": style})


def _spans(node_id: str, spans: list[tuple[str, str]]) -> Node:
    return Node(
        type="text",
        id=node_id,
        props={"spans": [{"text": t, "style": s} for t, s in spans]},
    )


def header_node(
    model: str,
    base_url: str,
    tool_names: list[str],
    active_tool: str | None,
    tokens: dict[str, int],
    status_label: str | None,
) -> Node:
    """The header bar. Spinner animation for `status_label` is a purely
    cosmetic, client-local detail (like connection status) — this only
    says *whether* a status is active and its label text; the client
    decides how to animate it, so this never needs resending 10x/second
    just to advance a spinner frame.

    Reserves an empty `connection-status` child the client fills and
    owns entirely — this function never puts anything there.
    """
    children = [
        _spans(
            "header-title",
            [(" ⚡ AGENT", "bold bright_cyan")],
        ),
        Node(type="text", id="connection-status", props={}),
        _text(
            "header-tokens",
            f"tokens {tokens.get('total', 0):,} ",
            "bold bright_white",
        ),
        _text("header-config", f"  model {model}    url {base_url}", "grey62"),
        _tools_row(tool_names, active_tool),
    ]
    if status_label is not None:
        children.append(
            _text("header-status", f"  {status_label}…", "bold bright_yellow")
        )
    return Node(
        type="container",
        id="header",
        props={"direction": "vertical"},
        children=children,
    )


def _tools_row(tool_names: list[str], active_tool: str | None) -> Node:
    spans: list[tuple[str, str]] = [("  tools  ", "grey62")]
    for name in tool_names:
        active = name == active_tool
        tool_style = "bold bright_green" if active else "grey50"
        spans.append((("▶" if active else " ") + name + "  ", tool_style))
    return _spans("header-tools", spans)


def footer_info_node(path: str, projects: list[dict[str, Any]], room_id: str) -> Node:
    """The footer's one-line status ("project X room Y" / "projects
    A, B room Y"), mirroring ui/app.py's old _apply_state() exactly."""
    if len(projects) > 1:
        names = ", ".join(p["name"] for p in sorted(projects, key=lambda p: p["name"]))
        text = f"projects {names}   room {room_id}"
    else:
        text = f"project {path}   room {room_id}"
    return _text("footer-info", text, INFO)


def footer_input_node(awaiting_reply: bool, awaiting_resync: bool) -> Node:
    """The footer input's placeholder/mode — never its live typed
    value, which is client-local and must survive a replace untouched
    (see this module's docstring)."""
    if awaiting_reply:
        placeholder = "Your answer…"
    elif awaiting_resync:
        placeholder = "y/n"
    else:
        placeholder = "Ask a follow-up, or 'exit' to quit."
    return Node(
        type="input",
        id="footer-input",
        props={"placeholder": placeholder, "password": False},
    )


def command_list_node() -> Node:
    """The 4 slash commands' data, sent once — see this module's
    COMMANDS docstring above."""
    return Node(
        type="list",
        id="command-popup",
        props={"kind": "options", "display": False},
        children=[
            Node(
                type="text",
                id=f"command-{name}",
                props={"text": f"{usage}  —  {description}", "value": name},
            )
            for name, usage, description in COMMANDS
        ],
    )


def question_modal_node(question: str, options: list[str] | None) -> Node | None:
    """None when there are no options — matches ui/app.py's exact old
    rule: a free-text ask() never shows a modal, only the footer
    input's placeholder changes (handled by footer_input_node)."""
    if not options:
        return None
    return Node(
        type="container",
        id="modal",
        props={"direction": "vertical", "kind": "question"},
        children=[
            _text("modal-question", question, QUESTION),
            Node(
                type="container",
                id="modal-options",
                props={"direction": "horizontal"},
                children=[
                    Node(type="button", id=f"opt-{i}", props={"label": option})
                    for i, option in enumerate(options)
                ],
            ),
        ],
    )


def settings_modal_node(settings: list[dict[str, Any]]) -> Node:
    """One row per setting. A secret setting's Input starts blank
    (never pre-filled with the masked value `/settings/list` sends —
    submitting those literally would overwrite the real value with
    garbage); a non-secret setting's Input starts pre-filled with its
    real current value. Mirrors ui/app.py's old SettingsModal exactly."""
    rows = []
    for s in settings:
        label = s["label"]
        if s["scope"] == "new-rooms":
            label += " (new rooms)"
        rows.append(
            Node(
                type="container",
                id=f"setting-{s['key']}-row",
                props={"direction": "horizontal"},
                children=[
                    _text(f"setting-{s['key']}-label", label, INFO),
                    Node(
                        type="input",
                        id=f"setting-{s['key']}",
                        props={
                            "value": "" if s["secret"] else s["value"],
                            "placeholder": (
                                "unchanged — type to replace" if s["secret"] else ""
                            ),
                            "password": s["secret"],
                        },
                    ),
                ],
            )
        )
    rows.append(_text("settings-hint", "Enter to save a field, Escape to close.", INFO))
    return Node(
        type="container",
        id="modal",
        props={"direction": "vertical", "kind": "settings"},
        children=rows,
    )


def _list_spans(items: Any) -> list[tuple[str, str]]:
    """Colored bullets rather than plain "• " text — reuses the existing
    `spans` prop (a "text" node's multi-run styling, already fully
    supported by both clients) instead of a new node type, since a
    bullet list is still fundamentally one run of text per item."""
    if not isinstance(items, list) or not items:
        return [("(empty list)", INFO)]
    spans: list[tuple[str, str]] = []
    for i, item in enumerate(items):
        spans.append(("• ", "bright_cyan"))
        spans.append((str(item), INFO))
        if i < len(items) - 1:
            spans.append(("\n", INFO))
    return spans


def _facts_spans(pairs: Any) -> list[tuple[str, str]]:
    """Bold accent-colored labels, plain values — same reasoning as
    _list_spans above: a label/value line is one text run per line, not
    a grid, so `spans` earns its keep here where `table` (a real Node
    type, above) wouldn't."""
    if not isinstance(pairs, dict) or not pairs:
        return [("(no facts given)", INFO)]
    label_width = max(len(str(k)) for k in pairs)
    spans: list[tuple[str, str]] = []
    items = list(pairs.items())
    for i, (key, value) in enumerate(items):
        spans.append((f"{str(key).rjust(label_width)}:  ", "bold bright_cyan"))
        spans.append((str(value), INFO))
        if i < len(items) - 1:
            spans.append(("\n", INFO))
    return spans


def _table_node(node_id: str, headers: Any, rows: Any) -> Node:
    """A `table` block compiles to a real `type="table"` Node — its own
    primitive, not a formatted "text" block — rendered as a genuine
    table by each client in its own best way: `ui/app.py` builds a
    `rich.table.Table` (native box-drawing, right at home in a
    terminal); `desktop/renderer.js` builds a real HTML `<table>`. Both
    are meaningfully more legible than the column-padded plain text this
    used to flatten into — the "change the struct as much as you want"
    invitation this feature grew out of, spent on the one block kind
    where a real grid actually earns its keep."""
    headers = [str(h) for h in headers] if isinstance(headers, list) else []
    str_rows = [
        [str(cell) for cell in row]
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, list)
    ]
    return Node(type="table", id=node_id, props={"headers": headers, "rows": str_rows})


def summarize_blocks(blocks: list[Any], limit: int = 160) -> str:
    """A compact, single-line synopsis of a show_ui call's blocks — used
    when a quick-reply is clicked (wire/routes.py's _dispatch_quick_reply)
    to give the *agent's* next turn textual context about which panel the
    click came from, without repeating the whole panel verbatim into
    what's actually shown to the user (which stays just the button's own
    label — see Room.run_prompt()'s `llm_text` split)."""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        if kind in ("text", "markdown"):
            parts.append(str(block.get("text", "")))
        elif kind == "list" and isinstance(block.get("items"), list):
            parts.append(", ".join(str(i) for i in block["items"]))
        elif kind == "facts" and isinstance(block.get("pairs"), dict):
            parts.append(", ".join(f"{k}: {v}" for k, v in block["pairs"].items()))
        elif kind == "table" and isinstance(block.get("headers"), list):
            parts.append(f"a table ({', '.join(str(h) for h in block['headers'])})")
    return preview(" — ".join(p for p in parts if p), limit)


def _block_node(node_id: str, block: Any) -> Node:
    """One show_ui block -> one Node. Defensive by necessity: `block`'s
    shape comes straight from the LLM's tool-call arguments, which can
    be malformed (a typo'd "kind", a list where a dict was expected) —
    this never raises, it degrades to a plain-text rendering of
    whatever it got instead, so one bad block can't fail an entire turn.
    """
    if not isinstance(block, dict):
        return _text(node_id, str(block), INFO)
    kind = block.get("kind")
    if kind == "markdown":
        return Node(
            type="text",
            id=node_id,
            props={"text": str(block.get("text", "")), "format": "markdown"},
        )
    if kind == "text":
        return _text(node_id, str(block.get("text", "")), INFO)
    if kind == "list":
        return _spans(node_id, _list_spans(block.get("items", [])))
    if kind == "facts":
        return _spans(node_id, _facts_spans(block.get("pairs")))
    if kind == "table":
        return _table_node(node_id, block.get("headers"), block.get("rows"))
    return _text(node_id, f"[unrecognized block kind {kind!r}]", INFO)


def agent_ui_node(
    node_id: str,
    title: str | None,
    blocks: list[Any],
    quick_replies: list[dict[str, str]],
) -> Node:
    """Compiles the show_ui tool's arguments (see tool/ui.py) into the
    same Node primitives every other content entry uses: a
    bordered/titled container — ui/app.py's _build() and
    desktop/renderer.js's build() both gained panel support for
    type="container", not just type="text", specifically for this —
    holding one text-ish node per block plus an optional row of
    quick-reply buttons (type="button", the same primitive
    question_modal_node()'s option buttons above already use). Nothing
    here is a new node type or a new client capability: this whole
    feature needed zero new client-side rendering code beyond that one
    generic container-panel extension, because both clients already
    understood every primitive this compiles into.

    `quick_replies` arrives as `[{"id", "label"}, ...]` with ids already
    decided by the caller (service/rooms.py's `Room.show_ui`) — this
    function is a pure compiler, matching this module's own "no I/O, no
    side effects" rule; it never generates an id itself.
    """
    children = [
        _block_node(f"{node_id}-block-{i}", block) for i, block in enumerate(blocks)
    ]
    if quick_replies:
        children.append(
            Node(
                type="container",
                id=f"{node_id}-replies",
                props={"direction": "horizontal"},
                children=[
                    Node(type="button", id=qr["id"], props={"label": qr["label"]})
                    for qr in quick_replies
                ],
            )
        )
    return Node(
        type="container",
        id=node_id,
        props={
            "direction": "vertical",
            "panel": True,
            # "✦ " marks this panel as agent-built at a glance — the one
            # visual cue distinguishing it from a plain answer panel
            # (grey35 border, no title) or an error panel (red,
            # "error") since both use the exact same primitive.
            "panel_title": f"✦ {title}" if title else "✦",
            "border_style": "bright_cyan",
            "padding": [1, 2],
        },
        children=children,
    )


def content_entry_node(kind: str, node_id: str, **fields: Any) -> Node:
    """One node for the content transcript, appended (never replaced)
    as the conversation grows. `kind` matches Room.append_transcript()'s
    own "type" field 1:1 for every persisted kind (message, tool_call,
    tool_result, answer, question, resync_suggested, agent_ui), plus two
    kinds transcript never persists: "error" (transient by design —
    replay never shows a stale error) and "info" (a local status line —
    usage hints, "Saved X" confirmations — with no conversational
    meaning to replay, previously built ad hoc client-side in
    ui/app.py).
    """
    if kind == "message":
        return _text(node_id, f"> {fields['text']}", MESSAGE)
    if kind == "tool_call":
        return _spans(
            node_id,
            [
                ("→ ", THINK),
                (fields["name"], TOOL),
                (f"({preview(fields['args'], 90)})", THINK),
            ],
        )
    if kind == "tool_result":
        return _text(node_id, f"← {preview(fields['output'], 90)}", THINK)
    if kind == "question":
        return _text(node_id, f"? {fields['text']}", QUESTION)
    if kind == "answer":
        return Node(
            type="text",
            id=node_id,
            props={
                "text": fields["text"],
                "format": "markdown",
                "panel": True,
                "border_style": "grey35",
                "padding": [1, 2],
            },
        )
    if kind == "error":
        return Node(
            type="text",
            id=node_id,
            props={
                "text": fields["message"],
                "style": ERROR,
                "panel": True,
                "panel_title": "error",
                "border_style": "red",
                "padding": [0, 2],
            },
        )
    if kind == "resync_suggested":
        changed = fields.get("changed", 0)
        total = fields.get("total", 0)
        text = (
            f"? {changed} of {total} files have changed since this project was "
            "last analyzed. Re-analyze? (y/n)"
        )
        return _text(node_id, text, QUESTION)
    if kind == "info":
        return _text(node_id, fields["text"], INFO)
    if kind == "agent_ui":
        return agent_ui_node(
            node_id,
            fields.get("title"),
            fields.get("blocks", []),
            fields.get("quick_replies", []),
        )
    raise ValueError(f"unknown content entry kind {kind!r}")


def root_tree(
    *,
    path: str,
    projects: list[dict[str, Any]],
    room_id: str,
    model: str,
    base_url: str,
    tool_names: list[str],
    active_tool: str | None,
    tokens: dict[str, int],
    status_label: str | None,
    awaiting_reply: bool,
    awaiting_resync: bool,
    transcript_nodes: list[Node] | None = None,
) -> Node:
    """The full initial tree, sent once on /session/create or
    /session/resume. `transcript_nodes` — built by the caller via
    content_entry_node() per Room.transcript entry — replays a resumed
    room's history through the exact same rendering path live events
    use, so there's only ever one way any given kind gets drawn.
    """
    return Node(
        type="container",
        id="root",
        props={"direction": "vertical"},
        children=[
            header_node(model, base_url, tool_names, active_tool, tokens, status_label),
            Node(
                type="list",
                id="content",
                props={"kind": "log"},
                children=transcript_nodes or [],
            ),
            Node(
                type="container",
                id="footer",
                props={"direction": "vertical"},
                children=[
                    footer_info_node(path, projects, room_id),
                    command_list_node(),
                    footer_input_node(awaiting_reply, awaiting_resync),
                ],
            ),
        ],
    )

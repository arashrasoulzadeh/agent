"""Node/UIOp: the server-driven UI's component schema.

A pure data shape — the functions that build these from `Room` state
live in `service/ui_builder.py`, not here (the same separation
`models/file_metadata.py` draws around workspace/'s indexer). No
(de)serialization methods: `service/rooms.py` owns turning these into
wire payloads via `dataclasses.asdict()` at the point they're emitted —
these trees are small (a handful to a few dozen nodes), so the
recursive-deepcopy cost `asdict()` carries is never worth hand-rolling
around here, unlike a whole project's file index.

Only three ops, deliberately: **replace** a bounded subtree (the header,
the footer, a modal, a single settings row), **append** one child to a
growing list (the content transcript is the only thing in this app that
ever grows — everything else fully replaces on change), **remove**
(dismiss a modal). No tree-diffing engine — matches this project's
existing "resend the whole thing, don't diff" precedent
(`session.state` already resends its full payload on every change).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """One drawable element. `type` is one of a small, fixed vocabulary
    the client already knows how to render: "container" (props:
    direction="vertical"|"horizontal", optionally also panel=True with
    panel_title/border_style/padding — a bordered/titled box around its
    children), "text" (props: text, style, or spans: a list of
    {text, style} runs, optionally format="markdown" and/or the same
    panel props as above wrapping a single renderable), "input" (props:
    placeholder, password, value), "button" (props: label), "list"
    (props: kind="log"|"options" — a plain growing scrollback vs. a
    selectable option list), "table" (props: headers, rows — a real
    grid, not formatted text; see service/ui_builder.py's
    agent_ui_node())."""

    type: str
    id: str
    props: dict[str, Any] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)


@dataclass
class UIOp:
    """One instruction for the client to apply. `node` is required for
    "replace"/"append", omitted (None) for "remove". `target` is the id
    of the node being replaced, appended to, or removed."""

    op: str
    target: str
    node: Node | None = None

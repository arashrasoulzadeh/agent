# Writing a hook

A hook is one `.py` file under `extra/` that registers `@hook(...)`-
decorated functions. There is no separate registration step beyond the
decorator itself: drop the file in, and `hooks/loader.py` imports it the
next time the process starts, which runs the decorators as an import-time
side effect. This is a different, more general extension point than
[`docs/MODULES.md`](MODULES.md)'s tools: hooks let outside code observe
or rewrite what's already happening — a prompt, an answer, a tool
result — or add a tool, without editing `agent/` or `tool/` at all.

## The minimum: a hook

```python
# extra/trim_whitespace.py
"""Strips stray whitespace off every incoming prompt."""

from hooks import hook


@hook("before_prompt")
def strip_whitespace(text: str) -> str:
    return text.strip()
```

That's the whole thing. `hooks/loader.discover()` imports every file
under `extra/` (except `__init__.py` and anything starting with `_`),
which registers `strip_whitespace` under the `"before_prompt"` hook
point. Nothing in `extra/__init__.py`, `hooks/`, or anywhere else needs
to change.

## Hook points

| Hook | Signature | Kind | Fires |
| --- | --- | --- | --- |
| `before_prompt` | `(text: str) -> str` | filter | In `agent/analyst.py`'s `ask()`, before the query is appended to the conversation. |
| `after_answer` | `(text: str) -> str` | filter | In `agent/analyst.py`'s `ask()`, right before returning the final answer. |
| `on_tool_result` | `(text: str) -> str` | filter | In `_log_step()`, before a tool result reaches the `Sink` — lets a hook rewrite tool output before it's broadcast/persisted. |
| `on_tool_call` | `(name: str, args: str) -> None` | notify | In `_log_step()`, for each tool call. The tool has already run by the time this fires (langchain's own agent loop executes it internally) — this observes, it can't prevent or rewrite the call. |
| `on_tools_collected` | `(tools: list[BaseTool]) -> list[BaseTool]` | filter | Once, in `tool/__init__.py`, after discovery collects `AGENT_TOOLS`, before it's exposed. Return `tools + [my_tool]` to add a tool. |

## Filter vs. notify

Two dispatch flavors (`hooks/dispatch.py`), both isolating each
callback's exception (logged, never propagated — a broken hook can't
break another hook's turn or crash the caller):

- **`filter`** threads a value through every hook registered for that
  point, in order — each hook receives the *previous* hook's return
  value, not the original. A hook returning `None` means "no change";
  return `""` explicitly if you actually want to blank out text.
- **`notify`** calls every hook registered for that point for its side
  effects only and discards whatever they return — for hooks that
  observe rather than transform (`on_tool_call`).

## Multi-file ordering

When more than one `extra/*.py` file registers a hook for the same
point, they run in the order their files were imported — alphabetical
by filename (the same sort `tool/registry.py` and `hooks/loader.py`
both use). Within one file, hooks run in the order they're decorated.

## Adding a tool from a hook

```python
# extra/add_reverse_tool.py
from langchain_core.tools import tool

from hooks import hook


@tool
def reverse_text(text: str) -> str:
    """Reverse a string.

    Args:
        text: The text to reverse.
    """
    return text[::-1]


@hook("on_tools_collected")
def add_reverse_tool(tools: list) -> list:
    return [*tools, reverse_text]
```

## A trap worth knowing

`tool/__init__.py` calls `hooks.loader.discover()` *before* it finishes
assembling `AGENT_TOOLS` — so a hook file must never do
`from tool import AGENT_TOOLS` (or any other tool-package attribute) at
module scope; `tool` is only partially initialized at that point.
Importing a specific submodule directly (`from tool.cat import cat`) is
fine.

## Worked examples

`extra/_example_hook.py` (a `before_prompt` text-mutating hook + an
`on_tools_collected` tool-adding hook) and
`extra/_example_logging_hook.py` (an `on_tool_call` notify hook + an
`after_answer` filter hook) are both underscore-prefixed on purpose —
`core.discovery.import_all()` (used by both `tool/registry.py` and
`hooks/loader.py`) skips any file starting with `_`, the same rule
`__init__.py` itself follows. Without that prefix, every fresh clone of
this project would silently ship these files' tool and hooks as live,
active behavior. Read them for reference; copy to a non-underscore name
(and give it a real purpose) to actually enable something like them.

## Testing a hook

Import `hooks.dispatch` and call `filter()`/`notify()` directly — no
server or room needed:

```python
from hooks import dispatch
from hooks.registry import hook


def test_strip_whitespace():
    @hook("before_prompt")
    def strip_whitespace(text):
        return text.strip()

    assert dispatch.filter("before_prompt", "  hi  ") == "hi"
```

See `tests/test_hooks.py` for how registration, dispatch, and `extra/`
discovery are each tested in isolation (against a temporary registry and
a temporary `extra/` directory, so tests never touch the real one).

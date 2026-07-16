"""A worked example of the hooks contract — not a real hook.

Underscore-prefixed on purpose: `core.discovery.import_all()` (used by
both `tool/registry.py` and `hooks/loader.py`) skips any file starting
with `_`, exactly like `__init__.py` itself. Without that prefix, every
fresh clone of this project would silently ship this file's tool and
its prompt-mutating hook as live, active behavior. Copy this file to a
non-underscore name (and give it a real purpose) to actually enable it.

Demonstrates the two things the hooks system is for:

1. Rewriting text in flight, via a `filter`-style hook (`before_prompt`).
2. Adding a tool, via the one `on_tools_collected` hook every discovered
   tool list passes through before becoming `tool.AGENT_TOOLS`.

Trap worth knowing: `tool/__init__.py` calls `hooks.loader.discover()`
*before* it finishes assembling `AGENT_TOOLS` — so a hook file must never
do `from tool import AGENT_TOOLS` (or any other tool-package attribute)
at module scope; `tool` is only partially initialized at that point.
Importing a specific submodule directly (`from tool.cat import cat`) is
fine.
"""

from langchain_core.tools import tool

from hooks import hook


@hook("before_prompt")
def _strip_whitespace(text: str) -> str:
    """Trim stray leading/trailing whitespace off every incoming prompt."""
    return text.strip()


@tool
def _reverse_text(text: str) -> str:
    """Reverse a string. A trivial example tool, not something real.

    Args:
        text: The text to reverse.
    """
    return text[::-1]


@hook("on_tools_collected")
def _add_reverse_text_tool(tools: list) -> list:
    return [*tools, _reverse_text]

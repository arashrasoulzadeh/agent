"""A second worked example — covers the two hook points `_example_hook.py`
doesn't: `on_tool_call` (notify-only) and `after_answer` (filter).

Underscore-prefixed on purpose, same reason as `_example_hook.py`: never
auto-discovered, so it's safe as living documentation. Copy it to a
non-underscore name to actually enable something like it.
"""

import logging

from hooks import hook

logger = logging.getLogger("extra.example_logging_hook")


@hook("on_tool_call")
def _log_every_tool_call(name: str, args: str) -> None:
    """Side-effect only — notify hooks can't prevent or rewrite the call
    (it's already run by the time this fires), just observe it."""
    logger.info("tool called: %s(%s)", name, args)


@hook("after_answer")
def _append_signature(text: str) -> str:
    """Filter hooks return the (possibly rewritten) value — here, a
    fixed footer appended to every final answer."""
    return f"{text}\n\n— answered with a little help from _example_logging_hook.py"

"""Colored console channels, mirrored to a log file.

Each kind of event gets its own color on the terminal and a plain-text,
timestamped line in the log file (ANSI codes are never written to disk):

    think     gray      agent activity — tool calls and their results
    message   cyan      a question going in to the agent
    output    green     the agent's final answer
    request   blue      raw LLM request
    response  magenta   raw LLM response

Set COSIST_LOG to change the log file path, NO_COLOR to disable color, or
FORCE_COLOR to keep color when stdout is not a terminal.
"""

import logging
import os
import sys

RESET = "\033[0m"
GRAY = "\033[90m"
CYAN = "\033[36m"
GREEN = "\033[32m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"

LOG_FILE = os.getenv("COSIST_LOG", "cosist.log")

_COLOR_ENABLED = bool(os.environ.get("FORCE_COLOR")) or (
    sys.stdout.isatty() and not os.environ.get("NO_COLOR")
)

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Build the file logger on first use."""
    global _logger
    if _logger is None:
        logger = logging.getLogger("cosist")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        _logger = logger
    return _logger


def _emit(channel: str, color: str, text: object) -> None:
    """Print `text` in `color`, and append it to the log under `channel`."""
    text = str(text)
    print(f"{color}{text}{RESET}" if _COLOR_ENABLED else text)
    _get_logger().info("[%s] %s", channel, " ".join(text.split()))


def think(text: object) -> None:
    """Agent activity: tool calls and tool results."""
    _emit("think", GRAY, text)


def message(text: object) -> None:
    """A question going in to the agent."""
    _emit("message", CYAN, text)


def output(text: object) -> None:
    """The agent's final answer."""
    _emit("output", GREEN, text)


def request(text: object) -> None:
    """A raw LLM request."""
    _emit("request", BLUE, text)


def response(text: object) -> None:
    """A raw LLM response."""
    _emit("response", MAGENTA, text)


def preview(text: object, limit: int = 200) -> str:
    """Collapse whitespace and truncate a value for a trace line."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

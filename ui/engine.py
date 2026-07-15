"""Logging plumbing shared by every screen.

Every event is mirrored to a colorless, timestamped line in the log file
regardless of whether it's shown in the app, and long values are
truncated before they're shown anywhere:

    request   verbose only   raw LLM request
    response  verbose only   raw LLM response

Set AGENT_LOG to change the log file path, or pass --verbose
(AGENT_VERBOSE=1) to also show the request/response channels in the
transcript instead of only logging them.
"""

import logging
import os

from rich.text import Text

from ui import state, style

LOG_FILE = os.getenv("AGENT_LOG", "agent.log")

_verbose = bool(os.environ.get("AGENT_VERBOSE"))
_logger: logging.Logger | None = None


def set_verbose(value: bool) -> None:
    """Show the raw LLM request/response channels in the transcript."""
    global _verbose
    _verbose = value


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        logger = logging.getLogger("agent")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        _logger = logger
    return _logger


def record(channel: str, text: object) -> None:
    """Log without printing — for text a screen already shows."""
    _get_logger().info("[%s] %s", channel, " ".join(str(text).split()))


def _show(text: str, style_name: str) -> None:
    app = state.get_app()
    if app is not None:
        app.call_from_thread(app.write, Text(text, style=style_name))


def request(text: object) -> None:
    """A raw LLM request — logged always, shown only when verbose."""
    record("request", text)
    if _verbose:
        _show(str(text), style.REQUEST)


def response(text: object) -> None:
    """A raw LLM response — logged always, shown only when verbose."""
    record("response", text)
    if _verbose:
        _show(str(text), style.RESPONSE)


def preview(text: object, limit: int = 200) -> str:
    """Collapse whitespace and truncate a value for a trace line."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

"""Friendly error reporting.

Exception types are noisy and mean little to a user, so common ones are
mapped to a plain-English line instead of a traceback.
"""

from rich.panel import Panel
from rich.text import Text

from ui import state, style
from ui.engine import record

_FRIENDLY = {
    "APIConnectionError": "Could not reach the model API. Check your network "
    "and GAPGPT_BASE_URL.",
    "AuthenticationError": "The API rejected your key. Check GAPGPT_API_KEY.",
    "RateLimitError": "The API is rate limiting. Wait a moment and retry.",
    "APITimeoutError": "The model API timed out. Retry, or raise GAPGPT_TIMEOUT.",
    "NotFoundError": "The API does not know that model. Check GAPGPT_MODEL.",
}


def show(exc: Exception) -> None:
    """Report a failure as a readable message, not a traceback."""
    name = type(exc).__name__
    friendly = _FRIENDLY.get(name, str(exc) or name)
    record("error", f"{name}: {exc}")
    app = state.get_app()
    if app is None:
        return
    panel = Panel(
        Text(friendly, style=style.ERROR),
        title="error",
        title_align="left",
        border_style="red",
        padding=(0, 2),
    )
    app.call_from_thread(app.write, panel)

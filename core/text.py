"""Generic text helpers with no other dependencies."""


def preview(text: object, limit: int = 200) -> str:
    """Collapse whitespace and truncate a value for a log line."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

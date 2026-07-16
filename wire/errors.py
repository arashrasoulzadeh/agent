"""Turns an exception raised while running a turn into a plain-English
message for the `error` event — exception types mean nothing to a client,
and the client shouldn't need its own copy of this mapping.
"""

_FRIENDLY = {
    "APIConnectionError": "Could not reach the model API. Check your network "
    "and GAPGPT_BASE_URL.",
    "AuthenticationError": "The API rejected your key. Check GAPGPT_API_KEY.",
    "RateLimitError": "The API is rate limiting. Wait a moment and retry.",
    "APITimeoutError": "The model API timed out. Retry, or raise GAPGPT_TIMEOUT.",
    "NotFoundError": "The API does not know that model. Check GAPGPT_MODEL.",
}


def friendly(exc: Exception) -> str:
    name = type(exc).__name__
    return _FRIENDLY.get(name, str(exc) or name)

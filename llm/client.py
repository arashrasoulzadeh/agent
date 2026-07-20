"""LLM factory — dispatches to a provider builder by LLM_PROVIDER."""

import os

from langchain_core.language_models import BaseChatModel

from llm.providers import anthropic, gapgpt, ollama

_PROVIDERS = {
    "gapgpt": gapgpt.build,
    "anthropic": anthropic.build,
    "ollama": ollama.build,
}


def get_llm(temperature: float = 0, log_raw_io: bool = True) -> BaseChatModel:
    """Build a chat model for the configured provider.

    Reads LLM_PROVIDER from the environment (default "gapgpt", today's
    only behavior) and dispatches to the matching llm/providers/*.py
    module's build(), which reads that provider's own env vars (load a
    .env before calling this). A request timeout is set by each provider
    so a stalled endpoint fails fast instead of blocking the pipeline
    forever. When log_raw_io is True (default), every call's raw request
    and response is traced on its own console channel and mirrored to
    the log file.
    """
    provider = os.getenv("LLM_PROVIDER", "gapgpt")
    try:
        build = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"unknown LLM_PROVIDER {provider!r}; choose one of {sorted(_PROVIDERS)}"
        ) from None
    return build(temperature, log_raw_io)


def describe_active() -> tuple[str, str]:
    """(model, endpoint) for the currently configured provider — display
    only (the header bar, /session/create's state snapshot), not used
    to build the actual client. Never raises on a missing required env
    var (unlike get_llm()); a display label just falls back to a
    placeholder instead of blocking rendering.
    """
    provider = os.getenv("LLM_PROVIDER", "gapgpt")
    if provider == "anthropic":
        return (
            os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
            "api.anthropic.com",
        )
    if provider == "ollama":
        return (
            os.getenv("OLLAMA_MODEL", "(unset)"),
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    return (
        os.getenv("GAPGPT_MODEL", "gpt-4o-mini"),
        os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"),
    )

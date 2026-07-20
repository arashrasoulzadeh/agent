"""LLM builder for the Anthropic API."""

import os

from langchain_anthropic import ChatAnthropic

from llm.providers._common import callbacks_for


def build(temperature: float, log_raw_io: bool) -> ChatAnthropic:
    """Reads ANTHROPIC_API_KEY, ANTHROPIC_MODEL, and ANTHROPIC_TIMEOUT
    from the environment (load a .env before calling this).
    """
    return ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        temperature=temperature,
        timeout=float(os.getenv("ANTHROPIC_TIMEOUT", "60")),
        max_retries=2,
        callbacks=callbacks_for(log_raw_io),
    )

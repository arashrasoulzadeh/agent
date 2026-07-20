"""LLM builder for the GapGPT (OpenAI-compatible) API — the default provider."""

import os

from langchain_openai import ChatOpenAI

from llm.providers._common import callbacks_for


def build(temperature: float, log_raw_io: bool) -> ChatOpenAI:
    """Reads GAPGPT_API_KEY, GAPGPT_BASE_URL, GAPGPT_MODEL, and
    GAPGPT_TIMEOUT from the environment (load a .env before calling this).
    """
    return ChatOpenAI(
        model=os.getenv("GAPGPT_MODEL", "gpt-4o-mini"),
        api_key=os.environ["GAPGPT_API_KEY"],
        base_url=os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"),
        temperature=temperature,
        timeout=float(os.getenv("GAPGPT_TIMEOUT", "60")),
        max_retries=2,
        callbacks=callbacks_for(log_raw_io),
    )

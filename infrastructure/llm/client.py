"""LLM factory for the GapGPT (OpenAI-compatible) API."""

import os

from langchain_openai import ChatOpenAI

from helpers.callbacks import RawIOLogger


def get_llm(temperature: float = 0, log_raw_io: bool = True) -> ChatOpenAI:
    """Build a ChatOpenAI client pointed at GapGPT.

    Reads GAPGPT_API_KEY, GAPGPT_BASE_URL, GAPGPT_MODEL, and
    GAPGPT_TIMEOUT from the environment (load a .env before calling this).

    A request timeout is set so a stalled endpoint fails fast instead of
    blocking the pipeline forever. When log_raw_io is True (default), every
    call's raw request and response is traced on its own console channel
    and mirrored to the log file.
    """
    return ChatOpenAI(
        model=os.getenv("GAPGPT_MODEL", "gpt-4o-mini"),
        api_key=os.environ["GAPGPT_API_KEY"],
        base_url=os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"),
        temperature=temperature,
        timeout=float(os.getenv("GAPGPT_TIMEOUT", "60")),
        max_retries=2,
        callbacks=[RawIOLogger()] if log_raw_io else None,
    )

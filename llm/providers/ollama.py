"""LLM builder for a local Ollama server."""

import os

from langchain_ollama import ChatOllama

from llm.providers._common import callbacks_for


def build(temperature: float, log_raw_io: bool) -> ChatOllama:
    """Reads OLLAMA_MODEL (required — depends entirely on what's pulled
    locally, no sensible default) and OLLAMA_BASE_URL from the
    environment (load a .env before calling this).
    """
    return ChatOllama(
        model=os.environ["OLLAMA_MODEL"],
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=temperature,
        callbacks=callbacks_for(log_raw_io),
    )

"""Shared helper for llm/providers/*.py builders."""

from langchain_core.callbacks import BaseCallbackHandler

from llm.callbacks import RawIOLogger


def callbacks_for(log_raw_io: bool) -> list[BaseCallbackHandler] | None:
    return [RawIOLogger()] if log_raw_io else None

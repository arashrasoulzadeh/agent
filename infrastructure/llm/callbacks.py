"""LangChain callback that logs each LLM call's raw request and response.

This runs wherever the LLM does — the server process — so it logs
through the standard `logging` module rather than a UI: there's no
`ui` to report through anymore now that the client is a separate process
talking over a socket. Set AGENT_VERBOSE=1 to also print these lines
(interfaces/ws/app.py configures logging for the whole process).
"""

import logging
import os
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from core.text import preview

logger = logging.getLogger("llm")
_verbose = bool(os.environ.get("AGENT_VERBOSE"))


def _format_messages(messages: list[BaseMessage]) -> str:
    # The system message carries the private project map (the metadata
    # tool's output) and is resent on every single call — showing its
    # content here would just repeat internal, agent-only context back
    # at the user on every verbose request line for no benefit.
    parts = [
        f"{m.type}=<private project context, {len(str(m.content))} chars>"
        if m.type == "system"
        else f"{m.type}={preview(m.content)}"
        for m in messages
    ]
    return " | ".join(parts)


class RawIOLogger(BaseCallbackHandler):
    """Log the raw messages sent to, and returned by, the LLM."""

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        for batch in messages:
            self._log(f"⇢ llm request: {_format_messages(batch)}")

    def on_llm_end(self, resp: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for generations in resp.generations:
            for generation in generations:
                msg = getattr(generation, "message", None)
                text = msg.content if msg is not None else generation.text
                # A tool-calling turn carries no content — the whole reply is
                # the tool calls, so show those instead of an empty line.
                if not text and getattr(msg, "tool_calls", None):
                    text = ", ".join(
                        f"{c['name']}({c['args']})" for c in msg.tool_calls
                    )
                self._log(f"⇠ llm response: {preview(text)}")

    def _log(self, message: str) -> None:
        logger.info(message)
        if _verbose:
            print(f"  {message}")

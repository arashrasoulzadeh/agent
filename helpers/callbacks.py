"""LangChain callback that logs each LLM call's raw request and response."""

from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from helpers.console import preview, request, response


def _format_messages(messages: list[BaseMessage]) -> str:
    return " | ".join(f"{m.type}={preview(m.content)}" for m in messages)


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
            request(f"  ⇢ llm request: {_format_messages(batch)}")

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
                response(f"  ⇠ llm response: {preview(text)}")

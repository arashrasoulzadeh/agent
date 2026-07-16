"""Stage 2 — analysis.

Builds the reasoning agent and answers questions against a previously
collected ProjectContext. This stage owns the LLM and the agent; the
orchestrator just hands it context and questions.

A ProjectAnalyst holds a conversation session: once started, each `ask()`
remembers prior turns (including tool calls) so follow-up questions can
build on earlier answers without re-explaining the project.

While the agent works, its tool calls and tool results are reported to a
`Sink` (see agent/sink.py) so whatever is driving this analyst — a
websocket server, a test, or nothing at all — can show them without this
module needing to know how.

When the agent is not confident and the project itself cannot settle the
point, it uses the `ask` tool to put the question to the user and carries
on from their answer rather than guessing.

This module takes its tools, its LLM, and its sink as plain constructor
arguments instead of importing `tool/` or `llm/` to build them itself —
the concrete tool list and the LLM client are handed in by whoever
constructs a ProjectAnalyst (`service/rooms.py`, in the real app), which
is what actually calls `llm.get_llm(...)` and passes `tool.AGENT_TOOLS`.
That keeps this class reusable and independently testable without a real
LLM or a real toolset.

Two hook points (hooks/, see docs/HOOKS.md) let an extra/ file rewrite
text in flight without editing this module: `before_prompt` on the
incoming query, `after_answer` on the final answer. A third pair reports
(and, for tool results, lets a hook rewrite) what happens mid-turn:
`on_tool_call` (observe only) and `on_tool_result` (can rewrite).
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

import hooks
from agent.prompts import SYSTEM_PROMPT, context_message
from agent.sink import NullSink, Sink
from models.context import ProjectContext


class ProjectAnalyst:
    """Reason about a project and answer questions about it."""

    def __init__(
        self,
        llm,
        sink: Sink | None = None,
        tools: list[BaseTool] | None = None,
    ):
        self.llm = llm
        self.sink = sink or NullSink()
        self.tools = tools if tools is not None else []
        self._agent = None
        self._messages: list = []

    def _log_step(self, message) -> None:
        """Report a tool call or tool result as the agent works."""
        if isinstance(message, AIMessage):
            usage = message.usage_metadata
            if usage:
                self.sink.tokens(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("total_tokens", 0),
                )
            for call in message.tool_calls:
                args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
                # Notify-only: the tool has already run by the time this
                # fires (langchain's agent loop executes it internally),
                # so this observes the call rather than being able to
                # prevent or rewrite it.
                hooks.dispatch.notify("on_tool_call", call["name"], args)
                self.sink.tool_call(call["name"], args)
        elif isinstance(message, ToolMessage):
            text = hooks.dispatch.filter("on_tool_result", message.content)
            self.sink.tool_result(text)

    @property
    def agent(self):
        """Lazily build the agent so construction stays cheap."""
        if self._agent is None:
            self._agent = create_agent(
                model=self.llm,
                tools=self.tools,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agent

    def start_session(self, context: ProjectContext) -> None:
        """Reset the conversation and seed it with the project's context."""
        self._messages = [{"role": "system", "content": context_message(context)}]

    def resume(self, messages: list) -> None:
        """Restore a conversation saved by a previous session."""
        self._messages = messages

    @property
    def messages(self) -> list:
        """The conversation so far, for a caller to persist."""
        return self._messages

    def ask(self, query: str) -> str:
        """Answer a question, remembering prior turns in this session.

        Streams the agent's steps so tool calls and their results reach
        the sink as they happen, rather than only the final answer.
        """
        if not self._messages:
            raise RuntimeError("Call start_session() before ask().")
        query = hooks.dispatch.filter("before_prompt", query)
        self._messages.append({"role": "user", "content": query})

        seen = len(self._messages)
        messages = self._messages
        for step in self.agent.stream({"messages": messages}, stream_mode="values"):
            messages = step["messages"]
            for message in messages[seen:]:
                self._log_step(message)
            seen = len(messages)

        self._messages = messages
        answer = self._messages[-1].content
        return hooks.dispatch.filter("after_answer", answer)

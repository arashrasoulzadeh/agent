"""Stage 2 — analysis.

Builds the reasoning agent and answers questions against a previously
collected ProjectContext. This stage owns the LLM and the agent; the
orchestrator just hands it context and questions.

A ProjectAnalyst holds a conversation session: once started, each `ask()`
remembers prior turns (including tool calls) so follow-up questions can
build on earlier answers without re-explaining the project.

While the agent works, its tool calls and tool results are reported to a
`Sink` (see pipeline/sink.py) so whatever is driving this analyst — a
websocket server, a test, or nothing at all — can show them without this
module needing to know how.

When the agent is not confident and the project itself cannot settle the
point, it uses the `ask` tool to put the question to the user and carries
on from their answer rather than guessing.

This module never imports `modules/` — the concrete list of tools the
agent can call is handed in by whoever constructs it (server/rooms.py,
in the real app), not hardcoded here. That keeps `pipeline/` reusable
without the agent's specific toolset, and is what "core depends on
nothing module-specific" means in practice: swap in a different tool
list, or none at all, with no change to this file.
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from helpers import get_llm
from pipeline.context import ProjectContext
from pipeline.prompts import SYSTEM_PROMPT, context_message
from pipeline.sink import NullSink, Sink


class ProjectAnalyst:
    """Reason about a project and answer questions about it."""

    def __init__(
        self,
        llm=None,
        temperature: float = 0.0,
        sink: Sink | None = None,
        tools: list[BaseTool] | None = None,
    ):
        self.llm = llm or get_llm(temperature)
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
                self.sink.tool_call(call["name"], args)
        elif isinstance(message, ToolMessage):
            self.sink.tool_result(message.content)

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
        self._messages.append({"role": "user", "content": query})

        seen = len(self._messages)
        messages = self._messages
        for step in self.agent.stream({"messages": messages}, stream_mode="values"):
            messages = step["messages"]
            for message in messages[seen:]:
                self._log_step(message)
            seen = len(messages)

        self._messages = messages
        return self._messages[-1].content

"""Stage 2 — analysis.

Builds the reasoning agent and answers questions against a previously
collected ProjectContext. This stage owns the LLM and the agent; the
orchestrator just hands it context and questions.

A ProjectAnalyst holds a conversation session: once started, each `ask()`
remembers prior turns (including tool calls) so follow-up questions can
build on earlier answers without re-explaining the project.

While the agent works, its tool calls and tool results are printed live
as a gray "thinking" trace, so it's visible what it's inspecting before
the final answer lands.
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from helpers import get_llm, preview, think
from pipeline.context import ProjectContext
from pipeline.prompts import SYSTEM_PROMPT, context_message
from tools import AGENT_TOOLS


def _log_step(message) -> None:
    """Print a gray trace line for a tool call or tool result."""
    if isinstance(message, AIMessage) and message.tool_calls:
        for call in message.tool_calls:
            args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
            think(f"  → {call['name']}({args})")
    elif isinstance(message, ToolMessage):
        think(f"  ← {preview(message.content)}")


class ProjectAnalyst:
    """Reason about a project and answer questions about it."""

    def __init__(self, llm=None, temperature: float = 0.0):
        self.llm = llm or get_llm(temperature)
        self._agent = None
        self._messages: list = []

    @property
    def agent(self):
        """Lazily build the agent so construction stays cheap."""
        if self._agent is None:
            self._agent = create_agent(
                model=self.llm,
                tools=AGENT_TOOLS,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agent

    def start_session(self, context: ProjectContext) -> None:
        """Reset the conversation and seed it with the project's context."""
        self._messages = [{"role": "system", "content": context_message(context)}]

    def analyze(self, query: str, context: ProjectContext) -> str:
        """One-shot analysis: answer a single question in a fresh session."""
        self.start_session(context)
        return self.ask(query)

    def ask(self, query: str) -> str:
        """Answer a question, remembering prior turns in this session.

        Streams the agent's steps so tool calls and their results print as
        a gray trace while it works, rather than only the final answer.
        """
        if not self._messages:
            raise RuntimeError("Call start_session() before ask().")
        self._messages.append({"role": "user", "content": query})

        seen = len(self._messages)
        messages = self._messages
        for step in self.agent.stream({"messages": messages}, stream_mode="values"):
            messages = step["messages"]
            for message in messages[seen:]:
                _log_step(message)
            seen = len(messages)

        self._messages = messages
        return self._messages[-1].content

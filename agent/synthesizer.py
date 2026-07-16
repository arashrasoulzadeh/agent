"""Stage 3 — synthesis.

Converts the analyst's human-readable answer into a compact, structured
context block optimized for another LLM agent to load and reason over.
This stage does no tool use; it is a pure LLM transformation.

Takes `llm` as a plain constructor argument rather than building one
itself — `service/rooms.py` calls `llm.get_llm(...)` and hands the result
in, so this class stays reusable and testable without a real LLM client.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from agent.prompts import SYNTHESIS_SYSTEM, synthesis_message
from models.context import ProjectContext


class ContextSynthesizer:
    """Turn a prose analysis into AI-ready context."""

    def __init__(self, llm, fmt: str = "markdown"):
        self.llm = llm
        self.fmt = fmt

    def synthesize(self, answer: str, context: ProjectContext) -> str:
        result = self.llm.invoke(
            [
                SystemMessage(content=SYNTHESIS_SYSTEM),
                HumanMessage(content=synthesis_message(answer, self.fmt)),
            ]
        )
        return result.content

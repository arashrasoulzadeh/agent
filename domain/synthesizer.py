"""Stage 3 — synthesis.

Converts the analyst's human-readable answer into a compact, structured
context block optimized for another LLM agent to load and reason over.
This stage does no tool use; it is a pure LLM transformation.

Takes `llm` as a plain constructor argument rather than building one via
`infrastructure/` — this is `domain/`, and a domain object depending on
infrastructure would point the dependency arrow the wrong way.
`application/rooms.py` calls `infrastructure.llm.get_llm(...)` and hands
the result in.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from domain.context import ProjectContext
from domain.prompts import SYNTHESIS_SYSTEM, synthesis_message


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

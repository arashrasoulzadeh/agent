"""Stage 3 — synthesis.

Converts the analyst's human-readable answer into a compact, structured
context block optimized for another LLM agent to load and reason over.
This stage does no tool use; it is a pure LLM transformation.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from helpers import get_llm
from pipeline.context import ProjectContext
from pipeline.prompts import SYNTHESIS_SYSTEM, synthesis_message


class ContextSynthesizer:
    """Turn a prose analysis into AI-ready context."""

    def __init__(self, llm=None, temperature: float = 0.0, fmt: str = "markdown"):
        self.llm = llm or get_llm(temperature)
        self.fmt = fmt

    def synthesize(self, answer: str, context: ProjectContext) -> str:
        result = self.llm.invoke(
            [
                SystemMessage(content=SYNTHESIS_SYSTEM),
                HumanMessage(content=synthesis_message(answer, self.fmt)),
            ]
        )
        return result.content

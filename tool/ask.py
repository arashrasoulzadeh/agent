"""Human-in-the-loop `ask` tool.

Lets the agent put a question to the user mid-task and use the reply,
instead of guessing when it is genuinely missing information. Routed
through `core.ask_context` rather than a specific transport, so this tool
works the same whether it's a websocket server's room asking a connected
client, a test, or nothing at all.
"""

from langchain_core.tools import tool

from core import ask_context


@tool
def ask(question: str) -> str:
    """Ask the user a question and return their answer.

    Use this when you are genuinely unsure or need information only the
    user has — a preference, a decision between options, or intent you
    cannot infer. Do not use it for anything you could answer by reading
    the project yourself.

    Args:
        question: The question to put to the user.
    """
    reply = ask_context.ask(question)
    if reply is None:
        return "The user did not answer. Proceed with your best judgement."
    if not reply:
        return "The user gave no answer. Proceed with your best judgement."

    return reply

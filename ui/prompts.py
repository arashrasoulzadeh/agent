"""Routes the agent's own `ask` tool question through the running app.

Called from the worker thread executing the agent's tool-calling loop;
blocks that thread until the footer Input widget (on the main thread)
delivers a reply.
"""

from ui import state
from ui.engine import record


def ask_user(question: str) -> str | None:
    """Show a question from the agent and block until it's answered.

    Returns None if there's no running app to ask through — shouldn't
    happen in practice, but keeps the contract with modules/ask.py intact.
    """
    record("question", question)
    app = state.get_app()
    if app is None:
        return None
    return app.ask_and_wait(question)

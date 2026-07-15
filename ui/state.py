"""Holds a reference to the single running AgentApp instance.

ui/trace.py, ui/prompts.py, ui/answer.py, ui/error.py, and ui/engine.py
all need to push into whichever widgets the running app owns; routing
through this tiny module avoids each of them importing ui.app directly
(which itself imports all of them, to wire up the transcript/header).
"""

_app = None


def set_app(app) -> None:
    global _app
    _app = app


def get_app():
    return _app


def clear_app() -> None:
    global _app
    _app = None

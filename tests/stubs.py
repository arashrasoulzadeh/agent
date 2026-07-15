"""Shared test doubles for tests/test_server.py and tests/test_app.py.

Every pipeline here returns canned, instant text — never a real LLM call,
never a real API key, never a real token spent running this suite.
"""

import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import websockets

from server import app as server_app
from server import rooms


class StubAnalyst:
    def __init__(self):
        self._messages = []

    def resume(self, messages):
        self._messages = messages

    @property
    def messages(self):
        return self._messages


class StubPipeline:
    """Returns canned, instant text for any question.

    Accepts (and ignores) the same keyword arguments Room passes to a
    real ProjectPipeline — events, tools, collector — so Room's wiring
    doesn't need a special case for tests; only `sink` is actually used.
    """

    def __init__(self, sink, **_ignored):
        self.sink = sink
        self.analyst = StubAnalyst()
        self.context = None
        self.started_with = None
        self.questions: list[str] = []

    def start(self, path: str = ".") -> None:
        self.started_with = path
        self.analyst._messages = [{"role": "system", "content": f"ctx for {path}"}]

    def ask(self, question: str) -> str:
        self.questions.append(question)
        return f"stub answer to: {question}"


class ToolCallingPipeline(StubPipeline):
    """Like StubPipeline, but the question 'use-tool' drives a fake tool
    call/result through the sink first."""

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if question == "use-tool":
            self.sink.tool_call("cat", "path='README.md'")
            self.sink.tool_result("# hi")
            return "done"
        return f"stub answer to: {question}"


class AskToolPipeline(StubPipeline):
    """Like StubPipeline, but the question 'ask-me' drives the `ask` tool
    via core.ask_context — exercising the same path modules/ask.py uses."""

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if question == "ask-me":
            from core import ask_context

            reply = ask_context.ask("what should I call this?")
            return f"got: {reply}"
        return f"stub answer to: {question}"


class SlowPipeline(StubPipeline):
    """Like StubPipeline, but holds a turn open briefly — for tests that
    need to reliably observe a *transient* in-flight state (e.g. the
    header's "working" row) rather than a race against an instant stub."""

    def ask(self, question: str) -> str:
        time.sleep(0.4)
        return super().ask(question)


class FailingPipeline:
    """A pipeline whose project load always fails."""

    def __init__(self, sink, **_ignored):
        self.sink = sink
        self.analyst = StubAnalyst()

    def start(self, path: str = ".") -> None:
        raise ValueError("bad project path")

    def ask(self, question: str) -> str:
        raise AssertionError("should never be reached")


@asynccontextmanager
async def running_server(pipeline_factory=StubPipeline):
    """A real websockets server on an OS-assigned port, backed by a temp
    rooms/ directory and the given (stub) pipeline factory. Yields the
    ws:// URI to connect to."""
    original_factory = rooms.pipeline_factory
    original_dir = rooms.ROOMS_DIR
    rooms.ROOMS.clear()
    rooms.pipeline_factory = pipeline_factory
    tmp_rooms = Path(tempfile.mkdtemp()) / "rooms"
    rooms.ROOMS_DIR = tmp_rooms
    try:
        async with websockets.serve(server_app.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            yield f"ws://127.0.0.1:{port}"
    finally:
        rooms.ROOMS.clear()
        rooms.pipeline_factory = original_factory
        rooms.ROOMS_DIR = original_dir
        shutil.rmtree(tmp_rooms.parent, ignore_errors=True)

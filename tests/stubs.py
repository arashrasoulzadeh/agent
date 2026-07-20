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

from models.context import ProjectContext
from service import rooms
from wire import app as server_app
from workspace import config as workspace_config


class StubAnalyst:
    def __init__(self):
        self._messages = []

    def start_session(self, context) -> None:
        self._messages = [{"role": "system", "content": f"ctx for {context.path}"}]

    def resume(self, messages):
        self._messages = messages

    @property
    def messages(self):
        return self._messages


class StubSynthesizer:
    """Never a real LLM call — canned, instant text, mirroring
    agent.synthesizer.ContextSynthesizer's shape (`.synthesize(answer,
    context)`) just enough for service.rooms.Room._cache_synthesis()."""

    def synthesize(self, answer: str, context) -> str:
        return f"stub synthesis of: {answer}"


class StubPipeline:
    """Returns canned, instant text for any question.

    Constructed from just a sink (see `_wrap_as_factory` below) — never
    touches `llm.get_llm()` or any other network/API-key dependent
    wiring, unlike `service.rooms.default_pipeline_factory`.
    """

    def __init__(self, sink):
        self.sink = sink
        self.analyst = StubAnalyst()
        self.synthesizer = StubSynthesizer()
        self.context = None
        self.started_with = None
        self.questions: list[str] = []

    def start(self, path: str = ".") -> None:
        self.started_with = path
        self.context = ProjectContext(path=path, raw=f"ctx for {path}")
        self.analyst._messages = [{"role": "system", "content": f"ctx for {path}"}]

    def ask(self, question: str) -> str:
        # Mirrors agent.ProjectPipeline.ask()'s own precondition (see its
        # "Call start() before ask()." RuntimeError) — real code exercised
        # this same check on service/rooms.py's Room.get_or_load() path
        # (a resumed room never called start()) while this stub silently
        # skipped it, which is exactly how that regression went unnoticed
        # by this whole test suite until it was hit manually.
        if self.context is None:
            raise RuntimeError("Call start() before ask().")
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
    via core.ask_context — exercising the same path tool/ask.py uses.
    'ask-with-options' does the same but with a small set of known
    answers, exercising the button-question path."""

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if question == "ask-me":
            from core import ask_context

            reply = ask_context.ask("what should I call this?")
            return f"got: {reply}"
        if question == "ask-with-options":
            from core import ask_context

            reply = ask_context.ask("pick one", options=["a", "b", "c"])
            return f"got: {reply}"
        return f"stub answer to: {question}"


class ShowUiPipeline(StubPipeline):
    """Like StubPipeline, but the question 'show-me' drives the
    `show_ui` tool via core.ui_context — exercising the same path
    tool/ui.py uses. 'show-me-with-replies' does the same plus a small
    set of quick-reply buttons, exercising the click -> /prompt path."""

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if question in ("show-me", "show-me-with-replies"):
            from core import ui_context

            blocks = [
                {"kind": "text", "text": "intro line"},
                {"kind": "table", "headers": ["A", "B"], "rows": [["1", "2"]]},
                {"kind": "facts", "pairs": {"Recommended": "pnpm"}},
                {"kind": "list", "items": ["x", "y"]},
                {"kind": "markdown", "text": "**bold**"},
            ]
            quick_replies = (
                ["Option A", "Option B"]
                if question == "show-me-with-replies"
                else None
            )
            result = ui_context.show("Comparison", blocks, quick_replies)
            return f"shown: {result}"
        return f"stub answer to: {question}"


class SlowPipeline(StubPipeline):
    """Like StubPipeline, but holds a turn open briefly — for tests that
    need to reliably observe a *transient* in-flight state (e.g. the
    header's "working" row) rather than a race against an instant stub."""

    def ask(self, question: str) -> str:
        time.sleep(0.4)
        return super().ask(question)


class FailingPipeline:
    """A pipeline whose analysis always fails.

    The failure lives in ask() rather than start(): Room's real bootstrap
    flow (service/rooms.py's _collect_and_start()) no longer calls
    pipeline.start() at all — it seeds context itself from workspace/'s
    lightweight index — so ask() is the one call a bootstrap turn
    actually makes into this stub, real or not.
    """

    def __init__(self, sink):
        self.sink = sink
        self.analyst = StubAnalyst()
        self.context = None

    def ask(self, question: str) -> str:
        raise ValueError("bad project path")


def _wrap_as_factory(pipeline_cls):
    """Adapt a stub pipeline class (constructed from just a sink) to the
    `(config, events, room) -> pipeline` shape `Room` actually calls,
    using the real `RoomSink` so tool-call/tool-result/tokens still
    broadcast through the room exactly like a real pipeline's would."""

    def factory(config, events, room):
        return pipeline_cls(rooms.RoomSink(room))

    return factory


@asynccontextmanager
async def running_server(pipeline_factory=StubPipeline, base_dir=None):
    """A real websockets server on an OS-assigned port, backed by a temp
    rooms/ directory and the given (stub) pipeline class. Yields the
    ws:// URI to connect to.

    Also redirects workspace/config.py's SESSION_ROOT into the same temp
    base dir — Room._ensure_workspace_project() (service/rooms.py) always
    touches a real SessionManager(), so without this every test would
    silently read/write the real ~/.agent-session-root on disk.

    `base_dir`, if given, is used as-is and NOT cleaned up on exit — pass
    the same directory to two separate `running_server()` calls (like
    test_resume_loads_from_disk_when_not_live already does for rooms/) to
    simulate a server restart that still sees the same on-disk cache.
    Omit it (the default) for a fresh, self-cleaning temp dir per call.
    """
    original_factory = rooms.pipeline_factory
    original_rooms_dir = rooms.ROOMS_DIR
    original_session_root = workspace_config.SESSION_ROOT
    rooms.ROOMS.clear()
    rooms.pipeline_factory = _wrap_as_factory(pipeline_factory)
    owns_base_dir = base_dir is None
    base = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp())
    rooms.ROOMS_DIR = base / "rooms"
    workspace_config.SESSION_ROOT = base / "sessions"
    try:
        async with websockets.serve(server_app.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            yield f"ws://127.0.0.1:{port}"
    finally:
        rooms.stop_all_room_watchers()
        rooms.ROOMS.clear()
        rooms.pipeline_factory = original_factory
        rooms.ROOMS_DIR = original_rooms_dir
        workspace_config.SESSION_ROOT = original_session_root
        if owns_base_dir:
            shutil.rmtree(base, ignore_errors=True)

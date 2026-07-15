"""Rooms: one per project session, persisted to rooms/{uuid}.json.

A Room owns a ProjectPipeline, the set of `Transport`s
(infrastructure/transport/base.py) currently subscribed to it,
turn/awaiting-reply state, and the queue the `ask` tool blocks on for its
reply. Every turn reports through a Sink (domain/sink.py) that broadcasts
protocol events through those transports instead of touching a renderer
directly, and every change — a new message, a tool call, a token update —
is saved via `RoomRepository`
(infrastructure/persistence/room_repository.py) immediately, so a room
can be resumed later even if the server restarts in between. Room builds
its own payload dict — that's its own state, not a persistence detail —
the repository just writes/reads it.

Room never imports `websockets` or anything else transport-specific — it
only knows `Transport.send()`. That's the boundary requirement 1 asks
for: a REST or gRPC adapter subscribes its own Transport implementation
to a room exactly the way WebSocketTransport does, with no change here.

The blocking pipeline call runs via `asyncio.to_thread`, exactly like the
old Textual worker thread did; `core.ask_context` and
`core.guard.set_project_root` are both set *inside* that thread (not
before dispatching to it) so concurrently running rooms — each analyzing
a different project, potentially — stay isolated from one another.
"""

import asyncio
import os
import queue
import uuid as uuid_lib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)

from core import ask_context, guard
from domain import ProjectPipeline
from domain.analyst import ProjectAnalyst
from domain.config import PipelineConfig
from domain.context import ContextCollector
from domain.events import LoggingStageObserver, StageEventBus
from domain.synthesizer import ContextSynthesizer
from infrastructure.llm import get_llm
from infrastructure.persistence.room_repository import RoomRepository
from infrastructure.transport.base import Transport
from interfaces.ws import events
from interfaces.ws.errors import friendly
from modules import AGENT_TOOLS, metadata

TOOL_NAMES = [tool.name for tool in AGENT_TOOLS]

ROOMS_DIR = Path(__file__).resolve().parent.parent / "rooms"

BOOTSTRAP_QUERY = (
    "Give me a clear overview of this project: what it is, its purpose, "
    "its tech stack, and how it's organized. Read whatever files you need "
    "to be confident in your answer."
)

# Live rooms, keyed by id. A room stays here for as long as the server
# process runs, whether or not any client is currently subscribed to it —
# that's what lets a second client attach to an in-progress conversation
# without reloading it from disk.
ROOMS: dict[str, "Room"] = {}

def default_pipeline_factory(
    config: PipelineConfig, events: StageEventBus, room: "Room"
) -> ProjectPipeline:
    """Wire the agent's concrete toolset, metadata source, and LLM into
    domain/ — ProjectAnalyst, ContextSynthesizer, and ContextCollector all
    take these as injected parameters instead of importing modules/ or
    infrastructure/ themselves, so domain/ stays reusable and
    dependency-free of both."""
    return ProjectPipeline(
        config=config,
        events=events,
        analyst=ProjectAnalyst(
            llm=get_llm(config.analysis_temperature),
            sink=RoomSink(room),
            tools=AGENT_TOOLS,
        ),
        synthesizer=ContextSynthesizer(
            llm=get_llm(config.synthesis_temperature),
            fmt=config.synthesis_format,
        ),
        # metadata is a langchain BaseTool (needs a dict arg); adapt it to
        # ContextCollector's plain Callable[[str], str] contract.
        collector=ContextCollector(
            metadata_fn=lambda path: metadata.invoke({"path": path})
        ),
    )


# A seam for tests: swap in a factory that never touches the network (see
# tests/test_server.py / tests/stubs.py) instead of default_pipeline_factory,
# which builds a real LLM client — and needs a real API key — the moment
# it's called. Room calls this as a single unit so tests don't have to
# pass through a real get_llm() call just because they only want to stub
# out the pipeline.
pipeline_factory = default_pipeline_factory


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RoomSink:
    """Reports one room's tool activity as broadcast protocol events."""

    def __init__(self, room: "Room") -> None:
        self.room = room

    def tool_call(self, name: str, args: str) -> None:
        self.room.active_tool = name
        self.room.broadcast_now(events.TOOL_CALL, {"name": name, "args": args})
        self.room.append_transcript({"type": "tool_call", "name": name, "args": args})

    def tool_result(self, text: str) -> None:
        self.room.active_tool = None
        self.room.broadcast_now(events.TOOL_RESULT, {"output": text})
        self.room.append_transcript({"type": "tool_result", "output": text})

    def tokens(self, prompt: int, completion: int, total: int) -> None:
        self.room.tokens["prompt"] = prompt
        self.room.tokens["completion"] = completion
        self.room.tokens["total"] += total or (prompt + completion)
        self.room.broadcast_now(events.TOKENS, dict(self.room.tokens))
        self.room.save()


class Room:
    def __init__(self, room_id: str, path: str, loop: asyncio.AbstractEventLoop):
        self.id = room_id
        self.path = path
        self.loop = loop
        self.clients: set[Transport] = set()
        self.turn_active = False
        self.awaiting_reply = False
        self.status_label: str | None = None
        self.active_tool: str | None = None
        self.tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.created_at = _now()
        self.updated_at = self.created_at
        self.transcript: list[dict[str, Any]] = []
        self.reply_queue: queue.Queue[str] = queue.Queue()

        # Fresh each time, reading whatever ROOMS_DIR currently is — this
        # is what lets tests/stubs.py's `rooms.ROOMS_DIR = tmp_dir`
        # monkeypatch (set *before* constructing any Room) keep working.
        self.repo = RoomRepository(ROOMS_DIR)

        self.events = StageEventBus()
        self.events.subscribe(LoggingStageObserver(f"domain.stage.room.{room_id}"))
        config = PipelineConfig()
        self.pipeline = pipeline_factory(config, self.events, self)

    # ---- construction / persistence ----------------------------------

    @classmethod
    def create(cls, path: str, loop: asyncio.AbstractEventLoop) -> "Room":
        room = cls(str(uuid_lib.uuid4()), path, loop)
        room.turn_active = True  # claimed up front; run_bootstrap() clears it
        room.status_label = "reading the project"
        ROOMS[room.id] = room
        room.save()
        asyncio.create_task(room.run_bootstrap())
        return room

    @classmethod
    def get_or_load(
        cls, room_id: str, loop: asyncio.AbstractEventLoop
    ) -> "Room | None":
        """An in-memory room if one's already live, else load it from disk."""
        if room_id in ROOMS:
            return ROOMS[room_id]

        raw = RoomRepository(ROOMS_DIR).load(room_id)
        if raw is None:
            return None

        room = cls(raw["id"], raw["path"], loop)
        room.tokens = raw.get("tokens", room.tokens)
        room.created_at = raw.get("created_at", room.created_at)
        room.transcript = raw.get("transcript", [])
        room.pipeline.analyst.resume(messages_from_dict(raw.get("messages", [])))
        ROOMS[room.id] = room
        return room

    @classmethod
    def list_saved(cls) -> list[dict[str, Any]]:
        return RoomRepository(ROOMS_DIR).list_saved()

    def save(self) -> None:
        """Save this room's full state, atomically.

        Called after every message, tool call, and token update — not
        just at the end of a turn — so a crash mid-turn loses as little
        as possible.
        """
        self.updated_at = _now()
        payload = {
            "id": self.id,
            "path": self.path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tokens": self.tokens,
            # start_session() seeds this with a plain dict, not yet a
            # BaseMessage, until the first turn actually streams through
            # the agent — normalize before serializing so an early save
            # (e.g. a bootstrap that fails before any turn completes)
            # doesn't crash on a mixed list.
            "messages": messages_to_dict(
                convert_to_messages(self.pipeline.analyst.messages)
            ),
            "transcript": self.transcript,
        }
        self.repo.save(self.id, payload)

    def append_transcript(self, entry: dict[str, Any]) -> None:
        self.transcript.append({**entry, "ts": _now()})
        self.save()

    # ---- clients --------------------------------------------------------

    def subscribe(self, client: Transport) -> None:
        self.clients.add(client)

    def unsubscribe(self, client: Transport) -> None:
        self.clients.discard(client)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "model": os.getenv("GAPGPT_MODEL", "gpt-4o-mini"),
            "base_url": os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"),
            "tools": TOOL_NAMES,
            "turn_active": self.turn_active,
            "status_label": self.status_label,
            "awaiting_reply": self.awaiting_reply,
            "active_tool": self.active_tool,
            "tokens": self.tokens,
            "transcript": self.transcript,
        }

    def broadcast_now(self, name: str, data: dict) -> None:
        """Broadcast from *any* thread, blocking until it's actually sent.

        Tool calls/results/tokens land on the worker thread running the
        agent's loop; this is this module's equivalent of Textual's
        `call_from_thread` — hop onto the event loop, wait for the send.
        """
        future = asyncio.run_coroutine_threadsafe(
            events.broadcast(self.clients, self.id, name, data), self.loop
        )
        future.result()

    async def _emit(self, name: str, data: dict) -> None:
        await events.broadcast(self.clients, self.id, name, data)

    async def broadcast_state(self) -> None:
        await self._emit(events.SESSION_STATE, self.state_snapshot())

    # ---- turns ------------------------------------------------------------
    #
    # /prompt must respond immediately and let a turn run in the
    # background (`server/routes.py` fires it via asyncio.create_task):
    # a turn can call the `ask` tool partway through, which blocks the
    # worker thread on a reply that arrives as its own request on this
    # *same* connection. If the /prompt handler itself awaited the whole
    # turn, the connection's receive loop would never get back around to
    # reading that /reply — a deadlock. try_start_turn()/try_consume_reply()
    # are synchronous and called with no `await` in between the check and
    # the flip, so two requests arriving back-to-back can't both pass.

    def try_start_turn(self) -> bool:
        if self.turn_active:
            return False
        self.turn_active = True
        return True

    def try_consume_reply(self) -> bool:
        if not self.awaiting_reply:
            return False
        self.awaiting_reply = False
        return True

    async def run_bootstrap(self) -> None:
        """Assumes try_start_turn() already succeeded (Room.create() sets
        turn_active — and status_label — itself, so a client can't /prompt
        before this runs)."""
        try:
            await asyncio.to_thread(self._collect_and_start)
        except Exception as exc:
            self.turn_active = False
            self.status_label = None
            await self._emit(events.ERROR, {"message": friendly(exc)})
            return
        await self._run_turn(BOOTSTRAP_QUERY)

    def _collect_and_start(self) -> None:
        guard.set_project_root(self.path)
        self.pipeline.start(self.path)

    async def run_prompt(self, text: str) -> None:
        """Assumes try_start_turn() already succeeded."""
        self.status_label = "thinking"
        await self.broadcast_state()
        self.append_transcript({"type": "message", "role": "user", "text": text})
        await self._emit(events.MESSAGE, {"role": "user", "text": text})
        await self._run_turn(text)

    async def _run_turn(self, question: str) -> None:
        try:
            answer = await asyncio.to_thread(self._ask_blocking, question)
        except Exception as exc:
            await self._emit(events.ERROR, {"message": friendly(exc)})
        else:
            self.append_transcript({"type": "answer", "text": answer})
            await self._emit(events.ANSWER, {"text": answer})
        finally:
            self.turn_active = False
            self.status_label = None
            self.active_tool = None
            await self.broadcast_state()
            self.save()

    def _ask_blocking(self, question: str) -> str:
        guard.set_project_root(self.path)
        with ask_context.asker(self._ask_and_wait):
            return self.pipeline.ask(question)

    # ---- the agent's own mid-turn question -------------------------------

    def _ask_and_wait(self, question: str) -> str | None:
        """Called from the worker thread, inside the `ask` tool."""
        self.awaiting_reply = True
        self.append_transcript({"type": "question", "text": question})
        self.broadcast_now(events.QUESTION, {"text": question})
        future = asyncio.run_coroutine_threadsafe(self.broadcast_state(), self.loop)
        future.result()
        return self.reply_queue.get()

    async def deliver_reply(self, text: str) -> None:
        """Assumes try_consume_reply() already succeeded."""
        self.append_transcript({"type": "message", "role": "user", "text": text})
        await self._emit(events.MESSAGE, {"role": "user", "text": text})
        await self.broadcast_state()
        self.reply_queue.put(text)

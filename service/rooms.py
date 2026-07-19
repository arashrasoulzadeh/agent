"""Rooms: one per project session, persisted to rooms/{id}.json.

A room's id is derived from its project path (room_id_for_path(): an
md5 of the resolved absolute path), not a random uuid — so analyzing the
same project again finds the same room and resumes it instead of
starting a new, empty one (see wire/routes.py's session_create and
Room.create() below).

This is the use-case layer: `Room` owns a `ProjectPipeline` (agent/), the
set of `Transport`s (wire/transport/base.py) currently subscribed to it,
turn/awaiting-reply state, and the queue the `ask` tool blocks on for its
reply. Every turn reports through a Sink (agent/sink.py) that broadcasts
protocol events through those transports instead of touching a renderer
directly, and every change — a new message, a tool call, a token update —
is saved via `RoomRepository` (service/room_repository.py) immediately,
so a room can be resumed later even if the server restarts in between.
Room builds its own payload dict — that's its own state, not a
persistence detail — the repository just writes/reads it.

`default_pipeline_factory` is the one place the agent's concrete
toolset (tool.AGENT_TOOLS), metadata source (tool.metadata), and LLM
client (llm.get_llm) get wired into a `ProjectPipeline` — agent/ itself
takes all three as injected constructor arguments and builds none of
them, so it stays reusable and testable without a real LLM or toolset.

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
import hashlib
import logging
import os
import queue
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)

from agent import ProjectPipeline
from agent.analyst import ProjectAnalyst
from agent.collector import ContextCollector
from agent.config import PipelineConfig
from agent.events import LoggingStageObserver, StageEventBus
from agent.synthesizer import ContextSynthesizer
from core import ask_context, guard, room_context
from llm import get_llm
from models.context import ProjectContext
from models.project_index import ProjectIndex
from models.project_synthesis import ProjectSynthesis
from models.ui import Node, UIOp
from service import ui_builder
from service.room_repository import RoomRepository
from tool import AGENT_TOOLS, metadata
from wire import events
from wire.errors import friendly
from wire.transport.base import Transport
from workspace.config import WORKSPACE_PROJECT_NAME
from workspace.ignore import IgnoreRules
from workspace.index_repository import IndexRepository
from workspace.indexer import ProjectIndexer
from workspace.manager import (
    ProjectNameConflict,
    ProjectNotFound,
    SessionAlreadyExists,
    SessionManager,
)
from workspace.serialize import to_lightweight_context
from workspace.synthesis_repository import SynthesisRepository
from workspace.watcher import ProjectWatcher

logger = logging.getLogger("service.rooms")

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
    agent/ — ProjectAnalyst, ContextSynthesizer, and ContextCollector all
    take these as injected parameters instead of importing tool/ or
    llm/ themselves, so agent/ stays reusable and dependency-free of
    both."""
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


def room_id_for_path(path: str) -> str:
    """A stable room id derived from a project path, not a random one.

    Resolved to an absolute, canonical path first, so "." and the
    equivalent absolute path (or a path reached through a symlink) hash
    to the same id. This is what makes analyzing the same project twice
    resume the existing conversation automatically (see Room.create())
    instead of piling up a fresh, randomly-named room — and a fresh
    bootstrap LLM call — every single run.
    """
    resolved = str(Path(path).expanduser().resolve())
    return hashlib.md5(resolved.encode("utf-8")).hexdigest()


# How much of a project can drift (fraction of tracked files added,
# removed, or content-changed) since its cached ProjectSynthesis was made
# before a room stops trusting it silently and asks the client whether to
# re-analyze instead (see Room._collect_and_start(), the `resync.suggested`
# event, and wire/routes.py's /resync).
RESYNC_CHANGE_THRESHOLD = 0.2

# Tracks each room's background ProjectWatcher (workspace/watcher.py) for
# the life of the server process — started the first time a (room,
# project) pair is attached (Room._ensure_watcher()), stopped by
# stop_all_room_watchers() in wire/app.py's serve() shutdown, alongside
# module lifecycle.stop_all(). Keyed by (room_id, project_name), not just
# room_id, since a room can now have more than one attached project, each
# with its own watcher.
ROOM_WATCHERS: dict[tuple[str, str], ProjectWatcher] = {}


class CannotRemovePrimaryProject(Exception):
    """Raised by Room.remove_project() when asked to detach a room's own
    primary project — the one its id is derived from (room_id_for_path()),
    which must never change once a room exists."""


def stop_all_room_watchers() -> None:
    # One misbehaving watcher must never stop the rest from being
    # stopped/cleared — this runs at server shutdown and at the end of
    # every test using tests/stubs.py's running_server(), so a single bad
    # entry aborting the loop early would leak every watcher after it.
    for key, watcher in list(ROOM_WATCHERS.items()):
        try:
            watcher.stop()
        except Exception:
            logger.exception("failed to stop project watcher for %r", key)
    ROOM_WATCHERS.clear()


def _workspace_project_dir(
    room_id: str, project_name: str = WORKSPACE_PROJECT_NAME
) -> Path:
    return SessionManager().session_root / room_id / project_name


def _workspace_context(room_id: str, path: str) -> ProjectContext:
    """The lightweight, tier-1 ProjectContext spanning EVERY project
    attached to this room (path + one-line description per file, no full
    signatures — see workspace/serialize.py's to_lightweight_context()),
    built from workspace/'s cached indexes rather than a fresh
    ContextCollector walk. Passing no `project` filter to
    to_lightweight_context() already renders one '## Project: name (root)'
    section per attachment — no manual loop needed here. Full per-file
    structural detail is available on demand via the `describe` tool
    (tool/describe.py). Caller must ensure every attached project is
    already attached/reconciled (_ensure_workspace_project()) first.
    `path` stays the room's primary project's resolved path (this
    field's existing meaning), even though `raw` may describe more than
    one project."""
    return ProjectContext(
        path=str(Path(path).expanduser().resolve()),
        raw=to_lightweight_context(room_id),
    )


def _index_diff(
    old_index: ProjectIndex | None, new_index: ProjectIndex
) -> tuple[int, int]:
    """(changed_count, total_count) — changed counts files in
    `new_index` that are new or content-changed relative to
    `old_index`, plus files `old_index` had that `new_index` no longer
    does."""
    old_files = old_index.files if old_index is not None else {}
    new_files = new_index.files
    changed = sum(
        1
        for rel, meta in new_files.items()
        if old_files.get(rel) is None or old_files[rel].sha256 != meta.sha256
    )
    removed = sum(1 for rel in old_files if rel not in new_files)
    total = max(len(old_files), len(new_files), 1)
    return changed + removed, total


def _change_fraction(old_index: ProjectIndex | None, new_index: ProjectIndex) -> float:
    """1.0 (treat as "fully changed") when there's no prior baseline to
    compare against at all."""
    if old_index is None or not old_index.files:
        return 1.0
    changed, total = _index_diff(old_index, new_index)
    return changed / total


ReconcileResults = dict[str, tuple[ProjectIndex | None, ProjectIndex]]


def _aggregate_index_diff(results: ReconcileResults) -> tuple[int, int]:
    """_index_diff(), summed across every attached project."""
    changed = total = 0
    for old_index, new_index in results.values():
        c, t = _index_diff(old_index, new_index)
        changed += c
        total += t
    return changed, total


def _aggregate_change_fraction(results: ReconcileResults) -> float:
    """_change_fraction(), generalized across every attached project:
    if ANY attached project has no prior baseline at all, the whole set
    reads as fully changed — same "no baseline = fully changed" rule
    _change_fraction() already applies to a single project. Numerically
    identical to _change_fraction() for a single-project room (its dict
    has exactly one entry)."""
    if any(old is None or not old.files for old, _ in results.values()):
        return 1.0
    changed, total = _aggregate_index_diff(results)
    return changed / total if total else 0.0


class RoomSink:
    """Reports one room's tool activity as broadcast protocol events."""

    def __init__(self, room: "Room") -> None:
        self.room = room

    def tool_call(self, name: str, args: str) -> None:
        self.room.active_tool = name
        self.room.broadcast_now(events.TOOL_CALL, {"name": name, "args": args})
        self.room.broadcast_ui_now(
            self.room._content_ops("tool_call", name=name, args=args)
        )
        self.room.append_transcript({"type": "tool_call", "name": name, "args": args})

    def tool_result(self, text: str) -> None:
        self.room.active_tool = None
        self.room.broadcast_now(events.TOOL_RESULT, {"output": text})
        self.room.broadcast_ui_now(self.room._content_ops("tool_result", output=text))
        self.room.append_transcript({"type": "tool_result", "output": text})

    def tokens(self, prompt: int, completion: int, total: int) -> None:
        self.room.tokens["prompt"] = prompt
        self.room.tokens["completion"] = completion
        self.room.tokens["total"] += total or (prompt + completion)
        self.room.broadcast_now(events.TOKENS, dict(self.room.tokens))
        self.room.broadcast_ui_now(self.room._state_ops())
        self.room.save()


class Room:
    def __init__(
        self,
        room_id: str,
        path: str,
        loop: asyncio.AbstractEventLoop,
        projects: dict[str, str] | None = None,
    ):
        self.id = room_id
        self.path = path  # the primary/identity project's path — unchanged meaning
        self.projects: dict[str, str] = (
            dict(projects) if projects is not None else {WORKSPACE_PROJECT_NAME: path}
        )
        self.loop = loop
        self.clients: set[Transport] = set()
        self.turn_active = False
        self.awaiting_reply = False
        self.resync_suggested = False
        self.status_label: str | None = None
        self.active_tool: str | None = None
        self.tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.created_at = _now()
        self.updated_at = self.created_at
        self.transcript: list[dict[str, Any]] = []
        self.reply_queue: queue.Queue[str] = queue.Queue()
        # The agent's currently pending ask() options, if any — set in
        # _ask_and_wait, read by wire/routes.py's /ui/event to resolve
        # an "opt-N" click back to the option text it stands for. None
        # whenever no question with buttons is outstanding.
        self.pending_options: list[str] | None = None

        # Fresh each time, reading whatever ROOMS_DIR currently is — this
        # is what lets tests/stubs.py's `rooms.ROOMS_DIR = tmp_dir`
        # monkeypatch (set *before* constructing any Room) keep working.
        self.repo = RoomRepository(ROOMS_DIR)

        self.events = StageEventBus()
        self.events.subscribe(LoggingStageObserver(f"agent.stage.room.{room_id}"))
        config = PipelineConfig()
        self.pipeline = pipeline_factory(config, self.events, self)

    # ---- construction / persistence ----------------------------------

    @classmethod
    def create(cls, path: str, loop: asyncio.AbstractEventLoop) -> "Room":
        """Always builds a brand-new room and kicks off its bootstrap
        turn — callers that want "resume if this path was already
        analyzed, otherwise create" (i.e. every real caller; see
        wire/routes.py's session_create) should check get_or_load()
        with room_id_for_path(path) first and only fall back to this."""
        room = cls(room_id_for_path(path), path, loop)
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

        room = cls(raw["id"], raw["path"], loop, projects=raw.get("projects"))
        room.tokens = raw.get("tokens", room.tokens)
        room.created_at = raw.get("created_at", room.created_at)
        room.transcript = raw.get("transcript", [])
        room.pipeline.analyst.resume(messages_from_dict(raw.get("messages", [])))
        # Refreshes file metadata/signatures and (re)starts the watcher
        # for a room resumed after a server restart — the same freshness
        # guarantee a fresh bootstrap gets, just without the cache-check/
        # analysis decision that only applies to a brand-new turn.
        room._ensure_workspace_project()
        # resume() restores the analyst's own conversation, but
        # ProjectPipeline.ask() has a *separate* precondition — it needs
        # self.context (never persisted to rooms/{id}.json, and never set
        # by anything else on this path) or it raises "Call start()
        # before ask()." on the very next /prompt. Rebuilt cheaply from
        # workspace/'s already-reconciled index instead of a fresh
        # ContextCollector walk — this never touches the analyst's
        # restored messages, just satisfies the pipeline-level guard.
        room.pipeline.context = _workspace_context(room.id, room.path)
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
            "projects": dict(self.projects),
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
            "projects": self.project_list(),
            "model": os.getenv("GAPGPT_MODEL", "gpt-4o-mini"),
            "base_url": os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1"),
            "tools": TOOL_NAMES,
            "turn_active": self.turn_active,
            "status_label": self.status_label,
            "awaiting_reply": self.awaiting_reply,
            "resync_suggested": self.resync_suggested,
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
        await self._emit_ui(self._state_ops())

    # ---- server-driven UI ----------------------------------------------
    #
    # Everything the reference TUI client actually renders arrives as
    # `ui.update` ops built by service/ui_builder.py — the semantic
    # events above (session.state, message, tool.call, ...) still fire
    # for any other purpose, but the client only listens to this channel.

    def _state_ops(self) -> list[UIOp]:
        """The header/footer replace ops every state-affecting action
        sends alongside its own specific ops (a content append, a modal
        replace). Safe — cheap, even — to send unconditionally: the
        client updates an already-mounted widget's props in place
        rather than remounting it, so resending unchanged header/footer
        props never disrupts in-progress typing in footer-input."""
        model = os.getenv("GAPGPT_MODEL", "gpt-4o-mini")
        base_url = os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1")
        header = ui_builder.header_node(
            model,
            base_url,
            TOOL_NAMES,
            self.active_tool,
            self.tokens,
            self.status_label,
        )
        footer_info = ui_builder.footer_info_node(
            self.path, self.project_list(), self.id
        )
        footer_input = ui_builder.footer_input_node(
            self.awaiting_reply, self.resync_suggested
        )
        return [
            UIOp(op="replace", target="header", node=header),
            UIOp(op="replace", target="footer-info", node=footer_info),
            UIOp(op="replace", target="footer-input", node=footer_input),
        ]

    def ui_tree(self) -> Node:
        """The full initial component tree for a client that just
        connected or resumed — header/footer plus the whole transcript
        replayed through the exact same content_entry_node() rendering
        every live event uses (via _content_node), so there's only ever
        one way a given transcript kind gets drawn. Sent once, in
        /session/create's or /session/resume's response data
        (wire/routes.py) — never as part of a ui.update op."""
        model = os.getenv("GAPGPT_MODEL", "gpt-4o-mini")
        base_url = os.getenv("GAPGPT_BASE_URL", "https://api.gapgpt.app/v1")
        transcript_nodes = [
            self._content_node(
                entry["type"],
                **{k: v for k, v in entry.items() if k not in ("type", "ts")},
            )
            for entry in self.transcript
        ]
        return ui_builder.root_tree(
            path=self.path,
            projects=self.project_list(),
            room_id=self.id,
            model=model,
            base_url=base_url,
            tool_names=TOOL_NAMES,
            active_tool=self.active_tool,
            tokens=self.tokens,
            status_label=self.status_label,
            awaiting_reply=self.awaiting_reply,
            awaiting_resync=self.resync_suggested,
            transcript_nodes=transcript_nodes,
        )

    async def _emit_ui(self, ops: list[UIOp]) -> None:
        await self._emit(events.UI_UPDATE, {"ops": [asdict(op) for op in ops]})

    def broadcast_ui_now(self, ops: list[UIOp]) -> None:
        """broadcast_now()'s counterpart for UI ops — called from the
        worker thread (RoomSink's tool_call/tool_result/tokens, and
        _ask_and_wait, all run there)."""
        self.broadcast_now(events.UI_UPDATE, {"ops": [asdict(op) for op in ops]})

    def _content_node(self, kind: str, **fields: Any) -> Node:
        return ui_builder.content_entry_node(kind, uuid.uuid4().hex, **fields)

    def _content_ops(self, kind: str, **fields: Any) -> list[UIOp]:
        """One append op for a new content entry, plus the same
        header/footer refresh every state-affecting action sends —
        shared by the async path (append_content, below) and the
        worker-thread-safe sync path (RoomSink's tool_call/tool_result)."""
        node = self._content_node(kind, **fields)
        return [UIOp(op="append", target="content", node=node)] + self._state_ops()

    async def append_content(self, kind: str, **fields: Any) -> None:
        """Appends one transcript-kind entry to the content log and
        refreshes header/footer alongside it — the async counterpart to
        RoomSink's synchronous worker-thread appends below."""
        await self._emit_ui(self._content_ops(kind, **fields))

    async def push_modal(self, node: Node) -> None:
        await self._emit_ui([UIOp(op="replace", target="modal", node=node)])

    async def dismiss_modal(self) -> None:
        await self._emit_ui([UIOp(op="remove", target="modal")])

    # ---- turns ------------------------------------------------------------
    #
    # /prompt must respond immediately and let a turn run in the
    # background (`wire/routes.py` fires it via asyncio.create_task):
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

    def try_consume_resync(self) -> bool:
        if not self.resync_suggested:
            return False
        self.resync_suggested = False
        return True

    async def run_bootstrap(self) -> None:
        """Assumes try_start_turn() already succeeded (Room.create() sets
        turn_active — and status_label — itself, so a client can't /prompt
        before this runs)."""
        try:
            cached_answer, resync_info = await asyncio.to_thread(
                self._collect_and_start
            )
        except Exception as exc:
            self.turn_active = False
            self.status_label = None
            await self._emit(events.ERROR, {"message": friendly(exc)})
            return

        if cached_answer is not None:
            if resync_info is not None:
                self.resync_suggested = True
            await self._finish_turn_with_answer(cached_answer, resync_info)
            return

        await self._run_turn(BOOTSTRAP_QUERY, cache_after=True)

    async def run_resync(self) -> None:
        """Assumes try_start_turn() already succeeded — the confirmed
        response to a `resync.suggested` event (see wire/routes.py's
        /resync). Always runs a fresh analysis, bypassing the cache
        check entirely, unlike run_bootstrap()."""
        try:
            await asyncio.to_thread(self._collect_and_start_for_resync)
        except Exception as exc:
            self.turn_active = False
            self.status_label = None
            await self._emit(events.ERROR, {"message": friendly(exc)})
            return
        await self._run_turn(BOOTSTRAP_QUERY, cache_after=True)

    def project_list(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "path": path, "primary": name == WORKSPACE_PROJECT_NAME}
            for name, path in sorted(self.projects.items())
        ]

    def add_project(self, path: str, name: str | None = None) -> str:
        """Registers a new project against this room's own state only —
        the real attach/reconcile (workspace.manager.SessionManager.attach())
        happens later, inside _ensure_workspace_project(), the next time
        run_resync() runs (always triggered right after this by the
        caller — see wire/routes.py's /project/add). Raises
        ProjectNameConflict if `name` already maps to a different path in
        this room, mirroring SessionManager.attach()'s own check."""
        resolved = str(Path(path).expanduser().resolve())
        project_name = name or Path(resolved).name
        existing = self.projects.get(project_name)
        if existing is not None and Path(existing).resolve() != Path(resolved):
            raise ProjectNameConflict(
                f"project name {project_name!r} is already attached to "
                f"{existing!r}, not {resolved!r}"
            )
        self.projects[project_name] = resolved
        self.save()
        return project_name

    def remove_project(self, name: str) -> None:
        """Detaches `name` from both the workspace store and this room's
        own state, and stops its ProjectWatcher. Never allows removing
        the room's own primary project (its identity)."""
        if name == WORKSPACE_PROJECT_NAME:
            raise CannotRemovePrimaryProject(
                f"{name!r} is this room's primary project and cannot be removed"
            )
        if name not in self.projects:
            raise ProjectNotFound(f"project {name!r} is not attached to this room")
        SessionManager().detach(self.id, name)
        watcher = ROOM_WATCHERS.pop((self.id, name), None)
        if watcher is not None:
            watcher.stop()
        del self.projects[name]
        self.save()

    def _collect_and_start_for_resync(self) -> None:
        guard.set_project_roots(self.projects, primary=WORKSPACE_PROJECT_NAME)
        room_context.set_current_room(self.id)
        self._ensure_workspace_project()
        context = _workspace_context(self.id, self.path)
        self.pipeline.context = context
        self.pipeline.analyst.start_session(context)

    def _collect_and_start(self) -> tuple[str | None, dict | None]:
        """Runs on the bootstrap worker thread. Always attaches/
        reconciles this room's project into the workspace metadata
        store first (workspace/manager.py's SessionManager, keyed by
        this room's own id) — this is what populates/refreshes per-file
        signatures regardless of what happens next — then seeds the
        analyst's session from that same workspace-derived, lightweight
        context (_workspace_context()) regardless of whether a cached
        answer exists: by the time this runs, the index is always
        already built, so there's no reason for a fresh analysis to seed
        from anything thinner. `agent/`'s own ContextCollector/
        tool.metadata path is no longer used here at all — see
        docs/SESSIONS.md's "Room bootstrap integration".

        Returns (cached_answer, resync_info):
          - No cached ProjectSynthesis exists: returns (None, None); the
            caller still runs a real ask() against the session just seeded.
          - A cached ProjectSynthesis exists and the project hasn't
            drifted past RESYNC_CHANGE_THRESHOLD since it was made:
            returns (cached.answer, None) — no LLM call happens at all,
            the actual token-saving payoff.
          - A cached ProjectSynthesis exists but the project HAS
            drifted past the threshold: returns (cached.answer,
            {"changed", "total", "fraction"}) so the caller uses the
            (possibly stale) cached answer for now AND flags the room
            for a resync prompt, rather than silently trusting or
            discarding it.
        """
        guard.set_project_roots(self.projects, primary=WORKSPACE_PROJECT_NAME)
        room_context.set_current_room(self.id)
        project_dir = _workspace_project_dir(self.id)
        reconcile_results = self._ensure_workspace_project()

        context = _workspace_context(self.id, self.path)
        self.pipeline.context = context
        self.pipeline.analyst.start_session(context)

        cached = SynthesisRepository(project_dir).load()
        if cached is None:
            return None, None

        fraction = _aggregate_change_fraction(reconcile_results)
        if fraction < RESYNC_CHANGE_THRESHOLD:
            return cached.answer, None

        changed, total = _aggregate_index_diff(reconcile_results)
        return cached.answer, {"changed": changed, "total": total, "fraction": fraction}

    def _ensure_workspace_project(self) -> ReconcileResults:
        """Attach (idempotent) every project in self.projects into the
        workspace metadata store, keyed by the room's own id, reconciling
        each one's file index synchronously — the source of the "file
        metadata stays fresh" guarantee every time a room becomes active,
        whether freshly created, resumed, or re-bootstrapped. Also
        ensures a background ProjectWatcher is running for each. Returns
        {name: (index_before_this_reconcile_or_None, index_after)} so
        callers can measure how much changed, per project or aggregated
        (see _aggregate_change_fraction()).
        """
        manager = SessionManager()
        try:
            manager.create(self.id)
        except SessionAlreadyExists:
            pass

        results: ReconcileResults = {}
        for name, path in self.projects.items():
            project_dir = _workspace_project_dir(self.id, name)
            old_index = IndexRepository(project_dir).load()
            manager.attach(self.id, path, project_name=name)
            new_index = IndexRepository(project_dir).load()
            self._ensure_watcher(name, project_dir, new_index, path)
            results[name] = (old_index, new_index)
        return results

    def _ensure_watcher(
        self,
        project_name: str,
        project_dir: Path,
        index: ProjectIndex | None,
        path: str,
    ) -> None:
        if (self.id, project_name) in ROOM_WATCHERS:
            return
        if index is None:
            # ProjectWatcher assumes a valid, already-reconciled starting
            # index (see its own docstring) — this should never happen
            # right after a successful attach()/reconcile, but refusing
            # here is cheap insurance against ever registering a watcher
            # that would crash the next stop_all_room_watchers() call.
            logger.error(
                "no index available for project %r (room %s) — not starting a watcher",
                project_name,
                self.id,
            )
            return
        project_root = Path(path).expanduser().resolve()
        ignore_rules = IgnoreRules(project_root)
        indexer = ProjectIndexer(project_name, project_root, ignore_rules)
        watcher = ProjectWatcher(indexer, index, IndexRepository(project_dir))
        watcher.start()
        ROOM_WATCHERS[(self.id, project_name)] = watcher

    def _cache_synthesis(self, answer: str) -> None:
        """Runs the synthesize stage directly (ProjectPipeline.ask()
        already ran analyze; this doesn't repeat it) and persists the
        result, so the next brand-new room for this same project (no
        resumable rooms/{id}.json — e.g. after a reset) can skip the LLM
        entirely — see _collect_and_start()."""
        synthesized = self.pipeline.synthesizer.synthesize(
            answer, self.pipeline.context
        )
        project_dir = _workspace_project_dir(self.id)
        index = IndexRepository(project_dir).load()
        SynthesisRepository(project_dir).save(
            ProjectSynthesis(
                answer=answer,
                synthesized=synthesized,
                created_at=_now(),
                file_count=len(index.files) if index is not None else 0,
            )
        )

    async def _finish_turn_with_answer(
        self, answer: str, resync_info: dict | None
    ) -> None:
        """Delivers `answer` as this turn's result without running the
        LLM — used when a cached ProjectSynthesis already covers this
        bootstrap (see _collect_and_start()). Mirrors _run_turn()'s
        bookkeeping (transcript/ANSWER event/state/save) so a cache hit
        looks identical to a fresh answer to the client, just instant.
        """
        self.append_transcript({"type": "answer", "text": answer})
        await self._emit(events.ANSWER, {"text": answer})
        await self.append_content("answer", text=answer)
        if resync_info is not None:
            self.append_transcript({"type": "resync_suggested", **resync_info})
            await self._emit(events.RESYNC_SUGGESTED, resync_info)
            await self.append_content("resync_suggested", **resync_info)
        self.turn_active = False
        self.status_label = None
        self.active_tool = None
        await self.broadcast_state()
        self.save()

    async def run_prompt(self, text: str) -> None:
        """Assumes try_start_turn() already succeeded."""
        self.status_label = "thinking"
        await self.broadcast_state()
        self.append_transcript({"type": "message", "role": "user", "text": text})
        await self._emit(events.MESSAGE, {"role": "user", "text": text})
        await self.append_content("message", text=text, role="user")
        await self._run_turn(text)

    async def _run_turn(self, question: str, cache_after: bool = False) -> None:
        answer: str | None = None
        try:
            answer = await asyncio.to_thread(self._ask_blocking, question)
        except Exception as exc:
            message = friendly(exc)
            await self._emit(events.ERROR, {"message": message})
            await self.append_content("error", message=message)
        else:
            self.append_transcript({"type": "answer", "text": answer})
            await self._emit(events.ANSWER, {"text": answer})
            await self.append_content("answer", text=answer)
        finally:
            self.turn_active = False
            self.status_label = None
            self.active_tool = None
            await self.broadcast_state()
            self.save()

        # Caching happens after the turn is already marked finished — it's
        # an enhancement, not part of this turn's own correctness, and
        # must not hold turn_active True (and so block a follow-up
        # /prompt) while it writes to disk. A synthesis failure here must
        # not surface as an unhandled task exception either (run_bootstrap()
        # isn't awaited by anything that would catch it).
        if cache_after and answer is not None:
            try:
                await asyncio.to_thread(self._cache_synthesis, answer)
            except Exception:
                logger.exception(
                    "failed to cache project synthesis for room %s", self.id
                )

    def _ask_blocking(self, question: str) -> str:
        guard.set_project_roots(self.projects, primary=WORKSPACE_PROJECT_NAME)
        room_context.set_current_room(self.id)
        with ask_context.asker(self._ask_and_wait):
            return self.pipeline.ask(question)

    # ---- the agent's own mid-turn question -------------------------------

    def _ask_and_wait(
        self, question: str, options: list[str] | None = None
    ) -> str | None:
        """Called from the worker thread, inside the `ask` tool."""
        self.awaiting_reply = True
        self.pending_options = options
        self.append_transcript(
            {"type": "question", "text": question, "options": options}
        )
        self.broadcast_now(events.QUESTION, {"text": question, "options": options})
        ops = self._content_ops("question", text=question)
        modal_node = ui_builder.question_modal_node(question, options)
        if modal_node is not None:
            ops.append(UIOp(op="replace", target="modal", node=modal_node))
        self.broadcast_ui_now(ops)
        future = asyncio.run_coroutine_threadsafe(self.broadcast_state(), self.loop)
        future.result()
        return self.reply_queue.get()

    async def deliver_reply(self, text: str) -> None:
        """Assumes try_consume_reply() already succeeded."""
        self.pending_options = None
        self.append_transcript({"type": "message", "role": "user", "text": text})
        await self._emit(events.MESSAGE, {"role": "user", "text": text})
        await self.dismiss_modal()
        await self.append_content("message", text=text, role="user")
        await self.broadcast_state()
        self.reply_queue.put(text)

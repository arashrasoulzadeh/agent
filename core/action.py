"""The `/command` extensibility point — mirrors tool/'s own
auto-discovery pattern (tool/registry.py, core/discovery.py) for
client-facing slash commands instead of agent capabilities. Drop a
`.py` file into actions/ defining one module-level `Action` and it's
picked up automatically (actions/__init__.py, core/action_registry.py)
— nothing else needs touching to add a `/command`.

Every action has a `kind`, and the kind decides who ever sees it:

- "pre_prompt" / "post_prompt": pure data (`text`) — resolved entirely
  client-side (ui/app.py, desktop/renderer.js) the instant the command
  is accepted from the popup. The typed "/name" token is replaced
  inline by `text`, which then behaves like anything else the user
  typed: still editable, still backspace-deletable, character by
  character — never a hidden marker, never metadata riding along
  separately. This never reaches the server as a command at all, so
  `run` is always None for these two kinds.
- "ui" / "action": a real side effect, so `run()` executes server-side
  (wire/routes.py's _dispatch_footer_submit), against an ActionContext
  — a narrow capability surface, never a raw Room or Transport, so
  actions/ can depend only on core/ and never on service/ or wire/
  (what lets service/rooms.py and wire/routes.py both import actions/
  back without a cycle). wire/routes.py's own _RouteActionContext is
  the one concrete implementation, delegating to this module's own
  route functions (/project/add, /project/remove, ...) — so a command
  and its equivalent direct route stay behaviorally identical by
  construction, not by a parallel copy of the same logic.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Kind = Literal["pre_prompt", "post_prompt", "ui", "action"]


class ActionContext(Protocol):
    """What an "action"/"ui" kind Action.run() gets to do — see this
    module's own docstring for why this is a narrow Protocol rather
    than a Room/Transport import."""

    async def add_project(self, path: str, name: str | None) -> None: ...
    async def remove_project(self, name: str) -> None: ...
    async def show_settings(self) -> None: ...
    async def show_panel(
        self, title: str | None, blocks: list[dict[str, Any]]
    ) -> None: ...
    async def info(self, text: str) -> None: ...
    def project_list(self) -> list[dict[str, Any]]: ...


Run = Callable[[ActionContext, list[str]], Awaitable[None]]


@dataclass
class Action:
    name: str  # e.g. "/add" — the token the user types, "/" included
    usage: str  # e.g. "/add <path> [name]" — shown in the command popup
    description: str  # e.g. "Attach another project to this room"
    kind: Kind
    text: str | None = None  # pre_prompt/post_prompt only
    run: Run | None = None  # ui/action only

    def __post_init__(self) -> None:
        if not self.name.startswith("/"):
            raise ValueError(f"action name must start with '/': {self.name!r}")
        if self.kind in ("pre_prompt", "post_prompt") and not self.text:
            raise ValueError(f"{self.name}: a {self.kind} action needs 'text'")
        if self.kind in ("ui", "action") and self.run is None:
            raise ValueError(f"{self.name}: a {self.kind} action needs 'run'")

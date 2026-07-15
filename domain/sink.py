"""Where `ProjectAnalyst` reports tool calls, results, and token usage.

`pipeline/` has no business knowing whether it's driven by a websocket
server, a test, or nothing at all — so it reports through this narrow
interface instead of importing a renderer directly. `server/rooms.py`
implements one that broadcasts protocol events; `NullSink` is the default,
so `pipeline/` stays fully usable standalone (e.g. in tests) with no
sink at all.
"""

from typing import Protocol


class Sink(Protocol):
    def tool_call(self, name: str, args: str) -> None: ...
    def tool_result(self, text: str) -> None: ...
    def tokens(self, prompt: int, completion: int, total: int) -> None: ...


class NullSink:
    """Discards everything. The default when no sink is given."""

    def tool_call(self, name: str, args: str) -> None:
        pass

    def tool_result(self, text: str) -> None:
        pass

    def tokens(self, prompt: int, completion: int, total: int) -> None:
        pass

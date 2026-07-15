"""The module lifecycle contract.

A module under modules/ needs nothing from this file to add a
capability — a `@tool`-decorated function is enough (see
modules/__init__.py, core/registry.py). This only matters for a module
that has state to set up or tear down: a connection pool, a background
task, a cache warmed from disk, anything that shouldn't happen at import
time and needs to be released cleanly on shutdown.

To opt in, expose a module-level `MODULE` object implementing whichever
of these three methods you need — `core/registry.py` checks with
`hasattr`, not `isinstance`, so a module implementing only `stop()` (say,
to flush something on shutdown) is exactly as valid as one implementing
all three. See docs/MODULES.md for a full worked example.
"""

from typing import Any, Protocol, runtime_checkable

# The exact method names registry.py looks for. Keep this in sync with
# the Lifecycle protocol below — it's the single source of truth both
# the docs and the discovery code should point back to.
LIFECYCLE_METHODS = ("init", "start", "stop")


@runtime_checkable
class Lifecycle(Protocol):
    """Documents the contract; not used for isinstance checks (a Protocol
    with `runtime_checkable` only matches when *every* member is present,
    which would wrongly reject a module implementing just one hook)."""

    def init(self, config: dict[str, Any]) -> None:
        """Called once, before the server accepts its first connection.
        Read configuration, validate it, fail loudly if it's wrong."""

    def start(self) -> None:
        """Called once, right after every module's init() has run —
        acquire whatever resources the module needs while running
        (open a connection, spawn a background task)."""

    def stop(self) -> None:
        """Called once, during graceful shutdown — release whatever
        init()/start() acquired. Runs even if start() was never reached
        due to another module's init() failing, so make it tolerant of
        partial setup."""


def implements_lifecycle(obj: Any) -> bool:
    """True if `obj` implements at least one lifecycle hook."""
    return any(hasattr(obj, method) for method in LIFECYCLE_METHODS)

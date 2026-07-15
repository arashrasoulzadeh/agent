"""Agent-capability modules, auto-discovered from this folder.

Add a new capability by dropping a `.py` file here that defines a
langchain `@tool` — no registration step required. It's picked up by
`core.registry.discover()`, included in AGENT_TOOLS, and made importable
as `modules.<name>` automatically. Set a module-level `AGENT_TOOL = False`
to keep a tool out of the agent's default toolset (see delete.py,
metadata.py) while still leaving it directly importable.

A module with setup/teardown state (a connection pool, a background
task, ...) additionally exposes a module-level `MODULE` object
implementing the Lifecycle contract (core/module.py); those are
collected into LIFECYCLE_MODULES, which server/app.py calls
init()/start()/stop() on. See docs/MODULES.md for a full example.
"""

from core.registry import discover

_discovery = discover()
AGENT_TOOLS = _discovery.agent_tools
LIFECYCLE_MODULES = _discovery.lifecycle_modules
globals().update(_discovery.all_tools)

__all__ = ["AGENT_TOOLS", "LIFECYCLE_MODULES", *_discovery.all_tools.keys()]

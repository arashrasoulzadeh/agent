"""Agent-capability tools, auto-discovered from this folder.

Add a new capability by dropping a `.py` file here that defines a
langchain `@tool` — no registration step required. It's picked up by
`tool.registry.discover()`, included in AGENT_TOOLS, and made importable
as `tool.<name>` automatically. Set a module-level `AGENT_TOOL = False`
to keep a tool out of the agent's default toolset (see delete.py,
metadata.py) while still leaving it directly importable.

A module with setup/teardown state (a connection pool, a background
task, ...) additionally exposes a module-level `MODULE` object
implementing the Lifecycle contract (core/module.py); those are
collected into LIFECYCLE_MODULES, which wire/app.py calls
init()/start()/stop() on. See docs/MODULES.md for a full example.

This is also the bootstrap point for the hooks/extra system: before
AGENT_TOOLS is exposed, every extra/*.py file is imported (registering
its `@hook(...)`-decorated functions as a side effect), then the
collected tool list is run through the `on_tools_collected` hook — an
extra/ file returning `tools + [my_tool]` from that hook is how it adds
a tool with zero changes here. See docs/HOOKS.md.
"""

import hooks
from tool.registry import discover

hooks.loader.discover()

_discovery = discover()
AGENT_TOOLS = hooks.dispatch.filter("on_tools_collected", _discovery.agent_tools)
LIFECYCLE_MODULES = _discovery.lifecycle_modules
globals().update(_discovery.all_tools)

__all__ = ["AGENT_TOOLS", "LIFECYCLE_MODULES", *_discovery.all_tools.keys()]

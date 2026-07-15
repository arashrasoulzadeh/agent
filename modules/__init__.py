"""Agent-capability modules, auto-discovered from this folder.

Add a new capability by dropping a `.py` file here that defines a
langchain `@tool` — no registration step required. It's picked up by
`core.registry.discover_tools()`, included in AGENT_TOOLS, and made
importable as `modules.<name>` automatically. Set a module-level
`AGENT_TOOL = False` to keep a tool out of the agent's default toolset
(see delete.py, metadata.py) while still leaving it directly importable.
"""

from core.registry import discover_tools

_all_tools, AGENT_TOOLS = discover_tools()
globals().update(_all_tools)

__all__ = ["AGENT_TOOLS", *_all_tools.keys()]

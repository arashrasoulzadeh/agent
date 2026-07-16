"""Auto-discovery for agent-capability tools.

Every `.py` file in tool/ (except `__init__.py` and anything starting
with `_`) is imported exactly once, and from it this collects:

  - any langchain tool defined at module level (the capability itself)
  - an optional `MODULE` object implementing part or all of the
    Lifecycle contract (core/module.py) — init()/start()/stop() hooks,
    for a module with state to set up or tear down

Dropping a new file in tool/ is enough to add either kind of
capability — nothing here or in tool/__init__.py needs editing.

A module opts a tool out of the agent's default toolset with a
module-level `AGENT_TOOL = False` (see tool/delete.py,
tool/metadata.py): its tool is still discovered and importable, just
excluded from AGENT_TOOLS.

TOOLS_DIR/TOOL_PACKAGE are plain module attributes — tests point
them at a temp directory the same way tests/stubs.py repoints
service.rooms.ROOMS_DIR, rather than threading a parameter through.
"""

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import BaseTool

from core.discovery import import_all
from core.module import implements_lifecycle

TOOL_PACKAGE = "tool"
TOOLS_DIR = Path(__file__).resolve().parent.parent / TOOL_PACKAGE


@dataclass
class Discovery:
    all_tools: dict[str, BaseTool] = field(default_factory=dict)
    agent_tools: list[BaseTool] = field(default_factory=list)
    lifecycle_modules: list[object] = field(default_factory=list)


def discover() -> Discovery:
    """Import every module under tool/ once and collect what it offers."""
    result = Discovery()

    for module in import_all(TOOLS_DIR, TOOL_PACKAGE):
        enabled = getattr(module, "AGENT_TOOL", True)

        for value in vars(module).values():
            if isinstance(value, BaseTool):
                result.all_tools[value.name] = value
                if enabled:
                    result.agent_tools.append(value)

        lifecycle_obj = getattr(module, "MODULE", None)
        if lifecycle_obj is not None and implements_lifecycle(lifecycle_obj):
            result.lifecycle_modules.append(lifecycle_obj)

    return result


def discover_tools() -> tuple[dict[str, BaseTool], list[BaseTool]]:
    """Back-compat convenience: (all_tools_by_name, agent_tools) only."""
    result = discover()
    return result.all_tools, result.agent_tools

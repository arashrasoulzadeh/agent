"""Auto-discovery for agent-capability modules.

Every `.py` file in modules/ (except `__init__.py` and anything starting
with `_`) is imported automatically, and any langchain tool it defines at
module level is collected. Dropping a new file in modules/ is enough to
add a capability — nothing here or in modules/__init__.py needs editing.

A module opts out of the agent's default toolset with a module-level
`AGENT_TOOL = False` (see modules/delete.py, modules/metadata.py): its
tool is still discovered and importable, just excluded from AGENT_TOOLS.
"""

import importlib
import pkgutil
from pathlib import Path

from langchain_core.tools import BaseTool

MODULES_PACKAGE = "modules"
MODULES_DIR = Path(__file__).resolve().parent.parent / MODULES_PACKAGE


def discover_tools() -> tuple[dict[str, BaseTool], list[BaseTool]]:
    """Import every module under modules/ and collect its tools.

    Returns (all_tools_by_name, agent_tools): the second is the first
    filtered down to modules that didn't set AGENT_TOOL = False.
    """
    all_tools: dict[str, BaseTool] = {}
    agent_tools: list[BaseTool] = []

    infos = sorted(pkgutil.iter_modules([str(MODULES_DIR)]), key=lambda m: m.name)
    for info in infos:
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{MODULES_PACKAGE}.{info.name}")
        enabled = getattr(module, "AGENT_TOOL", True)
        for value in vars(module).values():
            if isinstance(value, BaseTool):
                all_tools[value.name] = value
                if enabled:
                    agent_tools.append(value)

    return all_tools, agent_tools

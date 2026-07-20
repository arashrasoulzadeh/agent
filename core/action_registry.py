"""Auto-discovery for `/command` actions — mirrors tool/registry.py's
own discovery mechanism (core/discovery.py) almost exactly, just
harvesting `Action` instances (core/action.py) from actions/ instead of
langchain tools from tool/.
"""

from pathlib import Path

from core.action import Action
from core.discovery import import_all

ACTIONS_PACKAGE = "actions"
ACTIONS_DIR = Path(__file__).resolve().parent.parent / ACTIONS_PACKAGE


def discover_actions() -> dict[str, Action]:
    """Import every module under actions/ once and collect its Action(s),
    keyed by name (e.g. "/add"). Iteration order matches import order —
    core.discovery.import_all's own sorted-by-filename guarantee — so
    the command popup's listing order is deterministic."""
    result: dict[str, Action] = {}
    for module in import_all(ACTIONS_DIR, ACTIONS_PACKAGE):
        for value in vars(module).values():
            if isinstance(value, Action):
                result[value.name] = value
    return result

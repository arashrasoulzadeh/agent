"""Client-facing `/command` actions, auto-discovered from this folder —
see core/action.py for the Action/ActionContext contract this whole
mechanism is built on. Add a new command by dropping a `.py` file here
that defines a module-level `Action` (conventionally named `action`,
though discovery scans every module-level value — see
core/action_registry.py) — no registration step required.
"""

from core.action_registry import discover_actions

ACTIONS = discover_actions()

__all__ = ["ACTIONS"]

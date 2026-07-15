"""Runs modules' init()/start()/stop() hooks (core/module.py) around the
server's own run.

Each call is isolated: one module's hook raising is logged and skipped,
never allowed to stop another module's hook from running or to crash
server startup/shutdown entirely. `stop_all` in particular always runs
every module's `stop()`, even if that module's own `init()`/`start()`
never got called (see server/app.py's `finally` block) — a module's
`stop()` should tolerate being called after a partial or failed setup.
"""

import logging
from typing import Any

logger = logging.getLogger("interfaces.ws.lifecycle")


def init_all(modules_list: list[Any], config: dict[str, Any]) -> None:
    for mod in modules_list:
        _call(mod, "init", config)


def start_all(modules_list: list[Any]) -> None:
    for mod in modules_list:
        _call(mod, "start")


def stop_all(modules_list: list[Any]) -> None:
    for mod in modules_list:
        _call(mod, "stop")


def _call(mod: Any, hook_name: str, *args: Any) -> None:
    hook = getattr(mod, hook_name, None)
    if hook is None:
        return
    try:
        hook(*args)
    except Exception:
        logger.exception("module %r failed its %s() hook", mod, hook_name)

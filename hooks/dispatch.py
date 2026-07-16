"""Call whatever's registered for a named hook point.

Two flavors, both isolating each callback's exception (logged, never
propagated — a broken hook must not be able to break another hook's turn
or crash whoever's dispatching), mirroring core/module.py's isolate-and-log
precedent for lifecycle hooks and agent/events.py's StageEventBus._notify
for stage observers:

- `filter(name, value, *a, **kw)` threads `value` through each registered
  callback in registration order — each callback receives the PREVIOUS
  callback's return value, not the original — and returns the final
  result. A callback returning `None` is treated as "no change" (return
  `""` explicitly to blank out text; returning `None` to mean "clear this"
  would be a silent, hard-to-debug way to lose data).
- `notify(name, *a, **kw)` calls every registered callback for its side
  effects only and discards whatever it returns — for hooks that observe
  rather than transform (e.g. on_tool_call).
"""

import logging

from hooks.registry import HOOKS

logger = logging.getLogger("hooks.dispatch")


def filter(name: str, value, *args, **kwargs):
    for callback in HOOKS.get(name, ()):
        try:
            result = callback(value, *args, **kwargs)
        except Exception:
            logger.exception("hook %r's %s raised", name, callback)
            continue
        if result is not None:
            value = result
    return value


def notify(name: str, *args, **kwargs) -> None:
    for callback in HOOKS.get(name, ()):
        try:
            callback(*args, **kwargs)
        except Exception:
            logger.exception("hook %r's %s raised", name, callback)

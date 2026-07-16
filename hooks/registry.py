"""The hook registry: a name -> ordered list of callbacks.

Kept separate from dispatch.py so extra/*.py files can do
`from hooks.registry import hook` (or `from hooks import hook`) without
needing anything else in this package to have finished initializing —
see hooks/__init__.py's docstring for why that ordering matters.
"""

from collections import defaultdict
from collections.abc import Callable

HOOKS: dict[str, list[Callable]] = defaultdict(list)


def hook(name: str) -> Callable:
    """Decorator: register a function under a named hook point.

    Registration order matters — dispatch.filter()/notify() call
    callbacks in the order they were registered, which (since extra/
    files are imported in sorted filename order, see hooks/loader.py) is
    deterministic across multiple files.
    """

    def decorator(fn: Callable) -> Callable:
        HOOKS[name].append(fn)
        return fn

    return decorator

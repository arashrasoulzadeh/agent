"""Auto-discovery for extra/*.py hook files.

Every `.py` file in extra/ (except `__init__.py` and anything starting
with `_`) is imported exactly once — importing it is enough to register
its `@hook(...)`-decorated functions, since the decorator's registration
happens as an import-time side effect (hooks/registry.py). Nothing here
inspects what a file actually registered; that's the whole point of a
decorator-based contract.

EXTRA_DIR/EXTRA_PACKAGE are plain module attributes — tests point them at
a temp directory the same way tests/test_registry.py repoints
core.registry.MODULES_DIR, rather than threading a parameter through.
"""

from pathlib import Path

from core.discovery import import_all

EXTRA_PACKAGE = "extra"
EXTRA_DIR = Path(__file__).resolve().parent.parent / EXTRA_PACKAGE


def discover() -> None:
    """Import every module under extra/ once, registering their hooks."""
    import_all(EXTRA_DIR, EXTRA_PACKAGE)

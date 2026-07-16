"""Generic "import every .py file in a directory, once" helper.

Shared by tool/registry.py (scanning tool/ for capabilities) and
hooks/loader.py (scanning extra/ for hook registrations) — both want the
exact same pkgutil scan/sort/skip/import loop, just harvesting a
different thing from each imported module afterward.
"""

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType


def import_all(directory: Path, package: str) -> list[ModuleType]:
    """Import every module under `directory` (the `package`'s own
    directory) once, skipping `__init__.py` and anything starting with
    `_`. Returns the imported module objects, in the same sorted-by-name
    order they were imported in — callers that harvest from more than one
    file rely on this order being deterministic."""
    modules = []
    infos = sorted(pkgutil.iter_modules([str(directory)]), key=lambda m: m.name)
    for info in infos:
        if info.name.startswith("_"):
            continue
        modules.append(importlib.import_module(f"{package}.{info.name}"))
    return modules

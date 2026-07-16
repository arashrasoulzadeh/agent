"""Third-party hook files, auto-discovered from this folder.

Drop a `.py` file here that registers `@hook(...)`-decorated functions
(see `hooks/`, docs/HOOKS.md) — no registration step beyond the decorator
itself. This package doesn't scan itself; `hooks.loader.discover()`
(called from `tool/__init__.py`, before `AGENT_TOOLS` is assembled) does
that from outside, the same way `tool/__init__.py` doesn't scan `tool/`
itself either.
"""

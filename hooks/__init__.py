"""Event hooks: let files in extra/ interact with the rest of the app
without editing it.

An extra/*.py file registers a callback against a named hook point:

    from hooks import hook

    @hook("before_prompt")
    def rewrite_prompt(text: str) -> str:
        return text.strip()

See docs/HOOKS.md for the full hook-point catalog and contract.

This package has zero dependencies on tool/agent/models/wire/service/ui —
every one of those may safely depend on hooks (one-directional, the same
rule core/ follows) without hooks ever depending back on any of them.

Import-time bootstrapping note: this module deliberately does NOT trigger
extra/ discovery itself. If it did, an extra/*.py file's `from hooks
import hook` could run while this very module was still mid-execution (a
partial-module circular import). Instead, tool/__init__.py — the existing
bootstrap point for auto-discovered capabilities — does `import hooks`
(fully resolving this module first) and only then calls
`hooks.loader.discover()`.
"""

from hooks import dispatch, loader
from hooks.registry import hook

__all__ = ["hook", "dispatch", "loader"]

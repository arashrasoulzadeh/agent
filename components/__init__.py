"""The UI vocabulary every renderer shares — the CLI (`ui/app.py`), the
server (`service/ui_builder.py`, `core/style.py`), and the desktop app
(`desktop/renderer.js`, via `components/js/`). `spec.json` is the single
source of truth; this module is Python's exporter of it, `components/js/`
is JS's. A new style token, exit word, or client-local UI constant is
added to `spec.json` once and every renderer picks it up — it should
never be hardcoded separately in `ui/app.py` or `desktop/renderer.js`
again.

Kept dependency-free (stdlib `json` only) so both the thin CLI process
and the server process can import it without pulling in anything extra.
"""

import json
from pathlib import Path
from typing import Any

_SPEC_PATH = Path(__file__).parent / "spec.json"


def load_spec() -> dict[str, Any]:
    """Re-reads spec.json fresh — tests use this to assert Python/JS agree
    on the same file without relying on this module's cached constants."""
    return json.loads(_SPEC_PATH.read_text())


_spec = load_spec()

STYLE_TOKENS: dict[str, str] = _spec["styleTokens"]
RICH_COLORS: dict[str, str] = _spec["richColors"]
NODE_TYPES: list[str] = _spec["nodeTypes"]
RESERVED_IDS: list[str] = _spec["reservedIds"]
EXIT_COMMANDS: set[str] = set(_spec["exitCommands"])
REPLY_PLACEHOLDERS: tuple[str, ...] = tuple(_spec["replyPlaceholders"])
SPINNER_FRAMES: str = _spec["spinnerFrames"]
CONNECTION_STATES: dict[str, list[str]] = _spec["connectionStates"]

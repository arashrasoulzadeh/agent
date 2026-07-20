"""Literal Rich style strings — semantic color roles, not a Console-bound
rich.theme.Theme, since the client renders through Textual's own console
rather than one this process controls.

Lives in core/, not ui/, because service/ui_builder.py is now the
authoritative source of "what's drawn" (including styling) — the
client's generic renderer just applies whatever style string a node's
props already carry, it doesn't need to know these names at all
anymore. service/ may import core/; it may not import ui/ (an outer,
interface-layer package), so these moved down to the layer both can
reach.

The values themselves live in components/spec.json, not here — that's
the one place the CLI, the server, and the desktop app all read the UI
vocabulary from (see components/__init__.py's docstring). This module
just re-exports them under their semantic names for code already
importing core.style.
"""

from components import STYLE_TOKENS

THINK = STYLE_TOKENS["THINK"]
TOOL = STYLE_TOKENS["TOOL"]
MESSAGE = STYLE_TOKENS["MESSAGE"]
QUESTION = STYLE_TOKENS["QUESTION"]
ERROR = STYLE_TOKENS["ERROR"]
INFO = STYLE_TOKENS["INFO"]
BANNER = STYLE_TOKENS["BANNER"]

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
"""

THINK = "grey50"
TOOL = "bright_cyan"
MESSAGE = "bold bright_cyan"
QUESTION = "bold bright_yellow"
ERROR = "bold red"
INFO = "grey62"
BANNER = "bold bright_cyan"

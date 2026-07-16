"""Literal Rich style strings shared by app.py, trace.py, answer.py, and
error.py.

RichLog renders through Textual's own console rather than a Console we
control, so styles here are literal color names instead of a registered
rich.theme.Theme's semantic aliases.
"""

THINK = "grey50"
TOOL = "bright_cyan"
MESSAGE = "bold bright_cyan"
QUESTION = "bold bright_yellow"
ERROR = "bold red"
INFO = "grey62"
BANNER = "bold bright_cyan"

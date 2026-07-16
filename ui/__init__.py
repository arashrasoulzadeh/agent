"""The full-screen agent TUI — a thin client of the agent's WebSocket
server (see wire/, and docs/PROTOCOL.md for the wire protocol).

`app.py` owns a single Textual App for the whole session: a header
(banner, token count, model/tools, turn status), a scrollable content log
(the conversation transcript), and a footer (project/room info + the
input line) — see its module docstring for the layout. Header and footer
are sized to their own content and Textual reflows everything on resize,
so nothing here ever grows past the terminal; the content log scrolls
internally instead of the terminal's own scrollback.

This app never runs a pipeline or touches an LLM — it connects, creates
or resumes a room, and renders whatever protocol events arrive. The rest
of this package is small renderers `app.py`'s receive loop calls directly
with (the app, the event's data), one file per kind of event:

    trace.py    tool-call/tool-result trace lines + the token count
    answer.py   the turn's final answer, appended to the transcript
    error.py    friendly error reporting
    style.py    the Rich style strings the above share
"""

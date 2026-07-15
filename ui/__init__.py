"""The full-screen agent TUI.

`app.py` owns a single Textual App for the whole session: a fixed header
(token count + turn status), a scrollable content log (the conversation
transcript), and a footer (project info + the input line) — see its
module docstring for the layout. Every region is sized as a fraction of
the terminal and Textual reflows them on resize, so nothing here ever
grows past the terminal; the content log scrolls internally instead of
the terminal's own scrollback.

The rest of this package is what `pipeline/analyst.py` and `modules/ask.py`
call into while a turn is running, on a worker thread:

    trace.py     tool-call/tool-result trace + token bookkeeping
    prompts.py   the agent's own `ask` tool question
    answer.py    the final answer, appended to the transcript
    error.py     friendly error reporting
    engine.py    file logging, shared by all of the above
    state.py     holds the one running AgentApp instance so the modules
                 above can reach it without importing app.py directly
"""

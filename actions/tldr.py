"""`/tldr` — suffix your question, asking for a short, one-line answer
instead of a full explanation. Resolved entirely client-side, same as
/explain — see that file's docstring and core/action.py's own.
"""

from core.action import Action

action = Action(
    name="/tldr",
    usage="/tldr",
    description="Suffix your question, asking for a short, one-line answer",
    kind="post_prompt",
    text=" Answer in one short sentence.",
)

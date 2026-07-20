"""`/explain` — prefix your question, asking for a step-by-step
explanation instead of a summary. Resolved entirely client-side (see
core/action.py's own docstring on the "pre_prompt"/"post_prompt"
kinds): the command popup sends `text` to the client once, and
accepting this command splices it directly into the input in place of
"/explain " — still yours to edit or backspace away like anything else
you typed. This file exists only to declare the command; nothing here
ever runs server-side.
"""

from core.action import Action

action = Action(
    name="/explain",
    usage="/explain",
    description="Prefix your question, asking for a step-by-step explanation",
    kind="pre_prompt",
    text="Explain step by step: ",
)

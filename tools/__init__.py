from tools.ask import ask
from tools.cat import cat
from tools.edit import edit
from tools.execute import execute
from tools.guard import is_secret, project_root, refusal, set_project_root
from tools.ls import ls
from tools.metadata import metadata
from tools.write import write

# Tools exposed to the reasoning agent. `metadata` is left out on purpose:
# it is the collector's preprocessing tool, and its output already reaches
# the agent as the private project map in the system prompt.
AGENT_TOOLS = [ls, cat, write, edit, execute, ask]

__all__ = [
    "AGENT_TOOLS",
    "ls",
    "cat",
    "write",
    "edit",
    "execute",
    "ask",
    "metadata",
    "is_secret",
    "refusal",
    "set_project_root",
    "project_root",
]

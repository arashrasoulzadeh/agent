from tools.ask import ask
from tools.cat import cat
from tools.create_directory import create_directory
from tools.delete import delete
from tools.edit import edit
from tools.execute import execute
from tools.guard import is_secret, project_root, refusal, set_project_root
from tools.ls import ls
from tools.metadata import metadata
from tools.write import write

# Tools exposed to the reasoning agent. Two are left out on purpose:
#   - `metadata` is the collector's preprocessing tool, and its output
#     already reaches the agent as the project map in the system prompt.
#   - `delete` is destructive and irreversible, so it stays opt-in.
AGENT_TOOLS = [ls, cat, write, edit, create_directory, execute, ask]

__all__ = [
    "AGENT_TOOLS",
    "ls",
    "cat",
    "write",
    "edit",
    "create_directory",
    "delete",
    "execute",
    "ask",
    "metadata",
    "is_secret",
    "refusal",
    "set_project_root",
    "project_root",
]

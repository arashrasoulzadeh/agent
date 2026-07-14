"""Shell `execute` tool.

Runs with its cwd pinned to the project root, and refuses commands that
name a path outside it or touch an env file. Treat this as a speed bump,
not a jail: command substitution, env vars and interpreters all offer ways
around a static check, so anything with shell access should be assumed
able to read what the process can read.
"""

import subprocess

from langchain_core.tools import tool

from tools.guard import escapes_root, mentions_secret, project_root


@tool
def execute(command: str, timeout: int = 30) -> str:
    """Run a shell command in the project folder and return its output.

    Args:
        command: The shell command to run. It must stay inside the project.
        timeout: Seconds to wait before killing the command.
    """
    if mentions_secret(command):
        return "Error: that command touches a protected env file."
    if escapes_root(command):
        return (
            f"Error: that command reaches outside the project folder "
            f"({project_root()})."
        )

    try:
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            cwd=project_root(),
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s."

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return f"Exit {result.returncode}: {output}"
    return output or "(no output)"

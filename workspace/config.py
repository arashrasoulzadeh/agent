"""Where sessions live on disk — the one thing every other module in
this package needs, without each having to duplicate the env-var lookup.
"""

import os
from pathlib import Path

SESSION_ROOT = Path(
    os.getenv("AGENT_SESSION_ROOT", str(Path.home() / ".agent-session-root"))
).expanduser()

"""Where sessions live on disk — the one thing every other module in
this package needs, without each having to duplicate the env-var lookup.
"""

import os
from pathlib import Path

SESSION_ROOT = Path(
    os.getenv("AGENT_SESSION_ROOT", str(Path.home() / ".agent-session-root"))
).expanduser()

# A room's project is always attached under this fixed name in a
# workspace session named after the room's own id (service/rooms.py's
# room_id_for_path()) — every room has exactly one project, so one fixed
# name is enough, no second lookup table needed. A workspace-level
# naming convention, not service/rooms.py-specific orchestration: both
# service/rooms.py and tool/describe.py need it, and tool/ importing
# FROM service/ would invert this project's dependency direction.
WORKSPACE_PROJECT_NAME = "project"

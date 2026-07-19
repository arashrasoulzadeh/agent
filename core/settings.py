"""Process-wide, runtime-editable configuration — settings a user can
change from the TUI's settings screen (`/settings`, see
service/ui_builder.py's settings_modal_node) instead of editing `.env`
and restarting the server.

Every setting here is also a plain env var, read by its own module
exactly as before (`llm/client.py`'s `get_llm()`, `llm/callbacks.py`'s
`_verbose()`, `tool/notion.py`'s `_headers()`) — this module doesn't
replace those reads, it just gives something a way to change
`os.environ` at runtime and have that change survive a restart.
`update_setting()` writes both `SETTINGS_FILE` (for persistence) and
`os.environ` (so the change is live immediately, no restart needed) —
whether a given setting's *consumer* actually re-reads `os.environ` on
every use (immediate) or only once per Room's construction (new-rooms
only) depends on that consumer, not on this module; see each
SettingSpec's `scope` for which is which.

`SETTINGS_FILE` lives at the repo root, alongside `rooms/` — same
seam convention `service/rooms.py`'s `ROOMS_DIR` and
`workspace/config.py`'s `SESSION_ROOT` already use (a plain
module-level `Path`, monkeypatched directly in tests, never threaded
through as a parameter). It can hold plaintext secrets exactly like
`.env` does, so it's gitignored the same way.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.json"


@dataclass(frozen=True)
class SettingSpec:
    key: str  # the env var name, e.g. "GAPGPT_MODEL"
    label: str  # human-readable label for the TUI
    secret: bool = False  # mask the value on read; never round-trip it raw
    default: str = ""
    scope: str = "new-rooms"  # "immediate" | "new-rooms" — see module docstring


SETTINGS: list[SettingSpec] = [
    SettingSpec("GAPGPT_API_KEY", "GapGPT API key", secret=True, scope="new-rooms"),
    SettingSpec(
        "GAPGPT_BASE_URL",
        "GapGPT base URL",
        default="https://api.gapgpt.app/v1",
        scope="new-rooms",
    ),
    SettingSpec("GAPGPT_MODEL", "Model", default="gpt-4o-mini", scope="new-rooms"),
    SettingSpec(
        "GAPGPT_TIMEOUT", "Request timeout (s)", default="60", scope="new-rooms"
    ),
    SettingSpec("AGENT_VERBOSE", "Verbose LLM I/O logging", scope="immediate"),
    SettingSpec("NOTION_API_KEY", "Notion API key", secret=True, scope="immediate"),
]

_BY_KEY = {spec.key: spec for spec in SETTINGS}


def get_spec(key: str) -> SettingSpec | None:
    """The SettingSpec for `key`, or None if it isn't one of SETTINGS —
    the public lookup callers outside this module use (wire/routes.py's
    /ui/event dispatch needs a setting's `secret`/`label` to decide
    whether a blank submit is a no-op) instead of reaching into _BY_KEY
    directly."""
    return _BY_KEY.get(key)


def _mask(value: str) -> str:
    return "•" * min(len(value), 8) if value else ""


def _load_persisted() -> dict[str, str]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def apply_persisted() -> None:
    """Pushes every persisted override into os.environ. Called once at
    server startup (wire/__main__.py's main()), right after
    load_dotenv() — a persisted override wins over .env from then on;
    a setting nobody has ever changed via the settings screen simply
    isn't in settings.json, so .env/the hardcoded default keeps working
    exactly as before this module existed."""
    for key, value in _load_persisted().items():
        os.environ[key] = value


def list_settings() -> list[dict]:
    """Every known setting's current value, safe to send to a client.

    Secret values are masked here, before they ever leave this
    function — the real value never round-trips back over the wire,
    matching how this project already treats credentials
    (core/guard.py's .env refusal, tool/notion.py never echoing a key
    back).
    """
    persisted = _load_persisted()
    result = []
    for spec in SETTINGS:
        raw = os.environ.get(spec.key, spec.default)
        result.append(
            {
                "key": spec.key,
                "label": spec.label,
                "secret": spec.secret,
                "scope": spec.scope,
                "value": _mask(raw) if spec.secret else raw,
                "set": spec.key in persisted or spec.key in os.environ,
            }
        )
    return result


def update_setting(key: str, value: str) -> None:
    """Persists `value` for `key` and applies it to os.environ
    immediately. Raises ValueError for a key that isn't one of
    SETTINGS — callers (wire/routes.py) turn that into a ProtocolError."""
    if key not in _BY_KEY:
        raise ValueError(f"unknown setting {key!r}")
    persisted = _load_persisted()
    persisted[key] = value
    SETTINGS_FILE.write_text(json.dumps(persisted, indent=2), encoding="utf-8")
    os.environ[key] = value

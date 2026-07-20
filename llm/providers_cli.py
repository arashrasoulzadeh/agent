"""`agent providers` — a different mode entirely, dispatched from cli.py
before any of its normal argument parsing (same reserved-word pattern
self_update.py's `agent update`/`agent uninstall` already use).

Prints which of the three llm/providers/*.py providers is active and
which of that provider's env vars are set — a static readout of
.env/settings.json, not a live API call (mirrors self_update.py's
--dry-run honesty: this reports configuration, not connectivity).

Runs client-side, so it only reflects reality when run from the same
checkout/host as the agent-server process — the same implicit
assumption `agent update`/`agent uninstall` already make about cli.py
and the server sharing a checkout.
"""

import os

from dotenv import load_dotenv

_ENV_VARS: dict[str, list[str]] = {
    "gapgpt": ["GAPGPT_API_KEY", "GAPGPT_BASE_URL", "GAPGPT_MODEL", "GAPGPT_TIMEOUT"],
    "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_TIMEOUT"],
    "ollama": ["OLLAMA_BASE_URL", "OLLAMA_MODEL"],
}


def _mask(value: str) -> str:
    return "*" * min(len(value), 8)


def run(argv: list[str]) -> int:
    load_dotenv()
    from core import settings

    settings.apply_persisted()

    active = os.getenv("LLM_PROVIDER", "gapgpt")
    for name, keys in _ENV_VARS.items():
        marker = "*" if name == active else " "
        print(f"{marker} {name}")
        for key in keys:
            value = os.environ.get(key)
            if value is None:
                print(f"    {key}: (not set)")
            elif "KEY" in key:
                print(f"    {key}: {_mask(value)}")
            else:
                print(f"    {key}: {value}")
    if active not in _ENV_VARS:
        print(
            f"\nLLM_PROVIDER={active!r} is not a known provider; choose one of "
            f"{sorted(_ENV_VARS)}"
        )
        return 1
    return 0

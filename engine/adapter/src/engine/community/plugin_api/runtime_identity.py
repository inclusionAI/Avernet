"""Read deployment-owned display metadata, never agent-authored workspace files."""

from __future__ import annotations

import os


def runtime_bot_name() -> str | None:
    """Return the platform Bot name supplied by runtime provisioning."""
    name = os.environ.get("TEAMCLAW_BOT_NAME", "").strip()
    if not name or len(name) > 255 or not name.isprintable():
        return None
    return name

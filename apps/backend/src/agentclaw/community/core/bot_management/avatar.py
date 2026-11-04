"""Display-only avatar projection shared by Bot collection contracts."""

import json
from collections.abc import Mapping
from typing import Any


def bot_avatar_url(bot: Mapping[str, Any]) -> str | None:
    """Use the saved avatar, without exposing other extension data."""
    ext = bot.get("ext")
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except (ValueError, TypeError):
            ext = None
    value = ext.get("avatar_url") if isinstance(ext, Mapping) else None
    for candidate in (value, bot.get("avatar_url")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None

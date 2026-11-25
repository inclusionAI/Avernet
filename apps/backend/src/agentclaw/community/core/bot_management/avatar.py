"""Display-only avatar projection shared by Bot collection contracts."""

import json
from collections.abc import Mapping
from typing import Any

_TEXT_MAX_BYTES = 65535
_AVATAR_MAX_BYTES = 24 * 1024


class AvatarTooLargeError(ValueError):
    """Avatar or its containing metadata exceeds the portable storage limit."""


def validate_avatar_storage(ext: Mapping[str, Any]) -> None:
    """Validate using the JSON encoding used by the Bot repository."""
    avatar = ext.get("avatar_url")
    if not isinstance(avatar, str):
        return
    try:
        avatar_size = len(avatar.encode("utf-8"))
        ext_size = len(json.dumps(ext).encode("utf-8"))
    except (TypeError, UnicodeEncodeError) as exc:
        raise AvatarTooLargeError("Avatar cannot be stored in Bot metadata") from exc
    if avatar_size > _AVATAR_MAX_BYTES or ext_size > _TEXT_MAX_BYTES:
        raise AvatarTooLargeError("Avatar does not fit in Bot metadata")


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

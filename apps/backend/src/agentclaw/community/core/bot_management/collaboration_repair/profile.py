"""Saved-record avatar projection shared by daily sync and explicit repair.

An empty string explicitly clears the avatar. An unreadable projection must
not become an empty string: best-effort outbound callers omit it instead.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from agentclaw.community.log import get_logger

logger = get_logger()

_AVATAR_URL_KEY = "avatar_url"


def avatar_from_record(bot: Mapping[str, object]) -> str:
    """Read the top-level avatar, then legacy ext, preserving explicit empty strings.

    Missing/None top-level values fall back to ext; absence in both returns "".
    Malformed ext or an explicitly present non-string avatar raises ValueError.
    """
    top_level = bot.get(_AVATAR_URL_KEY)
    if top_level is not None:
        return _string_value_or_invalid(top_level, source="bot record top level")

    ext = _structured_ext(bot)
    if _AVATAR_URL_KEY in ext:
        return _string_value_or_invalid(ext[_AVATAR_URL_KEY], source="bot record ext")
    return ""


def outbound_avatar_from_record(
    bot: Mapping[str, object], *, log_context: str
) -> str | None:
    """Return the saved avatar, or None on invalid data so callers omit the field.

    Unlike "", None never clears the remote avatar. Projection errors are logged
    without interrupting the enclosing best-effort registration/sync.
    """
    try:
        return avatar_from_record(bot)
    except ValueError as error:
        logger.warning(
            "%s: avatar projection unreadable, omitting avatar_url "
            "(remote keeps its stored avatar): %s",
            log_context,
            error,
        )
        return None


def _string_value_or_invalid(value: object, *, source: str) -> str:
    """Require a string; invalid values must not silently clear the remote avatar."""
    if isinstance(value, str):
        return value
    raise ValueError(
        f"Bot avatar_url in {source} must be a string, got {type(value).__name__}"
    )


def _structured_ext(bot: Mapping[str, object]) -> Mapping[str, object]:
    """Parse mapping/JSON ext; reject malformed or non-object data.

    Unlike the legacy update merge, repair must not turn unreadable ext into {}.
    """
    raw = bot.get("ext")
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"Bot ext is corrupt JSON: {error}") from error
        if isinstance(parsed, Mapping):
            return parsed
        raise ValueError(
            f"Bot ext JSON must decode to an object, got {type(parsed).__name__}"
        )
    raise ValueError(
        f"Bot ext must be an object or a JSON object string, got {type(raw).__name__}"
    )

"""Shared schema validation for the Skills Pool rollout configuration."""

from __future__ import annotations

from typing import Any

ENGINE_PROMOTION_ORDER = ("openclaw", "claude_code", "aicoding", "hermes")
CONTROL_KEYS = ("negative_controls", "teclaw_controls")
_REQUIRED_KEYS = frozenset({"enable_all", "promoted_engines", "whitelist"})
_ALLOWED_KEYS = _REQUIRED_KEYS | frozenset(CONTROL_KEYS)
_ENTRY_KEYS = frozenset({"owner_id", "bot_id", "batch_id"})


def _valid_identity(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (str, int))
        and str(value).strip() not in {"", "*"}
    )


def _valid_entries(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for entry in value:
        if not isinstance(entry, dict) or not set(entry).issubset(_ENTRY_KEYS):
            return False
        if not _valid_identity(entry.get("owner_id")):
            return False
        if not _valid_identity(entry.get("bot_id")):
            return False
        batch_id = entry.get("batch_id")
        if batch_id is not None and not _valid_identity(batch_id):
            return False
    return True


def is_valid_rollout_config_value(value: Any) -> bool:
    """Accept only the exact, fail-closed rollout configuration schema."""

    if not isinstance(value, dict):
        return False
    keys = set(value)
    if not _REQUIRED_KEYS.issubset(keys) or not keys.issubset(_ALLOWED_KEYS):
        return False
    if value.get("enable_all") is not False:
        return False

    engines = value.get("promoted_engines")
    if not isinstance(engines, list):
        return False
    promoted_engines = tuple(engines)
    if promoted_engines != ENGINE_PROMOTION_ORDER[: len(promoted_engines)]:
        return False

    return all(
        _valid_entries(value.get(key, []))
        for key in ("whitelist", *CONTROL_KEYS)
    )

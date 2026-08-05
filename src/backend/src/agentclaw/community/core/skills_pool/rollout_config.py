"""Shared schema validation for the Skills Pool rollout configuration."""

from __future__ import annotations

from typing import Any

ENGINE_PROMOTION_ORDER = ("openclaw", "claude_code", "aicoding", "hermes")
CONTROL_KEYS = ("negative_controls", "teclaw_controls")
_REQUIRED_KEYS = frozenset({"enable_all", "promoted_engines", "whitelist"})
_ALLOWED_KEYS = _REQUIRED_KEYS | frozenset(
    (*CONTROL_KEYS, "full_rollout_engines", "full_rollout_owners")
)
_ENTRY_KEYS = frozenset({"owner_id", "bot_id", "batch_id"})
_OWNER_ENTRY_KEYS = frozenset({"owner_id", "engine"})


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


def _valid_owner_entries(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    identities: set[tuple[str, str]] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _OWNER_ENTRY_KEYS:
            return False
        if not _valid_identity(entry.get("owner_id")):
            return False
        engine = entry.get("engine")
        if not isinstance(engine, str) or engine not in ENGINE_PROMOTION_ORDER:
            return False
        identity = (str(entry["owner_id"]), engine)
        if identity in identities:
            return False
        identities.add(identity)
    return True


def is_valid_rollout_config_value(value: Any) -> bool:
    """Accept only the exact, fail-closed rollout configuration schema."""

    if not isinstance(value, dict):
        return False
    keys = set(value)
    if not _REQUIRED_KEYS.issubset(keys) or not keys.issubset(_ALLOWED_KEYS):
        return False
    if not isinstance(value.get("enable_all"), bool):
        return False

    engines = value.get("promoted_engines")
    if not isinstance(engines, list):
        return False
    promoted_engines = tuple(engines)
    if (
        len(set(promoted_engines)) != len(promoted_engines)
        or any(engine not in ENGINE_PROMOTION_ORDER for engine in promoted_engines)
        or promoted_engines
        != tuple(
            engine
            for engine in ENGINE_PROMOTION_ORDER
            if engine in promoted_engines
        )
    ):
        return False
    full_rollout_engines = value.get("full_rollout_engines", [])
    if (
        not isinstance(full_rollout_engines, list)
        or any(not isinstance(engine, str) for engine in full_rollout_engines)
        or len(set(full_rollout_engines)) != len(full_rollout_engines)
        or any(engine not in promoted_engines for engine in full_rollout_engines)
    ):
        return False
    if not _valid_owner_entries(value.get("full_rollout_owners", [])):
        return False
    if any(
        entry["engine"] not in promoted_engines
        for entry in value.get("full_rollout_owners", [])
    ):
        return False

    return all(
        _valid_entries(value.get(key, [])) for key in ("whitelist", *CONTROL_KEYS)
    )


def normalize_rollout_config_value(value: Any) -> dict[str, object] | None:
    """Return the canonical persisted shape for a valid rollout config.

    Older records predate optional control fields.  Their missing keys are
    semantically equivalent to empty lists, but the canonical shape always
    writes every field so the first successful operator mutation upgrades the
    record atomically.
    """

    if not is_valid_rollout_config_value(value):
        return None
    assert isinstance(value, dict)

    def entries(key: str) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for raw in value.get(key, []):
            assert isinstance(raw, dict)
            entry = {
                "owner_id": str(raw["owner_id"]),
                "bot_id": str(raw["bot_id"]),
            }
            if raw.get("batch_id") is not None:
                entry["batch_id"] = str(raw["batch_id"])
            normalized.append(entry)
        return normalized

    owner_entries = [
        {
            "owner_id": str(raw["owner_id"]),
            "engine": str(raw["engine"]),
        }
        for raw in value.get("full_rollout_owners", [])
    ]

    return {
        "enable_all": value["enable_all"],
        "full_rollout_engines": list(value.get("full_rollout_engines", [])),
        "full_rollout_owners": owner_entries,
        "promoted_engines": list(value["promoted_engines"]),
        "whitelist": entries("whitelist"),
        "negative_controls": entries(CONTROL_KEYS[0]),
        "teclaw_controls": entries(CONTROL_KEYS[1]),
    }

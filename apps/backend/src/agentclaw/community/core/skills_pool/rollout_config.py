"""Validation and normalization for Skills Pool admission policies."""

from __future__ import annotations

from typing import Any

ENGINE_PROMOTION_ORDER = ("openclaw", "claude_code", "aicoding", "hermes")
ROLLOUT_SCHEMA_VERSION = 2

# v1 is read-only compatibility for already persisted rollout configuration.
CONTROL_KEYS = ("negative_controls", "teclaw_controls")
_V1_REQUIRED_KEYS = frozenset({"enable_all", "promoted_engines", "whitelist"})
_V1_ALLOWED_KEYS = _V1_REQUIRED_KEYS | frozenset(
    (*CONTROL_KEYS, "full_rollout_engines", "full_rollout_owners")
)
_V1_ENTRY_KEYS = frozenset({"owner_id", "bot_id", "batch_id"})

_V2_KEYS = frozenset(
    {
        "schema_version",
        "engine_admission",
        "bot_allowlist",
        "owner_rollouts",
        "environment_rollouts",
        "bot_exclusions",
    }
)
_BOT_RULE_KEYS = frozenset({"owner_id", "bot_id", "engine"})
_OWNER_RULE_KEYS = frozenset({"owner_id", "engine"})


def _valid_identity(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (str, int))
        and str(value).strip() not in {"", "*"}
    )


def _valid_engine(value: Any) -> bool:
    return isinstance(value, str) and value in ENGINE_PROMOTION_ORDER


def _valid_v1_entries(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for entry in value:
        if not isinstance(entry, dict) or not set(entry).issubset(_V1_ENTRY_KEYS):
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
        if not isinstance(entry, dict) or set(entry) != _OWNER_RULE_KEYS:
            return False
        if not _valid_identity(entry.get("owner_id")):
            return False
        if not _valid_engine(entry.get("engine")):
            return False
        identity = (str(entry["owner_id"]), str(entry["engine"]))
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _valid_bot_rules(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    identities: set[tuple[str, str, str]] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _BOT_RULE_KEYS:
            return False
        if not _valid_identity(entry.get("owner_id")):
            return False
        if not _valid_identity(entry.get("bot_id")):
            return False
        if not _valid_engine(entry.get("engine")):
            return False
        identity = (
            str(entry["owner_id"]),
            str(entry["bot_id"]),
            str(entry["engine"]),
        )
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _valid_engine_list(value: Any) -> bool:
    if not isinstance(value, list) or not all(
        _valid_engine(engine) for engine in value
    ):
        return False
    return len(set(value)) == len(value)


def _valid_v1(value: dict[str, object]) -> bool:
    keys = set(value)
    if not _V1_REQUIRED_KEYS.issubset(keys) or not keys.issubset(_V1_ALLOWED_KEYS):
        return False
    if not isinstance(value.get("enable_all"), bool):
        return False
    engines = value.get("promoted_engines")
    if not _valid_engine_list(engines):
        return False
    assert isinstance(engines, list)
    promoted = tuple(engines)
    if promoted != tuple(engine for engine in ENGINE_PROMOTION_ORDER if engine in promoted):
        return False
    full_engines = value.get("full_rollout_engines", [])
    if not _valid_engine_list(full_engines):
        return False
    assert isinstance(full_engines, list)
    if any(engine not in promoted for engine in full_engines):
        return False
    owners = value.get("full_rollout_owners", [])
    if not _valid_owner_entries(owners):
        return False
    assert isinstance(owners, list)
    if any(entry["engine"] not in promoted for entry in owners):
        return False
    return all(
        _valid_v1_entries(value.get(key, []))
        for key in ("whitelist", *CONTROL_KEYS)
    )


def _valid_v2(value: dict[str, object]) -> bool:
    if set(value) != _V2_KEYS or value.get("schema_version") != ROLLOUT_SCHEMA_VERSION:
        return False
    admission = value.get("engine_admission")
    if (
        not isinstance(admission, dict)
        or any(not _valid_engine(engine) for engine in admission)
        or any(not isinstance(enabled, bool) for enabled in admission.values())
    ):
        return False
    return (
        _valid_bot_rules(value.get("bot_allowlist"))
        and _valid_owner_entries(value.get("owner_rollouts"))
        and _valid_engine_list(value.get("environment_rollouts"))
        and _valid_bot_rules(value.get("bot_exclusions"))
    )


def is_valid_rollout_config_value(value: Any) -> bool:
    """Accept the legacy read schema and the canonical v2 write schema."""

    if not isinstance(value, dict):
        return False
    if "schema_version" in value:
        return _valid_v2(value)
    return _valid_v1(value)


def rollout_schema_version(value: object) -> int | None:
    if not isinstance(value, dict) or not is_valid_rollout_config_value(value):
        return None
    return ROLLOUT_SCHEMA_VERSION if "schema_version" in value else 1


def normalize_rollout_config_value(value: Any) -> dict[str, object] | None:
    """Return the canonical shape for CAS comparisons."""

    version = rollout_schema_version(value)
    if version is None:
        return None
    assert isinstance(value, dict)
    if version == ROLLOUT_SCHEMA_VERSION:
        admission = value["engine_admission"]
        assert isinstance(admission, dict)

        def bot_rules(key: str) -> list[dict[str, str]]:
            raw = value[key]
            assert isinstance(raw, list)
            return [
                {
                    "owner_id": str(entry["owner_id"]),
                    "bot_id": str(entry["bot_id"]),
                    "engine": str(entry["engine"]),
                }
                for entry in raw
            ]

        owners = value["owner_rollouts"]
        assert isinstance(owners, list)
        environments = value["environment_rollouts"]
        assert isinstance(environments, list)
        return {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "engine_admission": {
                engine: bool(admission[engine])
                for engine in ENGINE_PROMOTION_ORDER
                if engine in admission
            },
            "bot_allowlist": bot_rules("bot_allowlist"),
            "owner_rollouts": [
                {"owner_id": str(entry["owner_id"]), "engine": str(entry["engine"])}
                for entry in owners
            ],
            "environment_rollouts": [
                engine for engine in ENGINE_PROMOTION_ORDER if engine in environments
            ],
            "bot_exclusions": bot_rules("bot_exclusions"),
        }

    def legacy_entries(key: str) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        raw_entries = value.get(key, [])
        assert isinstance(raw_entries, list)
        for raw in raw_entries:
            assert isinstance(raw, dict)
            entry = {
                "owner_id": str(raw["owner_id"]),
                "bot_id": str(raw["bot_id"]),
            }
            if raw.get("batch_id") is not None:
                entry["batch_id"] = str(raw["batch_id"])
            normalized.append(entry)
        return normalized

    owners = value.get("full_rollout_owners", [])
    assert isinstance(owners, list)
    return {
        "enable_all": value["enable_all"],
        "full_rollout_engines": list(value.get("full_rollout_engines", [])),
        "full_rollout_owners": [
            {"owner_id": str(raw["owner_id"]), "engine": str(raw["engine"])}
            for raw in owners
        ],
        "promoted_engines": list(value["promoted_engines"]),
        "whitelist": legacy_entries("whitelist"),
        "negative_controls": legacy_entries(CONTROL_KEYS[0]),
        "teclaw_controls": legacy_entries(CONTROL_KEYS[1]),
    }

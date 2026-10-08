"""Deterministic task-level A/B experiment assignment.

The assignment is resolved once, before a task is persisted and its graph is
created.  Runtime code consumes only the resulting frozen execution config and
never re-buckets an in-flight task.
"""
from __future__ import annotations

import hashlib
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError

_AB_TEST_FIELDS = frozenset({"enabled", "experiment_id", "unit", "variants"})
_VARIANT_FIELDS = frozenset(
    {"name", "weight", "runtime_profile", "orchestration_mode"}
)
_PROFILE_FIELDS = frozenset(
    {
        "planner_strategy",
        "dispatcher_strategy",
        "search_strategy",
        "runner_strategy",
        "allowed_run_modes",
    }
)
_RUN_MODES = frozenset({"single_bot", "coop_group", "bbs"})
_ORCHESTRATION_MODES = frozenset({"centralized", "relay"})
_ASSIGNMENT_UNITS = frozenset({"task", "owner_user", "owner_bot"})


def apply_ab_test(
    execution_config: dict[str, Any],
    *,
    task_id: str,
    owner_user_id: str,
    owner_bot_id: str,
) -> dict[str, Any]:
    """Return a copied config with one experiment variant applied and audited.

    Variant weights are relative and need not sum to 100.  The stable bucket is
    derived with SHA-256 rather than Python's process-randomized ``hash()``.
    Only governed runtime-profile fields and the orchestration mode may vary.
    """
    resolved = dict(execution_config)
    raw = resolved.get("ab_test")
    if raw is None:
        resolved.pop("ab_assignment", None)
        return resolved
    if not isinstance(raw, dict):
        raise TaskStateError("execution_config.ab_test must be an object")
    _reject_unknown_fields(raw, _AB_TEST_FIELDS, "execution_config.ab_test")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TaskStateError("execution_config.ab_test.enabled must be a boolean")
    if not enabled:
        resolved.pop("ab_assignment", None)
        return resolved

    experiment_id = _required_string(
        raw.get("experiment_id"), "execution_config.ab_test.experiment_id"
    )
    unit = str(raw.get("unit") or "task").strip()
    if unit not in _ASSIGNMENT_UNITS:
        raise TaskStateError(
            "execution_config.ab_test.unit must be task, owner_user, or owner_bot"
        )
    unit_value = {
        "task": task_id,
        "owner_user": owner_user_id,
        "owner_bot": owner_bot_id,
    }[unit]
    if not str(unit_value).strip():
        raise TaskStateError(f"execution_config.ab_test unit {unit!r} is empty")

    raw_variants = raw.get("variants")
    if not isinstance(raw_variants, list) or len(raw_variants) < 2:
        raise TaskStateError(
            "execution_config.ab_test.variants must contain at least two variants"
        )
    variants = [_parse_variant(item, index) for index, item in enumerate(raw_variants)]
    names = [variant["name"] for variant in variants]
    if len(set(names)) != len(names):
        raise TaskStateError("execution_config.ab_test variant names must be unique")

    total_weight = sum(variant["weight"] for variant in variants)
    digest = hashlib.sha256(
        f"{experiment_id}\0{unit_value}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:16], 16) % total_weight
    selected = _select_variant(variants, bucket)

    profile_override = selected.get("runtime_profile")
    if profile_override is not None:
        base_profile = resolved.get("runtime_profile")
        merged_profile = dict(base_profile) if isinstance(base_profile, dict) else {}
        merged_profile.update(profile_override)
        resolved["runtime_profile"] = merged_profile
    orchestration_mode = selected.get("orchestration_mode")
    if orchestration_mode is not None:
        resolved["orchestration_mode"] = orchestration_mode

    resolved["ab_assignment"] = {
        "experiment_id": experiment_id,
        "variant": selected["name"],
        "unit": unit,
        "bucket": bucket,
        "total_weight": total_weight,
        "assignment_key_digest": digest,
    }
    return resolved


def _parse_variant(raw: Any, index: int) -> dict[str, Any]:
    path = f"execution_config.ab_test.variants[{index}]"
    if not isinstance(raw, dict):
        raise TaskStateError(f"{path} must be an object")
    _reject_unknown_fields(raw, _VARIANT_FIELDS, path)
    name = _required_string(raw.get("name"), f"{path}.name")
    weight = raw.get("weight")
    if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
        raise TaskStateError(f"{path}.weight must be a positive integer")

    variant: dict[str, Any] = {"name": name, "weight": weight}
    if "runtime_profile" in raw:
        variant["runtime_profile"] = _parse_runtime_profile(
            raw["runtime_profile"], f"{path}.runtime_profile"
        )
    if "orchestration_mode" in raw:
        mode = str(raw["orchestration_mode"] or "").strip().lower()
        if mode not in _ORCHESTRATION_MODES:
            raise TaskStateError(
                f"{path}.orchestration_mode must be centralized or relay"
            )
        variant["orchestration_mode"] = mode
    return variant


def _parse_runtime_profile(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TaskStateError(f"{path} must be an object")
    _reject_unknown_fields(raw, _PROFILE_FIELDS, path)
    profile: dict[str, Any] = {}
    for field in (
        "planner_strategy",
        "dispatcher_strategy",
        "search_strategy",
        "runner_strategy",
    ):
        if field in raw:
            profile[field] = _required_string(raw[field], f"{path}.{field}")
    if "allowed_run_modes" in raw:
        modes = raw["allowed_run_modes"]
        if not isinstance(modes, list) or not modes:
            raise TaskStateError(f"{path}.allowed_run_modes must be a non-empty list")
        normalized = [str(mode).strip() for mode in modes]
        if any(mode not in _RUN_MODES for mode in normalized):
            raise TaskStateError(
                f"{path}.allowed_run_modes only supports single_bot, coop_group, and bbs"
            )
        profile["allowed_run_modes"] = list(dict.fromkeys(normalized))
    return profile


def _select_variant(variants: list[dict[str, Any]], bucket: int) -> dict[str, Any]:
    upper_bound = 0
    for variant in variants[:-1]:
        upper_bound += variant["weight"]
        if bucket < upper_bound:
            return variant
    return variants[-1]


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskStateError(f"{path} must be a non-empty string")
    return value.strip()


def _reject_unknown_fields(
    value: dict[str, Any], allowed: frozenset[str], path: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TaskStateError(f"{path} contains unsupported fields: {', '.join(unknown)}")

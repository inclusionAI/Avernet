"""Credential-name-only profile admission for Group Context density."""
from __future__ import annotations

import re
from typing import Any

from ..run_state import RunStateError
from .contract import parsed_obligations

PROFILE_CONTRACT = "group-context-density-profile-v1"
_ALLOWED_ENV_NAMES = {"BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"}
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")


def _env_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ENV_NAME.fullmatch(value) is None or value not in _ALLOWED_ENV_NAMES:
        raise RunStateError(f"{field} must name an admitted credential environment variable")
    return value


def validate_density_profile(profile: object) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise RunStateError("Group Context density profile must be an object")
    if set(profile) != {"schema_version", "contract", "environment", "entrypoint", "auth", "operation_bindings"}:
        raise RunStateError("Group Context density profile has an unexpected key")
    if profile.get("schema_version") != 1 or profile.get("contract") != PROFILE_CONTRACT:
        raise RunStateError("Group Context density profile schema contract mismatch")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    auth = profile.get("auth")
    bindings = profile.get("operation_bindings")
    if not isinstance(environment, dict) or not isinstance(entrypoint, dict) or not isinstance(auth, dict) or not isinstance(bindings, dict):
        raise RunStateError("Group Context density profile lacks required sections")
    if set(environment) != {"tier", "id", "lifecycle", "deployment"}:
        raise RunStateError("Group Context density environment keys changed")
    tier = environment.get("tier")
    if tier not in {"local", "preprod"}:
        raise RunStateError("Group Context density tier must be local or preprod")
    if not isinstance(environment.get("id"), str) or not environment["id"]:
        raise RunStateError("Group Context density environment id is required")
    if set(entrypoint) != {"kind"}:
        raise RunStateError("Group Context density entrypoint keys changed")
    expected_entrypoint = "singlebox" if tier == "local" else "bcs-direct"
    expected_lifecycle = "local-provision" if tier == "local" else "remote-attach"
    expected_deployment = "singlebox" if tier == "local" else "preprod"
    if (entrypoint.get("kind"), environment.get("lifecycle"), environment.get("deployment")) != (
            expected_entrypoint, expected_lifecycle, expected_deployment):
        raise RunStateError("Group Context density environment posture is not admitted")
    if set(auth) != {"header_env"} or not isinstance(auth["header_env"], dict):
        raise RunStateError("Group Context density auth must carry header environment names")
    if set(auth["header_env"]) != {"Cookie", "Authorization"}:
        raise RunStateError("Group Context density auth headers changed")
    _env_name(auth["header_env"]["Cookie"], "auth.header_env.Cookie")
    _env_name(auth["header_env"]["Authorization"], "auth.header_env.Authorization")
    operations = {row.operation for row in parsed_obligations()}
    if set(bindings) != operations:
        raise RunStateError("Group Context density operation bindings changed")
    for operation, binding in bindings.items():
        if (not isinstance(binding, dict) or set(binding) != {"method", "path"}
                or not isinstance(binding["method"], str) or not isinstance(binding["path"], str)):
            raise RunStateError(f"invalid Group Context density binding: {operation}")
    if profile.get("secrets") is not None:
        raise RunStateError("Group Context density profile must not contain credential values")
    return profile

"""Credential-name-only admission for the two density tiers."""
from __future__ import annotations

import re
from typing import Any

from ..run_state import RunStateError
from .contract import parsed_obligations

PROFILE_CONTRACT = "structured-collaboration-density-profile-v1"
_ALLOWED_ENV_NAMES = {"BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"}
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")
_OPERATIONS = frozenset(row.operation for row in parsed_obligations())


def _env_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ENV_NAME.fullmatch(value) or value not in _ALLOWED_ENV_NAMES:
        raise RunStateError(f"{field} must name an admitted credential environment variable")
    return value


def validate_density_profile(profile: object) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise RunStateError("density profile must be an object")
    if profile.get("schema_version") != 1 or profile.get("contract") != PROFILE_CONTRACT:
        raise RunStateError("density profile schema contract mismatch")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    auth = profile.get("auth")
    bindings = profile.get("operation_bindings")
    if not isinstance(environment, dict) or not isinstance(entrypoint, dict) or not isinstance(auth, dict) or not isinstance(bindings, dict):
        raise RunStateError("density profile lacks environment, entrypoint, auth, or bindings")
    if profile.get("secrets") not in (None, {}):
        raise RunStateError("density profile must not contain credential values")
    tier = environment.get("tier")
    if tier == "local":
        if (entrypoint.get("kind"), environment.get("lifecycle"), environment.get("deployment")) != ("singlebox", "local-provision", "singlebox"):
            raise RunStateError("local density profile must be owned-local singlebox")
    elif tier == "preprod":
        if (entrypoint.get("kind"), environment.get("lifecycle"), environment.get("deployment")) != ("bcs-direct", "remote-attach", "preprod"):
            raise RunStateError("preprod density profile must be an unexecuted bcs-direct attachment")
    else:
        raise RunStateError("density profile tier must be local or preprod")
    headers = auth.get("header_env")
    if not isinstance(headers, dict) or set(headers) != {"Cookie", "Authorization"}:
        raise RunStateError("density profile must bind Cookie and Authorization by name")
    _env_name(headers["Cookie"], "auth.header_env.Cookie")
    _env_name(headers["Authorization"], "auth.header_env.Authorization")
    if set(bindings) != _OPERATIONS:
        raise RunStateError("density profile operation bindings are not the reviewed set")
    for name, binding in bindings.items():
        if not isinstance(binding, dict) or not isinstance(binding.get("method"), str) or not isinstance(binding.get("path"), str):
            raise RunStateError(f"invalid operation binding: {name}")
    return profile

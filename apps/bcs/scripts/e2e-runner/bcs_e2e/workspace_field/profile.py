"""Private dual-environment profile admission for the Workspace field slice.

The Workspace field family replays the frozen 63-atom density oracle through
an owned-local offline executor, so an admitted profile only carries the
environment identity, the owned-local executor shape, the two entrypoint
faces (owned-local Singlebox product CLI locally, Gateway product CLI on
preprod) and credential-free auth material.  The accepted key set is bounded
at every level: an unknown or missing key fails admission instead of being
ignored.

Auth material is restricted to environment-variable NAME references, and only
the two reviewed names are admitted: ``BCS_E2E_DRIVER_TOKEN`` (owned driver
Bot token) and ``BCS_E2E_HUMAN_COOKIE`` (Human session Cookie).  No accepted
shape can carry an inline credential value, and no credential value is ever
resolved, loaded or persisted by this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..profile import ConfigError, object_keys, unique_keys
from .group_workspace_field_obligations import workspace_field_profile

PROFILE_CONTRACT = "dual-workspace-field-profile-v1"
LOCAL_TIER = "local"
PREPROD_TIER = "preprod"
TIERS = frozenset({LOCAL_TIER, PREPROD_TIER})

DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
ALLOWED_ENV_REFERENCES = frozenset({DRIVER_TOKEN_ENV, HUMAN_COOKIE_ENV})

# Bounded key sets: the slice needs no observer, policy, route-binding or
# binary blocks, so anything beyond this set is rejected instead of ignored.
PROFILE_KEYS = frozenset({
    "contract", "environment", "executor", "entrypoint", "auth", "remote_validation"})
ENVIRONMENT_KEYS = frozenset({"tier", "id"})
EXECUTOR_KEYS = frozenset({"kind", "transport"})
ENTRYPOINT_KEYS = frozenset({"kind", "api_face", "cli_face"})
LOCAL_AUTH_KEYS = frozenset({"kind", "token_env"})
PREPROD_AUTH_KEYS = frozenset({"profiles"})
HUMAN_COOKIE_AUTH_KEYS = frozenset({"kind", "cookie_env"})
OWNED_BOT_AUTH_KEYS = frozenset({"kind", "token_env"})

OWNED_LOCAL_EXECUTOR = {"kind": "owned-local", "transport": "fake-offline"}
LOCAL_ENTRYPOINT = {"kind": "singlebox", "api_face": "legacy", "cli_face": "product"}
PREPROD_ENTRYPOINT = {"kind": "gateway", "api_face": "legacy", "cli_face": "product"}
LOCAL_AUTH = {"kind": "owned_local_bot_token", "token_env": DRIVER_TOKEN_ENV}
LOCAL_PROFILE_ID = "workspace-field-local"
PREPROD_PROFILE_ID = "workspace-field-preprod"

_ADMITTED_AUTH_KINDS = frozenset({"human-cookie", "owned_bot"})


def default_workspace_field_profile(*, tier: str) -> dict:
    """Return the canonical admitted template for one tier."""
    if tier not in TIERS:
        raise ConfigError("workspace field profile tier must be local or preprod")
    return admit_workspace_field_profile(workspace_field_profile(tier=tier))


def _env_reference(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in ALLOWED_ENV_REFERENCES:
        raise ConfigError(
            where + ": auth may only reference BCS_E2E_DRIVER_TOKEN or BCS_E2E_HUMAN_COOKIE")
    return value


def _admit_preprod_auth(profile: dict) -> dict:
    auth = object_keys(profile["auth"], set(PREPROD_AUTH_KEYS), "workspace field preprod auth")
    entries = auth["profiles"]
    if not isinstance(entries, list) or len(entries) != 2:
        raise ConfigError(
            "workspace field preprod auth must declare exactly the Human Cookie and driver Bot profiles")
    admitted: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("workspace field preprod auth profiles must be objects")
        kind = entry.get("kind")
        if kind == "human-cookie":
            shaped = object_keys(entry, set(HUMAN_COOKIE_AUTH_KEYS), "workspace field human-cookie auth")
            _env_reference(shaped["cookie_env"], "workspace field human-cookie auth.cookie_env")
            if shaped["cookie_env"] != HUMAN_COOKIE_ENV:
                raise ConfigError("workspace field human-cookie auth must reference BCS_E2E_HUMAN_COOKIE")
        elif kind == "owned_bot":
            shaped = object_keys(entry, set(OWNED_BOT_AUTH_KEYS), "workspace field owned_bot auth")
            _env_reference(shaped["token_env"], "workspace field owned_bot auth.token_env")
            if shaped["token_env"] != DRIVER_TOKEN_ENV:
                raise ConfigError("workspace field owned_bot auth must reference BCS_E2E_DRIVER_TOKEN")
        else:
            raise ConfigError("workspace field preprod auth profile kind is not admitted")
        if kind in admitted:
            raise ConfigError("workspace field preprod auth profiles must not repeat a kind")
        admitted[kind] = shaped
    if set(admitted) != _ADMITTED_AUTH_KINDS:
        raise ConfigError(
            "workspace field preprod auth must keep both the Human Cookie and driver Bot profiles")
    return {"profiles": [admitted["human-cookie"], admitted["owned_bot"]]}


def admit_workspace_field_profile(raw: Any) -> dict:
    """Admit one Workspace field dual-environment profile, tier-strictly."""
    profile = object_keys(raw, set(PROFILE_KEYS), "workspace field profile")
    if profile["contract"] != PROFILE_CONTRACT:
        raise ConfigError("workspace field profile: unsupported contract")
    environment = object_keys(profile["environment"], set(ENVIRONMENT_KEYS), "workspace field environment")
    tier = environment["tier"]
    if tier not in TIERS:
        raise ConfigError("workspace field environment.tier: only local and preprod are admitted")
    expected_id = LOCAL_PROFILE_ID if tier == LOCAL_TIER else PREPROD_PROFILE_ID
    if environment["id"] != expected_id:
        raise ConfigError("workspace field environment.id does not match its tier")
    executor = object_keys(profile["executor"], set(EXECUTOR_KEYS), "workspace field executor")
    if executor != dict(OWNED_LOCAL_EXECUTOR):
        raise ConfigError("workspace field executor must stay owned-local over the offline transport")
    entrypoint = object_keys(profile["entrypoint"], set(ENTRYPOINT_KEYS), "workspace field entrypoint")
    expected_entrypoint = dict(LOCAL_ENTRYPOINT) if tier == LOCAL_TIER else dict(PREPROD_ENTRYPOINT)
    if entrypoint != expected_entrypoint:
        raise ConfigError("workspace field entrypoint shape drifted from its tier admission")
    if tier == LOCAL_TIER:
        auth = object_keys(profile["auth"], set(LOCAL_AUTH_KEYS), "workspace field local auth")
        _env_reference(auth["token_env"], "workspace field local auth.token_env")
        if auth != dict(LOCAL_AUTH):
            raise ConfigError("workspace field local auth must reference only BCS_E2E_DRIVER_TOKEN")
    else:
        auth = _admit_preprod_auth(profile)
    if profile["remote_validation"] != "NOT_RUN":
        raise ConfigError("workspace field profile may not claim remote validation")
    # Detach so later mutation of the admitted profile cannot alias caller state.
    return json.loads(json.dumps({**profile, "auth": auth}))


def load_workspace_field_profile(path: Path) -> dict:
    """Load and admit a Workspace field profile from a regular non-symlink file."""
    value = Path(path)
    if value.is_symlink() or not value.is_file():
        raise ConfigError("workspace field profile must be a regular file")
    try:
        raw = json.loads(value.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except UnicodeError:
        raise ConfigError("workspace field profile must be UTF-8 JSON") from None
    except json.JSONDecodeError:
        raise ConfigError("workspace field profile must be UTF-8 JSON") from None
    except OSError:
        raise ConfigError("workspace field profile cannot be read") from None
    return admit_workspace_field_profile(raw)


def credential_refs(profile: dict) -> list[str]:
    """Return the sorted environment-variable NAME references the tier admits."""
    auth = profile["auth"]
    if "profiles" in auth:
        refs = set()
        for entry in auth["profiles"]:
            refs.add(entry.get("cookie_env") or entry.get("token_env"))
        return sorted(refs)
    return sorted({auth["token_env"]})

"""Strict profile admission for the dual Retired service policy density slice.

The density projection is a credential-free offline projection, but it still
runs on admitted dual-environment profiles, so the admission contract keeps
the delivery family's reviewed shapes without importing any live-transport
machinery:

* ``schema_version`` must be exactly 1 and the top-level key set is exact -
  an unknown key (including an inline ``secrets`` blob) fails closed;
* both admitted tiers are shaped: owned-local Singlebox rides an owned HTTP
  loopback, preprod rides the admitted bcs-direct station origin;
* the preprod leg admits only the bcs-direct entrypoint kind with the legacy
  product face (the service policy story's administration surface) on both
  tiers;
* ``auth`` carries environment variable NAME references only - uppercase
  bounded identifiers, never values - with the tier-app reviewed auth family
  (owned-local Bot tokens locally, the Human Cookie plus the owned dedicated
  driver Bot token reference on preprod); the retired-policy journey is a
  single-Bot journey, so no member actor is admitted;
* ``route_bindings`` binds exactly the nineteen reviewed station operations
  with the frozen method/path and the legacy API face; the projected derived
  teardown obligation is disclosed by the plan, never bound as a station.

No credential value is ever resolved or echoed: validation is pure shape
checking over an already-loaded dictionary.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..profile import unique_keys
from ..run_state import RunStateError
from .contract import DENSITY_OPERATION_ROWS

PROFILE_CONTRACT = "service-policy-density-profile-v1"

_ENV_REF = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")

_PROFILE_KEYS = frozenset({
    "schema_version", "contract", "environment", "entrypoint", "human",
    "actors", "auth", "route_bindings",
})
_ENVIRONMENT_KEYS = frozenset({"tier", "id", "expected_bcs_env"})
_ENTRYPOINT_KEYS = frozenset({"kind", "origin", "api_face", "manifest_face"})
_HUMAN_KEYS = frozenset({"user_id"})
_ACTOR_KEYS = frozenset({"bot_id", "owner_id", "dedicated"})
_AUTH_LOCAL_KEYS = frozenset({"kind", "driver_token_env"})
_AUTH_PREPROD_KEYS = frozenset({
    "kind", "driver_token_env", "cookie_env"})
_BINDING_KEYS = frozenset({"method", "path", "api_face"})


def _exact(value: object, keys: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RunStateError(f"{where}: exact key set required ({sorted(keys)})")
    return value


def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str) or _ENV_REF.fullmatch(value) is None):
        raise RunStateError(f"{where}: must be an uppercase environment variable name reference")
    return value


def _identifier(value: object, where: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise RunStateError(f"{where}: must be a bounded printable identifier")
    return value


def _origin(value: object, where: str) -> str:
    if (not isinstance(value, str) or not value.startswith(("http://", "https://"))
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise RunStateError(f"{where}: must be an ASCII http(s) origin without whitespace or controls")
    return value


def validate_service_policy_density_profile(raw: object) -> dict[str, Any]:
    """Admit one Retired service policy density profile without resolving anything."""
    if not isinstance(raw, dict):
        raise RunStateError("service policy density profile must be an object")
    profile = _exact(raw, _PROFILE_KEYS, "service policy density profile")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise RunStateError("service policy density profile: schema_version must be the integer 1")
    if profile["contract"] != PROFILE_CONTRACT:
        raise RunStateError("service policy density profile: unsupported contract")
    environment = _exact(profile["environment"], _ENVIRONMENT_KEYS,
                         "service policy density environment")
    tier = environment["tier"]
    if tier not in {"local", "preprod"}:
        raise RunStateError("service policy density environment.tier: only local or preprod is admitted")
    _identifier(environment["id"], "service policy density environment.id")
    expected_env = "local" if tier == "local" else "pre"
    if environment["expected_bcs_env"] != expected_env:
        raise RunStateError(
            f"service policy density environment.expected_bcs_env: {tier} tier must bind {expected_env!r}")
    entrypoint = _exact(profile["entrypoint"], _ENTRYPOINT_KEYS,
                        "service policy density entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise RunStateError(
            "service policy density entrypoint: the reviewed legacy administration face "
            "is required on both tiers")
    origin = _origin(entrypoint["origin"], "service policy density entrypoint.origin")
    if tier == "local":
        if entrypoint["kind"] != "singlebox":
            raise RunStateError("service policy density local leg requires the owned singlebox entrypoint")
        if not origin.startswith("http://") or not any(
                host in origin for host in ("127.0.0.1", "localhost", "[::1]")):
            raise RunStateError("service policy density local leg requires an owned HTTP loopback origin")
        if ":" not in origin.split("//", 1)[1]:
            raise RunStateError("service policy density local loopback origin must declare its port")
    else:
        if entrypoint["kind"] != "bcs-direct":
            raise RunStateError("service policy density preprod leg requires the bcs-direct station entrypoint")
        if not origin.startswith("https://"):
            raise RunStateError("service policy density preprod leg requires the admitted HTTPS station origin")
    human = _exact(profile["human"], _HUMAN_KEYS, "service policy density human")
    user_id = _identifier(human["user_id"], "service policy density human.user_id")
    actors = _exact(profile["actors"], frozenset({"driver"}),
                    "service policy density actors")
    actor = _exact(actors["driver"], _ACTOR_KEYS, "service policy density actors.driver")
    _identifier(actor["bot_id"], "service policy density actors.driver.bot_id")
    if actor["owner_id"] != user_id or actor["dedicated"] is not True:
        raise RunStateError(
            "service policy density actors.driver: the dedicated Bot must be owned by the "
            "configured Human; the retired-policy journey is a single-Bot journey and no "
            "member actor is admitted")
    auth = profile["auth"]
    if not isinstance(auth, dict):
        raise RunStateError("service policy density auth: must declare the reviewed auth family")
    if tier == "local":
        auth = _exact(auth, _AUTH_LOCAL_KEYS, "service policy density auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise RunStateError(
                "service policy density local auth.kind: owned-local Bot tokens are required")
        _env_ref(auth["driver_token_env"], "service policy density auth.driver_token_env")
    else:
        auth = _exact(auth, _AUTH_PREPROD_KEYS, "service policy density auth")
        if auth["kind"] != "human-cookie-with-owned-bot-tokens":
            raise RunStateError(
                "service policy density preprod auth.kind: the Human Cookie with the owned "
                "dedicated driver Bot token reference is required")
        _env_ref(auth["driver_token_env"], "service policy density auth.driver_token_env")
        _env_ref(auth["cookie_env"], "service policy density auth.cookie_env")
    bindings = profile["route_bindings"]
    reviewed_operations = {row["operation"] for row in DENSITY_OPERATION_ROWS}
    if not isinstance(bindings, dict) or set(bindings) != reviewed_operations:
        raise RunStateError(
            "service policy density route_bindings: exactly the nineteen reviewed station "
            "operations are required (the derived teardown projection is disclosed, never bound)")
    reviewed = {row["operation"]: row for row in DENSITY_OPERATION_ROWS}
    for operation, binding in bindings.items():
        binding = _exact(binding, _BINDING_KEYS, f"service policy density route_bindings.{operation}")
        row = reviewed[operation]
        if (binding["method"] != row["method"] or binding["path"] != row["path"]
                or binding["api_face"] != "legacy"):
            raise RunStateError(
                f"service policy density route_bindings.{operation}: method/path must match "
                f"the reviewed {row['method']} {row['path']} legacy binding")
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)

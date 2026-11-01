"""Dual-environment Operator product-CLI Profile validation plane.

Split out of ``operator_cli_runner.py`` under the repository 1,000-line
source-file policy (split-only; no behavior or public-API change). This
plane owns the frozen review tables (profile contract, client
relationships, legacy route bindings, station register bindings, actor
roles, visibility levels), the field validators and the dual-tier
Profile validator. The runner imports this plane and re-exports every
public name so ``bcs_e2e.operator_cli_runner`` importers are unaffected.
"""
from __future__ import annotations

import json

from .profile import (
    ConfigError,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    unique_keys,
)

OPERATOR_CLI_PROFILE_CONTRACT = "dual-operator-cli-profile-v1"

OPERATOR_CLI_RELATIONSHIPS = {
    "fixture_pool": "operator-cli-trusted-team",
    "roles": "CEO-PM-ENG-QA-CS",
    "group_cleanup": "creator-scoped-exact-once",
    "temporary_agent": "register-onboard-read-delete-404",
    "friendships": "two-in-case-admissions-retained",
    "web_onboard": "non-mutating",
}

OPERATOR_CLI_ROUTE_BINDINGS = {
    "operator_cli.bot_registry.read": {
        "method": "GET",
        "path": "/bots/{driver_actor_id}",
        "api_face": "legacy",
        "auth": "case_owned_driver",
        "response": "legacy-json",
        "traceparent": "disabled",
    },
    "operator_cli.group.delete": {
        "method": "DELETE",
        "path": "/groups/{group_id}?bot_id={member_actor_id}",
        "api_face": "legacy",
        "auth": "case_owned_member",
        "response": "legacy-json",
        "traceparent": "disabled",
    },
    "operator_cli.group.deleted_verify": {
        "method": "GET",
        "path": "/groups/{group_id}",
        "api_face": "legacy",
        "auth": "case_owned_member",
        "response": "legacy-json",
        "traceparent": "disabled",
    },
}

OPERATOR_CLI_STATION_BINDINGS = {
    "friend_connection_mutation.register_token.read": {
        "method": "GET",
        "path": "/register/token",
        "api_face": "station-legacy",
        "auth": "human_cookie",
        "response": "plain-json",
        "traceparent": "disabled",
    },
    "friend_connection_mutation.register_bot.create": {
        "method": "POST",
        "path": "/register?token={register_token}&bot-name={bot_name}",
        "api_face": "station-legacy",
        "auth": "register_token",
        "response": "station-register-json",
        "traceparent": "disabled",
    },
    "friend_connection_mutation.station_bot_delete": {
        "method": "DELETE",
        "path": "/bots/{bot_id}",
        "api_face": "station-legacy",
        "auth": "human_cookie_owned_bot",
        "response": "station-bot-delete-json",
        "traceparent": "disabled",
    },
    "friend_connection_mutation.station_bot_read": {
        "method": "GET",
        "path": "/bots/{bot_id}",
        "api_face": "station-legacy",
        "auth": "human_cookie_owned_bot",
        "response": "station-bot-read-json",
        "traceparent": "disabled",
    },
}

OPERATOR_CLI_ACTOR_ROLES = ("driver", "member", "eng", "qa", "cs")

OPERATOR_CLI_VISIBILITY_LEVELS = ("public", "protected", "private")

def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str)
            or not 3 <= len(value) <= 100
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value

def _actor(value: object, role: str, human: str) -> dict:
    actor = object_keys(value, {"bot_id", "owner_id", "dedicated", "visibility"},
                        f"actors.{role}")
    identifier(actor["bot_id"], f"actors.{role}.bot_id")
    if actor["owner_id"] != human or actor["dedicated"] is not True:
        raise ConfigError(f"actors.{role}: must be dedicated and owned by the configured Human")
    if role == "driver" and actor["visibility"] != "public":
        raise ConfigError("actors.driver.visibility: the operator driver Bot must be public")
    if role != "driver" and actor["visibility"] not in OPERATOR_CLI_VISIBILITY_LEVELS:
        raise ConfigError(f"actors.{role}.visibility: unsupported visibility level")
    return actor

def _route_binding(value: object, operation: str) -> dict:
    expected = OPERATOR_CLI_ROUTE_BINDINGS[operation]
    binding = object_keys(value, {"method", "path", "api_face", "auth", "response", "traceparent"},
                          f"route_bindings.{operation}")
    if (binding["method"] != expected["method"] or binding["path"] != expected["path"]
            or binding["api_face"] != "legacy" or binding["auth"] != expected["auth"]
            or binding["response"] != "legacy-json"
            or binding["traceparent"] not in {"disabled", "w3c"}):
        raise ConfigError(
            f"route_bindings.{operation}: binding differs from the reviewed operator contract")
    return binding

def validate_operator_cli_profile(raw: object) -> dict:
    """Validate one dual-environment Operator product-CLI Profile.

    The validator is case-local and never resolves environment references or
    persists credentials; the runner and the adapter both reject unvalidated
    Profiles before any process or transport is constructed.
    """
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("operator CLI profile must be an object")
    base_keys = {"schema_version", "contract", "environment", "entrypoint", "human",
                 "actors", "fixture_names", "auth", "relationships", "policy",
                 "cli", "route_bindings"}
    profile_keys = set(raw) if isinstance(raw, dict) else None
    if profile_keys is None:
        raise ConfigError("operator CLI profile must be an object")
    environment_preview = raw.get("environment")
    tier = (environment_preview.get("tier")
            if isinstance(environment_preview, dict) else None)
    if tier not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    expected_keys = ((base_keys | {"edge_case_bindings", "browser_origin"})
                     if tier == "preprod" else base_keys)
    if profile_keys != expected_keys:
        raise ConfigError(
            "operator CLI profile keys differ from the tier contract; preprod "
            "requires the two station edge bindings, Singlebox forbids them")
    profile = object_keys(raw, expected_keys, "operator CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != OPERATOR_CLI_PROFILE_CONTRACT):
        raise ConfigError("operator CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "operator CLI environment")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                             {"kind", "origin", "api_face", "manifest_face"},
                             "operator CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: operator CLI requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local operator CLI requires an owned loopback")
    else:
        if (entrypoint["kind"] != "bcs-direct" or target.scheme != "https"
                or target.hostname != "bcs-pre.alipay.com"):
            raise ConfigError(
                "entrypoint: preprod operator CLI requires the station legacy face "
                "origin bcs-pre.alipay.com")
    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], set(OPERATOR_CLI_ACTOR_ROLES), "actors")
    actor_ids = set()
    for role in OPERATOR_CLI_ACTOR_ROLES:
        actors[role] = _actor(actors[role], role, human["user_id"])
        actor_ids.add(actors[role]["bot_id"])
    if len(actor_ids) != len(OPERATOR_CLI_ACTOR_ROLES):
        raise ConfigError("actors: operator CLI requires five distinct dedicated Bots")
    names = object_keys(profile["fixture_names"], set(OPERATOR_CLI_ACTOR_ROLES),
                        "fixture_names")
    for role in OPERATOR_CLI_ACTOR_ROLES:
        identifier(names[role], f"fixture_names.{role}")
    if environment["tier"] == "local":
        auth = object_keys(profile["auth"],
                           {"kind", "driver_token_env", "member_token_env",
                            "eng_token_env", "qa_token_env", "cs_token_env"},
                           "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local operator CLI requires owned Bot tokens")
        for role in OPERATOR_CLI_ACTOR_ROLES:
            _env_ref(auth[f"{role}_token_env"], f"auth.{role}_token_env")
    else:
        auth = object_keys(profile["auth"],
                           {"kind", "cookie_env", "driver_token_env"}, "auth")
        if auth["kind"] != "human-cookie-with-owned-driver-bot-token":
            raise ConfigError(
                "auth.kind: preprod operator CLI requires the Human Cookie with the "
                "owned driver Bot token")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
    relationships = object_keys(profile["relationships"],
                                set(OPERATOR_CLI_RELATIONSHIPS), "relationships")
    if relationships != OPERATOR_CLI_RELATIONSHIPS:
        raise ConfigError("relationships: retained operator CLI effect admission differs")
    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"},
                         "operator CLI policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError(
            "policy.exclusive_use_confirmed: retained operator effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-cli", "command_family": "operator", "api_face": "legacy"}:
        raise ConfigError("cli: product CLI binding differs from the case contract")
    bindings = object_keys(profile["route_bindings"], set(OPERATOR_CLI_ROUTE_BINDINGS),
                           "route_bindings")
    for operation in OPERATOR_CLI_ROUTE_BINDINGS:
        bindings[operation] = _route_binding(bindings[operation], operation)
    if environment["tier"] == "preprod":
        station = object_keys(profile["edge_case_bindings"],
                              set(OPERATOR_CLI_STATION_BINDINGS), "edge_case_bindings")
        if station != OPERATOR_CLI_STATION_BINDINGS:
            raise ConfigError(
                "edge_case_bindings: station register bindings differ from the frozen "
                "station template")
        # The reviewed station ACE trio requires the explicit browser Origin/Referer
        # context; the transport only validates it when the profile declares it.
        origin(profile["browser_origin"], environment["tier"])
    profile["route_bindings"] = bindings
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)

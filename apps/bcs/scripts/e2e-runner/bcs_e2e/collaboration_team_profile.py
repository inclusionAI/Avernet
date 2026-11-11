"""Strict dual-environment Profile for the collaboration-team CLI Story."""

from __future__ import annotations

import json

from .profile import ConfigError, identifier, object_keys, parsed_origin, unique_keys


COLLABORATION_TEAM_PROFILE_CONTRACT = "dual-collaboration-team-cli-profile-v1"


def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str)
            or not 3 <= len(value) <= 100
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def _actor(value: object, role: str, human: str, visibility: str) -> dict:
    actor = object_keys(value, {"bot_id", "owner_id", "dedicated", "visibility"},
                         f"actors.{role}")
    identifier(actor["bot_id"], f"actors.{role}.bot_id")
    if actor["owner_id"] != human or actor["dedicated"] is not True:
        raise ConfigError(f"actors.{role}: must be dedicated and owned by the configured Human")
    if actor["visibility"] != visibility:
        raise ConfigError(f"actors.{role}.visibility: differs from the admitted CLI baseline")
    return actor


def validate_collaboration_team_profile(raw: object) -> dict:
    """Validate one case-local Profile without resolving credentials."""
    profile = object_keys(raw, {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "actors", "auth", "relationships", "policy", "cli",
    }, "collaboration-team CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != COLLABORATION_TEAM_PROFILE_CONTRACT):
        raise ConfigError("collaboration-team CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                             {"tier", "id", "expected_bcs_env"},
                             "collaboration-team environment")
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                             {"kind", "origin", "api_face", "manifest_face"},
                             "collaboration-team entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: collaboration-team requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local collaboration-team requires an owned loopback")
    else:
        if entrypoint["kind"] != "gateway" or target.scheme != "https":
            raise ConfigError("entrypoint: preprod collaboration-team requires the HTTPS gateway")

    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(
        profile["actors"], {"engineering", "customer_service", "driver", "qa"},
        "actors")
    for role, visibility in (("engineering", "public"), ("customer_service", "public"),
                             ("driver", "public"), ("qa", "public")):
        actors[role] = _actor(actors[role], role, human["user_id"], visibility)
    ids = [actors[role]["bot_id"] for role in
           ("engineering", "customer_service", "driver", "qa")]
    if len(set(ids)) != 4:
        raise ConfigError("actors: collaboration-team requires four distinct dedicated Bots")

    if environment["tier"] == "local":
        auth = object_keys(
            profile["auth"],
            {"kind", "engineering_token_env", "customer_service_token_env",
             "driver_token_env", "qa_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local collaboration-team requires owned Bot tokens")
        for role in ("engineering", "customer_service", "driver", "qa"):
            _env_ref(auth[f"{role}_token_env"], f"auth.{role}_token_env")
    else:
        auth = profile["auth"]
        if not isinstance(auth, dict):
            raise ConfigError("collaboration-team auth: expected an object")
        kind = auth.get("kind")
        if kind == "human-cookie-with-owned-driver-bot-token":
            object_keys(auth, {"kind", "cookie_env", "driver_token_env"}, "auth")
        elif kind == "human-cookie-with-owned-role-bot-token":
            base = {"kind", "cookie_env", "driver_token_env"}
            role_envs = {f"{role}_token_env" for role in
                         ("engineering", "customer_service", "qa")}
            keys = set(auth)
            if not base <= keys <= base | role_envs:
                raise ConfigError(
                    "collaboration-team auth: missing or unknown fields")
            if not keys & role_envs:
                raise ConfigError(
                    "collaboration-team auth: owned role Bot token credential "
                    "declares no per-role Bot token environment")
        else:
            raise ConfigError(
                "auth.kind: preprod collaboration-team requires a Human Cookie "
                "and the owned driver Bot token")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        for role in ("engineering", "customer_service", "qa"):
            if f"{role}_token_env" in auth:
                _env_ref(auth[f"{role}_token_env"], f"auth.{role}_token_env")

    relationships = object_keys(
        profile["relationships"],
        {"visibility", "friendship", "group", "cleanup", "retention"},
        "relationships")
    if relationships != {
        "visibility": "owned-cs-protect-then-public-restore",
        "friendship": "owned-eng-cs-request-then-reject",
        "group": "two-owned-pm-qa-groups",
        "cleanup": "none-runtime-or-operator-created",
        "retention": "request-and-group-history",
    }:
        raise ConfigError("relationships: collaboration-team admission differs")
    policy = object_keys(
        profile["policy"],
        {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
         "max_response_bytes", "failure_retention_hours"}, "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError("policy.exclusive_use_confirmed: collaboration-team requires exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: outside the collaboration-team contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-cli",
               "command_family": "collaboration-team",
               "api_face": "legacy"}:
        raise ConfigError("cli: collaboration-team product CLI binding differs")
    return json.loads(json.dumps(profile), object_pairs_hook=unique_keys)

"""Strict Profile for the dual-environment Channel product-CLI slice."""

from __future__ import annotations

import json

from .profile import ConfigError, identifier, object_keys, parsed_origin, unique_keys


CHANNEL_CLI_PROFILE_CONTRACT = "dual-channel-cli-profile-v1"

CHANNEL_CLI_BIND_OPERATION = "channel_cli.bind"
CHANNEL_CLI_LIST_BEFORE_OPERATION = "channel_cli.list_before"
CHANNEL_CLI_GROUP_CREATE_OPERATION = "channel_cli.group_create"
CHANNEL_CLI_SESSION_CREATE_OPERATION = "channel_cli.session_create"
CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION = (
    "channel_cli.conversation_empty")
CHANNEL_CLI_UNBIND_MISSING_OPERATION = "channel_cli.unbind_missing"
CHANNEL_CLI_LIST_AFTER_OPERATION = "channel_cli.list_after"
CHANNEL_CLI_SESSION_DELETE_OPERATION = "channel_cli.session.delete"
CHANNEL_CLI_GROUP_DELETE_OPERATION = "channel_cli.group.delete"

CHANNEL_CLI_CLI_OPERATIONS = (
    CHANNEL_CLI_BIND_OPERATION,
    CHANNEL_CLI_LIST_BEFORE_OPERATION,
    CHANNEL_CLI_GROUP_CREATE_OPERATION,
    CHANNEL_CLI_SESSION_CREATE_OPERATION,
    CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
    CHANNEL_CLI_UNBIND_MISSING_OPERATION,
    CHANNEL_CLI_LIST_AFTER_OPERATION,
)

PROFILE_EFFECTS = {
    CHANNEL_CLI_PROFILE_CONTRACT: {
        "bindings": "no-durable-binding",
        "group": "bounded-owned-bots-cli-channel",
        "initial_session": "deleted-with-group",
        "explicit_session": "deleted-with-group",
        "cleanup": "exact-owned-sessions-then-group",
        "participant_mode": "member-and-engine",
    },
}


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
    if actor["visibility"] != "public":
        raise ConfigError(f"actors.{role}.visibility: channel CLI requires public Bots")
    return actor


def validate_channel_cli_cleanup_bindings(value: object, member_id: str) -> dict:
    """Validate the two member-scoped legacy deletion routes."""
    expected = {
        CHANNEL_CLI_SESSION_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}?bot_id={member_actor_id}",
        },
        CHANNEL_CLI_GROUP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={member_actor_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("channel CLI cleanup bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_member"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: cleanup route differs from the product contract")
        result[operation] = dict(binding)
    if identifier(member_id, "actors.member.bot_id") != member_id:
        raise ConfigError("channel CLI cleanup member is invalid")
    return result


def validate_channel_cli_profile(raw: object) -> dict:
    """Validate one dual-environment Channel product-CLI Profile."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("channel CLI profile must be an object")
    profile = object_keys(raw, {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "actors", "auth", "relationships", "policy", "cli", "route_bindings",
    }, "channel CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != CHANNEL_CLI_PROFILE_CONTRACT):
        raise ConfigError("channel CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "channel CLI environment")
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                              {"kind", "origin", "api_face", "manifest_face"},
                              "channel CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: channel CLI requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local channel CLI requires an owned loopback")
    else:
        if entrypoint["kind"] != "gateway" or target.scheme != "https":
            raise ConfigError("entrypoint: preprod channel CLI requires the admitted HTTPS gateway")

    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], {"driver", "member", "eng"}, "actors")
    for role in ("driver", "member", "eng"):
        actors[role] = _actor(actors[role], role, human["user_id"])
    actor_ids = {actors[role]["bot_id"] for role in ("driver", "member", "eng")}
    if len(actor_ids) != 3:
        raise ConfigError("actors: channel CLI requires three distinct dedicated Bots")

    if environment["tier"] == "local":
        auth = object_keys(profile["auth"],
                           {"kind", "driver_token_env", "member_token_env", "eng_token_env"},
                           "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local channel CLI requires owned Bot tokens")
        for key in ("driver_token_env", "member_token_env", "eng_token_env"):
            _env_ref(auth[key], f"auth.{key}")
    else:
        auth = profile["auth"]
        if not isinstance(auth, dict):
            raise ConfigError("channel CLI auth: expected an object")
        base = {"kind", "cookie_env"}
        role_envs = {"driver_token_env", "member_token_env", "eng_token_env"}
        kind = auth.get("kind")
        if kind == "human-cookie":
            object_keys(auth, base, "auth")
        elif kind == "human-cookie-with-owned-role-bot-token":
            keys = set(auth)
            if not base <= keys <= base | role_envs:
                raise ConfigError("channel CLI auth: missing or unknown fields")
            if not keys & role_envs:
                raise ConfigError(
                    "channel CLI auth: owned role Bot token credential declares "
                    "no per-role Bot token environment")
        else:
            raise ConfigError("auth.kind: preprod channel CLI requires a Human Cookie")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        for role in ("driver", "member", "eng"):
            if f"{role}_token_env" in auth:
                _env_ref(auth[f"{role}_token_env"], f"auth.{role}_token_env")

    relationships = object_keys(
        profile["relationships"], set(PROFILE_EFFECTS[CHANNEL_CLI_PROFILE_CONTRACT]),
        "relationships")
    if relationships != PROFILE_EFFECTS[CHANNEL_CLI_PROFILE_CONTRACT]:
        raise ConfigError("relationships: retained Channel CLI effect admission differs")

    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"},
                         "channel CLI policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError("policy.exclusive_use_confirmed: retained Channel effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")

    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-cli", "command_family": "channel", "api_face": "legacy"}:
        raise ConfigError("cli: product CLI binding differs from the case contract")

    profile["route_bindings"] = validate_channel_cli_cleanup_bindings(
        profile["route_bindings"], actors["member"]["bot_id"])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)

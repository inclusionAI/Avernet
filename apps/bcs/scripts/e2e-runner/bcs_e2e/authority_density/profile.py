"""Credential-name-only admission for the two authority density tiers.

The local tier replays the frozen 20 business operations in memory beside the
owned-local posture of the landed dual authority envelope. The preprod tier is
an unexecuted bcs-direct attachment declaration: it freezes the duo credential
references, the staff-number environment references, the internal-and-public-v1
face posture, and the declared-but-unreviewed route binding inventory, so the
admission diagnosis can derive its named gaps from the profile itself. No
credential value is ever accepted; profile validation is shape-only.
"""
from __future__ import annotations

import re
from typing import Any
from ..run_state import RunStateError
from .contract import MINE_USER_ID, parsed_obligations, TARGET_USER_ID

PROFILE_CONTRACT = "authority-density-profile-v1"
ALLOWED_ENV_NAMES = {
    "BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN",
    "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
    "BCS_E2E_AUTHORITY_STAFF_B", "BCS_E2E_AUTHORITY_STAFF_MINE",
}
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")
_ROUTE_API_FACES = ("internal-v1", "public-v1")


def _operation_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in parsed_obligations():
        binding = {
            "method": row.method,
            "path_template": row.path_template,
            "actor": row.actor,
            "query": (dict([row.query]) if row.query else None),
        }
        previous = index.get(row.operation)
        if previous is not None and previous != binding:
            raise RunStateError("authority density operation bindings are not uniform: "
                                + row.operation)
        index[row.operation] = binding
    return index


def _env_name(value: Any, field: str) -> str:
    if (not isinstance(value, str) or _ENV_NAME.fullmatch(value) is None
            or value not in ALLOWED_ENV_NAMES):
        raise RunStateError(field + " must name an admitted credential environment variable")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise RunStateError(field + " must be a bounded non-empty identifier")
    if not re.fullmatch(r"[A-Za-z0-9_.:@-]+", value):
        raise RunStateError(field + " contains disallowed characters")
    return value


# RFC-free shape gate: a credential-free HTTPS origin with an optional
# trailing slash and optional numeric port. Userinfo, query, and fragment
# are rejected by pattern; no URL parsing library is imported.
_CREDENTIAL_FREE_ORIGIN = re.compile(
    r"https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](:[0-9]{1,5})?/?\Z")


def _origin(value: Any) -> str:
    if not isinstance(value, str) or _CREDENTIAL_FREE_ORIGIN.fullmatch(value) is None:
        raise RunStateError("entrypoint.origin must be a credential-free HTTPS origin")
    return value


def _policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != {"timeout_seconds", "max_response_bytes",
                                                       "failure_retention_hours"}:
        raise RunStateError("authority density policy keys changed")
    if type(policy["timeout_seconds"]) is not int or not 1 <= policy["timeout_seconds"] <= 120:
        raise RunStateError("policy.timeout_seconds outside 1..120")
    if type(policy["max_response_bytes"]) is not int or not 1 <= policy["max_response_bytes"] <= 1048576:
        raise RunStateError("policy.max_response_bytes outside 1..1048576")
    if type(policy["failure_retention_hours"]) is not int or not 1 <= policy["failure_retention_hours"] <= 168:
        raise RunStateError("policy.failure_retention_hours outside 1..168")


def _operation_bindings(bindings: Any) -> dict[str, dict[str, Any]]:
    expected = _operation_index()
    if not isinstance(bindings, dict) or set(bindings) != set(expected):
        raise RunStateError("authority density operation bindings are not the reviewed roster")
    for operation, binding in bindings.items():
        if (not isinstance(binding, dict) or set(binding) != {"method", "path_template",
                                                              "actor", "query"}
                or binding != expected[operation]):
            raise RunStateError("invalid authority density operation binding: " + operation)
    return bindings


def _route_bindings(bindings: Any) -> dict[str, dict[str, Any]]:
    expected = _operation_index()
    if not isinstance(bindings, dict) or set(bindings) != set(expected):
        raise RunStateError("authority density route bindings are not the roster of 20")
    for operation, binding in bindings.items():
        if not isinstance(binding, dict) or set(binding) != {"method", "path", "api_face",
                                                             "auth", "response", "traceparent",
                                                             "reviewed"}:
            raise RunStateError("invalid authority density route binding: " + operation)
        if (binding["method"] != expected[operation]["method"]
                or binding["path"] != expected[operation]["path_template"]):
            raise RunStateError("authority density route binding method/path drifted: " + operation)
        face = "public-v1" if operation == "public.mine.read" else "internal-v1"
        if binding["api_face"] != face:
            raise RunStateError("authority density route binding api_face drifted: " + operation)
        if binding["auth"] != "gateway-principal-jwt":
            raise RunStateError("authority density route binding auth drifted: " + operation)
        if binding["response"] != "business-envelope":
            raise RunStateError("authority density route binding response drifted: " + operation)
        if binding["traceparent"] not in ("disabled", "w3c"):
            raise RunStateError("authority density route binding traceparent drifted: " + operation)
        if binding["reviewed"] is not False:
            raise RunStateError("authority density route bindings must stay declared-unreviewed")
    return bindings


def validate_density_profile(profile: object) -> dict[str, Any]:
    """Admit exactly the local replay or the declared preprod attachment shape."""
    if not isinstance(profile, dict):
        raise RunStateError("authority density profile must be an object")
    if profile.get("schema_version") != 1 or profile.get("contract") != PROFILE_CONTRACT:
        raise RunStateError("authority density profile schema contract mismatch")
    if "secrets" in profile:
        raise RunStateError("authority density profile must not carry credential material")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    auth = profile.get("auth")
    authority = profile.get("authority")
    cli = profile.get("cli")
    bindings = profile.get("operation_bindings")
    if not all(isinstance(section, dict) for section in
               (environment, entrypoint, auth, authority, cli, bindings)):
        raise RunStateError("authority density profile lacks a required section")
    tier = environment.get("tier")
    if tier not in ("local", "preprod"):
        raise RunStateError("authority density tier must be local or preprod")
    _identifier(environment.get("id"), "environment.id")
    _operation_bindings(bindings)
    if tier == "local":
        if set(profile) != {"schema_version", "contract", "environment", "entrypoint",
                            "auth", "authority", "cli", "policy", "operation_bindings"}:
            raise RunStateError("authority density local profile keys changed")
        if set(environment) != {"tier", "id", "lifecycle", "deployment"}:
            raise RunStateError("authority density local environment keys changed")
        if (environment.get("lifecycle"), environment.get("deployment")) != (
                "local-provision", "singlebox"):
            raise RunStateError("authority density local posture must be owned-local singlebox")
        if set(entrypoint) != {"kind"} or entrypoint.get("kind") != "owned-local":
            raise RunStateError("authority density local entrypoint must be owned-local")
        if set(auth) != {"kind", "principal_signing_key_env"} or auth.get("kind") != "owned-local":
            raise RunStateError("authority density local auth must be owned-local")
        _env_name(auth.get("principal_signing_key_env"), "auth.principal_signing_key_env")
        if set(authority) != {"target_user_id", "mine_user_id"}:
            raise RunStateError("authority density local authority keys changed")
        if authority.get("target_user_id") != TARGET_USER_ID \
                or authority.get("mine_user_id") != MINE_USER_ID:
            raise RunStateError("authority density local authority fixtures differ from frozen")
        if set(cli) != {"transport", "command_family", "api_face"}:
            raise RunStateError("authority density local cli keys changed")
        if (cli.get("transport"), cli.get("command_family"), cli.get("api_face")) != (
                "owned-local-replay", "collaboration-authority", "public-v1"):
            raise RunStateError("authority density local cli posture differs from the frozen face")
    else:
        if set(profile) != {"schema_version", "contract", "environment", "entrypoint",
                            "human", "auth", "authority", "cli", "policy",
                            "operation_bindings", "route_bindings"}:
            raise RunStateError("authority density preprod profile keys changed")
        if set(environment) != {"tier", "id", "expected_bcs_env", "lifecycle", "deployment"}:
            raise RunStateError("authority density preprod environment keys changed")
        if (environment.get("expected_bcs_env"), environment.get("lifecycle"),
                environment.get("deployment")) != ("pre", "remote-attach", "preprod"):
            raise RunStateError("authority density preprod posture must be remote-attach preprod")
        if set(entrypoint) != {"kind", "origin", "api_face", "manifest_face"}:
            raise RunStateError("authority density preprod entrypoint keys changed")
        if (entrypoint.get("kind"), entrypoint.get("api_face"), entrypoint.get("manifest_face")) != (
                "bcs-direct", "internal-and-public-v1", "internal-and-public-v1"):
            raise RunStateError("authority density preprod entrypoint must bind both v1 faces")
        _origin(entrypoint.get("origin"))
        human = profile.get("human")
        if not isinstance(human, dict) or set(human) != {"user_id"}:
            raise RunStateError("authority density preprod human block keys changed")
        _identifier(human.get("user_id"), "human.user_id")
        if set(auth) != {"kind", "header_env"} or auth.get("kind") != "preprod-driver-bot-duo":
            raise RunStateError("authority density preprod auth must bind the duo references")
        headers = auth.get("header_env")
        if (not isinstance(headers, dict) or set(headers) != {"Authorization", "Cookie"}
                or _env_name(headers.get("Authorization"), "auth.header_env.Authorization")
                != "BCS_E2E_DRIVER_TOKEN"
                or _env_name(headers.get("Cookie"), "auth.header_env.Cookie")
                != "BCS_E2E_HUMAN_COOKIE"):
            raise RunStateError("authority density preprod duo references differ from frozen")
        if set(authority) != {"target_user_id_env", "mine_user_id_env"}:
            raise RunStateError("authority density preprod authority keys changed")
        if (_env_name(authority.get("target_user_id_env"), "authority.target_user_id_env")
                != "BCS_E2E_AUTHORITY_STAFF_B"):
            raise RunStateError("authority density preprod target staff reference differs")
        if (_env_name(authority.get("mine_user_id_env"), "authority.mine_user_id_env")
                != "BCS_E2E_AUTHORITY_STAFF_MINE"):
            raise RunStateError("authority density preprod mine staff reference differs")
        if set(cli) != {"transport", "command_family", "api_face"}:
            raise RunStateError("authority density preprod cli keys changed")
        if (cli.get("transport"), cli.get("command_family"), cli.get("api_face")) != (
                "product-cli-http", "collaboration-authority", "public-v1"):
            raise RunStateError("authority density preprod cli posture differs from the frozen face")
        _route_bindings(profile.get("route_bindings"))
    _policy(profile.get("policy"))
    return profile

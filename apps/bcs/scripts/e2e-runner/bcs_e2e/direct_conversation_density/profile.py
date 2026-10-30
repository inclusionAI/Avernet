"""Dual-environment profile contract for the Direct-conversation density slice.

The density projection rides the same reviewed wire operations as the frozen DM
journey (main submit/read/cancel/terminal read), so the admitted profile shapes
mirror the reviewed dual-direct-chat CLI profile gates: the owned-local arm is
an owned loopback Singlebox with bare owned-Bot token references, and the
preprod arm is the admitted HTTPS gateway ``bcs-pre.alipay.com`` with the legacy
product face, the qualified ``<bot_id>:<owner_id>`` station member form and a
declared browser origin.  Two shape rules are absolute:

* credential material is admitted as environment-variable NAME references only
  (``BCS_E2E_HUMAN_COOKIE`` / ``BCS_E2E_DRIVER_TOKEN`` family); any inline
  secret value fails closed;
* the density operation binding set is exactly the four reviewed main-journey
  operations — the supplementary ping lifecycle keeps its DM parent credit and
  is not re-declared here.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ..profile import ConfigError, object_keys, parsed_origin

DENSITY_PROFILE_CONTRACT = "dual-direct-conversation-density-profile-v1"

_ENV_REF = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")
_MEMBER_BOT_ID_LOCAL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,63}\Z")
_MEMBER_BOT_ID_PREPROD = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,63}:[0-9]{1,20}\Z")

# The four reviewed main-journey operations; aligned with
# direct_chat_cli_case_leaf.py constants (pinned by the alignment test).
DENSITY_OPERATION_BINDINGS = (
    ("direct_chat.main.submit", "POST", "/bots/{member_actor_id}/chat-async"),
    ("direct_chat.main.read", "GET", "/chat/runs/{run_id}"),
    ("direct_chat.main.cancel", "POST", "/chat/runs/{run_id}/cancel"),
    ("direct_chat.main.terminal", "GET", "/chat/runs/{run_id}"),
)
_DENSITY_OPERATION_NAMES = frozenset(name for name, _, _ in DENSITY_OPERATION_BINDINGS)


def _env_ref(value: object, where: str) -> str:
    if not isinstance(value, str) or _ENV_REF.fullmatch(value) is None:
        raise ConfigError(f"{where}: expected an uppercase environment variable reference")
    return value


def _no_secrets(profile: dict[str, Any]) -> None:
    secrets = profile.get("secrets")
    if secrets not in (None, {}):
        raise ConfigError("direct conversation density profile must not carry secret values")


def _validate_bindings(profile: dict[str, Any]) -> dict[str, Any]:
    bindings = profile.get("density_operation_bindings")
    if not isinstance(bindings, dict) or set(bindings) != _DENSITY_OPERATION_NAMES:
        raise ConfigError(
            "density_operation_bindings: exactly the four reviewed main-journey "
            "operations must be declared")
    for name, method, path in DENSITY_OPERATION_BINDINGS:
        binding = bindings[name]
        if not isinstance(binding, dict) or set(binding) != {"method", "path"}:
            raise ConfigError(f"density_operation_bindings.{name}: method/path shape required")
        if binding["method"] != method or binding["path"] != path:
            raise ConfigError(f"density_operation_bindings.{name}: binding differs from the DM route")
    return dict(bindings)


def _validate_journey(profile: dict[str, Any], *, tier: str) -> str:
    journey = object_keys(profile.get("journey"), {"member_bot_id"}, "journey")
    member_bot_id = journey["member_bot_id"]
    if not isinstance(member_bot_id, str):
        raise ConfigError("journey.member_bot_id: the PM target Bot id is required")
    if tier == "local":
        if _MEMBER_BOT_ID_LOCAL.fullmatch(member_bot_id) is None or ":" in member_bot_id:
            raise ConfigError(
                "journey.member_bot_id: local owned-local rehearsal requires the bare Bot id")
    else:
        if _MEMBER_BOT_ID_PREPROD.fullmatch(member_bot_id) is None:
            raise ConfigError(
                "journey.member_bot_id: preprod requires the qualified "
                "<bot_id>:<owner_id> station form")
    return member_bot_id


def _validate_auth(profile: dict[str, Any], *, tier: str) -> dict[str, str]:
    if tier == "local":
        auth = object_keys(
            profile.get("auth"), {"kind", "driver_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local density rehearsal requires owned-local bot tokens")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        return {"driver_token_env": auth["driver_token_env"]}
    auth = profile.get("auth")
    if isinstance(auth, dict) and auth.get("kind") == "human-cookie" and set(auth) == {
            "kind", "cookie_env"}:
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        return {"cookie_env": auth["cookie_env"]}
    if isinstance(auth, dict) and auth.get("kind") == "human-cookie-with-owned-bot-tokens" and set(auth) == {
            "kind", "driver_token_env", "member_token_env", "cookie_env"}:
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["member_token_env"], "auth.member_token_env")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        return dict(auth)
    raise ConfigError(
        "auth: preprod density requires the Human Cookie reference or the "
        "reviewed ACE trio (Human Cookie plus owned driver/member Bot token "
        "references); values are environment-variable names only")


def validate_density_profile(profile: object) -> dict[str, Any]:
    """Admit one direct-conversation density leg profile, per tier."""
    if not isinstance(profile, dict) or not hasattr(profile, "get"):
        raise ConfigError("direct conversation density profile must be an object")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    if not isinstance(environment, dict) or not isinstance(entrypoint, dict):
        raise ConfigError(
            "direct conversation density profile lacks environment or entrypoint")
    tier = environment.get("tier")
    if tier not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only owned-local Singlebox and preprod are admitted")
    object_keys(profile, {
        "schema_version", "contract", "environment", "entrypoint", "auth",
        "journey", "density_operation_bindings",
        *(["browser_origin"] if tier == "preprod" else []),
    }, "direct conversation density profile")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise ConfigError("direct conversation density profile: unsupported schema_version")
    if profile["contract"] != DENSITY_PROFILE_CONTRACT:
        raise ConfigError(f"direct conversation density profile: contract must be {DENSITY_PROFILE_CONTRACT}")
    object_keys(environment, {"tier", "id", "expected_bcs_env", "lifecycle", "deployment", "provider"},
                "direct conversation density environment")
    if tier == "local":
        if (environment["lifecycle"] != "local-provision" or environment["deployment"] != "singlebox"
                or environment["provider"] != "local"):
            raise ConfigError("local density leg must be owned-local singlebox provisioned")
    else:
        if (environment["lifecycle"] != "remote-attach" or environment["deployment"] != "managed"
                or environment["provider"] != "internal"):
            raise ConfigError("preprod density leg must be remote-attach managed internal")
    if tier == "local" and environment["expected_bcs_env"] != "local":
        raise ConfigError("environment.expected_bcs_env: local leg binds the owned-local tier")
    if tier == "preprod" and environment["expected_bcs_env"] != "pre":
        raise ConfigError("environment.expected_bcs_env: preprod leg binds the pre tier")

    entry = object_keys(entrypoint, {"kind", "origin", "api_face", "manifest_face"},
                        "direct conversation density entrypoint")
    if entry["api_face"] != "legacy" or entry["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: the density projection rides the reviewed legacy DM face")
    target = parsed_origin(entry["origin"], tier)
    if tier == "local":
        if (entry["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local density leg requires an owned loopback origin")
    else:
        if (entry["kind"] != "gateway" or target.scheme != "https"
                or target.hostname != "bcs-pre.alipay.com"):
            raise ConfigError(
                "entrypoint: preprod density leg requires the admitted HTTPS gateway bcs-pre.alipay.com")
        browser = profile.get("browser_origin")
        if not isinstance(browser, str) or not browser:
            raise ConfigError("browser_origin: preprod density leg requires the pinned Gateway origin")
        browser_target = parsed_origin(browser, "preprod")
        if browser_target.scheme != "https" or not browser_target.hostname:
            raise ConfigError("browser_origin: preprod requires an HTTPS Gateway origin")

    _validate_journey(profile, tier=tier)
    _validate_auth(profile, tier=tier)
    _validate_bindings(profile)
    _no_secrets(profile)
    return profile

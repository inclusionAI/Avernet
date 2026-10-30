"""Dual owned-local/preprod execution surface for Provider subbots.

The owned-local leg reuses the frozen 15-obligation / 131-check child story.
The preprod leg is deliberately a bounded route-surface probe until the
Provider control plane has a fully admitted remote execution contract.  It
uses wrong-method requests on the reviewed paths, so the probe cannot create,
disable, delete or otherwise mutate a Provider or Bot.

Both legs additionally project the 131-leaf density contract: local PASS
flows only through the frozen child receipt collective, while preprod stays
BLOCKED_INVENTORIED with named documented gaps and network_requests=0.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .case_executor import CaseExecution, StepOutcome
from .inventory import executor_digest
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    require_resolved,
    unique_keys,
)
from .provider_subbots.contract import (
    CASE_ID as SOURCE_CASE_ID,
    PARENT,
    REQUIREMENTS,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
    SUITE as SOURCE_SUITE,
    provider_subbots_source_binding,
)
from .provider_subbots.density import (
    DENSITY_CONTRACT_ID,
    DENSITY_LEAVES,
    DENSITY_PREFIX,
    LOCAL_DERIVATION,
    STATUS_BLOCKED_INVENTORIED,
    build_case_density_projection,
    density_contract_digest,
)
from .provider_subbots.run import local_provider_subbots_run
from .run_state import RunStateError, write
from .transport import tls_context

SCHEMA_VERSION = 1
PROFILE_CONTRACT = "dual-provider-subbots-profile-v1"
REPORT_CONTRACT = "dual-provider-subbots-case-run-v1"
CASE_ID = "DUAL-PROVIDER-SUBBOTS-01"
SUITE = "dual-provider-subbots-v1"
CASE_KEYS = ("provider-subbots",)
FIXTURE_EFFECT = "PROVIDER_RETIRED_SUBBOTS_TOMBSTONED"
_CASE_METHOD = "execute_provider_subbots_obligation"
_COMMON_PROFILE_KEYS = {
    "schema_version", "contract", "environment", "entrypoint", "human",
    "auth", "policy", "cli", "fixture",
}
_PREPROD_PROFILE_KEYS = _COMMON_PROFILE_KEYS | {
    "route_bindings", "browser_origin", "credential_files",
}
_PRESENT_PROBE_STATUSES = frozenset({400, 401, 403, 405})
_ABSENT_STATUS = 404


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


def _env_ref(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", value)
    ):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def _bounded_env(name: str, where: str) -> str:
    value = os.environ.get(name, "")
    if (
        not value
        or not value.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value) > 8192
    ):
        raise ConfigError(f"{where}: missing or invalid environment credential")
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# One wrong-method request per reviewed surface.  The canonical method is the
# real story operation; the probe method is intentionally different so a
# present Axum route answers 405 instead of performing a write.  A 404 from the
# target is compared with the same 404 from an absent baseline path.
_PROBE_ROWS = (
    {
        "operation": "provider.register",
        "canonical_method": "POST",
        "probe_method": "GET",
        "path": "/providers",
        "caller": "human",
        "expectation": "Provider registration route is mounted",
    },
    {
        "operation": "provider.discovery_slug",
        "canonical_method": "GET",
        "probe_method": "POST",
        "path": "/providers/by-slug/{provider_slug}",
        "caller": "human",
        "expectation": "Public Provider slug discovery route is mounted",
    },
    {
        "operation": "provider.bot_create",
        "canonical_method": "POST",
        "probe_method": "PUT",
        "path": "/providers/{provider_id}/bots",
        "caller": "provider_admin",
        "expectation": "Provider Bot create/list route is mounted",
    },
    {
        "operation": "provider.bot_delete",
        "canonical_method": "DELETE",
        "probe_method": "POST",
        "path": "/providers/{provider_id}/bots/{provider_bot_ref}",
        "caller": "provider_admin",
        "expectation": "Provider Bot delete/patch route is mounted",
    },
    {
        "operation": "provider.disable",
        "canonical_method": "POST",
        "probe_method": "GET",
        "path": "/providers/{provider_id}/disable",
        "caller": "provider_admin",
        "expectation": "Provider disable route is mounted",
    },
    {
        "operation": "provider.enable",
        "canonical_method": "POST",
        "probe_method": "GET",
        "path": "/providers/{provider_id}/enable",
        "caller": "provider_admin",
        "expectation": "Provider enable route is mounted",
    },
    {
        "operation": "bot.self_lookup",
        "canonical_method": "GET",
        "probe_method": "POST",
        "path": "/api/v1/collaboration/bots/me",
        "caller": "bot_runtime",
        "expectation": "Bot self-lookup route is mounted",
    },
    {
        "operation": "bot.callback",
        "canonical_method": "POST",
        "probe_method": "GET",
        "path": "/bot/events",
        "caller": "bot_runtime",
        "expectation": "Provider callback route is mounted",
    },
    {
        "operation": "gateway.signed_principal",
        "canonical_method": "GET",
        "probe_method": "POST",
        "path": "/openapi/v1/collaboration/public-groups",
        "caller": "signed_principal",
        "expectation": "Signed-principal Gateway route is mounted",
    },
)
PROBE_BY_OPERATION = {row["operation"]: row for row in _PROBE_ROWS}
_BASELINE_PATH = "/__bcs_e2e_absent_provider_subbots_probe__"
_PROBE_ID = "__bcs_e2e_probe__"


def _source_path(path: str) -> str:
    return re.sub(r"\{([a-z_]+)\}", r"${\1}", path)


def _probe_path(path: str) -> str:
    values = {
        "provider_slug": _PROBE_ID,
        "provider_id": _PROBE_ID,
        "provider_bot_ref": _PROBE_ID,
    }
    for key, value in values.items():
        path = path.replace("{" + key + "}", quote(value, safe=""))
    if "{" in path or "}" in path:
        raise ConfigError("provider subbots route has an unresolved path parameter")
    return path


def provider_subbots_case_plan() -> dict:
    """Return the frozen obligation plan shared by both environment legs."""
    binding = provider_subbots_source_binding()
    return {
        "case_id": CASE_ID,
        "source_case_id": SOURCE_CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "requirement_count": len(REQUIREMENTS),
        "story_checks": STORY_CHECKS,
        "density_contract": DENSITY_CONTRACT_ID,
        "density_prefix": DENSITY_PREFIX,
        "density_leaves": DENSITY_LEAVES,
        "density_contract_digest": density_contract_digest(),
        "source_binding_digest": fingerprint(binding),
        "probe_operations": [row["operation"] for row in _PROBE_ROWS],
    }


def provider_subbots_adaptability() -> dict:
    """Describe the dual surface without granting remote release credit."""
    return {
        "case_id": CASE_ID,
        "source_case_id": SOURCE_CASE_ID,
        "legacy_id": PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "selected_obligations": [
            "Provider registration and public discovery",
            "Provider-admin complete-ref validation and sibling registration",
            "Scoped complete-ref validation and distinct sibling identity",
            "duplicate-reference conflicts",
            "self, legacy API, callback and signed-principal actor binding",
            "metadata/onboarding parent immutability",
            "parent discovery fail-closed",
            "disable/enable live-state enforcement",
            "deletion tombstones and Provider retirement",
        ],
        "environments": [
            "owned-local-full-story",
            "preprod-control-plane-route-probe",
        ],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "preprod_executor_status": "ROUTE_PROBE_ONLY_AWAITING_FULL_STORY_CREDENTIALS",
        "required_assertion_count": STORY_CHECKS,
        "density_contract": DENSITY_CONTRACT_ID,
        "density_leaves": DENSITY_LEAVES,
        "density_local_derivation": LOCAL_DERIVATION,
        "density_preprod_status": STATUS_BLOCKED_INVENTORIED,
        "density_remote_validation": "NOT_RUN",
        "density_grants_release_credit": False,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The owned-local leg executes the frozen 131-check story. The preprod "
            "leg performs a bounded wrong-method route-surface probe for the "
            "Provider create/disable/delete and related routes; it does not claim "
            "the full 131-check story because the reviewed credential set has no "
            "principal signing key for the signed-principal obligation."
        ),
    }


def _validate_route_bindings(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(PROBE_BY_OPERATION):
        raise ConfigError("provider subbots route bindings are incomplete")
    checked = {}
    for operation, row in PROBE_BY_OPERATION.items():
        raw = object_keys(
            value[operation],
            {"method", "path", "api_face", "auth", "response", "traceparent"},
            f"route_bindings.{operation}",
        )
        if (
            raw["method"] != row["canonical_method"]
            or raw["path"] != row["path"]
            or raw["api_face"] != "legacy"
            or raw["auth"] != row["caller"]
            or raw["response"] != "legacy-json"
            or raw["traceparent"] != "disabled"
        ):
            raise ConfigError(
                f"route_bindings.{operation}: Provider route differs from the reviewed contract"
            )
        checked[operation] = dict(raw)
    return checked


def validate_provider_subbots_profile(raw: object) -> dict:
    """Validate the dual Provider-subbots profile without resolving credentials."""
    if not isinstance(raw, dict):
        raise ConfigError("provider subbots profile must be an object")
    environment_value = raw.get("environment")
    tier = environment_value.get("tier") if isinstance(environment_value, dict) else None
    expected_keys = (
        _PREPROD_PROFILE_KEYS if tier == "preprod" else _COMMON_PROFILE_KEYS
    )
    profile = object_keys(raw, expected_keys, "provider subbots profile")
    if (
        type(profile["schema_version"]) is not int
        or profile["schema_version"] != 1
        or profile["contract"] != PROFILE_CONTRACT
    ):
        raise ConfigError("provider subbots profile: unsupported version or contract")
    environment = object_keys(
        profile["environment"], {"tier", "id", "expected_bcs_env"},
        "provider subbots environment",
    )
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only owned-local and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != (
        "local" if environment["tier"] == "local" else "pre"
    ):
        raise ConfigError("environment.expected_bcs_env: environment binding mismatch")

    entrypoint = profile["entrypoint"]
    if environment["tier"] == "local":
        entrypoint = object_keys(entrypoint, {"kind"}, "provider subbots entrypoint")
        if entrypoint["kind"] != "owned-local":
            raise ConfigError("entrypoint.kind: local Provider subbots require owned-local")
        human = object_keys(profile["human"], {"user_id"}, "human")
        identifier(human["user_id"], "human.user_id")
        auth = object_keys(profile["auth"], {"kind"}, "auth")
        if auth["kind"] != "owned-local":
            raise ConfigError("auth.kind: local Provider subbots require owned-local")
    else:
        entrypoint = object_keys(
            entrypoint, {"kind", "origin", "api_face", "manifest_face"},
            "provider subbots entrypoint",
        )
        if entrypoint["kind"] not in {"gateway", "bcs-direct"}:
            raise ConfigError("entrypoint.kind: preprod Provider subbots require gateway or bcs-direct")
        if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
            raise ConfigError("entrypoint: Provider subbots require the legacy product face")
        if parsed_origin(entrypoint["origin"], "preprod").scheme != "https":
            raise ConfigError("entrypoint.origin: preprod Provider subbots require HTTPS")
        human = object_keys(profile["human"], {"user_id"}, "human")
        identifier(human["user_id"], "human.user_id")
        auth = object_keys(
            profile["auth"], {"kind", "cookie_env", "probe_bot_token_env"}, "auth"
        )
        if auth["kind"] != "preprod-human-cookie":
            raise ConfigError("auth.kind: preprod Provider subbots require the Human Cookie")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        _env_ref(auth["probe_bot_token_env"], "auth.probe_bot_token_env")
        browser_origin = profile["browser_origin"]
        if (
            not isinstance(browser_origin, str)
            or not browser_origin.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in browser_origin)
        ):
            raise ConfigError("browser_origin: ASCII origin without whitespace or controls")
        origin(browser_origin, "preprod")
        files = profile["credential_files"]
        if not isinstance(files, dict) or not files:
            raise ConfigError("credential_files: expected a nonempty environment-to-path map")
        for ref, path in files.items():
            _env_ref(ref, "credential_files")
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or not path.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
            ):
                raise ConfigError("credential_files: expected absolute ASCII paths")
        profile["route_bindings"] = _validate_route_bindings(profile["route_bindings"])

    fixture = object_keys(
        profile["fixture"], {"webhook_url", "takeover_bot_id"}, "fixture"
    )
    if (
        not isinstance(fixture["webhook_url"], str)
        or not fixture["webhook_url"].startswith("https://")
        or not fixture["webhook_url"].isascii()
    ):
        raise ConfigError("fixture.webhook_url: expected an HTTPS URL")
    identifier(fixture["takeover_bot_id"], "fixture.takeover_bot_id")
    policy = object_keys(
        profile["policy"],
        {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
         "max_response_bytes", "failure_retention_hours"},
        "policy",
    )
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if policy["exclusive_use_confirmed"] is not True:
        raise ConfigError("policy.exclusive_use_confirmed: Provider executions require exclusive use")
    for key, maximum in (
        ("timeout_seconds", 30),
        ("max_response_bytes", 65536),
        ("failure_retention_hours", 168),
    ):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the Provider contract")
    cli = object_keys(
        profile["cli"], {"transport", "command_family", "api_face"}, "cli"
    )
    if (
        cli["transport"] not in {"owned-local-story", "product-http"}
        or cli["command_family"] != "provider-subbots"
        or cli["api_face"] != "legacy"
    ):
        raise ConfigError("cli: Provider subbots bind the reviewed legacy product surface")
    if environment["tier"] == "local" and cli["transport"] != "owned-local-story":
        raise ConfigError("cli.transport: local Provider subbots require the owned-local story")
    if environment["tier"] == "preprod" and cli["transport"] != "product-http":
        raise ConfigError("cli.transport: preprod Provider subbots require product HTTP")
    return json.loads(
        json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys
    )


def load_provider_subbots_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("provider subbots case must be provider-subbots")
    if path.is_symlink() or not path.is_file():
        raise ConfigError("provider subbots profile must be a regular file")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("provider subbots profile is unreadable JSON") from None
    return validate_provider_subbots_profile(raw)


def _local_execution(result: dict) -> CaseExecution:
    observed = result.get("child_receipt", {}).get("observed_checks", 0)
    checked = set(result.get("checks", {}).get("checked", []))
    passed = (
        result.get("status") == LOCAL_STATUS
        and result.get("checks", {}).get("status") == "PASS"
        and observed == STORY_CHECKS
        and checked == set(REQUIREMENTS)
    )
    error_type = None if passed else result.get("error_type") or "LOCAL_PROVIDER_SUBBOTS_BLOCKED"
    steps = []
    for index, requirement_id in enumerate(REQUIREMENTS):
        step_passed = passed and requirement_id in checked
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method=_CASE_METHOD,
            status="PASS" if step_passed else "NOT_RUN",
            data={"source_case_id": SOURCE_CASE_ID} if step_passed else None,
            evidence={
                "story_checks": observed,
                "assertion_scope": "FROZEN_CASE_LEVEL_CHILD",
                "source_case_id": SOURCE_CASE_ID,
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            } if step_passed else {},
            error_type=None if passed else (error_type if index == 0 else None),
        ))
    return CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status="PASS" if passed else "BLOCKED",
        steps=tuple(steps),
        completed_count=len(REQUIREMENTS) if passed else 0,
        total_count=len(REQUIREMENTS),
        error_type=None if passed else error_type,
        ready_to_execute=passed,
    )


def _preprod_execution(probe: dict) -> CaseExecution:
    error_type = (
        None if probe.get("status") == "PASS"
        else probe.get("error_type") or "PREPROD_PROVIDER_SUBBOTS_PROBE_BLOCKED"
    )
    # Even a passing route-surface probe does not execute the 131-check story.
    full_error = "PREPROD_FULL_STORY_NOT_EXECUTED"
    steps = tuple(
        StepOutcome(
            requirement_id=requirement_id,
            method=_CASE_METHOD,
            status="NOT_RUN",
            data=None,
            evidence={
                "probe_status": probe.get("status"),
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            },
            error_type=error_type if index == 0 else None,
        )
        for index, requirement_id in enumerate(REQUIREMENTS)
    )
    return CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status="BLOCKED",
        steps=steps,
        completed_count=0,
        total_count=len(REQUIREMENTS),
        error_type=error_type or full_error,
        ready_to_execute=False,
    )


class ProviderSubbotsProbeClient:
    """Bounded wrong-method probe client; it never stores credentials."""

    def __init__(self, profile: dict):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], "preprod").rstrip("/")
        self.browser = origin(profile["browser_origin"], "preprod")
        self.cookie = _bounded_env(profile["auth"]["cookie_env"], "auth.cookie_env")
        self.bot_token = _bounded_env(
            profile["auth"]["probe_bot_token_env"], "auth.probe_bot_token_env"
        )
        self.opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=tls_context()), _NoRedirect()
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self.cookie,
            "Origin": self.browser,
            "Referer": self.browser + "/",
            "Accept": "application/json",
        }

    def request(self, *, method: str, path: str, bearer: bool = False) -> dict:
        headers = self._headers()
        if bearer:
            headers["Authorization"] = "Bearer " + self.bot_token
        request = Request(self.base + path, method=method, headers=headers)
        try:
            try:
                response = self.opener.open(
                    request, timeout=self.profile["policy"]["timeout_seconds"]
                )
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise RunStateError("provider subbots probe response exceeded its byte budget")
                return {
                    "method": method,
                    "path": path,
                    "http_status": status,
                    "response_bytes": len(raw),
                    "authorization_sent": bearer,
                    "cookie_sent": True,
                }
        except (OSError, URLError, ValueError):
            raise RunStateError("provider subbots probe transport failed") from None


def run_preprod_route_probe(profile: dict, *, client: object | None = None) -> dict:
    """Probe the reviewed preprod paths with wrong methods; no writes occur."""
    client = ProviderSubbotsProbeClient(profile) if client is None else client
    baseline = client.request(method="GET", path=_BASELINE_PATH)
    if baseline["http_status"] != _ABSENT_STATUS:
        return {
            "status": "BLOCKED",
            "error_type": "PREPROD_PROBE_BASELINE_UNEXPECTED",
            "baseline": baseline,
            "checks": [],
            "present_count": 0,
            "absent_count": 0,
            "required_count": len(_PROBE_ROWS),
            "credential_kind": "preprod-human-cookie",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
    checks = []
    for row in _PROBE_ROWS:
        bearer = row["caller"] in {"provider_admin", "bot_runtime", "signed_principal"}
        record = client.request(
            method=row["probe_method"], path=_probe_path(row["path"]), bearer=bearer
        )
        status = record["http_status"]
        present = status in _PRESENT_PROBE_STATUSES
        absent = status == _ABSENT_STATUS
        record.update({
            "operation": row["operation"],
            "canonical_method": row["canonical_method"],
            "probe_method": row["probe_method"],
            "expectation": row["expectation"],
            "route_present": present,
            "route_absent": absent,
        })
        checks.append(record)
    present_count = sum(row["route_present"] for row in checks)
    absent_count = sum(row["route_absent"] for row in checks)
    passed = present_count == len(_PROBE_ROWS) and absent_count == 0
    return {
        "status": "PASS" if passed else "BLOCKED",
        "error_type": None if passed else "PREPROD_PROVIDER_SUBBOTS_ROUTE_ABSENT",
        "baseline": baseline,
        "checks": checks,
        "present_count": present_count,
        "absent_count": absent_count,
        "required_count": len(_PROBE_ROWS),
        "credential_kind": "preprod-human-cookie",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def _assertion_summary(result: dict | None, probe: dict, density: dict) -> dict:
    observed = 0
    if isinstance(result, dict):
        observed = result.get("child_receipt", {}).get("observed_checks", 0)
    return {
        "full_story_required_assertion_count": STORY_CHECKS,
        "full_story_observed_assertion_count": observed,
        "obligation_count": len(REQUIREMENTS),
        "preprod_probe_required_count": probe.get("required_count", 0),
        "preprod_probe_observed_count": len(probe.get("checks", [])),
        "density_leaf_total": density["leaves_total"],
        "density_leaf_pass_count": density["counts"]["pass"],
        "density_leaf_not_run_count": density["counts"]["not_run"],
        "density_counting_rule": (
            "The 131 density leaves equal the frozen child receipt denominator; local PASS "
            "flows only through the frozen child receipt collective, while preprod and any "
            "partial or failing receipt stay honestly NOT_RUN."
        ),
        "counting_rule": (
            "The frozen child count is 131 live HTTP assertions; every observed "
            "count is taken from the actual child receipt or probe response list."
        ),
    }


def dual_provider_subbots_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
    binary: Path | None = None,
    binary_sha256: str | None = None,
) -> dict:
    """Run one Provider-subbots case through the owned-local or preprod leg."""
    if case_key not in CASE_KEYS:
        raise ConfigError("provider subbots case must be provider-subbots")
    run_id = identifier(run_id, "provider subbots run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "provider subbots requires explicit write permission and exact Run confirmation"
        )
    profile = load_provider_subbots_profile(profile_path, case_key)
    require_resolved(profile)
    tier = profile["environment"]["tier"]
    if tier == "local":
        if binary is None or binary_sha256 is None:
            raise ConfigError("local Provider subbots require --binary and --binary-sha256")
    elif binary is not None or binary_sha256 is not None:
        raise ConfigError("preprod Provider subbots must not receive an owned BCS binary")

    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute()
        or requested_state_dir.is_symlink()
        or requested_state_dir.exists()
        or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError("provider subbots requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("provider subbots requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / f"provider-subbots-{case_key}-profile.json"
    binding_file = state_dir / f"provider-subbots-{case_key}-binding.json"
    report_file = state_dir / f"provider-subbots-{case_key}-run.json"
    write(profile_file, profile)
    source_binding = provider_subbots_source_binding()
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "suite": SUITE,
        "source_suite": SOURCE_SUITE,
        "case_id": CASE_ID,
        "source_case_id": SOURCE_CASE_ID,
        "parent_id": PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "source_binding_digest": fingerprint(source_binding),
        "density_contract": DENSITY_CONTRACT_ID,
        "density_contract_digest": density_contract_digest(),
        "density_leaves": DENSITY_LEAVES,
        "requirement_ids": list(REQUIREMENTS),
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)

    local_result = None
    probe = {}
    try:
        if tier == "local":
            local_result = local_provider_subbots_run(
                binary=Path(binary),
                binary_sha256=binary_sha256,
                state_dir=state_dir / "owned-provider-subbots",
                run_id=run_id,
                confirm_run_id=run_id,
                allow_local_processes=True,
                allow_fixture_write=True,
                allow_write=True,
            )
            execution = _local_execution(local_result)
        else:
            probe = run_preprod_route_probe(profile)
            execution = _preprod_execution(probe)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"provider subbots {case_key} case execution is unavailable: {error}"
        ) from None

    density_projection = build_case_density_projection(tier, local_result, probe)
    status = "PASS" if execution.status == "PASS" else "BLOCKED"
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "source_case_id": SOURCE_CASE_ID,
        "parent_id": PARENT,
        "run_id": run_id,
        "status": status,
        "profile_digest": digest,
        "executor_digest": artifact,
        "source_binding_digest": binding["source_binding_digest"],
        "state_dir": str(state_dir),
        "environment": {
            "tier": tier,
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "obligation_summary": {
            "total": len(REQUIREMENTS),
            "requirement_ids": list(REQUIREMENTS),
            "story_checks": STORY_CHECKS,
            "source_case_id": SOURCE_CASE_ID,
        },
        "assertion_summary": _assertion_summary(local_result, probe, density_projection),
        "density_projection": density_projection,
        "preprod_probe": probe or None,
        "case": execution.snapshot(),
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
        },
        "resource_disposition": FIXTURE_EFFECT,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": (
            "owned_local_ephemeral_principal"
            if tier == "local"
            else "preprod_human_cookie_route_probe"
        ),
    }
    if local_result is not None:
        report["local_owned_run"] = {
            "status": local_result.get("status"),
            "child_receipt": local_result.get("child_receipt"),
            "checks": local_result.get("checks", {}).get("status"),
            "canonical_summary_status": local_result.get("canonical_summary", {}).get("status"),
            "state_dir": str(state_dir / "owned-provider-subbots"),
        }
    write(report_file, report)
    return report

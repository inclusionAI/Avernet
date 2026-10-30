"""Receipt lifecycle for the nine reviewed Edge friend-mutation cases."""
from __future__ import annotations

from pathlib import Path

from .case_executor import execute_case
from .edge_friend_mutation_case import CASE_IDS, edge_friend_mutation_case_plan
from .inventory import executor_digest
from .profile import ConfigError, fingerprint, identifier, load
from .remote_edge_friend_mutation_adapter import RemoteEdgeFriendMutationAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-edge-friend-mutation-case-run-v1"
CREDENTIAL_KIND = "human_cookie+registered_bot_token_presence"
MUTATION_RELATIONSHIP = "bounded-owned-bot-mutations"
_FAILURE_DISPOSITION = "UNKNOWN_OWNED_BOT_MUTATIONS"

_CASE_KEY_BY_ID = {
    case_id.removeprefix("LOCAL-EDGE-").removesuffix("-01").lower(): case_id
    for case_id in CASE_IDS
}
CASE_KEYS = tuple(_CASE_KEY_BY_ID)

_PUBLIC_OPERATIONS = {
    "friend-accept-reject-cancel": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.accept",
        "friend_connection_mutation.reject",
        "friend_connection_mutation.cancel",
        "friend_connection_mutation.requests_sent.read",
        "friend_connection_mutation.requests_received.read",
        "friend_connection_mutation.connections.read",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.station_admission.read",
    ),
    "friend-revoke": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.accept",
        "friend_connection_mutation.requests_sent.read",
        "friend_connection_mutation.requests_received.read",
        "friend_connection_mutation.connections.read",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.revoke",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.legacy_revoke",
        "friend_connection_mutation.station_admission.read",
    ),
    "friend-full-lifecycle": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.accept",
        "friend_connection_mutation.requests_sent.read",
        "friend_connection_mutation.requests_received.read",
        "friend_connection_mutation.requests_all.read",
        "friend_connection_mutation.connections.read",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.legacy_revoke",
        "friend_connection_mutation.station_admission.read",
    ),
    "friend-mutual-auto-approve": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.accept",
        "friend_connection_mutation.requests_sent.read",
        "friend_connection_mutation.requests_received.read",
        "friend_connection_mutation.connections.read",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.station_admission.read",
    ),
    "error-admission-branches": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.accept",
        "friend_connection_mutation.requests_sent.read",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.station_admission.read",
    ),
    "connect-private-discover": (
        "friend_connection_mutation.create",
        "friend_connection_mutation.bot_detail.read",
        "friend_connection_mutation.visibility.write",
        "friend_connection_mutation.discover_denied.read",
        "friend_connection_mutation.register_token.read",
        "friend_connection_mutation.register_bot.create",
        "friend_connection_mutation.station_admission.read",
        "friend_connection_mutation.station_discover_collaborate.read",
        "friend_connection_mutation.station_discover_role_no_org.read",
        "friend_connection_mutation.station_discover_org_no_requester.read",
    ),
    "ensure-bot": (),
    "tc-friend-auth-sync": (),
    "dept-scope-friend-ext": () ,
}

_RESOURCE_DISPOSITIONS = {
    "friend-accept-reject-cancel": "REGISTERED_BOTS_RETAINED",
    "friend-revoke": "REGISTERED_BOT_RETAINED;EDGES_REVOKED;FRESH_MUTUAL_PENDING_RETAINED",
    "friend-full-lifecycle": "REGISTERED_BOT_RETAINED;VISIBILITY_RESTORED_PUBLIC;FRESH_MUTUAL_PENDING_RETAINED",
    "friend-mutual-auto-approve": "REGISTERED_BOT_RETAINED;BOTH_EDGES_RETAINED",
    "error-admission-branches": "REGISTERED_BOT_RETAINED",
    "connect-private-discover": "REGISTERED_BOT_RETAINED;PRIVATE_MEMBER_RESTORED_PUBLIC",
    "ensure-bot": "PREPROD_SERVICE_SCOPE_GAP",
    "tc-friend-auth-sync": "PREPROD_SERVICE_SCOPE_GAP",
    "dept-scope-friend-ext": "PREPROD_SERVICE_SCOPE_GAP",
}

PRODUCT_DEFECTS = {
    "PD-PRIVATE-CREATE-500": {
        "observed": "Gateway private-target create returns HTTP 500 code 50000 data.error_code=internal_error.",
        "shell_expectation": "HTTP 403 private-target denial.",
    },
    "PD-GATEWAY-REVOKE-ASYMMETRY": {
        "observed": "Gateway DELETE uses caller_default_actor, ignores act-as, and returns deleted=false.",
        "station_closure": "Station legacy DELETE with Human Cookie plus owned-Bot Bearer can revoke both edges.",
    },
    "PD-DISCOVER-BRANCH-DRIFT": {
        "role_no_org": "Real station returns 400 invalid_operation with params.message=role_requires_organization_code.",
        "organization_no_requester": "Real station returns 403 forbidden with error=organization discovery requires a bot caller.",
    },
}

DOCUMENTED_GAPS = {
    "GATEWAY_HUMAN_DISCOVER_401": "Gateway human-Cookie bot discovery is rejected on the public face (observed 401 code 401000; 422 code 422000 after cookie refresh); discovery is a station bot-only surface here.",
    "PREPROD_SERVICE_SCOPE_GAP": "ensure-bot, TC friend-auth sync, and department-scope extensions need a user-credential/mock-Provider batch unavailable in preprod.",
}


def load_edge_friend_mutation_case_profile(path: Path) -> dict:
    profile = load(path)
    entrypoint = profile.get("entrypoint", {})
    environment = profile.get("environment", {})
    gateway_admitted = (
        entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
    ) or (
        entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
    )
    if not gateway_admitted or entrypoint.get("api_face") != "public-v1":
        raise ConfigError(
            "Edge friend-mutation gateway profile must be public-v1 preprod Gateway or local Singlebox")
    if profile.get("relationships", {}).get("friendship") != MUTATION_RELATIONSHIP:
        raise ConfigError(
            "Edge friend-mutation gateway profile requires bounded-owned-bot-mutations admission")
    if not profile.get("browser_origin"):
        raise ConfigError("Edge friend-mutation gateway profile requires browser_origin")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "Edge friend-mutation gateway profile must bind only the Human Cookie")
    if not isinstance(profile.get("actors"), dict) or not {"driver", "member"} <= set(profile["actors"]):
        raise ConfigError("Edge friend-mutation gateway profile requires its dedicated actors")
    return profile


def _load_station_profile(path: Path, gateway_profile: dict) -> dict:
    profile = load(path)
    entrypoint = profile.get("entrypoint", {})
    if entrypoint.get("kind") != "bcs-direct":
        raise ConfigError("Edge friend-mutation station profile must use bcs-direct")
    if profile.get("relationships", {}).get("friendship") != MUTATION_RELATIONSHIP:
        raise ConfigError(
            "Edge friend-mutation station profile requires bounded-owned-bot-mutations admission")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" not in headers:
        raise ConfigError(
            "Edge friend-mutation station profile requires Human Cookie and owned-Bot Authorization references")
    if profile.get("actors") != gateway_profile.get("actors"):
        raise ConfigError("Edge friend-mutation profiles must declare identical actors")
    return profile


def _singlebox_closure_gap(execution: object) -> bool:
    """True when every blocking step is a reviewed singlebox-closure gap."""
    gap_steps = [step for step in execution.steps if step.error_gap]
    return bool(gap_steps) and all(
        isinstance(step.error_gap, dict) and step.error_gap.get("singlebox_closure") is True
        for step in gap_steps
    )


def normalize_edge_friend_mutation_case_key(value: str) -> str:
    if value in _CASE_KEY_BY_ID:
        return value
    lowered = value.lower()
    if lowered in _CASE_KEY_BY_ID:
        return lowered
    if value.upper() in CASE_IDS:
        return next(
            key for key, case_id in _CASE_KEY_BY_ID.items() if case_id == value.upper()
        )
    raise ConfigError("Edge friend-mutation case must select one of the nine reviewed cases")


def _environment_slice(profile: dict) -> dict:
    return {
        "tier": profile["environment"]["tier"],
        "id": profile["environment"]["id"],
        "entrypoint_kind": profile["entrypoint"]["kind"],
        "api_face": profile["entrypoint"]["api_face"],
    }


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (
        not requested.is_absolute()
        or requested.is_symlink()
        or requested.exists()
        or not requested.parent.is_dir()
        or requested.parent.is_symlink()
    ):
        raise ConfigError("Edge friend-mutation case requires a fresh private state directory")
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("Edge friend-mutation case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def dual_edge_friend_mutation_case_run(
    *,
    profile_path: Path,
    station_profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    key = normalize_edge_friend_mutation_case_key(case_key)
    case_id = _CASE_KEY_BY_ID[key]
    run_id = identifier(run_id, "Edge friend-mutation run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "Edge friend-mutation case requires explicit write permission and exact Run confirmation")
    gateway_profile = load_edge_friend_mutation_case_profile(profile_path)
    station_profile = _load_station_profile(station_profile_path, gateway_profile)
    gateway_digest = fingerprint(gateway_profile)
    station_digest = fingerprint(station_profile)
    artifact = executor_digest()
    plan = edge_friend_mutation_case_plan(case_id)
    requirement_ids = list(plan.requirement_ids)
    public_operations = list(_PUBLIC_OPERATIONS[key])
    planned_disposition = _RESOURCE_DISPOSITIONS[key]
    state_dir = _fresh_state_dir(state_dir)

    profile_file = state_dir / "edge-friend-mutation-profile.json"
    binding_file = state_dir / "edge-friend-mutation-binding.json"
    report_file = state_dir / "edge-friend-mutation-run.json"
    write(profile_file, {
        "schema_version": SCHEMA_VERSION,
        "gateway": gateway_profile,
        "station": station_profile,
    })
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": case_id,
        "parent_id": plan.parent_id,
        "run_id": run_id,
        "profiles": {"gateway_digest": gateway_digest, "station_digest": station_digest},
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "planned_resource_disposition": planned_disposition,
        "case_scope": {
            "requirement_ids": requirement_ids,
            "public_operations": public_operations,
        },
        "product_defects": PRODUCT_DEFECTS,
        "documented_gaps": DOCUMENTED_GAPS,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteEdgeFriendMutationAdapter(gateway_profile, station_profile)
        execution = execute_case(plan, adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"Edge friend-mutation case execution is unavailable: {error}") from None

    success = execution.status == "PASS"
    if success:
        disposition = planned_disposition
    elif _singlebox_closure_gap(execution):
        # A case blocked solely by a reviewed, documented preprod service scope
        # gap never mutates the shared fixture; its receipt keeps the planned
        # gap disposition instead of the conservative unknown-mutations label.
        disposition = planned_disposition
    else:
        disposition = _FAILURE_DISPOSITION
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": case_id,
        "parent_id": plan.parent_id,
        "run_id": run_id,
        "status": "PASS" if success else "BLOCKED",
        "profiles": {"gateway_digest": gateway_digest, "station_digest": station_digest},
        "executor_digest": artifact,
        "environment": {
            "gateway": _environment_slice(gateway_profile),
            "station": _environment_slice(station_profile),
        },
        "case": execution.snapshot(),
        "case_scope": {
            "requirement_ids": requirement_ids,
            "public_operations": public_operations,
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "credential_kind": CREDENTIAL_KIND,
        "resource_disposition": disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "product_defects": PRODUCT_DEFECTS,
        "documented_gaps": DOCUMENTED_GAPS,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report

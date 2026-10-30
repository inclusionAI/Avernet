"""Dual-environment case runner form for the service-policy density slice.

This module mirrors the reviewed dual-case-runner form (profile admission,
explicit Run confirmation gate, fresh private state directory, binding and
receipt triple) while staying strictly offline: the density projection is
computed from the source-anchored roster against the admitted profile, the
owned-local tier演算s through the deterministic replay fixture, and the
preprod tier fail-closes to the named-gap BLOCKED_INVENTORIED receipt.

The wrapper command ``dual-service-policy-density-run`` is registered by
the mainline serial wave: parser, registration table entry and CLI dispatch
all route to the authoritative handler in this package while the projection
stays strictly offline.

No credential value ever enters a receipt: profiles carry environment
variable NAME references only and are captured by digest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..inventory import executor_digest
from ..profile import (
    ConfigError,
    fingerprint,
    identifier,
    require_resolved,
    unique_keys,
)
from ..run_state import RunStateError, write
from .contract import (
    CASE_ID,
    DENSITY_ASSERTION_COUNT,
    DENSITY_FIELD_COUNT,
    DENSITY_PARENT_COUNT,
    DUAL_SERVICE_POLICY_DENSITY_COMMAND,
    REPORT_CONTRACT,
    service_policy_density_plan,
)
from .profile import validate_service_policy_density_profile
from .run import (
    STATUS_BLOCKED_INVENTORIED,
    STATUS_PASS,
    run_service_policy_density,
)

SCHEMA_VERSION = 1
CREDENTIAL_KIND_LOCAL = "owned_local_bot_token"
CREDENTIAL_KIND_PREPROD = "human_cookie_with_owned_driver_bot_token"
RESOURCE_DISPOSITION = (
    "TWO_DISPOSABLE_GROUPS_THREE_DISPOSABLE_SESSIONS_SIX_LOCAL_DISPOSITIONS_"
    "PREPROD_DRIVER_BOT_FENCED")
REGISTRATION_STATE = "REGISTERED_MAINLINE_SERIAL"


def service_policy_density_credential_kind(profile: dict[str, Any]) -> str:
    """Label the admitted credential family without echoing any secret."""
    return (CREDENTIAL_KIND_LOCAL if profile["environment"]["tier"] == "local"
            else CREDENTIAL_KIND_PREPROD)


def _load_admitted_profile(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("service policy density profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("service policy density profile is unreadable JSON") from None
    return validate_service_policy_density_profile(raw)


def _fresh_private_state_dir(requested: Path) -> Path:
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError(
            "service policy density case requires a fresh private state directory")
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("service policy density case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def dual_service_policy_density_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict[str, Any]:
    """Execute the offline forty-point density projection and write its receipt.

    The Run confirmation gate is enforced before any filesystem write: the
    caller must pass explicit write permission and the exact run id, matching
    the reviewed dual-case posture.  Execution itself stays offline for both
    tiers (owned-local replay演算 locally; preprod fail-closes to the named
    gap), so no socket is opened from this wrapper.
    """
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "service policy density case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "service policy density run ID")
    profile = _load_admitted_profile(Path(profile_path))
    require_resolved(profile)
    plan = service_policy_density_plan()
    profile_digest = fingerprint(profile)
    artifact = executor_digest()
    state_dir = _fresh_private_state_dir(Path(state_dir))
    credential_kind = service_policy_density_credential_kind(profile)

    profile_file = state_dir / "service-policy-density-profile.json"
    binding_file = state_dir / "service-policy-density-binding.json"
    report_file = state_dir / "service-policy-density-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": plan["parent"],
        "run_id": run_id,
        "command": DUAL_SERVICE_POLICY_DENSITY_COMMAND,
        "registration": REGISTRATION_STATE,
        "profile_digest": profile_digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "planned_resource_disposition": RESOURCE_DISPOSITION,
        "case_scope": {
            "parent_obligation_count": DENSITY_PARENT_COUNT,
            "field_obligation_count": DENSITY_FIELD_COUNT,
            "expected_obligation_count": DENSITY_ASSERTION_COUNT,
        },
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
            "api_face": profile["entrypoint"]["api_face"],
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)

    try:
        execution = run_service_policy_density(profile=profile)
    except (RunStateError, OSError) as error:
        raise ConfigError(
            f"service policy density case execution is unavailable: {error}") from None

    projected_status = execution["status"]
    if projected_status == STATUS_PASS:
        status = STATUS_PASS
    elif projected_status == STATUS_BLOCKED_INVENTORIED:
        status = STATUS_BLOCKED_INVENTORIED
    else:
        status = "BLOCKED"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": plan["parent"],
        "run_id": run_id,
        "command": DUAL_SERVICE_POLICY_DENSITY_COMMAND,
        "registration": REGISTRATION_STATE,
        "status": status,
        "profile_digest": profile_digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "environment": binding["environment"],
        "case_scope": binding["case_scope"],
        "execution": execution,
        "resource_disposition": RESOURCE_DISPOSITION,
        "remote_validation": execution["remote_validation"],
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    if "gap" in execution:
        report["gap"] = execution["gap"]
    write(report_file, report)
    return report

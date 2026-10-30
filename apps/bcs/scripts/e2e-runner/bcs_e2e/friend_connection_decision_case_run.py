"""Command lifecycle for the reviewed public friend-connection decisions.

This slice intentionally leaves the bounded rejection and acceptance evidence in place.
It does not create a second friend surface or silently turn the retained effect into a
cleanup completion."""
from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_friend_connection_decision_contract import (
    CASE_ID,
    FRIENDSHIP_PARENT,
    PUBLIC_OPERATIONS,
    RESOURCE_DISPOSITION,
    execute_friend_connection_decision_case,
    friend_connection_decision_adaptability,
    friend_connection_decision_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_friend_connection_decision_adapter import RemoteFriendConnectionDecisionAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-friend-connection-decision-case-run-v1"
CREDENTIAL_KIND = "human_cookie"

BINDING_KEYS = frozenset(dict.fromkeys(PUBLIC_OPERATIONS))


def load_friend_connection_decision_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("friend-connection decision profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "friend-connection decision profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "friend-connection decision profile must be Cookie-bound and must not carry a Bot Authorization reference")
    relationships = profile.get("relationships", {}).get("friendship")
    if relationships != "bounded-owned-bot-decisions":
        raise ConfigError(
            "friend-connection decision profile must explicitly admit only bounded owned-Bot decisions")
    return profile


def dual_friend_connection_decision_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "friend-connection decision case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "friend-connection decision run ID")
    profile = load_friend_connection_decision_case_profile(profile_path)
    plan = friend_connection_decision_case_plan()
    adaptability = friend_connection_decision_adaptability()
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
        raise ConfigError("friend-connection decision case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("friend-connection decision case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "friend-connection-decision-profile.json"
    binding_file = state_dir / "friend-connection-decision-binding.json"
    report_file = state_dir / "friend-connection-decision-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": FRIENDSHIP_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "planned_resource_disposition": RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_operations": list(dict.fromkeys(PUBLIC_OPERATIONS)),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteFriendConnectionDecisionAdapter(profile)
        execution = execute_friend_connection_decision_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"friend-connection decision case execution is unavailable: {error}") from None

    success = execution.status == "PASS"
    disposition = RESOURCE_DISPOSITION if success else "UNKNOWN_OWNED_BOT_DECISIONS"
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": FRIENDSHIP_PARENT,
        "run_id": run_id,
        "status": "PASS" if success else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_operations": list(dict.fromkeys(PUBLIC_OPERATIONS)),
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report

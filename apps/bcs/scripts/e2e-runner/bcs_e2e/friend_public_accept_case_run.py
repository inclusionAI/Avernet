"""Command lifecycle for the reviewed public friend auto-acceptance slice.

This slice creates one exact owned-Bot friendship and then reads it back. It does
not silently remove the friendship and it does not broaden the effect admission."""
from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_friend_public_accept_contract import (
    CASE_ID,
    FRIENDSHIP_PARENT,
    friend_public_accept_adaptability,
    friend_public_accept_case_plan,
    execute_friend_public_accept_case,
    RESOURCE_DISPOSITION,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_friend_public_accept_adapter import RemoteFriendPublicAcceptAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-friend-public-accept-case-run-v1"
CREDENTIAL_KIND = "human_cookie"
BINDING_KEYS = frozenset(
    {
        "friend_connection_public_accept.create",
        "friend_connection_public_accept.driver_connections.read",
    }
)


def load_friend_public_accept_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("friend public acceptance profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "friend public acceptance profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "friend public acceptance profile must be Cookie-bound and must not carry a Bot Authorization reference")
    relationships = profile.get("relationships", {}).get("friendship")
    if relationships != "bounded-owned-bot-public-acceptance":
        raise ConfigError(
            "friend public acceptance profile must explicitly admit only bounded owned-Bot public acceptance")
    return profile


def dual_friend_public_accept_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "friend public acceptance case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "friend public acceptance run ID")
    profile = load_friend_public_accept_case_profile(profile_path)
    plan = friend_public_accept_case_plan()
    adaptability = friend_public_accept_adaptability()
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
        raise ConfigError("friend public acceptance case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("friend public acceptance case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "friend-public-accept-profile.json"
    binding_file = state_dir / "friend-public-accept-binding.json"
    report_file = state_dir / "friend-public-accept-run.json"
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
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteFriendPublicAcceptAdapter(profile)
        execution = execute_friend_public_accept_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"friend public acceptance case execution is unavailable: {error}") from None

    success = execution.status == "PASS"
    disposition = RESOURCE_DISPOSITION if success else "UNKNOWN_OWNED_BOT_PUBLIC_ACCEPTANCE"
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

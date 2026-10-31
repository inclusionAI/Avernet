"""Command entry for the reviewed dual Session product-CLI cases."""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .local_session_contract import PARENT
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .session_cli_profile import (
    validate_session_cli_collection_profile,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_create_get_profile,
    validate_session_cli_lifecycle_profile,
)
from .session_cli_shared import RemoteSessionCliInviteJoinAdapter
from .session_cli_shared import RemoteSessionCliLifecycleAdapter
from .session_cli_shared import SESSION_CLI_INVITE_JOIN_CASE_ID, SESSION_CLI_INVITE_JOIN_EFFECT
from .session_cli_shared import SESSION_CLI_LIFECYCLE_CASE_ID, SESSION_CLI_LIFECYCLE_EFFECT
from .session_cli_shared import execute_session_cli_invite_join_case
from .session_cli_shared import execute_session_cli_lifecycle_case
from .session_cli_complete_invoke import RemoteSessionCliCompleteInvokeAdapter
from .session_cli_complete_invoke import SESSION_CLI_COMPLETE_INVOKE_CASE_ID, SESSION_CLI_COMPLETE_INVOKE_EFFECT
from .session_cli_complete_invoke import execute_session_cli_complete_invoke_case
from .session_cli_profile import SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT
from .session_cli_collection import RemoteSessionCliCollectionAdapter
from .session_cli_collection import SESSION_CLI_COLLECTION_CASE_ID, SESSION_CLI_COLLECTION_EFFECT
from .session_cli_collection import execute_session_cli_collection_case
from .session_file_core_contract import RemoteSessionCliBotGroupsAdapter, RemoteSessionCliCreateGetAdapter
from .session_file_core_contract import SESSION_CLI_BOT_GROUPS_CASE_ID, FINAL_EFFECT
from .session_file_core_contract import SESSION_CLI_CREATE_GET_CASE_ID
from .session_file_core_contract import execute_session_cli_bot_groups_case
from .session_file_core_contract import execute_session_cli_create_get_case
from .workspace_cli_commands import WorkspaceCliRunnerError
from .workspace_cli_group_invite import RemoteSessionCliGroupInviteAdapter
from .workspace_cli_session_invite import RemoteSessionCliSessionInviteAdapter
from .workspace_cli_group_invite import (
    SESSION_CLI_GROUP_INVITE_CASE_ID,
    SESSION_CLI_GROUP_INVITE_EFFECT,
)
from .workspace_cli_session_invite import (
    SESSION_CLI_SESSION_INVITE_CASE_ID,
    SESSION_CLI_SESSION_INVITE_EFFECT,
)
from .workspace_cli_group_invite import execute_session_cli_group_invite_case
from .workspace_cli_session_invite import execute_session_cli_session_invite_case
from .workspace_cli_runner import WorkspaceCliRunner

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-session-cli-case-run-v1"
SESSION_PARENT = PARENT
CASE_KEYS = (
    "create-get",
    "bot-groups",
    "group-invite",
    "session-invite",
    "invite-join",
    "lifecycle",
    "complete-invoke",
    "collection",
)

_CASES = {
    "create-get": {
        "case_id": SESSION_CLI_CREATE_GET_CASE_ID,
        "validator": validate_session_cli_create_get_profile,
        "adapter": RemoteSessionCliCreateGetAdapter,
        "execute": execute_session_cli_create_get_case,
        "fixture_effect": FINAL_EFFECT,
    },
    "bot-groups": {
        "case_id": SESSION_CLI_BOT_GROUPS_CASE_ID,
        "validator": validate_session_cli_create_get_profile,
        "adapter": RemoteSessionCliBotGroupsAdapter,
        "execute": execute_session_cli_bot_groups_case,
        "fixture_effect": FINAL_EFFECT,
    },
    "group-invite": {
        "case_id": SESSION_CLI_GROUP_INVITE_CASE_ID,
        "validator": validate_session_cli_create_get_profile,
        "adapter": RemoteSessionCliGroupInviteAdapter,
        "execute": execute_session_cli_group_invite_case,
        "fixture_effect": SESSION_CLI_GROUP_INVITE_EFFECT,
    },
    "session-invite": {
        "case_id": SESSION_CLI_SESSION_INVITE_CASE_ID,
        "validator": validate_session_cli_create_get_profile,
        "adapter": RemoteSessionCliSessionInviteAdapter,
        "execute": execute_session_cli_session_invite_case,
        "fixture_effect": SESSION_CLI_SESSION_INVITE_EFFECT,
    },
    "invite-join": {
        "case_id": SESSION_CLI_INVITE_JOIN_CASE_ID,
        "validator": validate_session_cli_create_get_profile,
        "adapter": RemoteSessionCliInviteJoinAdapter,
        "execute": execute_session_cli_invite_join_case,
        "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
    },
    "lifecycle": {
        "case_id": SESSION_CLI_LIFECYCLE_CASE_ID,
        "validator": validate_session_cli_lifecycle_profile,
        "adapter": RemoteSessionCliLifecycleAdapter,
        "execute": execute_session_cli_lifecycle_case,
        "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
    },
    "complete-invoke": {
        "case_id": SESSION_CLI_COMPLETE_INVOKE_CASE_ID,
        "validator": validate_session_cli_complete_invoke_profile,
        "adapter": RemoteSessionCliCompleteInvokeAdapter,
        "execute": execute_session_cli_complete_invoke_case,
        "fixture_effect": SESSION_CLI_COMPLETE_INVOKE_EFFECT,
    },
    "collection": {
        "case_id": SESSION_CLI_COLLECTION_CASE_ID,
        "validator": validate_session_cli_collection_profile,
        "adapter": RemoteSessionCliCollectionAdapter,
        "execute": execute_session_cli_collection_case,
        "fixture_effect": SESSION_CLI_COLLECTION_EFFECT,
    },
}


def load_session_cli_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "session_case must be create-get, bot-groups, group-invite, session-invite, "
            "invite-join, lifecycle, complete-invoke or collection")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"session {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(f"session {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def _receipt_credential_kind(profile: dict) -> str:
    """Label the preprod credential family without echoing any secret."""
    if profile["environment"]["tier"] == "local":
        return "owned_local_bot_token"
    if (profile["contract"] == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT
            and profile["auth"]["kind"] == "human-cookie-with-owned-bot-tokens"):
        return "human_cookie_with_owned_bot_tokens"
    return "human_cookie"


def dual_session_cli_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "session_case must be create-get, bot-groups, group-invite, session-invite, "
            "invite-join, lifecycle, complete-invoke or collection")
    run_id = identifier(run_id, f"session {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"session {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_session_cli_case_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(f"session {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"session {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    profile_file = state_dir / f"session-{case_key}-profile.json"
    binding_file = state_dir / f"session-{case_key}-binding.json"
    report_file = state_dir / f"session-{case_key}-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"],
        "parent_id": SESSION_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        runner = WorkspaceCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = _CASES[case_key]["adapter"](profile, runner)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(f"session {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": SESSION_PARENT,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
        },
        "resource_disposition": _CASES[case_key]["fixture_effect"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": _receipt_credential_kind(profile),
    }
    write(report_file, report)
    return report

"""Command entry for the reviewed dual Session-file core case."""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .session_file_core_contract import (
    CASE_ID,
    FIXTURE_EFFECT,
    SESSION_FILE_PARENT,
    RemoteSessionFileCoreAdapter,
    execute_session_file_core_case,
)
from .workspace_cli_commands import WorkspaceCliRunnerError
from .workspace_cli_profile import validate_session_file_core_profile
from .workspace_cli_runner import WorkspaceCliRunner

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-session-file-core-case-run-v1"
PROFILE_FILE = "session-file-core-profile.json"
BINDING_FILE = "session-file-core-binding.json"
REPORT_FILE = "session-file-core-run.json"


def load_session_file_core_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("session-file profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("session-file profile is unreadable JSON") from None
    return validate_session_file_core_profile(raw)


def dual_session_file_core_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    run_id = identifier(run_id, "session-file run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "session-file core requires explicit write permission and exact Run confirmation")
    profile = load_session_file_core_case_profile(profile_path)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError("session-file core requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("session-file core requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    write(state_dir / PROFILE_FILE, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": SESSION_FILE_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(state_dir / BINDING_FILE, binding)
    try:
        runner = WorkspaceCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = RemoteSessionFileCoreAdapter(profile, runner)
        execution = execute_session_file_core_case(adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(f"session-file core case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": SESSION_FILE_PARENT,
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
            "error_detail": execution.error_detail,
        },
        "resource_disposition": FIXTURE_EFFECT,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": (
            "owned_local_bot_token" if profile["environment"]["tier"] == "local" else "human_cookie_with_owned_driver_bot_token"
        ),
    }
    write(state_dir / REPORT_FILE, report)
    return report

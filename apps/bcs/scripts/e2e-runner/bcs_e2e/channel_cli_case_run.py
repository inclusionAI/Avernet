"""Command entry for the reviewed dual Channel product-CLI case."""

from __future__ import annotations

from pathlib import Path
import json

from .channel_cli_profile import validate_channel_cli_profile
from .channel_cli_runner import ChannelCliRunner, ChannelCliRunnerError
from .inventory import executor_digest
from .local_channel_cli_contract import PARENT
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .remote_channel_cli_adapter import (
    CHANNEL_CLI_CASE_ID,
    CHANNEL_CLI_EFFECT,
    RemoteChannelCliAdapter,
    execute_channel_cli_case,
)
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-channel-cli-case-run-v1"
PROFILE_FILE = "channel-cli-profile.json"
BINDING_FILE = "channel-cli-binding.json"
REPORT_FILE = "channel-cli-run.json"


def load_channel_cli_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("channel CLI profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("channel CLI profile is unreadable JSON") from None
    return validate_channel_cli_profile(raw)


def dual_channel_cli_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    run_id = identifier(run_id, "channel CLI run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "channel CLI requires explicit write permission and exact Run confirmation")
    profile = load_channel_cli_case_profile(profile_path)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError("channel CLI requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("channel CLI requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    write(state_dir / PROFILE_FILE, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CHANNEL_CLI_CASE_ID,
        "parent_id": PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(state_dir / BINDING_FILE, binding)
    try:
        runner = ChannelCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = RemoteChannelCliAdapter(profile, runner)
        execution = execute_channel_cli_case(adapter)
    except (ConfigError, RunStateError, ChannelCliRunnerError, OSError) as error:
        raise ConfigError(f"channel CLI case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": PARENT,
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
        "resource_disposition": CHANNEL_CLI_EFFECT,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": (
            "owned_local_bot_token" if profile["environment"]["tier"] == "local" else "human_cookie"
        ),
    }
    write(state_dir / REPORT_FILE, report)
    return report

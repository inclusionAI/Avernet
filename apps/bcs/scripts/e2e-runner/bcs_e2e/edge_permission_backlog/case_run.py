"""Dual-environment command entry for the two Edge-permission backlog cases.

The runner is offline by design: it consumes the frozen obligations from the
existing Edge friend-mutation plan and returns a dual receipt that keeps the
local and preprod outcomes separate.  It never turns a local contract into a
remote validation or grants release credit.
"""
from __future__ import annotations

import json
from pathlib import Path

from .case import (
    CASE_KEYS,
    SUITE,
    edge_permission_backlog_case_contract,
)
from ..inventory import executor_digest
from ..profile import ConfigError, identifier
from ..run_state import write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-edge-permission-backlog-case-run-v1"
CREDENTIAL_KIND = "owned-local"  # evolves by profile tier; never stores a value


def _profile_contract() -> dict:
    return {
        "schema_version": 1,
        "contract": "dual-edge-permission-backlog-profile-v1",
        "environment": {"tier", "id", "entrypoint"},
            "policy": {"timeout_seconds", "maximum_response_bytes"},
    }


def load_edge_permission_backlog_profile(path: Path) -> dict:
    """Load and validate one offline dual-case profile without resolving secrets."""
    return _load_profile(path)


def _load_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("Edge-permission backlog profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("Edge-permission backlog profile is unreadable JSON") from None
    if not isinstance(raw, dict):
        raise ConfigError("Edge-permission backlog profile must be a JSON object")
    expected = _profile_contract()
    if set(raw) != set(expected):
        raise ConfigError("Edge-permission backlog profile has an invalid field set")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ConfigError("Edge-permission backlog profile version is unsupported")
    environment = raw["environment"]
    if (not isinstance(environment, dict)
            or set(environment) != {"tier", "id", "entrypoint"}
            or environment.get("tier") not in ("local", "preprod")
            or not isinstance(environment.get("id"), str)
            or not environment["id"].strip()
            or environment.get("entrypoint") not in (
                "singlebox" if environment.get("tier") == "local" else "gateway",
            )):
        raise ConfigError("Edge-permission backlog profile environment is invalid")
    policy = raw["policy"]
    if (not isinstance(policy, dict)
            or set(policy) != expected["policy"]
            or type(policy.get("timeout_seconds")) is not int
            or type(policy.get("maximum_response_bytes")) is not int
            or not 1 <= policy.get("timeout_seconds", 0) <= 60
            or not 1 <= policy.get("maximum_response_bytes", 0) <= 1_048_576):
        raise ConfigError("Edge-permission backlog profile policy is invalid")
    return raw


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink()
            or requested.exists() or not requested.parent.is_dir()
            or requested.parent.is_symlink()):
        raise ConfigError("Edge-permission backlog requires a fresh private state directory")
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("Edge-permission backlog requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def dual_edge_permission_backlog_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    confirm_run_id: str,
    allow_write: bool,
) -> dict:
    profile = _load_profile(profile_path)
    run_id = identifier(run_id, "Edge-permission backlog run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "Edge-permission backlog requires explicit write permission and exact Run confirmation")
    if case_key not in CASE_KEYS:
        raise ConfigError("Edge-permission backlog case must be one of the two reviewed slices")
    case = edge_permission_backlog_case_contract(case_key)
    binding_digest = executor_digest()
    state = _fresh_state_dir(state_dir)
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": case.case_id,
        "parent_id": case.parent_id,
        "case_version": case.case_version,
        "requirement_ids": list(case.requirement_ids),
        "required_assertion_count": case.required_assertion_count,
        "environment": profile["environment"],
        "status": "BLOCKED_INVENTORY",
        "local_status": "LOCAL_MODEL_READY",
        "preprod_status": "NOT_RUN",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": CREDENTIAL_KIND,
        "execution": "offline dual receipt; no local or preprod transport is triggered",
    }
    write(state / "edge-permission-backlog-binding.json", {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "suite": SUITE,
        "case_key": case_key,
        "case_id": case.case_id,
        "parent_id": case.parent_id,
        "run_id": run_id,
        "profile_digest": binding_digest,
        "requirement_ids": list(case.requirement_ids),
        "state_dir": str(state),
    })
    write(state / "edge-permission-backlog-run.json", report)
    return report

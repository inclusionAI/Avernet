"""Offline dual-envelope run lifecycle for the Wave-D3 density overlay.

This module renders one dual envelope per density case key — the
offline-computable owned-local arm and the honest recorded-receipt preprod arm
— without triggering any local or preprod transport.  It follows the shared
runner disciplines:

* explicit write permission plus exact Run confirmation;
* a fresh private 0700 state directory; existing evidence is never overwritten;
* profiles are captured by digest only and the offline profile contract carries
  no auth face, so no credential value can enter the receipt;
* the execution-face registration snapshot is read from the committed
  executor-migration rows through the frozen inventory loader; the row
  annotation lag is delegated to the describe-only proposal in
  :mod:`~bcs_e2e.edge_permission_backlog.density_registration_proposal`;
* degenerate envelope statuses are mapped with a documented priority: an
  owned-local pillar contradiction wins over a preprod receipt contradiction,
  an attached evidence root without any family receipt maps to the blocked
  evidence conflict, and everything else keeps the enrolled-not credit
  envelope status.

The density leaves stay ``NOT_ENROLLED``: the envelope records their anchors,
their seam bindings and their recorded-receipt classification, and it never
grants release credit for obligations the committed registration does not
execute yet.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..inventory import executor_digest, load_executor_migration
from ..profile import ConfigError, fingerprint, identifier
from ..run_state import RunStateError, write
from .density_contract import (
    BINDING_CONTRACT,
    DENSITY_CASE_KEYS,
    DEGENERATE_BLOCKED_EVIDENCE_CONFLICT,
    DEGENERATE_LOCAL_MODEL_CONFLICT,
    DEGENERATE_PREPROD_RECEIPT_DRIFT,
    ENROLLMENT_NOT_ENROLLED,
    ENVELOPE_STATUS,
    LOCAL_MODEL_READY,
    OPTIONAL_COMMAND_REGISTRATION,
    OPTIONAL_DENSITY_COMMAND,
    PREPROD_NOT_RUN,
    REGISTRATION_STATE,
    REMOTE_VALIDATION,
    REPORT_CONTRACT,
    SCHEMA_VERSION,
    SUITE,
    assert_family_alignment,
    density_case_row,
    existing_envelope_projection,
)
from .density_local_arm import density_local_arm_receipt
from .density_obligations import (
    DENSITY_LEAF_COUNT,
    EXISTING_TOTAL,
    PARSED_TOTAL,
    build_density_obligations,
    density_reconciliation,
    source_digest,
)
from .density_preprod_arm import density_preprod_arm_receipt

PROFILE_CONTRACT = "dual-edge-permission-density-profile-v1"
BINDING_FILE = "dual-edge-permission-density-binding.json"
RUN_FILE = "dual-edge-permission-density-run.json"
CREDENTIAL_KIND = "offline_dual_receipt_no_transport"
EXECUTOR_MIGRATION_ANNOTATION_LAG = (
    "the committed executor-migration rows still carry empty gates/notes for "
    "the five density parents; the describe-only row delta citing the r5/r6/r7 "
    "recorded receipts and the wpc owned-local PASS lives in "
    "density_registration_proposal.py and was not applied here")

_PROFILE_TOP_KEYS = {"schema_version", "contract", "environment", "policy"}
_PROFILE_ENV_KEYS = {"tier", "id", "entrypoint"}
_PROFILE_POLICY_KEYS = {"timeout_seconds", "maximum_response_bytes"}
_PROFILE_ENTRYPOINT_BY_TIER = {"local": "singlebox", "preprod": "gateway"}


def validate_density_profile(profile: object) -> dict:
    """Admit one offline envelope profile without resolving any secret."""
    if not isinstance(profile, dict):
        raise ConfigError("edge permission density profile must be a JSON object")
    if set(profile) != _PROFILE_TOP_KEYS:
        raise ConfigError(
            "edge permission density profile has an invalid field set; the "
            "offline contract carries no origin, actors or auth bindings")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != SCHEMA_VERSION:
        raise ConfigError("edge permission density profile version is unsupported")
    if profile["contract"] != PROFILE_CONTRACT:
        raise ConfigError("edge permission density profile contract is unsupported")
    environment = profile["environment"]
    if not isinstance(environment, dict) or set(environment) != _PROFILE_ENV_KEYS:
        raise ConfigError("edge permission density profile environment is invalid")
    tier = environment["tier"]
    if tier not in _PROFILE_ENTRYPOINT_BY_TIER:
        raise ConfigError("edge permission density profile tier is invalid")
    if environment["entrypoint"] != _PROFILE_ENTRYPOINT_BY_TIER[tier]:
        raise ConfigError(
            "edge permission density profile entrypoint does not match its tier")
    if not isinstance(environment["id"], str) or not environment["id"].strip():
        raise ConfigError("edge permission density profile environment id is invalid")
    policy = profile["policy"]
    if not isinstance(policy, dict) or set(policy) != _PROFILE_POLICY_KEYS:
        raise ConfigError("edge permission density profile policy is invalid")
    if type(policy["timeout_seconds"]) is not int or not 1 <= policy["timeout_seconds"] <= 60:
        raise ConfigError("edge permission density profile timeout is out of bounds")
    if (type(policy["maximum_response_bytes"]) is not int
            or not 1 <= policy["maximum_response_bytes"] <= 1_048_576):
        raise ConfigError(
            "edge permission density profile response bound is out of bounds")
    return profile


def load_density_profile(path: Path) -> dict:
    """Load one offline envelope profile from a regular, non-symlink file."""
    requested = Path(path)
    if requested.is_symlink() or not requested.is_file():
        raise ConfigError("edge permission density profile must be a regular file")
    try:
        raw = json.loads(requested.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("edge permission density profile is unreadable JSON") from None
    return validate_density_profile(raw)


def registration_snapshot(case_key: str) -> dict:
    """Project the committed execution-face registration for one density case."""
    row = density_case_row(case_key)
    projection = existing_envelope_projection()
    registered_keys = (projection["read_family"]["case_keys"]
                       + projection["mutation_family"]["case_keys"])
    committed = load_executor_migration()
    status = committed.get(row["parent_id"]) or {}
    return {
        "execution_command": row["run_command"],
        "case_key": case_key,
        "case_key_registered_in_family": case_key in registered_keys,
        "optional_density_command": OPTIONAL_DENSITY_COMMAND,
        "optional_command_registration": OPTIONAL_COMMAND_REGISTRATION,
        "registration_state": REGISTRATION_STATE,
        "executor_migration_row": {
            "id": row["parent_id"],
            "mode": status.get("mode"),
            "preprod_verdict": status.get("preprod_verdict"),
            "gates": list(status.get("gates") or []),
            "notes": list(status.get("notes") or []),
        },
        "annotation_lag": EXECUTOR_MIGRATION_ANNOTATION_LAG,
    }


def degenerate_status(local_arm: dict[str, Any],
                      preprod_arm: dict[str, Any]) -> str:
    """Map the two arms onto one degenerate envelope status, priority fixed."""
    if local_arm.get("status") != LOCAL_MODEL_READY:
        return DEGENERATE_LOCAL_MODEL_CONFLICT
    availability = preprod_arm.get("family_receipt_availability")
    if availability in ("NO_FAMILY_RECEIPTS", "ROOT_UNAVAILABLE"):
        return DEGENERATE_BLOCKED_EVIDENCE_CONFLICT
    if preprod_arm.get("content_conflicts") or availability == "PARTIAL":
        return DEGENERATE_PREPROD_RECEIPT_DRIFT
    return ENVELOPE_STATUS


def frozen_obligation_refs() -> list[dict[str, Any]]:
    """Index the six frozen executed obligations the overlay refines."""
    from ..edge_friend_mutation_case import edge_friend_mutation_case_plan
    from ..edge_read_case import edge_read_case_plans

    refs: list[dict[str, Any]] = []
    for case_key, row in _case_rows().items():
        if row["family_kind"] == "friend_mutation":
            plan = edge_friend_mutation_case_plan(row["case_id"])
            for requirement in plan.requirements:
                refs.append({
                    "case_key": case_key,
                    "case_id": plan.case_id,
                    "requirement_id": requirement.requirement_id,
                    "adapter_method": requirement.adapter_method,
                    "relation": "FROZEN_EXECUTED_UMBRELLA",
                })
        else:
            plans = edge_read_case_plans()
            plan = next(item for item in plans if item.case_id == row["case_id"])
            for requirement_id in plan.requirement_ids:
                refs.append({
                    "case_key": case_key,
                    "case_id": plan.case_id,
                    "requirement_id": requirement_id,
                    "adapter_method": plan.adapters[requirement_id],
                    "relation": "FROZEN_EXECUTED_SLICE",
                })
    return refs


def _case_rows() -> dict[str, dict[str, Any]]:
    return {case_key: density_case_row(case_key) for case_key in DENSITY_CASE_KEYS}


def build_density_envelope(
    *,
    case_key: str,
    profile: dict,
    run_id: str,
    evidence_root: Path | None = None,
) -> dict:
    """Render one offline dual density envelope, fail-closed."""
    row = density_case_row(case_key)
    assert_family_alignment()
    local_arm = density_local_arm_receipt(case_key, evidence_root)
    preprod_arm = density_preprod_arm_receipt(case_key, evidence_root)
    status = degenerate_status(local_arm, preprod_arm)
    leaves = build_density_obligations()
    family_leaves = [item for item in leaves if item.case_key == case_key]
    reconciliation = density_reconciliation()
    environment = profile["environment"]
    conflicts = list(local_arm.get("conflicts") or [])
    conflicts.extend(preprod_arm.get("conflicts") or [])
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "suite": SUITE,
        "case_key": case_key,
        "case_id": row["case_id"],
        "public_case_id": row.get("public_case_id"),
        "parent_id": row["parent_id"],
        "family_kind": row["family_kind"],
        "run_command": row["run_command"],
        "run_id": run_id,
        "status": status,
        "local_status": local_arm["status"],
        "preprod_status": PREPROD_NOT_RUN,
        "remote_validation": REMOTE_VALIDATION,
        "grants_release_credit": False,
        "enrollment": ENROLLMENT_NOT_ENROLLED,
        "credential_kind": CREDENTIAL_KIND,
        "environment": {
            "tier": environment["tier"],
            "id": environment["id"],
            "entrypoint": environment["entrypoint"],
        },
        "profile_digest": fingerprint(profile),
        "executor_digest": executor_digest(),
        "frozen_linkage": {
            "case_key": case_key,
            "case_id": row["case_id"],
            "public_case_id": row.get("public_case_id"),
            "parent_id": row["parent_id"],
            "frozen_requirement_ids": list(row["frozen_requirement_ids"]),
            "seam_methods": list(row["seam_methods"]),
            "frozen_obligation_refs": frozen_obligation_refs(),
        },
        "obligation_roster": {
            "parsed_total": PARSED_TOTAL,
            "frozen_existing_total": EXISTING_TOTAL,
            "density_leaves_total": DENSITY_LEAF_COUNT,
            "projected_superset_total": reconciliation["projected_superset_total"],
            "per_parent": {
                parent: {
                    "parsed": item["parsed"],
                    "frozen_existing_obligations": item["frozen_existing_obligations"],
                    "density_leaves": item["density_leaves"],
                    "projected_superset": item["projected_superset"],
                }
                for parent, item in reconciliation["per_parent"].items()},
            "not_charged_source_sites": reconciliation["not_charged_source_sites"],
            "decomposition_rule": reconciliation["decomposition_rule"],
            "roster_snapshot": reconciliation["roster_snapshot"],
        },
        "density_leaf_anchors": [item.as_dict() for item in family_leaves],
        "family_leaf_index": {
            case_key: [leaf.obligation_id for leaf in leaves
                       if leaf.case_key == case_key]
            for case_key in DENSITY_CASE_KEYS
        },
        "source_digest": source_digest(),
        "existing_envelope_projection": existing_envelope_projection(),
        "local_arm": local_arm,
        "preprod_arm": preprod_arm,
        "registration": registration_snapshot(case_key),
        "conflicts": conflicts,
        "execution": (
            "offline dual envelope; no local and no preprod transport is "
            "triggered, the density leaves stay NOT_ENROLLED, and no local "
            "model projection or recorded preprod row is promoted into "
            "release credit"),
        "state_dir": None,
    }


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink()
            or requested.exists() or not requested.parent.is_dir()
            or requested.parent.is_symlink()):
        raise ConfigError(
            "edge permission density requires a fresh private state directory")
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(
            "edge permission density requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def dual_edge_permission_density_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    confirm_run_id: str,
    allow_write: bool,
    evidence_root: Path | None = None,
) -> dict:
    """Render and persist one offline dual density envelope receipt pair."""
    run_id = identifier(run_id, "Edge permission density run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "edge permission density requires explicit write permission and "
            "exact Run confirmation")
    if case_key not in DENSITY_CASE_KEYS:
        raise RunStateError("unknown edge_permission density case key: " + str(case_key))
    profile = load_density_profile(Path(profile_path))
    if evidence_root is not None:
        root = Path(evidence_root)
        if root.is_symlink() or not root.is_dir():
            raise ConfigError(
                "edge permission density evidence root must be an existing directory")
    state_dir = _fresh_state_dir(Path(state_dir))
    envelope = build_density_envelope(
        case_key=case_key, profile=profile, run_id=run_id,
        evidence_root=evidence_root)
    envelope["state_dir"] = str(state_dir)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": BINDING_CONTRACT,
        "suite": SUITE,
        "case_key": case_key,
        "case_id": envelope["case_id"],
        "public_case_id": envelope["public_case_id"],
        "parent_id": envelope["parent_id"],
        "run_id": run_id,
        "profile_digest": envelope["profile_digest"],
        "executor_digest": envelope["executor_digest"],
        "frozen_requirement_ids": envelope["frozen_linkage"]["frozen_requirement_ids"],
        "density_leaf_ids": [item["obligation_id"]
                             for item in envelope["density_leaf_anchors"]],
        "environment": envelope["environment"],
        "evidence_root": str(evidence_root) if evidence_root is not None else None,
        "state_dir": str(state_dir),
    }
    write(state_dir / BINDING_FILE, binding)
    write(state_dir / RUN_FILE, envelope)
    return envelope

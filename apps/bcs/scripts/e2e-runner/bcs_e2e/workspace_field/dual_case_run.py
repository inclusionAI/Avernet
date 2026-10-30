"""Dual-environment case runner for the Workspace field-density slice.

This is the workspace_field twin of ``structured_density/dual_case_run.py``
for the parent story ``story_user_operates_group_workspace`` (43 obligations
= 17 coarse + 26 FIELD rows over the frozen 63-atom density oracle):

- **local tier**: the offline fake transport replays the retained Workspace
  journals and the 43-obligation projection is executed locally.  The local
  PASS criterion requires the result evidence (63 atoms) and the assertion
  projection (43 obligations) to roll up identically, zero FAIL rows, and a
  full PASS except the one strict ``fuse_error_payload_shape`` obligation,
  which the offline normal-branch replay cannot exercise and which stays an
  honestly inventoried NOT_RUN gap instead of a fabricated PASS.
- **preprod tier**: fail closed.  The leg is admitted by its dual-profile
  contract (Human Cookie + driver Bot token, environment-variable NAME
  references only), then recorded as ``BLOCKED_INVENTORIED`` with the named
  face gap ``PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED``.  All 43
  obligations stay NOT_RUN, ``network_requests`` stays 0, and
  ``grants_release_credit`` stays false.  No remote PASS is ever forged.

The receipt triple — case binding, case report, gap receipt — is written into
a fresh private state directory.  The raw profile is never persisted; only
its SHA-256 digest and its tier/face identity are written, and the auth
blocks reduce to the two admitted environment-variable names.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..group_workspace_density import ATOM_COUNT, load_group_workspace_density_contract
from ..inventory import executor_digest
from ..profile import ConfigError, fingerprint, identifier
from ..run_state import RunStateError, write
from .group_workspace_field_obligations import (
    BASELINE_COUNT,
    CONTRACT_ID,
    FIELD_COUNT,
    OBLIGATION_COUNT,
    PARENT_STORY,
    VALID_STATUS,
    FakeWorkspaceTransport,
    load_workspace_field_contract,
    preprod_not_run_receipt,
    project_workspace_obligations,
    validate_workspace_field_contract,
)
from .profile import (
    LOCAL_TIER,
    PREPROD_TIER,
    credential_refs,
    load_workspace_field_profile,
)

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-workspace-field-case-run-v1"
GAP_CONTRACT = REPORT_CONTRACT + "-gap-v1"
PROFILE_DIGEST_CONTRACT = REPORT_CONTRACT + "-profile-digest-v1"
CASE_ID = "DUAL-WORKSPACE-FIELD-01"

# Wrapper seam for the mainline serial wave: the constant and the handler
# exist so the registration surface can adopt them verbatim, but this wave
# deliberately does not register the command.
DUAL_WORKSPACE_FIELD_COMMAND = "dual-workspace-field-run"

CREDENTIAL_KIND_LOCAL = "owned_local_bot_token_env_reference_only"
CREDENTIAL_KIND_PREPROD = "human_cookie_and_driver_bot_token_env_reference_only"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

FUSE_PREDICATE = "fuse_error_payload_shape"

# Named, inventoried gaps.  Preprod: the dual product/Gateway workspace-field
# faces behind this projection were not inventoried for live execution in this
# wave.  Local: the strict non-zero fuse exit with a structured JSON payload
# branch is not exercised by the offline normal-branch replay.
PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED = (
    "PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED")
LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED = (
    "LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED")
LOCAL_WORKSPACE_FIELD_ASSERTION_FAILURE = "LOCAL_WORKSPACE_FIELD_ASSERTION_FAILURE"
LOCAL_WORKSPACE_FIELD_UNACKNOWLEDGED_NOT_RUN = "LOCAL_WORKSPACE_FIELD_UNACKNOWLEDGED_NOT_RUN"

PREPROD_FACE_GAP_REASON = (
    "the preprod product/Gateway Workspace field faces behind the 43-obligation "
    "projection are not inventoried for live dual execution in this wave; the leg "
    "fail-closes with zero network requests and every obligation stays NOT_RUN "
    "instead of fabricating a remote PASS")
LOCAL_FUSE_GAP_REASON = (
    "the non-zero bcs-cli fuse exit carrying a structured JSON payload branch is "
    "not exercised by the offline fake transport's normal exit; the stricter FIELD "
    "decomposition keeps exactly this obligation honest NOT_RUN instead of rolling "
    "a normal exit up into PASS")

BINDING_FILE = "workspace-field-case-binding.json"
REPORT_FILE = "workspace-field-case-report.json"
GAP_FILE = "workspace-field-case-gaps.json"
PROFILE_DIGEST_FILE = "workspace-field-profile-digest.json"
RAW_PROFILE_FILE = "workspace-field-profile.json"

ATOM_ORACLE_CONTRACT = "group-workspace-density-contract.json"
OFFLINE_EXECUTION_MODE = "OFFLINE_FAKE_TRANSPORT_OWNED_LOCAL_PROJECTION"
LOCAL_SUCCESS_DISPOSITION = "OFFLINE_FAKE_TRANSPORT_NO_LIVE_RESOURCE"
PREPROD_BLOCKED_DISPOSITION = "NO_LIVE_RESOURCE_NO_SOCKET_NO_REMOTE_EXECUTION"


def _gap_descriptor(gap_id: str, obligation_ids: list[str], reason: str) -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "status": STATUS_NOT_RUN,
        "obligation_count": len(obligation_ids),
        "obligation_ids": list(obligation_ids),
        "network_requests": 0,
        "grants_release_credit": False,
        "reason": reason,
    }


def _admit_fresh_state_dir(requested: Path) -> Path:
    if (
        not requested.is_absolute()
        or requested.is_symlink()
        or requested.exists()
        or not requested.parent.is_dir()
        or requested.parent.is_symlink()
    ):
        raise ConfigError("workspace field case requires a fresh private state directory")
    resolved = requested.resolve()
    if resolved.is_symlink() or resolved.exists():
        raise ConfigError("workspace field case requires a fresh private state directory")
    resolved.mkdir(mode=0o700, parents=False)
    return resolved


def _independent_rollup(atom_rows: list[dict]) -> str:
    statuses = {row["status"] for row in atom_rows}
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if statuses == {STATUS_PASS}:
        return STATUS_PASS
    return STATUS_NOT_RUN


def verify_result_assertion_isomorphism(
    contract: dict, density: dict, projection: dict
) -> dict[str, Any]:
    """Cross-check the obligation projection against the atom-level evidence.

    The result layer (63 density atoms from the oracle) and the assertion
    layer (43 obligation rows) must agree exactly: the obligations partition
    the 63 oracle atoms, every referenced atom exists in the oracle and in the
    recorded evidence, and each obligation's status is the deterministic roll
    "up of its atoms (with the strict fuse branch keeping PASS evidence as an
    honest NOT_RUN).  Any divergence raises instead of persisting a receipt.
    """
    oracle = load_group_workspace_density_contract()
    oracle_ids = {row["id"] for row in oracle["density_requirements"]}
    evidence_rows = density.get("requirements", [])
    evidence_ids = {row["id"] for row in evidence_rows}
    if len(oracle_ids) != ATOM_COUNT or len(evidence_ids) != ATOM_COUNT:
        raise RunStateError("workspace field density evidence does not mirror the 63-atom oracle")
    if oracle_ids != evidence_ids:
        raise RunStateError("workspace field density evidence drifted from the atom oracle")
    referenced: list[str] = []
    fuse_obligation: str | None = None
    for obligation in contract["obligations"]:
        referenced.extend(obligation["atom_ids"])
        if obligation.get("predicate") == FUSE_PREDICATE:
            fuse_obligation = obligation["obligation_id"]
    if len(set(referenced)) != ATOM_COUNT or set(referenced) != oracle_ids:
        raise RunStateError("workspace field obligations do not partition the 63 oracle atoms")
    rollup = contract.get("baseline_atom_rollup") or {}
    baseline_ids = [atom for atoms in rollup.values() for atom in atoms]
    if len(set(baseline_ids)) != ATOM_COUNT or set(baseline_ids) != oracle_ids:
        raise RunStateError("workspace field baseline roll-up does not partition the oracle")

    atom_index = {row["id"]: row for row in evidence_rows}
    rows = {row["obligation_id"]: row for row in projection["obligations"]}
    if len(rows) != OBLIGATION_COUNT:
        raise RunStateError("workspace field projection lost obligation rows")
    recomputed_counts = {status: 0 for status in VALID_STATUS}
    for obligation in contract["obligations"]:
        atom_evidence = [atom_index[atom] for atom in obligation["atom_ids"]]
        expected = _independent_rollup(atom_evidence)
        if obligation.get("predicate") == FUSE_PREDICATE and expected == STATUS_PASS:
            # The strict error-branch decomposition is not exercised by the
            # normal offline exit; keep it honest NOT_RUN.
            expected = STATUS_NOT_RUN
        row = rows[obligation["obligation_id"]]
        if row["status"] != expected:
            raise RunStateError("workspace field obligation roll-up diverges from atom evidence")
        recomputed_counts[row["status"]] += 1
    if recomputed_counts != projection["counts"]:
        raise RunStateError("workspace field projection counts diverge from obligation rows")
    return {
        "verified": True,
        "atom_oracle": ATOM_ORACLE_CONTRACT,
        "atom_count": ATOM_COUNT,
        "referenced_atom_count": len(set(referenced)),
        "oracle_only": True,
        "oracle_atom_ids": sorted(oracle_ids),
        "fuse_obligation": fuse_obligation,
    }


def _local_verdict(projection: dict, fuse_obligation: str | None) -> tuple[str, list[dict]]:
    """Apply the local PASS criterion to a verified projection.

    PASS requires zero FAIL rows and every NOT_RUN row to be the named strict
    fuse branch gap.  Any failure or any unexplained NOT_RUN stays BLOCKED
    with its own inventoried gap descriptor.
    """
    counts = projection["counts"]
    rows = projection["obligations"]
    failed_ids = [row["obligation_id"] for row in rows if row["status"] == STATUS_FAIL]
    not_run_ids = [row["obligation_id"] for row in rows if row["status"] == STATUS_NOT_RUN]
    allowed_not_run = {fuse_obligation} if fuse_obligation else set()
    gaps: list[dict] = []
    if failed_ids:
        gaps.append(_gap_descriptor(
            LOCAL_WORKSPACE_FIELD_ASSERTION_FAILURE, failed_ids,
            "the offline projection observed contradicting evidence; the case fail-closes "
            "instead of persisting a PASS receipt"))
    unexplained = [obligation for obligation in not_run_ids if obligation not in allowed_not_run]
    if unexplained:
        gaps.append(_gap_descriptor(
            LOCAL_WORKSPACE_FIELD_UNACKNOWLEDGED_NOT_RUN, unexplained,
            "the offline executor produced no evidence for obligations outside the named "
            "fuse branch gap; they stay NOT_RUN instead of being counted as PASS"))
    if gaps:
        return STATUS_BLOCKED, gaps
    if not_run_ids:
        gaps.append(_gap_descriptor(
            LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED, not_run_ids, LOCAL_FUSE_GAP_REASON))
    return STATUS_PASS, gaps


def _local_execution(profile: dict, contract: dict) -> dict[str, Any]:
    """Execute the offline projection over the retained density journals."""
    transport = FakeWorkspaceTransport(profile)
    receipt = transport.execute()
    if receipt.get("status") != STATUS_PASS:
        raise RunStateError("workspace field offline executor lost its local PASS receipt")
    density = receipt["density"]
    projection = project_workspace_obligations(contract, density, profile=profile)
    isomorphism = verify_result_assertion_isomorphism(contract, density, projection)
    status, gaps = _local_verdict(projection, isomorphism["fuse_obligation"])
    counts = projection["counts"]
    executed = counts[STATUS_PASS]
    return {
        "status": status,
        "mode": OFFLINE_EXECUTION_MODE,
        "result_evidence": {
            "atom_oracle": ATOM_ORACLE_CONTRACT,
            "atom_count": ATOM_COUNT,
            "atom_counts": dict(density["counts"]),
            "journal_digest": fingerprint(receipt["journals"]),
            "transport": "fake-offline",
            "transport_calls": receipt["calls"],
        },
        "assertion_projection": {
            "obligation_count": OBLIGATION_COUNT,
            "counts": dict(counts),
            "baseline_counts": dict(projection["baseline_counts"]),
            "field_counts": dict(projection["field_counts"]),
            "completed_count": executed,
            "total_count": OBLIGATION_COUNT,
            "isomorphism": isomorphism,
        },
        "reports": {
            "expected_obligations": OBLIGATION_COUNT,
            "observed_obligations": len(projection["obligations"]),
            "executed_obligations": executed,
            "not_run_obligations": counts[STATUS_NOT_RUN],
        },
        "projection": projection,
        "gaps": gaps,
        "error_type": None if status == STATUS_PASS else "LOCAL_WORKSPACE_FIELD_EXECUTION_BLOCKED",
        "network_requests": 0,
    }


def _preprod_execution(profile: dict) -> dict[str, Any]:
    """Fail closed with the inventoried preprod NOT_RUN receipt."""
    receipt = preprod_not_run_receipt(profile)
    obligation_ids = [row["obligation_id"] for row in load_workspace_field_contract()["obligations"]]
    gap = _gap_descriptor(
        PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED, obligation_ids,
        PREPROD_FACE_GAP_REASON)
    return {
        "status": STATUS_NOT_RUN,
        "mode": "PREPROD_FAIL_CLOSED_NOT_INVENTORIED",
        "reason_class": PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED,
        "reason": gap["reason"],
        "preprod_verdict": STATUS_NOT_RUN,
        "obligation_receipt": receipt,
        "completed_count": 0,
        "total_count": OBLIGATION_COUNT,
        "counts": dict(receipt["counts"]),
        "gaps": [gap],
        "network_requests": 0,
    }


def _environment_view(profile: dict) -> dict[str, Any]:
    return {
        "tier": profile["environment"]["tier"],
        "id": profile["environment"]["id"],
        "entrypoint_kind": profile["entrypoint"]["kind"],
        "api_face": profile["entrypoint"]["api_face"],
        "cli_face": profile["entrypoint"]["cli_face"],
    }


def _case_scope() -> dict[str, Any]:
    return {
        "parent_story": PARENT_STORY,
        "obligation_count": OBLIGATION_COUNT,
        "baseline_obligation_count": BASELINE_COUNT,
        "field_obligation_count": FIELD_COUNT,
        "atom_count": ATOM_COUNT,
        "atom_oracle": ATOM_ORACLE_CONTRACT,
        "oracle_only_references": True,
        "execution_surface": OFFLINE_EXECUTION_MODE,
    }


def dual_workspace_field_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict[str, Any]:
    """Run the Workspace field dual-environment case and persist its receipts.

    Local tiers execute the 43-obligation offline projection; preprod fails
    closed at admission with named gaps.  Both tiers write the same receipt
    triple plus a profile-digest-only artifact, and neither ever persists a
    credential value.
    """
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "workspace field case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "workspace field run ID")
    profile = load_workspace_field_profile(Path(profile_path))
    tier = profile["environment"]["tier"]
    contract = validate_workspace_field_contract(load_workspace_field_contract())
    main_digest = fingerprint(profile)
    artifact = executor_digest()
    refs = credential_refs(profile)

    resolved_state_dir = _admit_fresh_state_dir(Path(state_dir))
    credential_kind = (
        CREDENTIAL_KIND_LOCAL if tier == LOCAL_TIER else CREDENTIAL_KIND_PREPROD)
    environment = _environment_view(profile)
    case_scope = _case_scope()

    digest_payload = {
        "schema_version": SCHEMA_VERSION,
        "contract": PROFILE_DIGEST_CONTRACT,
        "case_id": CASE_ID,
        "run_id": run_id,
        "profile_digest": main_digest,
        "environment": {"tier": tier, "id": profile["environment"]["id"]},
        "credential_refs": refs,
        "raw_profile_persisted": False,
        "note": "only the profile digest and its tier/face identity are persisted",
    }
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT_STORY,
        "run_id": run_id,
        "profile_digest": main_digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "credential_refs": refs,
        "environment": environment,
        "case_scope": case_scope,
        "planned_resource_disposition": (
            LOCAL_SUCCESS_DISPOSITION if tier == LOCAL_TIER else PREPROD_BLOCKED_DISPOSITION),
        "admission": {
            "allow_write_confirmed": True,
            "confirm_run_id_matched": True,
            "raw_profile_persisted": False,
            "auth_scope": "environment-variable-name-references-only",
        },
        "receipt_files": {
            "case_binding": BINDING_FILE,
            "case_report": REPORT_FILE,
            "gap_receipt": GAP_FILE,
            "profile_digest": PROFILE_DIGEST_FILE,
        },
        "state_dir": str(resolved_state_dir),
    }
    write(resolved_state_dir / PROFILE_DIGEST_FILE, digest_payload)
    write(resolved_state_dir / BINDING_FILE, binding)

    if tier == LOCAL_TIER:
        execution = _local_execution(profile, contract)
        status = execution["status"]
        disposition = (
            LOCAL_SUCCESS_DISPOSITION if status == STATUS_PASS
            else "OFFLINE_PROJECTION_FAIL_CLOSED_NO_LIVE_RESOURCE")
        preprod_verdict = None
    else:
        execution = _preprod_execution(profile)
        status = STATUS_BLOCKED_INVENTORIED
        disposition = PREPROD_BLOCKED_DISPOSITION
        preprod_verdict = STATUS_NOT_RUN

    gap_ids = [descriptor["gap_id"] for descriptor in execution["gaps"]]
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT_STORY,
        "run_id": run_id,
        "status": status,
        "preprod_verdict": preprod_verdict,
        "profile_digest": main_digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "credential_refs": refs,
        "environment": environment,
        "case_scope": case_scope,
        "execution": {
            "status": execution["status"],
            "mode": execution["mode"],
            "result_evidence": execution["result_evidence"] if tier == LOCAL_TIER else None,
            "assertion_projection": execution["assertion_projection"] if tier == LOCAL_TIER else None,
            "reports": execution["reports"] if tier == LOCAL_TIER else None,
            "reason_class": execution.get("reason_class"),
            "reason": execution.get("reason"),
            "obligation_receipt": execution["obligation_receipt"] if tier != LOCAL_TIER else None,
            "completed_count": (
                execution["assertion_projection"]["completed_count"] if tier == LOCAL_TIER else 0),
            "total_count": OBLIGATION_COUNT,
            "counts": (
                execution["assertion_projection"]["counts"] if tier == LOCAL_TIER
                else dict(execution["counts"])),
            "projection": execution["projection"] if tier == LOCAL_TIER else None,
            "error_type": execution["error_type"] if tier == LOCAL_TIER else (
                PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED),
        },
        "documented_gaps": gap_ids,
        "gap_descriptors": execution["gaps"],
        "network_requests": execution["network_requests"],
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
        "resource_disposition": disposition,
        "gap_receipt_file": GAP_FILE,
        "profile_digest_only": True,
        "raw_profile_persisted": False,
        "state_dir": str(resolved_state_dir),
    }
    write(resolved_state_dir / REPORT_FILE, report)

    gap_receipt = {
        "schema_version": SCHEMA_VERSION,
        "contract": GAP_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT_STORY,
        "run_id": run_id,
        "status": status,
        "preprod_verdict": preprod_verdict,
        "tier": tier,
        "documented_gaps": gap_ids,
        "gaps": execution["gaps"],
        "obligation_count": OBLIGATION_COUNT,
        "counts": report["execution"]["counts"],
        "gaps_count": len(gap_ids),
        "not_run_obligation_count": report["execution"]["counts"][STATUS_NOT_RUN],
        "network_requests": 0,
        "credential_refs": refs,
        "profile_digest": main_digest,
        "executor_digest": artifact,
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
        "state_dir": str(resolved_state_dir),
    }
    write(resolved_state_dir / GAP_FILE, gap_receipt)
    return report


def dual_workspace_field_command_handler(args: Any) -> int:
    """CLI-shaped wrapper for the unregistered workspace field command.

    The registration surface stays untouched in this wave (mainline serial);
    the handler keeps the flat runner rc semantics: 0 only for a local PASS
    case, 2 for admission refusals, honest BLOCKED/BLOCKED_INVENTORIED
    receipts and fail-closed errors.
    """
    try:
        report = dual_workspace_field_case_run(
            profile_path=args.profile,
            state_dir=args.state_dir,
            run_id=args.run_id,
            allow_write=args.allow_write,
            confirm_run_id=args.confirm_run_id,
        )
    except (ConfigError, RunStateError) as error:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": DUAL_WORKSPACE_FIELD_COMMAND,
            "status": STATUS_BLOCKED,
            "ready_to_execute": False,
            "grants_release_credit": False,
            "reason": str(error),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == STATUS_PASS else 2

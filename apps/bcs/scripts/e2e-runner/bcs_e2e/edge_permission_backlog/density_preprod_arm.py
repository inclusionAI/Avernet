"""Honest preprod recorded-receipt arm for the Wave-D3 density overlay.

No preprod transport is ever triggered here.  The five density parents already
ran their preprod legs inside the recorded dual rounds, and this module only
re-reads those receipts offline:

* the four write-domain parents replay the r5 dual mutation round receipts
  (``20261031-regression-r5-edge-mutation``); the mutual-auto-approve parent
  additionally replays the r6 round (``20261031-regression-r6-edge-mutation``)
  for the divergence disclosure on the duplicate echo;
* the read parent replays the r5 dual read round receipt
  (``20261031-regression-r5-edge-read``) and the r7 friend-connections state
  run (``20261031-regression-r7-preprod``);
* the WRITE_ANNEX leaf of the read parent is classified from the cross-family
  citation of the r5 error-admission mutation receipt, whose
  ``friend_connection_mutation.create`` request rows record the CEO->ENG
  create answering 201 with request ids echoed.

Every attached receipt is verified against the frozen case identity, the
honest NOT_RUN posture and the family contract, then each density leaf is
classified against its recorded projection through
:mod:`~bcs_e2e.edge_permission_backlog.density_eval`.  Per-leaf projection
keys the dual receipts do not carry stay ``NOT_OBSERVABLE`` — a material gap,
never silent PASS credit.  The arm also cross-checks the observed actor ids
against ``preprod.json`` (driver on the read leg, member on the mutual leg); a
missing identity is disclosed, a contradicting identity is a conflict.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..profile import ConfigError
from ..run_state import RunStateError
from .density_contract import (
    DENSITY_CASE_KEYS,
    FRIEND_REQUEST_AND_LIST_CASE_KEY,
    LOCAL_MODEL_READY,
    MUTATION_FAMILY_CONTRACT,
    MUTATION_FAMILY_KIND,
    PREPROD_NOT_RUN,
    READ_FAMILY_CONTRACT,
    REMOTE_VALIDATION,
    ENROLLMENT_NOT_ENROLLED,
    density_case_row,
)
from .density_eval import classification_totals, classify_recorded
from .density_obligations import build_density_obligations

MUTATION_R5_ROUND = "20261031-regression-r5-edge-mutation"
MUTATION_R6_ROUND = "20261031-regression-r6-edge-mutation"
READ_R5_ROUND = "20261031-regression-r5-edge-read"
R7_PREPROD_ROUND = "20261031-regression-r7-preprod"
READ_R7_RELATIVE = "edge-read/friend-connections-state/edge-friend-connections-run.json"
MUTATION_CREDENTIAL_KIND = "human_cookie+registered_bot_token_presence"
READ_CREDENTIAL_KIND = "human_cookie"
CITED_OPERATION = "friend_connection_mutation.create"
PREPROD_JSON_NAME = "preprod.json"

# round label, receipt relative path, family contract, credential kind
MUTATION_RECEIPT_SPECS: dict[str, list[tuple[str, str, str, str]]] = {
    "error-admission-branches": [
        (MUTATION_R5_ROUND, "error-admission-branches-stdout.json",
         MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND)],
    "connect-private-discover": [
        (MUTATION_R5_ROUND, "connect-private-discover-stdout.json",
         MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND)],
    "accept-reject-cancel": [
        (MUTATION_R5_ROUND, "accept-reject-cancel-stdout.json",
         MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND)],
    "mutual-auto-approve": [
        (MUTATION_R5_ROUND, "mutual-auto-approve-stdout.json",
         MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND),
        (MUTATION_R6_ROUND, "mutual-auto-approve-stdout.json",
         MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND)],
    FRIEND_REQUEST_AND_LIST_CASE_KEY: [
        (READ_R5_ROUND, "friend-connections-stdout.json",
         READ_FAMILY_CONTRACT, READ_CREDENTIAL_KIND),
        (R7_PREPROD_ROUND, READ_R7_RELATIVE,
         READ_FAMILY_CONTRACT, READ_CREDENTIAL_KIND)],
}
CITED_RECEIPT_SPEC = (MUTATION_R5_ROUND, "error-admission-branches-stdout.json")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_round(root: Path, label: str, relative: str,
                conflicts: list[str]) -> dict[str, Any]:
    path = root / label / relative
    if path.is_symlink() or not path.is_file():
        conflicts.append(label + " receipt is missing: " + relative)
        return {"round": label, "file": relative, "availability": "MISSING"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        conflicts.append(label + " receipt is unreadable: " + type(error).__name__)
        return {"round": label, "file": relative, "availability": "UNREADABLE"}
    return {
        "round": label, "file": relative, "availability": "ATTACHED",
        "path": str(path), "file_digest": _digest(path),
        "bytes": path.stat().st_size, "receipt": raw,
    }


def _step_zero(receipt: Any) -> dict[str, Any] | None:
    if not isinstance(receipt, dict):
        return None
    case = receipt.get("case")
    if not isinstance(case, dict):
        return None
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    step = steps[0]
    return step if isinstance(step, dict) else None


def _required_receipt_keys(row: dict[str, Any]) -> tuple[str, ...]:
    if row["family_kind"] == MUTATION_FAMILY_KIND:
        return (MUTATION_FAMILY_CONTRACT, MUTATION_CREDENTIAL_KIND)
    return (READ_FAMILY_CONTRACT, READ_CREDENTIAL_KIND)


def _verify_envelope(case_key: str, round_row: dict[str, Any],
                     conflicts: list[str]) -> dict[str, Any]:
    """Verify one attached receipt against the frozen density case row."""
    row = density_case_row(case_key)
    receipt = round_row.get("receipt")
    if not isinstance(receipt, dict):
        conflicts.append(round_row["round"] + " receipt is not a JSON object")
        return {"envelope_verified": False}
    contract, credential_kind = _required_receipt_keys(row)
    case = receipt.get("case") if isinstance(receipt.get("case"), dict) else {}
    steps = case.get("steps") if isinstance(case.get("steps"), list) else []
    case_scope = receipt.get("case_scope") if isinstance(receipt.get("case_scope"), dict) else {}
    execution = receipt.get("execution") if isinstance(receipt.get("execution"), dict) else {}
    profiles = receipt.get("profiles") if isinstance(receipt.get("profiles"), dict) else {}
    recorded = {
        "case_id": receipt.get("case_id"),
        "parent_id": receipt.get("parent_id"),
        "status": receipt.get("status"),
        "run_id": receipt.get("run_id"),
        "contract": receipt.get("contract"),
        "credential_kind": receipt.get("credential_kind"),
        "remote_validation": receipt.get("remote_validation"),
        "grants_release_credit": receipt.get("grants_release_credit"),
        "resource_disposition": receipt.get("resource_disposition"),
        "executor_digest": receipt.get("executor_digest"),
        "profile_digest": receipt.get("profile_digest"),
        "gateway_digest": profiles.get("gateway_digest"),
        "station_digest": profiles.get("station_digest"),
        "execution_status": execution.get("status"),
        "execution_completed": execution.get("completed_count"),
        "execution_total": execution.get("total_count"),
        "case_status": case.get("status"),
        "case_completed": case.get("completed_count"),
        "case_total": case.get("total_count"),
        "steps_count": len(steps),
        "step_requirement_ids": [step.get("requirement_id") for step in steps
                                 if isinstance(step, dict)],
        "case_scope_requirement_ids": case_scope.get("requirement_ids"),
        "public_operations": case_scope.get("public_operations"),
        "product_defects": sorted((receipt.get("product_defects") or {}).keys()),
        "documented_gaps": sorted((receipt.get("documented_gaps") or {}).keys()),
    }
    label = round_row["round"]
    acceptable_ids = {row["case_id"]}
    if row.get("public_case_id"):
        acceptable_ids.add(row["public_case_id"])
    if recorded["case_id"] not in acceptable_ids:
        conflicts.append(label + " case_id " + str(recorded["case_id"])
                         + " left the frozen density case identity")
    if recorded["parent_id"] != row["parent_id"]:
        conflicts.append(label + " parent_id drifted from the frozen parent")
    if recorded["status"] != "PASS":
        conflicts.append(label + " envelope status is not the recorded PASS")
    if recorded["contract"] != contract:
        conflicts.append(label + " contract left the frozen family contract")
    if recorded["credential_kind"] != credential_kind:
        conflicts.append(label + " credential kind left the frozen family kind")
    if recorded["remote_validation"] != REMOTE_VALIDATION:
        conflicts.append(label + " remote_validation is not the honest NOT_RUN")
    if recorded["grants_release_credit"] is not False:
        conflicts.append(label + " receipt claims release credit")
    if recorded["case_status"] != "PASS":
        conflicts.append(label + " case status is not the recorded PASS")
    if recorded["execution_status"] != "PASS":
        conflicts.append(label + " execution status is not the recorded PASS")
    if recorded["execution_completed"] != recorded["execution_total"]:
        conflicts.append(label + " execution counts are not converged")
    if recorded["case_completed"] != recorded["case_total"]:
        conflicts.append(label + " case counts are not converged")
    if recorded["steps_count"] != recorded["case_completed"]:
        conflicts.append(label + " journaled steps disagree with the case counts")
    expected_reqs = list(row["frozen_requirement_ids"])
    if recorded["step_requirement_ids"] != expected_reqs:
        conflicts.append(label + " journaled requirement ids left the frozen requirement set")
    if row["family_kind"] == MUTATION_FAMILY_KIND:
        if recorded["case_scope_requirement_ids"] != expected_reqs:
            conflicts.append(label + " case_scope requirement ids drifted")
        if not recorded["public_operations"]:
            conflicts.append(label + " case_scope journals no public operations")
    if not recorded["resource_disposition"]:
        conflicts.append(label + " receipt journals no resource disposition")
    if not recorded["executor_digest"]:
        conflicts.append(label + " receipt journals no executor digest")
    if row["family_kind"] == MUTATION_FAMILY_KIND:
        if not recorded["gateway_digest"] or not recorded["station_digest"]:
            conflicts.append(label + " receipt journals no dual-environment digests")
    else:
        if not recorded["profile_digest"]:
            conflicts.append(label + " receipt journals no profile digest")
    recorded["envelope_verified"] = not conflicts
    return recorded


def _cited_create_rows(receipt: Any) -> dict[str, Any]:
    """Extract the cross-family create rows from the cited mutation receipt."""
    step = _step_zero(receipt)
    evidence = (step or {}).get("evidence")
    requests = evidence.get("requests") if isinstance(evidence, dict) else None
    if not isinstance(requests, list):
        return {"cited": False, "create_rows_total": 0, "create_rows_201": 0,
                "create_rows_request_id_echoed": False}
    creates = [item for item in requests
               if isinstance(item, dict) and item.get("operation") == CITED_OPERATION]
    answered = [item for item in creates if item.get("http_status") == 201]
    echoed = bool(creates) and all(
        item.get("request_id_echoed") is True for item in creates)
    return {
        "cited": bool(creates),
        "create_rows_total": len(creates),
        "create_rows_201": len(answered),
        "create_rows_request_id_echoed": echoed,
    }


def _mutual_actor_ids(data: Any) -> set[str]:
    actors: set[str] = set()
    admissions = data.get("admissions") if isinstance(data, dict) else None
    if not isinstance(admissions, dict):
        return actors
    for key in admissions:
        for endpoint in str(key).split("->"):
            endpoint = endpoint.strip()
            if endpoint and not endpoint.startswith("bot_"):
                actors.add(endpoint)
    return actors


def _read_caller_actor_ids(receipt: Any) -> set[str]:
    actors: set[str] = set()
    if not isinstance(receipt, dict):
        return actors
    case = receipt.get("case")
    steps = case.get("steps") if isinstance(case, dict) else None
    if not isinstance(steps, list):
        return actors
    for step in steps:
        evidence = step.get("evidence") if isinstance(step, dict) else None
        caller = evidence.get("caller_actor_id") if isinstance(evidence, dict) else None
        if caller:
            actors.add(str(caller))
    return actors


def load_preprod_actors(evidence_root: Path | None) -> dict[str, Any]:
    """Load the driver/member actor expectation from the room-level preprod.json."""
    if evidence_root is None:
        return {"availability": "NOT_ATTACHED", "basis":
                "no evidence root attached; preprod.json actor expectation is not read"}
    path = Path(evidence_root).parent / PREPROD_JSON_NAME
    if path.is_symlink() or not path.is_file():
        return {"availability": "NOT_ATTACHED", "path": str(path),
                "basis": "preprod.json is not attached next to the evidence root"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"availability": "UNREADABLE", "path": str(path),
                "basis": "preprod.json is unreadable; identity expectation not loaded"}
    actors = raw.get("actors") if isinstance(raw, dict) else None
    if not isinstance(actors, dict):
        return {"availability": "INVALID", "path": str(path),
                "basis": "preprod.json carries no actors object"}
    driver = (actors.get("driver") or {}).get("bot_id")
    member = (actors.get("member") or {}).get("bot_id")
    return {"availability": "ATTACHED", "path": str(path),
            "driver_bot_id": driver, "member_bot_id": member}


def identity_cross_check(
        case_key: str, rounds: list[dict[str, Any]],
        actors: dict[str, Any]) -> dict[str, Any]:
    """Cross-check observed actor ids against the preprod.json expectation."""
    observed: set[str] = set()
    for round_row in rounds:
        receipt = round_row.get("receipt")
        if not isinstance(receipt, dict):
            continue
        step = _step_zero(receipt)
        data = (step or {}).get("data")
        if isinstance(data, dict):
            observed |= _mutual_actor_ids(data)
        observed |= _read_caller_actor_ids(receipt)
    driver = actors.get("driver_bot_id")
    member = actors.get("member_bot_id")
    mismatches: list[str] = []
    if actors.get("availability") != "ATTACHED":
        basis = "preprod.json actors not attached; identity expectation undisclosed"
    else:
        basis = "observed actor ids compared against preprod.json driver/member"
    driver_observed = bool(driver) and driver in observed
    member_observed = bool(member) and member in observed
    if driver and not driver_observed and case_key == FRIEND_REQUEST_AND_LIST_CASE_KEY:
        mismatches.append("read-leg caller_actor_id does not carry the preprod driver bot id")
    if member and not member_observed and case_key == "mutual-auto-approve":
        mismatches.append("mutual admissions do not carry the preprod member bot id")
    return {
        "availability": actors.get("availability"),
        "observed_actor_ids": sorted(observed),
        "driver_expected": driver,
        "member_expected": member,
        "driver_observed_on_this_leg": driver_observed,
        "member_observed_on_this_leg": member_observed,
        "mismatches": mismatches,
        "basis": basis,
    }


def _recorded_projection(rounds: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the step-zero data projection of the first attached round."""
    for round_row in rounds:
        step = _step_zero(round_row.get("receipt"))
        if step is not None and isinstance(step.get("data"), dict):
            return step["data"]
    return None


def density_preprod_arm_receipt(case_key: str,
                                evidence_root: Path | None = None) -> dict[str, Any]:
    """Render one density preprod arm from the recorded dual receipts."""
    if case_key not in DENSITY_CASE_KEYS:
        raise RunStateError("unknown edge_permission density case key: " + str(case_key))
    row = density_case_row(case_key)
    leaves = tuple(item for item in build_density_obligations()
                   if item.case_key == case_key)
    conflicts: list[str] = []
    content_conflicts: list[str] = []
    rounds: list[dict[str, Any]] = []
    availability = "NOT_ATTACHED"
    actors = load_preprod_actors(evidence_root)

    if evidence_root is not None:
        root = Path(evidence_root)
        if root.is_symlink() or not root.is_dir():
            availability = "ROOT_UNAVAILABLE"
            conflicts.append("preprod evidence root is not an existing directory")
        else:
            specs = MUTATION_RECEIPT_SPECS[case_key]
            attached = 0
            for label, relative, _contract, _kind in specs:
                round_row = _load_round(root, label, relative, conflicts)
                if round_row.get("availability") == "ATTACHED":
                    attached += 1
                    recorded = _verify_envelope(case_key, round_row, content_conflicts)
                    round_row["envelope"] = recorded
                else:
                    round_row["envelope"] = {"envelope_verified": False}
                rounds.append(round_row)
            if attached == 0:
                availability = "NO_FAMILY_RECEIPTS"
                conflicts.append(
                    "the attached evidence root carries none of this family's "
                    "recorded preprod receipts")
            elif attached < len(specs):
                availability = "PARTIAL"
            else:
                availability = "ATTACHED"
    conflicts.extend(content_conflicts)

    # per-leaf recorded classification
    classifications = []
    cited_rows: dict[str, Any] = {}
    cited_round: dict[str, Any] = {
        "round": CITED_RECEIPT_SPEC[0], "file": CITED_RECEIPT_SPEC[1],
        "availability": "NOT_ATTACHED",
    }
    if case_key == FRIEND_REQUEST_AND_LIST_CASE_KEY and evidence_root is not None \
            and availability not in ("ROOT_UNAVAILABLE",):
        root = Path(evidence_root)
        cited_round = _load_round(root, CITED_RECEIPT_SPEC[0], CITED_RECEIPT_SPEC[1], conflicts)
        if cited_round.get("availability") == "ATTACHED":
            cited_rows = _cited_create_rows(cited_round.get("receipt"))
    if case_key == FRIEND_REQUEST_AND_LIST_CASE_KEY:
        projection = {
            "cited": cited_rows.get("cited") is True,
            "create_rows_total": cited_rows.get("create_rows_total", 0),
            "create_rows_201": cited_rows.get("create_rows_201", 0),
        }
        if cited_round.get("availability") == "ATTACHED":
            citation = {
                "round": CITED_RECEIPT_SPEC[0], "file": CITED_RECEIPT_SPEC[1],
                "availability": "ATTACHED",
                "create_rows_total": cited_rows.get("create_rows_total"),
                "create_rows_201": cited_rows.get("create_rows_201"),
                "create_rows_request_id_echoed": cited_rows.get("create_rows_request_id_echoed"),
                "basis": (
                    "the annex has no preprod receipt of its own; it is classified "
                    "from the cross-family citation of the recorded r5 mutation "
                    "round create rows only, and never completes the frozen read slice"),
            }
        else:
            citation = {
                "round": CITED_RECEIPT_SPEC[0], "file": CITED_RECEIPT_SPEC[1],
                "availability": cited_round.get("availability"),
                "basis": ("the cited cross-family receipt is not attached; "
                          "the annex leaf stays NOT_OBSERVABLE"),
            }
        for leaf in leaves:
            recorded = projection if leaf.evaluator_key == "create_request_annex" else None
            classifications.append(_classify_leaf(leaf, recorded, citation))
    else:
        data = _recorded_projection(rounds)
        for leaf in leaves:
            classifications.append(_classify_leaf(leaf, data, None))

    identity = identity_cross_check(case_key, rounds, actors)
    conflicts.extend(identity["mismatches"])

    return {
        "arm": "preprod",
        "case_key": case_key,
        "case_id": row["case_id"],
        "public_case_id": row.get("public_case_id"),
        "parent_id": row["parent_id"],
        "preprod_status": PREPROD_NOT_RUN,
        "remote_validation": REMOTE_VALIDATION,
        "enrollment": ENROLLMENT_NOT_ENROLLED,
        "family_receipt_availability": availability,
        "rounds": [
            {
                "round": item["round"],
                "file": item["file"],
                "availability": item.get("availability"),
                "file_digest": item.get("file_digest"),
                "envelope": item.get("envelope"),
            }
            for item in rounds],
        "leaf_classifications": classifications,
        "classification_totals": classification_totals(classifications),
        "identity_cross_check": identity,
        "conflicts": conflicts,
        "content_conflicts": content_conflicts,
        "promotion_guard": (
            "recorded preprod PASS rows of the frozen families are re-read here "
            "as classification input only; the density leaves stay NOT_ENROLLED "
            "and the preprod leg stays NOT_RUN until an authorized enrolled "
            "round journals per-leaf projection keys"),
        "execution": (
            "offline re-read of the recorded dual preprod receipts; no preprod "
            "transport is triggered and no local projection is promoted"),
        "credential_kind": "recorded_receipt_reread_no_transport",
        "basis": (
            "dual preprod legs recorded PASS in the r5/r6/r7 rounds with "
            "remote_validation NOT_RUN and grants_release_credit false; this arm "
            "discloses per-leaf classification against those projections"),
    }


def _classify_leaf(leaf: Any, recorded: Any,
                   citation: dict[str, Any] | None) -> dict[str, Any]:
    row = classify_recorded(leaf.as_dict(), recorded)
    row["relation"] = leaf.relation
    row["family"] = leaf.family
    if citation is not None:
        row["citation"] = citation
    else:
        row["citation"] = {
            "basis": "recorded step-zero projection of the attached round receipt(s)",
        }
    return row


def density_preprod_arms(evidence_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Render all five density preprod arms."""
    return {case_key: density_preprod_arm_receipt(case_key, evidence_root)
            for case_key in DENSITY_CASE_KEYS}

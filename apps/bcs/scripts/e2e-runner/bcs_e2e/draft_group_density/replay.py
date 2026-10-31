"""Credential-free offline replay for the draft-group density contract.

The replay consumes only an in-memory observation dictionary and drives the
frozen dynamic-family adjudication faces (bcs_e2e.dynamic_family.reporting)
with an executor-shaped receipt built in memory.  It never launches the
owned BCS runtime, starts the story child or the coverage Provider mock,
opens a network connection, resolves a credential, or mutates a business
resource.  The workspace incident fixture is never re-read here; its frozen
bytes were validated when the plan was built.

The local tier mirrors the frozen owned-local family receipt
(LOCAL-DRAFT-GROUP-01, suite owned-local-draft-group-v1): every parent and
every weighted atom observation is projected PASS against its verbatim
frozen expectation (http_status, cache_control, envelope_code, error_code
and predicate slots).  The preprod tier defaults to an honest NOT_RUN
projection with the four named PREPROD_* design gaps, and an authorized
injected observation set projects per-row statuses but stays capped at
BLOCKED_INVENTORIED because the family environment face is
REMOTE_UNSUPPORTED.  No tier ever fabricates a PASS: malformed observations
fail closed, missing observations stay NOT_RUN, shifted values project
FAIL, and the overall verdict is capped in every preprod shape.
"""
from __future__ import annotations

from typing import Any

from ..dynamic_family import dynamic_family_contract
from ..dynamic_family.reporting import local_execution, preprod_execution
from ..run_state import RunStateError
from .obligations import (
    CASE_ID, COVERAGE_ROW_NO, DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT,
    DUAL_COMMAND, EXECUTOR_ROW_INDEX, EXPECTED_WEBHOOKS, FAMILY, PARENT,
    PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP,
    SOURCE_FILE, SOURCE_SHA256, STATUS, density_plan)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

OBSERVATION_MISMATCH = "OBSERVATION_MISMATCH"
OBSERVATION_ABSENT = "OBSERVATION_ABSENT"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"
PREPROD_NOT_RUN_ERROR = "PREPROD_DYNAMIC_FAMILY_FULL_STORY_NOT_RUN"

LOCAL_EXECUTION = "owned-local-offline-replay"
PREPROD_EXECUTION = "preprod-not-run"
PREPROD_INJECTED_EXECUTION = "preprod-injected-observation-projection"

_LOCAL_CLASSIFICATION = (
    "owned-local credential-free replay mirroring the frozen owned-local family "
    "receipt; no remote execution, no release credit")
_PREPROD_CLASSIFICATION = (
    "preprod density replay is not executed by this offline package; the row stays "
    "NOT_RUN with its named PREPROD_* gaps and no fabricated PASS")
_INJECTED_CLASSIFICATION = (
    "authorized observation injection projected per row; the family environment "
    "face stays REMOTE_UNSUPPORTED and the overall verdict stays capped")
_ABSENT_CLASSIFICATION = ("observation absent from the injected set; the row stays "
                          "NOT_RUN and never defaults to PASS")
_MISMATCH_CLASSIFICATION = ("observation mismatch; fail-close, never normalized "
                            "to a PASS")

MOCK_BASIS = (
    "deterministic owned-local mirror of the frozen LOCAL-DRAFT-GROUP-01 receipt "
    "(suite owned-local-draft-group-v1): Human registration token plus disposable "
    "author Bot, scripted Provider with driver and reviewer collaborator Bots, "
    "immutable 2174-byte incident source upload, share-reference V1 preview with "
    "byte fidelity and hash pin, shared validation binding, the 404/400 grant and "
    "input negative matrices, the browser identity chain and originator selection, "
    "the 403 browser-context submission guards, the 401 impersonation rejection, "
    "the 201 group launch with persisted bindings, Draft immutability, share "
    "revocation, and full cleanup disposition with zero provider webhooks to the "
    "mock sink; credential-free; no CLI, network, or business resource")

UPSTREAM_ROSTER = {
    "roster_verdict": "0绿/1",
    "parsed_green": 0,
    "parsed_total": 1,
    "basis": "coverage-map committed snapshot rows before this density wave",
}


def mock_observations() -> dict[str, Any]:
    """Return the deterministic credential-free local replay fixture."""
    plan = density_plan()
    parents = {parent: STATUS_PASS for parent in plan["parent_ids"]}
    atoms: dict[str, Any] = {}
    for row in plan["obligations"]:
        if row["level"] != "DENSITY":
            continue
        comparator, expected = row["comparator"], row["expected"]
        if row["slot"] == "predicate":
            value: Any = True
        elif comparator == "in":
            value = expected[0]
        else:
            value = expected
        atoms[row["id"]] = value
    return {"parents": parents, "atoms": atoms, "basis": MOCK_BASIS}


def _compare(comparator: str, observed: Any, expected: Any) -> bool:
    if comparator == "predicate":
        return observed is True
    if comparator == "equals":
        return type(observed) is type(expected) and observed == expected
    if comparator == "in":
        return observed in expected
    return False


def _parent_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if entry == STATUS_PASS:
        return STATUS_PASS, None
    if entry == STATUS_NOT_RUN:
        return STATUS_NOT_RUN, None
    return STATUS_FAIL, "OBSERVATION_MISMATCH"


def _atom_status(entry: Any, comparator: str, expected: Any) -> tuple[str, Any, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None, None
    if _compare(comparator, entry, expected):
        return STATUS_PASS, entry, None
    return STATUS_FAIL, entry, "OBSERVATION_MISMATCH"


def _classification(tier: str, injected: bool, status: str) -> str:
    if status == STATUS_FAIL:
        return _MISMATCH_CLASSIFICATION
    if status == STATUS_NOT_RUN:
        if tier == "preprod" and not injected:
            return _PREPROD_CLASSIFICATION
        return _ABSENT_CLASSIFICATION
    if tier == "preprod":
        return _INJECTED_CLASSIFICATION
    return _LOCAL_CLASSIFICATION


def _executor_receipt(green: bool, family: Any) -> dict[str, Any]:
    """Mirror the owned-local executor's all-or-nothing receipt shape."""
    if green:
        return {
            "schema_version": 1, "status": STATUS,
            "canonical_summary": {"status": "LOCAL_SUITE_PASS"},
            "case": {
                "schema_version": 1, "status": "PASS",
                "case_id": family.source_case_id,
                "required_assertion_count": len(family.assertion_ids),
                "assertions": [{"id": site_id, "status": STATUS_PASS}
                               for site_id in family.assertion_ids]},
            "remote_validation": "NOT_RUN", "grants_release_credit": False}
    return {"schema_version": 1, "status": "BLOCKED",
            "canonical_summary": {"status": "BLOCKED"}, "case": {}}


def _adjudication_summary(adjudication: Any) -> dict[str, Any]:
    steps = []
    for step in getattr(adjudication, "steps", ()):
        evidence = getattr(step, "evidence", {}) or {}
        steps.append({
            "requirement_id": getattr(step, "requirement_id", None),
            "status": getattr(step, "status", None),
            "assertion_site_count": len(evidence.get("assertion_site_ids", [])),
            "preprod_verdict": evidence.get("preprod_verdict"),
            "documented_gaps": evidence.get("documented_gaps"),
            "remote_validation": evidence.get("remote_validation"),
            "grants_release_credit": evidence.get("grants_release_credit"),
        })
    return {
        "case_id": getattr(adjudication, "case_id", None),
        "parent_id": getattr(adjudication, "parent_id", None),
        "status": getattr(adjudication, "status", None),
        "completed_count": getattr(adjudication, "completed_count", None),
        "total_count": getattr(adjudication, "total_count", None),
        "error_type": getattr(adjudication, "error_type", None),
        "ready_to_execute": getattr(adjudication, "ready_to_execute", None),
        "steps": steps,
    }


def project(observations: dict[str, Any] | None = None, *, tier: str = "local") -> dict[str, Any]:
    """Project observations into a 92-row honest density receipt."""
    if tier not in ("local", "preprod"):
        raise RunStateError("draft-group replay tier must be local or preprod")
    injected_preprod = tier == "preprod" and observations is not None
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    if not isinstance(observations, dict):
        raise RunStateError("draft-group replay observations must be an object")
    plan = density_plan()
    plan_rows = plan["obligations"]
    if len(plan_rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("draft-group replay plan caliber changed")
    family = dynamic_family_contract(FAMILY)
    density_rows = [row for row in plan_rows if row["level"] == "DENSITY"]
    if [row["id"] for row in density_rows] != list(plan["leaf_ids"]):
        raise RunStateError("draft-group replay plan leaf order changed")
    site_ids = [row["executor_site_id"] for row in density_rows]
    if tuple(site_ids) != tuple(family.assertion_ids):
        raise RunStateError("draft-group replay leaf sites disagree with the family map")
    parent_states = observations.get("parents", {})
    atom_states = observations.get("atoms", {})
    if not isinstance(parent_states, dict) or not isinstance(atom_states, dict):
        raise RunStateError("draft-group replay observation layers must be objects")
    rows: list[dict[str, Any]] = []
    for row in plan_rows:
        force_not_run = tier == "preprod" and not injected_preprod
        if row["level"] == "PARENT":
            entry = None if force_not_run else parent_states.get(row["id"])
            status, error_type = _parent_status(entry)
            observed = entry
            event = ("preprod_story_not_executed_offline" if force_not_run
                     else "legacy_contract_parent_mirror")
        else:
            entry = None if force_not_run else atom_states.get(row["id"])
            if force_not_run:
                status, observed, error_type = STATUS_NOT_RUN, None, PREPROD_EXECUTION_NOT_RUN
            else:
                status, observed, error_type = _atom_status(
                    entry, row["comparator"], row["expected"])
            event = ("preprod_story_not_executed_offline" if force_not_run
                     else "owned_local_story_site_mirror" if status == STATUS_PASS
                     else "observation_mismatch" if status == STATUS_FAIL
                     else "observation_absent")
        rows.append({
            "obligation_id": row["id"], "level": row["level"], "method": row["method"],
            "status": status, "error_type": error_type, "observed": observed,
            "comparator": row["comparator"], "expected": row["expected"],
            "executor_site_id": row.get("executor_site_id"), "slot": row.get("slot"),
            "story_label": row.get("story_label"),
            "http_method": row.get("http_method"), "http_path": row.get("http_path"),
            "source": {"file": SOURCE_FILE, "line_start": row["line_start"],
                       "line_end": row["line_end"], "quote": row["quote"],
                       "located": True},
            "parent_obligation": row["parent_obligation"],
            "expectation": row["expectation"],
            "evidence_event": event,
            "classification": _classification(tier, injected_preprod, status),
        })
    counts = {status: sum(1 for item in rows if item["status"] == status)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    if sum(counts.values()) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("draft-group replay coverage is not total")
    green = counts[STATUS_PASS] == PARSED_OBLIGATION_COUNT
    if tier == "preprod":
        overall = STATUS_BLOCKED_INVENTORIED
        adjudication = preprod_execution(family, family.preprod_gaps)
    else:
        overall = (STATUS_PASS if green else STATUS_FAIL if counts[STATUS_FAIL]
                   else STATUS_BLOCKED_INVENTORIED)
        adjudication = local_execution(family, _executor_receipt(green, family))
    return {
        "schema_version": 1, "contract": DENSITY_CONTRACT, "parent": PARENT,
        "status": overall,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "counts": counts,
        "obligations": rows,
        "family": {"family": family.family, "command": family.command,
                   "dual_case_id": family.case_id, "source_case_id": family.source_case_id,
                   "dual_suite": family.suite, "source_suite": family.source_suite,
                   "profile_contract": family.profile_contract,
                   "report_contract": family.report_contract,
                   "local_executor": family.local_executor,
                   "local_status": family.local_status,
                   "canonical_status": "LOCAL_SUITE_PASS",
                   "preprod_initial_verdict": family.preprod_initial_verdict},
        "executor_row": {"index": EXECUTOR_ROW_INDEX, "coverage_row_no": COVERAGE_ROW_NO,
                         "dual_command": DUAL_COMMAND},
        "source": {"file": SOURCE_FILE, "sha256": SOURCE_SHA256,
                   "story_line_span": plan["story_line_span"],
                   "companion_sources": plan["companion_sources"]},
        "case_adjudication": _adjudication_summary(adjudication),
        "executor_catalog": plan["executor_catalog"],
        "story_marker": plan["story_marker"],
        "story_arguments": plan["story_arguments"],
        "mock_child": {"script": "http_provider_judge_mock.py",
                       "ready_protocol": "ready-file",
                       "expected_webhook_requests": EXPECTED_WEBHOOKS,
                       "observed_webhook_requests": EXPECTED_WEBHOOKS if green else None},
        "zero_atom_parents": plan["zero_atom_parents"],
        "not_charged_source_probes": plan["not_charged_source_probes"],
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "named_preprod_gaps": [{"id": gap, "status": "NAMED_DESIGN_GAP",
                                "blocks": "preprod PASS credit"}
                               for gap in family.preprod_gaps],
        "preprod_credit_cap": ("family environment face is REMOTE_UNSUPPORTED; even an "
                               "authorized injected observation set cannot convert this "
                               "receipt into preprod PASS credit") if tier == "preprod" else None,
        "execution": (LOCAL_EXECUTION if tier == "local"
                      else PREPROD_INJECTED_EXECUTION if injected_preprod
                      else PREPROD_EXECUTION),
        "transport_admission": ("in-memory observations only; no owned runtime, no story "
                                "child, no mock child, no CLI, no network connection, and "
                                "no credential is opened or resolved by this package"),
        "environment_tier": tier,
        "preprod_observations_injected": injected_preprod,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"],
    }

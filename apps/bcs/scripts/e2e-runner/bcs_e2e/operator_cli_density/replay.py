"""Credential-free offline replay for the operator CLI density contract.

The replay consumes only an in-memory observation dictionary and mirrors the
committed dual-operator-cli-run adjudication face with a real
bcs_e2e.case_executor CaseExecution built in memory from the 13 frozen
parent obligations and their 15 source-anchored density leaves.  It never
launches the owned BCS runtime, starts the story harness, invokes the
product CLI binary, opens a transport, resolves a credential, or mutates a
business resource.

The local tier mirrors the frozen owned-local family receipt
(LOCAL-OPERATOR-CLI-01, suite owned-local-operator-cli-v1): every parent and
every atom observation is projected PASS against its frozen shell
expectation.  The preprod tier defaults to an honest NOT_RUN projection with
the four named PREPROD_* design gaps, and an authorized injected observation
set projects per-row statuses but stays capped at BLOCKED_INVENTORIED
because the dual face stays mock-only until live effect admission and the
reviewed duo credentials land.  No tier ever fabricates a PASS: malformed
observations fail closed, missing observations stay NOT_RUN, shifted values
project FAIL, and the overall verdict is capped in every preprod shape.
"""
from __future__ import annotations

from typing import Any

from ..case_executor import CaseExecution, StepOutcome
from ..operator_cli_runner import OPERATOR_CLI_EFFECT
from ..remote_operator_cli_adapter import OPERATOR_CLI_BLOCKER
from ..run_state import RunStateError
from .contract import (
    ADAPTER_METHODS, CANONICAL_STATUS, CASE_ID, CLI_OPERATIONS, DUAL_CASE_ID,
    DUAL_COMMAND, DUAL_SUITE, FAMILY, HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV,
    LOCAL_EXECUTOR, LOCAL_STATUS, PARENT_STORY, PARENT_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT, DENSITY_OBLIGATION_COUNT, PREPROD_GAPS,
    PREPROD_INITIAL_VERDICT, PROFILE_CONTRACT, REPORT_CONTRACT, REQUIREMENTS,
    RESIDUAL_COUNT_GAP, SOURCE_FILE, SOURCE_SHA256, SUITE, COMPANION_SOURCES)
from .density import (ASSERTION_IDS, EXPECTED_LEAF_COUNTS, SITE_IDS, Obligation,
                      density_plan)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

OBSERVATION_MISMATCH = "OBSERVATION_MISMATCH"
OBSERVATION_ABSENT = "OBSERVATION_ABSENT"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"
ADJUDICATION_BLOCKED = "LOCAL_DUAL_OPERATOR_CLI_BLOCKED"
PREPROD_NOT_RUN_ERROR = "PREPROD_OPERATOR_CLI_FULL_STORY_NOT_RUN"

LOCAL_EXECUTION = "owned-local-offline-replay"
PREPROD_EXECUTION = "preprod-not-run"
PREPROD_INJECTED_EXECUTION = "preprod-injected-observation-projection"

_LOCAL_CLASSIFICATION = (
    "owned-local credential-free replay mirroring the frozen owned-local "
    "receipt; no preprod execution, no release credit")
_PREPROD_CLASSIFICATION = (
    "preprod density replay is not executed by this offline package; the row "
    "stays NOT_RUN with its named PREPROD_* gaps and no fabricated PASS")
_INJECTED_CLASSIFICATION = (
    "authorized observation injection projected per row; the dual face stays "
    "mock-only and the overall verdict stays capped")
_ABSENT_CLASSIFICATION = ("observation absent from the injected set; the row "
                          "stays NOT_RUN and never defaults to PASS")
_MISMATCH_CLASSIFICATION = ("observation mismatch; fail-close, never "
                            "normalized to a PASS")

MOCK_BASIS = (
    "deterministic owned-local mirror of the frozen LOCAL-OPERATOR-CLI-01 "
    "receipt (suite owned-local-operator-cli-v1): credential-free unauthenticated "
    "health, the complete five-trusted-team-Bot listing, the targeted read, the "
    "客服 discover hit, the visibility set-public plus exact restore round-trip, "
    "the connect session handle with token bytes scrubbed, the non-mutating web "
    "onboard registration URL, the direct-onboard persist trio and its "
    "read-once/delete-once/404 closure, the update-status echo round-trip with "
    "unchanged registry readback and busy restore, the creator PM/ENG group "
    "create/get round trip with exact scoped delete, the detached chat handle, "
    "the chat-run status plus authoritative cancel pair, the paginated "
    "list-groups shape validator, and the friend list after the single in-case "
    "CEO-ENG admission; no CLI binary, no harness child, no transport, no "
    "credential")

UPSTREAM_ROSTER = {
    "roster_verdict": "23绿/26",
    "parsed_green": 23,
    "parsed_total": 26,
    "basis": "coverage-map committed snapshot rows before this density wave",
}

# Frozen once-only product-CLI operation slices per parent obligation; the
# ordered union cross-checks the executor face at projection time.
PARENT_OPERATIONS = {
    "OPERATOR-CLI.health": ["operator_cli.health"],
    "OPERATOR-CLI.list": ["operator_cli.list"],
    "OPERATOR-CLI.get": ["operator_cli.get"],
    "OPERATOR-CLI.discover": ["operator_cli.discover"],
    "OPERATOR-CLI.visibility": ["operator_cli.visibility"],
    "OPERATOR-CLI.connect": ["operator_cli.connect"],
    "OPERATOR-CLI.onboard-web": ["operator_cli.onboard_web",
                                 "operator_cli.list_after_web_onboard"],
    "OPERATOR-CLI.direct-onboard": ["operator_cli.onboard_direct",
                                     "operator_cli.onboard_readback"],
    "OPERATOR-CLI.update-status": ["operator_cli.update_status"],
    "OPERATOR-CLI.create-group": ["operator_cli.create_group",
                                  "operator_cli.get_group"],
    "OPERATOR-CLI.chat": ["operator_cli.friend_request_chat",
                          "operator_cli.friend_accept_chat",
                          "operator_cli.chat"],
    "OPERATOR-CLI.list-groups": ["operator_cli.list_groups"],
    "OPERATOR-CLI.friend-list": ["operator_cli.friend_request_list",
                                 "operator_cli.friend_accept_list",
                                 "operator_cli.friend_list"],
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
        if comparator == "predicate":
            value: Any = True
        elif comparator == "exit_zero":
            value = 0
        elif comparator == "equals":
            value = expected
        elif comparator == "contains":
            value = "prefix " + str(expected) + " suffix"
        elif comparator == "contains_any":
            value = " ".join(str(needle) for needle in expected)
        else:
            value = expected
        atoms[row["id"]] = value
    return {"parents": parents, "atoms": atoms, "basis": MOCK_BASIS}


def _compare(comparator: str, observed: Any, expected: Any) -> bool:
    if comparator == "predicate":
        return observed is True
    if comparator == "equals":
        return type(observed) is type(expected) and observed == expected
    if comparator == "contains":
        return isinstance(observed, str) and str(expected) in observed
    if comparator == "contains_any":
        return (isinstance(observed, str)
                and any(str(needle) in observed for needle in expected))
    if comparator == "exit_zero":
        return observed == 0 and type(observed) is int
    return False


def _parent_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if entry == STATUS_PASS:
        return STATUS_PASS, None
    if entry == STATUS_NOT_RUN:
        return STATUS_NOT_RUN, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _atom_status(entry: Any, comparator: str,
                 expected: Any) -> tuple[str, Any, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None, None
    if _compare(comparator, entry, expected):
        return STATUS_PASS, entry, None
    return STATUS_FAIL, entry, OBSERVATION_MISMATCH


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


def _parent_operation_union() -> list[str]:
    return [operation for requirement in REQUIREMENTS
            for operation in PARENT_OPERATIONS[requirement]]


def _case_execution(tier: str, density: dict[str, str],
                    row_map: dict[str, Obligation]) -> CaseExecution:
    """Mirror the dual face's all-or-nothing 13-step adjudication."""
    per_parent_sites: dict[str, list[str]] = {
        requirement: [] for requirement in REQUIREMENTS}
    for leaf in ASSERTION_IDS:
        row = row_map[leaf]
        per_parent_sites[row.parent_obligation].append(row.executor_site_id)
    steps: list[StepOutcome] = []
    completed = 0
    first_bad: str | None = None
    blocking_error: str | None = None
    blocking_detail: str | None = None
    for index, requirement in enumerate(REQUIREMENTS):
        sites = per_parent_sites[requirement]
        evidence: dict[str, Any] = {
            "assertion_site_ids": sites,
            "assertion_site_count": len(sites),
            "cli_operations": PARENT_OPERATIONS[requirement],
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        }
        if tier == "preprod":
            evidence["preprod_verdict"] = STATUS_BLOCKED_INVENTORIED
            evidence["documented_gaps"] = list(PREPROD_GAPS)
            steps.append(StepOutcome(
                requirement_id=requirement, method=ADAPTER_METHODS[index],
                status=STATUS_NOT_RUN, data=None, evidence=evidence,
                error_type=None))
            continue
        parent_green = density.get(requirement) == STATUS_PASS
        sites_green = all(density.get(site) == STATUS_PASS for site in sites)
        if parent_green and sites_green:
            steps.append(StepOutcome(
                requirement_id=requirement, method=ADAPTER_METHODS[index],
                status=STATUS_PASS, data=True, evidence=evidence,
                error_type=None))
            completed += 1
            continue
        if first_bad is None:
            first_bad = requirement
            failing = [key for key in (requirement, *sites)
                       if density.get(key) == STATUS_FAIL]
            absent = [key for key in (requirement, *sites)
                      if density.get(key) == STATUS_NOT_RUN]
            blocking_error = (OBSERVATION_MISMATCH if failing
                              else OBSERVATION_ABSENT)
            blocking_detail = ("operator-cli density projection refused the "
                               "all-or-nothing receipt; failing rows: "
                               + ",".join(failing) + "; absent rows: "
                               + ",".join(absent))
        steps.append(StepOutcome(
            requirement_id=requirement, method=ADAPTER_METHODS[index],
            status=STATUS_NOT_RUN, data=None, evidence=evidence,
            error_type=blocking_error if requirement == first_bad else None,
            error_detail=blocking_detail if requirement == first_bad else None))
    if tier == "preprod":
        return CaseExecution(
            case_id=DUAL_CASE_ID, parent_id=PARENT_STORY, status="BLOCKED",
            steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
            error_type=PREPROD_NOT_RUN_ERROR, ready_to_execute=False,
            error_detail="preprod operator CLI story not executed by this "
                         "offline package; the four named PREPROD_* gaps stay "
                         "on file")
    ok = completed == len(REQUIREMENTS)
    return CaseExecution(
        case_id=DUAL_CASE_ID, parent_id=PARENT_STORY,
        status="PASS" if ok else "BLOCKED", steps=tuple(steps),
        completed_count=completed, total_count=len(REQUIREMENTS),
        error_type=None if ok else (blocking_error or ADJUDICATION_BLOCKED),
        ready_to_execute=ok,
        error_detail=None if ok else (blocking_detail
                                      or "operator-cli density projection not green"))


def _adjudication_summary(adjudication: CaseExecution) -> dict[str, Any]:
    snapshot = adjudication.snapshot()
    summary = dict(snapshot)
    summary["steps"] = [
        {"requirement_id": step["requirement_id"], "method": step["method"],
         "status": step["status"],
         "assertion_site_count": len(step["evidence"].get("assertion_site_ids", [])),
         "cli_operations": step["evidence"].get("cli_operations"),
         "preprod_verdict": step["evidence"].get("preprod_verdict"),
         "documented_gaps": step["evidence"].get("documented_gaps"),
         "remote_validation": step["evidence"].get("remote_validation"),
         "grants_release_credit": step["evidence"].get("grants_release_credit"),
         "error_type": step["error_type"], "error_detail": step["error_detail"]}
        for step in snapshot["steps"]]
    return summary


def project(observations: dict[str, Any] | None = None, *,
            tier: str = "local") -> dict[str, Any]:
    """Project observations into a 28-row honest density receipt."""
    if tier not in ("local", "preprod"):
        raise RunStateError("operator-cli replay tier must be local or preprod")
    injected_preprod = tier == "preprod" and observations is not None
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    if not isinstance(observations, dict):
        raise RunStateError("operator-cli replay observations must be an object")
    plan = density_plan()
    plan_rows = plan["obligations"]
    if len(plan_rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("operator-cli replay plan caliber changed")
    if plan["assertion_ids"] != list(ASSERTION_IDS):
        raise RunStateError("operator-cli replay assertion identity changed")
    if plan["site_ids"] != list(SITE_IDS):
        raise RunStateError("operator-cli replay site identity changed")
    if _parent_operation_union() != list(CLI_OPERATIONS):
        raise RunStateError("operator-cli parent operation union changed")
    row_map = {row["id"]: Obligation(
        obligation_id=row["id"], level=row["level"], method=row["method"],
        line_start=row["line_start"], line_end=row["line_end"],
        quote=row["quote"], parent_obligation=row["parent_obligation"],
        expectation=row["expectation"], operation=row["operation"],
        field=row["field"], comparator=row["comparator"],
        expected=row["expected"], executor_site_id=row["executor_site_id"],
        slot=row["slot"], story_label=row["story_label"],
        source_name=row["source_name"]) for row in plan_rows}
    parent_states = observations.get("parents", {})
    atom_states = observations.get("atoms", {})
    if not isinstance(parent_states, dict) or not isinstance(atom_states, dict):
        raise RunStateError("operator-cli replay observation layers must be objects")
    density: dict[str, str] = {}
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
        density[row["id"]] = status
        if row["executor_site_id"]:
            density[row["executor_site_id"]] = status
        rows.append({
            "obligation_id": row["id"], "level": row["level"], "method": row["method"],
            "status": status, "error_type": error_type, "observed": observed,
            "comparator": row["comparator"], "expected": row["expected"],
            "executor_site_id": row.get("executor_site_id"), "slot": row.get("slot"),
            "story_label": row.get("story_label"),
            "http_method": None, "http_path": None,
            "source": {"file": SOURCE_FILE, "source_name": row["source_name"],
                       "source_file": row["source_file"],
                       "line_start": row["line_start"], "line_end": row["line_end"],
                       "quote": row["quote"], "located": True},
            "parent_obligation": row["parent_obligation"],
            "expectation": row["expectation"],
            "evidence_event": event,
            "classification": _classification(tier, injected_preprod, status),
        })
    counts = {status: sum(1 for item in rows if item["status"] == status)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    if sum(counts.values()) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("operator-cli replay coverage is not total")
    green = counts[STATUS_PASS] == PARSED_OBLIGATION_COUNT
    if tier == "preprod":
        overall = STATUS_BLOCKED_INVENTORIED
    else:
        overall = (STATUS_PASS if green else STATUS_FAIL if counts[STATUS_FAIL]
                   else STATUS_BLOCKED_INVENTORIED)
    adjudication = _case_execution(tier, density, row_map)
    return {
        "schema_version": 1, "contract": "operator-cli-density-v1",
        "parent": PARENT_STORY,
        "status": overall,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "counts": counts,
        "obligations": rows,
        "family": {"family": FAMILY, "command": DUAL_COMMAND,
                   "dual_command": DUAL_COMMAND, "dual_case_id": DUAL_CASE_ID,
                   "legacy_id": PARENT_STORY, "source_case_id": CASE_ID,
                   "dual_suite": DUAL_SUITE, "source_suite": SUITE,
                   "profile_contract": PROFILE_CONTRACT,
                   "report_contract": REPORT_CONTRACT,
                   "local_executor": LOCAL_EXECUTOR,
                   "local_executor_status": "LOCAL_FULL_EXECUTOR",
                   "local_status": LOCAL_STATUS,
                   "canonical_status": CANONICAL_STATUS,
                   "preprod_initial_verdict": PREPROD_INITIAL_VERDICT,
                   "adapter_methods": list(ADAPTER_METHODS),
                   "cli_operations_count": len(CLI_OPERATIONS),
                   "environments": ["singlebox-product-cli",
                                    "preprod-product-cli"],
                   "cli_surface": "product-bcs-cli-operator",
                   "credential_free_local": True,
                   "blocker": OPERATOR_CLI_BLOCKER},
        "executor_row": {"index": 9, "coverage_row_no": 9,
                         "dual_command": DUAL_COMMAND},
        "source": {"file": SOURCE_FILE, "sha256": SOURCE_SHA256,
                   "story_line_span": [1784, 1799],
                   "companion_sources": dict(COMPANION_SOURCES)},
        "case_adjudication": _adjudication_summary(adjudication),
        "executor_catalog": plan["executor_catalog"],
        "harness": plan["harness"],
        "story_arguments": plan["story_arguments"],
        "story_marker": plan["story_marker"],
        "mock_child": plan["mock_child"],
        "resource_disposition": OPERATOR_CLI_EFFECT,
        "per_parent_atom_counts": dict(EXPECTED_LEAF_COUNTS),
        "zero_atom_parents": plan["zero_atom_parents"],
        "not_charged_source_probes": plan["not_charged_source_probes"],
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "upstream_issue": plan["upstream_issue"],
        "named_preprod_gaps": [{"id": gap, "status": "NAMED_DESIGN_GAP",
                                "blocks": "preprod PASS credit"}
                               for gap in PREPROD_GAPS],
        "preprod_credit_cap": (
            "the dual-operator-cli-run face stays mock-only until live effect "
            "admission lands; even an authorized injected observation set cannot "
            "convert this receipt into preprod PASS credit") if tier == "preprod"
        else None,
        "execution": (LOCAL_EXECUTION if tier == "local"
                      else PREPROD_INJECTED_EXECUTION if injected_preprod
                      else PREPROD_EXECUTION),
        "transport_admission": ("in-memory observations only; no owned runtime, "
                                "no story harness child, no CLI binary, no "
                                "transport connection, and no credential is "
                                "opened or resolved by this package"),
        "environment_tier": tier,
        "preprod_observations_injected": injected_preprod,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }

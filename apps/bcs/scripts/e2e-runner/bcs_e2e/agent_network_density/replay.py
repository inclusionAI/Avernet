"""Credential-free offline replay for the agent-network density contract.

The replay consumes only an in-memory observation dictionary (parent leg
citations plus verbatim parsed-record texts) and projects the frozen
11-row density plan.  It never launches the owned BCS runtime, starts the
story child, opens a network connection, resolves a credential, or mutates a
business resource.  The agent_network family is deliberately outside the
dynamic_family registry, so this module mirrors the owned-local reporting
face statically (LOCAL-NETWORK-01, suite owned-local-network-v1) instead of
resolving a dynamic-family adjudication contract; nothing here re-executes
any leg.

Projection semantics:

- The eight parent rows mirror the committed evidence citation: locally the
  r7 preprod PASS face and the owned-local LOCAL-NETWORK-01 conclusion; the
  mirror never claims fresh execution.
- The three density leaves are frozen upstream parsed-record mirrors, not
  probes: a structurally matching observation projects NOT_RUN by design
  (observing the upstream record is not executing the assertion), a drifted
  text fail-closes to FAIL, and an absent record stays NOT_RUN.
- The preprod tier defaults every row to an honest NOT_RUN with the three
  named PREPROD_* design gaps; even an authorized injected observation set
  stays capped at NOT_RUN overall because no leg of this offline package can
  mint preprod PASS credit.

No tier ever fabricates a PASS at the receipt level.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from .obligations import (
    ACTOR_SHA256, CASE_ID, COMMON_SHA256, CONTRACT_FILE, DENSITY_CONTRACT,
    DUAL_COMMANDS, DENSITY_OBLIGATION_COUNT, EXECUTOR_ROW_INDEX, PARENT,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT, NAMED_PREPROD_GAPS, NAMED_PREPROD_GAP_DETAILS,
    READ_CASE_KEYS, READ_REPORT_CONTRACT, REGISTER_REPORT_CONTRACT,
    RESIDUAL_COUNT_GAP, RUNNER_SHA256, SOURCE_FILE, SOURCE_SHA256,
    STATUS_REPORT_CONTRACT, STATIC_CATALOG_EXPECTED, STORY_LINE_SPAN, SUITE,
    UPSTREAM_ROSTER, density_plan)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
EXECUTION_LOCAL = "owned-local-offline-mirror"
EXECUTION_PREPROD = "preprod-not-run"
EXECUTION_INJECTED = "preprod-injected-observation-projection"

OBSERVATION_MISMATCH = "OFFLINE_MIRROR_OBSERVATION_MISMATCH"
OBSERVATION_ABSENT = "OFFLINE_MIRROR_OBSERVATION_ABSENT"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_DENSITY_MIRROR_NOT_RUN"
ADJUDICATION_NOT_EXECUTED = "OFFLINE_DENSITY_MIRROR_NOT_EXECUTION"
ADJUDICATION_MISMATCH = "OFFLINE_DENSITY_MIRROR_OBSERVATION_MISMATCH"

MOCK_BASIS = (
    "deterministic credential-free offline mirror: the eight parent rows cite "
    "the committed r7 preprod PASS face and the owned-local LOCAL-NETWORK-01 "
    "conclusion; the three density leaves carry the verbatim frozen poolab "
    "parsed-record texts; no owned runtime, no story child, no CLI, no "
    "network, no credential")

FAMILY_FACE = {
    "family": "agent_network (static mirror; deliberately not registered in "
              "the dynamic_family map)",
    "legacy_id": PARENT,
    "dual_commands": list(DUAL_COMMANDS),
    "read_case_keys": list(READ_CASE_KEYS),
    "report_contracts": {"read": READ_REPORT_CONTRACT,
                         "register": REGISTER_REPORT_CONTRACT,
                         "status": STATUS_REPORT_CONTRACT},
    "source_case_id": CASE_ID,
    "source_suite": SUITE,
    "local_executor": "bcs_e2e.local_network_run.local_network_run",
    "local_status": "LOCAL_NETWORK_PASS",
    "canonical_status": "LOCAL_SUITE_PASS",
    "completion_contract": "owned-local-network-completion-v1",
    "rc_semantics": "local-network-run rc=0 iff LOCAL_NETWORK_PASS; rc=130 "
                    "CANCELLED; rc=2 otherwise",
    "committed_preprod_face": "PASS(r7) 8/8 leaves (r7-progress.tsv "
                              "network×8 rc=0); this density wave keeps the "
                              "committed face notes-only",
    "remote_validation": "NOT_RUN",
    "grants_release_credit": False,
}

OWNED_LOCAL_LEDGER = [
    "initial-fixture-ceo", "initial-fixture-pm", "initial-fixture-eng",
    "initial-fixture-qa", "initial-bots-my-snapshot",
    "platform-health", "platform-me", "platform-repair-info",
    "platform-missing-secret", "actor-list", "actor-search",
    "actor-status-before", "actor-status-hidden", "actor-status-hidden-readback",
    "actor-status-online", "actor-status-online-readback", "actor-status-final",
    "bots-my", "bots-paged", "bots-query", "register-token",
    "register-owned-bot", "registered-owner-proof", "onboard-owner-proof",
    "onboard-owned-bot", "browser-url-owner-proof", "browser-onboard-url",
    "admin-owner-proof", "admin-reonboard-owned-bot", "admin-detail-readback",
    "delete-owner-proof", "delete-owned-bot", "deleted-bot-404",
    "final-fixture-ceo", "final-fixture-pm", "final-fixture-eng",
    "final-fixture-qa", "final-bots-my-snapshot"]


def mock_observations() -> dict[str, Any]:
    """Return the deterministic credential-free offline mirror fixture."""
    plan = density_plan()
    parents = {parent: STATUS_PASS for parent in plan["parent_ids"]}
    atoms: dict[str, Any] = {}
    for row in plan["obligations"]:
        if row["level"] == "DENSITY":
            atoms[row["id"]] = row["expected"]
    return {"parents": parents, "atoms": atoms, "basis": MOCK_BASIS}


def _parent_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if entry == STATUS_PASS:
        return STATUS_PASS, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _atom_status(entry: Any, expected: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, OBSERVATION_ABSENT
    if isinstance(entry, str) and entry == expected:
        return STATUS_NOT_RUN, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _classification(tier: str, injected: bool, status: str,
                    level: str) -> str:
    if status == STATUS_FAIL:
        return ("observation text drifted from the frozen snapshot record; "
                "fail-close, never normalized to a PASS")
    if tier == "preprod" and injected:
        return ("authorized injected citation; the row never becomes preprod "
                "PASS credit because the offline package is a mirror, not a "
                "probe")
    if status == STATUS_NOT_RUN:
        if tier == "preprod" and not injected:
            return ("preprod density mirror is not executed by this offline "
                    "package; the row stays NOT_RUN with its named PREPROD_* "
                    "gaps and no fabricated PASS")
        if tier == "preprod":
            return ("authorized injected citation; the row never becomes "
                    "preprod PASS credit because the offline package is a "
                    "mirror, not a probe")
        if level == "DENSITY":
            return ("upstream parsed record mirrored as frozen evidence; the "
                    "mirror is not a local probe and never projects PASS")
        return ("committed leg citation absent from the observation set; the "
                "row stays NOT_RUN and never defaults to PASS")
    return ("committed leg citation mirrored from the r7 preprod PASS face "
            "and the owned-local LOCAL-NETWORK-01 conclusion; not re-executed "
            "by this package")


def _contract_rows() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / CONTRACT_FILE
    return json.loads(path.read_text(encoding="utf-8"))["requirements"]


def _static_site_counts(plan: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for requirement in plan["parent_ids"]:
        counts[requirement] = 0
    for row in _contract_rows():
        source = "stories.sh" if row["source"] == "stories.sh" else "actor.sh"
        counts[row["id"]] = STATIC_CATALOG_EXPECTED[source][row["function"]]["total"]
    return counts


def _adjudication(parent_rows: list[dict[str, Any]], overall: str,
                  plan: dict[str, Any]) -> dict[str, Any]:
    static = _static_site_counts(plan)
    charged: dict[str, int] = {requirement: 0 for requirement in plan["parent_ids"]}
    for row in plan["obligations"]:
        if row["level"] == "DENSITY":
            charged[row["parent_obligation"]] = charged.get(row["parent_obligation"],
                                                            0) + 1
    steps = []
    for row in parent_rows:
        requirement = row["obligation_id"]
        steps.append({
            "requirement_id": requirement,
            "status": row["status"],
            "mirror": True,
            "provenance": "committed r7 preprod PASS citation and owned-local "
                          "LOCAL-NETWORK-01 face; not re-executed here",
            "assertion_site_count": static.get(requirement),
            "upstream_parsed_records": charged.get(requirement, 0),
            "preprod_verdict": "PASS(r7) committed citation; unchanged by this "
                               "notes-only density wave",
            "documented_gaps": list(NAMED_PREPROD_GAPS),
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
    completed = sum(1 for step in steps if step["status"] == STATUS_PASS)
    return {
        "case_id": CASE_ID, "parent_id": PARENT, "status": overall,
        "completed_count": completed, "total_count": PARENT_OBLIGATION_COUNT,
        "error_type": (None if overall != STATUS_FAIL
                       else ADJUDICATION_MISMATCH),
        "ready_to_execute": False,
        "mirror": True,
        "provenance": "static mirror of the owned-local reporting face "
                      "(owned-local-network-v1; V1_REQUIREMENTS 8; "
                      "expected_assertion_count 8); no re-execution",
        "steps": steps,
    }


def project(observations: dict[str, Any] | None = None,
            *, tier: str = "local") -> dict[str, Any]:
    """Project observations into the honest 11-row density mirror receipt."""
    if tier not in ("local", "preprod"):
        raise RunStateError("agent-network replay tier must be local or preprod")
    injected_preprod = tier == "preprod" and observations is not None
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    if not isinstance(observations, dict):
        raise RunStateError("agent-network replay observations must be an object")
    plan = density_plan()
    plan_rows = plan["obligations"]
    if len(plan_rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("agent-network replay plan caliber changed")
    parent_states = observations.get("parents", {})
    atom_states = observations.get("atoms", {})
    basis = observations.get("basis")
    if (not isinstance(parent_states, dict) or not isinstance(atom_states, dict)
            or (basis is not None and not isinstance(basis, str))):
        raise RunStateError("agent-network replay observation layers must be "
                            "objects and the basis must be a string when set")
    rows: list[dict[str, Any]] = []
    for row in plan_rows:
        force_not_run = tier == "preprod" and not injected_preprod
        if row["level"] == "PARENT":
            if force_not_run:
                entry = None
                status, error_type = STATUS_NOT_RUN, PREPROD_EXECUTION_NOT_RUN
            else:
                entry = parent_states.get(row["id"])
                if entry is None:
                    status, error_type = STATUS_NOT_RUN, OBSERVATION_ABSENT
                elif entry == STATUS_PASS:
                    status, error_type = STATUS_PASS, None
                else:
                    status, error_type = STATUS_FAIL, OBSERVATION_MISMATCH
            observed = entry
            event = ("preprod_story_not_executed_offline" if force_not_run
                     else "committed_leg_citation_mirror" if status == STATUS_PASS
                     else "observation_mismatch" if status == STATUS_FAIL
                     else "citation_absent")
        else:
            entry = None if force_not_run else atom_states.get(row["id"])
            if force_not_run:
                status, error_type = STATUS_NOT_RUN, PREPROD_EXECUTION_NOT_RUN
            else:
                status, error_type = _atom_status(entry, row["expected"])
            observed = entry
            event = ("preprod_story_not_executed_offline" if force_not_run
                     else "upstream_record_mirrored_offline"
                     if status == STATUS_NOT_RUN and error_type is None
                     else "observation_mismatch" if status == STATUS_FAIL
                     else "observation_absent")
        rows.append({
            "obligation_id": row["id"], "level": row["level"],
            "method": row["method"], "status": status, "error_type": error_type,
            "observed": observed, "comparator": row["comparator"],
            "expected": row["expected"],
            "source": {"file": row["source_file"],
                       "line_start": row["line_start"], "line_end": row["line_end"],
                       "quote": row["quote"], "located": True},
            "parent_obligation": row["parent_obligation"],
            "expectation": row["expectation"],
            "evidence_event": event,
            "classification": _classification(tier, injected_preprod, status,
                                              row["level"]),
        })
    counts = {status: sum(1 for item in rows if item["status"] == status)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    if sum(counts.values()) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("agent-network replay coverage is not total")
    overall = STATUS_FAIL if counts[STATUS_FAIL] else STATUS_NOT_RUN
    parent_rows = [item for item in rows if item["level"] == "PARENT"]
    return {
        "schema_version": 1, "contract": DENSITY_CONTRACT, "parent": PARENT,
        "status": overall,
        "overall_basis": "density leaves are frozen upstream record mirrors, "
                         "not probes; the receipt never projects PASS at the "
                         "overall level offline",
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "counts": counts,
        "obligations": rows,
        "family": dict(FAMILY_FACE),
        "executor_row": {"index": EXECUTOR_ROW_INDEX, "coverage_row_no": 2,
                         "dual_commands": list(DUAL_COMMANDS)},
        "source": {"file": SOURCE_FILE, "sha256": SOURCE_SHA256,
                   "story_line_span": list(STORY_LINE_SPAN),
                   "companion_sources": {"actor.sh": ACTOR_SHA256,
                                         "common.sh": COMMON_SHA256,
                                         "e2e.sh": RUNNER_SHA256}},
        "case_adjudication": _adjudication(parent_rows, overall, plan),
        "parsed_absorption": dict(plan["parsed_absorption"]),
        "snapshot_provenance": dict(plan["snapshot_provenance"]),
        "static_statement_catalog": dict(plan["static_statement_catalog"]),
        "emission_chain_disclosure": dict(plan["emission_chain_disclosure"]),
        "wave_scope_unreached_parents": dict(plan["wave_scope_unreached_parents"]),
        "not_charged_source_probes": list(plan["not_charged_source_probes"]),
        "story_marker": {"note": "the shell story family emits no single "
                                 "machine marker line; the runner story loop "
                                 "and the require_status guard are the "
                                 "observable flow anchors"},
        "mock_child": {"present": False,
                       "note": "owned-local single-stack executor; no "
                               "Provider/Judge mock child; the panel "
                               "index.umd.js digest probe replaces the "
                               "upstream manifest asset fetch semantics"},
        "owned_local_ledger": {
            "operations": list(OWNED_LOCAL_LEDGER),
            "operation_count": len(OWNED_LOCAL_LEDGER),
            "fixture_bookends": 10,
            "business_operations": 28,
            "caliber": "the owned-local 38-operation ledger contains no "
                       "manifest/asset-404 segment: the owned-local executor "
                       "substitutes a panel asset digest probe for the "
                       "upstream manifest asset fetch leg",
            "resource_disposition": "TEMPORARY_BOT_DELETED_FIXTURES_RETAINED",
            "expected_assertion_count": 8,
            "mock_boundary_not_proven": ["remote authentication and "
                                         "authorization", "Bot engine reasoning",
                                         "remote onboarding and cleanup",
                                         "full release qualification"]},
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "named_preprod_gaps": [{"id": gap, "status": "NAMED_DESIGN_GAP",
                                "detail": NAMED_PREPROD_GAP_DETAILS[gap],
                                "blocks": "preprod PASS credit"}
                               for gap in NAMED_PREPROD_GAPS],
        "preprod_credit_cap": ("this offline density package opens no "
                               "transport and its three leaves are frozen "
                               "record mirrors, not probes; authorized "
                               "injection never converts a row into preprod "
                               "PASS credit and the committed DUAL/PASS r7 "
                               "face stays untouched") if tier == "preprod" else None,
        "execution": (EXECUTION_LOCAL if tier == "local"
                      else EXECUTION_INJECTED if injected_preprod
                      else EXECUTION_PREPROD),
        "transport_admission": ("in-memory observations only; no owned runtime, "
                                "no story child, no CLI, no network connection, "
                                "and no credential is opened or resolved by "
                                "this package"),
        "environment_tier": tier,
        "preprod_observations_injected": injected_preprod,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"],
    }

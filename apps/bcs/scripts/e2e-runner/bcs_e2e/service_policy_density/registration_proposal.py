"""Describe-only mainline integration proposal for the Wave-D completion.

The fifteen marker-completion leaves ``SERVICE-POLICY.F33``-``F47`` need **no
new registered command**: they ride the existing
``dual-service-policy-density-run`` envelope (the landed first wave's
wrapper command, already registered in ``cli.py`` and
``case_command_registration.py`` and already planning-declared in
``inventory.py``), because every completion leaf binds one of the nineteen
reviewed station operations of that envelope.  This module freezes the exact
integration text an authorized wave can apply plus the honest accounting
increments for the red-lined shared faces (``executor-migration.json``,
``coverage-map/coverage-map-20261031.json``, ``inventory.py``) - **nothing
here is applied**: ``applied`` stays false,
``new_registration_command`` stays false and
``shared_face_files_touched`` stays empty.

The contested 410 unit is disclosed, never silently closed: the proposal
carries the named product/form-difference item, the coverage-map M11 defect
registry membership, and the P4 material gate association into every shared
note it proposes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from .completion import (
    COMPLETION_GAP_ID,
    COMPLETION_LEAF_COUNT,
    COMPLETION_WAVE_TOKEN,
    COVERAGE_MAP_MATERIAL_GAP,
    FORM_DIFFERENCE_ITEM_ID,
    FORM_DIFFERENCE_NAMED_ITEM,
    LEDGER_GAP,
    MATERIAL_GATE,
    MARKER_CHARGED_CHECKS,
    P4_GATE_ID,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    RESIDUAL_COUNT_GAP,
    R1_WAVE_TOKEN,
    SOURCE_FILE,
    STORY_PY_SHA256,
    STORY_SH_FILE,
    STORY_SH_SHA256,
    build_service_policy_density_completion_obligations,
    completion_reconciliation,
    marker_unit_disposition,
)
from .contract import CASE_ID, DUAL_SERVICE_POLICY_DENSITY_COMMAND

REPO_ROOT = Path(__file__).resolve().parents[6]
PROPOSAL_STATUS = "DESCRIBE_ONLY"
APPLIED = False
NEW_REGISTRATION_COMMAND = False
# The Wave-D registration proposal token; the describe-only proposal
# fragments and the frozen completion wave token are the same string by
# design so the shipped reconcile layer and this proposal cannot drift.
PROPOSAL_TOKEN = "SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031"
ENVELOPE_COMMAND = DUAL_SERVICE_POLICY_DENSITY_COMMAND
EXECUTOR_ROW_INDEX = 38
EXECUTOR_ROW_ID = PARENT
COVERAGE_MAP_ROW = 19

# Read-only anchors that must exist verbatim in the current tree; a drift here
# means the proposal text needs re-review, not silent rebasing.  All anchor
# files are outside this wave's write domain - they are verified, never edited.
ANCHORS: dict[str, tuple[str, ...]] = {
    "apps/bcs/scripts/e2e-runner/bcs_e2e/cli.py": (
        "from .service_policy_density.reporting import "
        "dual_service_policy_density_case_run",
        'elif args.command == "dual-service-policy-density-run":',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/case_command_registration.py": (
        "service_policy_density_case = commands.add_parser(",
        '"dual-service-policy-density-run",',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/inventory.py": (
        "    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031',",
        "    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031': 1,",
    ),
    "apps/bcs/scripts/e2e-runner/executor-migration.json": (
        '"id": "story_retired_service_policy"',
        '"SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031"',
    ),
    "apps/bcs/scripts/e2e-runner/coverage-map/coverage-map-20261031.json": (
        '"parsed_minus_obligations": 15',
        "形态差核对+P4 关联",
        '"id": "M11"',
        "service-policy 410 形态差",
        '"case_id": "story_retired_service_policy"',
    ),
}

PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE = (
    "density-projection(service_policy_density completion Wave-D): marker 23 "
    "closed (flat wrapper 8 + completion leaves 15 SERVICE-POLICY.F33-F47: "
    "legacy 7 / retired 1 / invocation 3 / guard 1 / conversion 3), "
    "residual_count_gap=0, family density rows 55 (R1 superset 40 + Wave-D 15, "
    "calibers never merged); contested 410 unit dual-charge visible: station "
    "410 frozen-anchor + sim-observed 200 documented-exception registered as "
    + FORM_DIFFERENCE_ITEM_ID + " (coverage-map " + COVERAGE_MAP_MATERIAL_GAP
    + " member) tied to P4 material gate " + P4_GATE_ID + "; owned-local "
    "completion replay 15/15 PASS station-form only; preprod completion leg "
    "honest NOT_RUN (" + COMPLETION_GAP_ID + ", gap ledger GAP-SP-D-01/02); "
    "remote_validation=NOT_RUN; grants_release_credit=false")

PROPOSED_COVERAGE_MAP_ROW_NOTE = (
    "密度完成 Wave-D：marker 23 收口（flat 8 + completion 15 叶 "
    "SERVICE-POLICY.F33-F47；legacy 7/retired 1/invocation 3/guard 1/"
    "conversion 3），residual_count_gap=0；形态差 410-vs-200 登记为 "
    "documented-exception 具名项（" + FORM_DIFFERENCE_ITEM_ID + "，M11 在案，"
    "P4 物料门控 " + P4_GATE_ID + " 关联披露，不静默改判）；owned-local 离线 "
    "15/15 PASS 仅站面口径；preprod 完成腿 NOT_RUN 具名 gap（GAP-SP-D-01/02）；"
    "remote_validation=NOT_RUN；grants_release_credit=false")


def assert_anchors() -> None:
    """Fail closed when a proposal anchor no longer matches the tree."""
    for relative, needles in ANCHORS.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RunStateError(f"registration anchor file missing: {relative}")
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise RunStateError(
                    f"registration anchor drifted in {relative}: {needle!r}")


def describe_only_mainline_fragment() -> str:
    """Return the propose-only integration text for an authorized wave."""
    return (
        "DESCRIBE_ONLY token=" + PROPOSAL_TOKEN + "\n"
        "- executor-migration.json row #38 story_retired_service_policy: append "
        "note " + COMPLETION_WAVE_TOKEN + " AFTER the existing " + R1_WAVE_TOKEN
        + " note (order preserved: R1 first, Wave-D second); no mode, verdict, "
        "gate or other note changes; the marker ledger closes (residual_count_gap=0: "
        "23 marker checks - 8 flat obligations - 15 completion leaves)\n"
        "- inventory.py: append the token to EXECUTOR_MIGRATION_NOTES and add "
        "its count entry (1) to _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS; the "
        "reviewed-release parent set and counts stay untouched\n"
        "- coverage-map row #19: append the proposed row note "
        "(形态差核对+P4 关联 disposition: registered-not-rejudged; named item "
        + FORM_DIFFERENCE_ITEM_ID + " rides the registered exception); the r8 "
        "authoritative BLOCKED verdict and the dual note stay byte-identical\n"
        "- envelope reuse: the leaves ride " + ENVELOPE_COMMAND + "; cli.py and "
        "case_command_registration.py stay byte-identical this wave, and no new "
        "parser/dispatch/registration is proposed\n"
        "- material gate " + P4_GATE_ID + ": closure of the contested 410 unit "
        "requires the P4 provider live-admission landing observation plus "
        "the M11 form-difference disposition; env names BCS_E2E_HUMAN_COOKIE "
        "/ BCS_E2E_DRIVER_TOKEN only, values never touched\n"
        "- red lines: executor-migration.json, coverage-map/*, inventory.py, "
        "cli.py, case_command_registration.py and every other session's write "
        "domain stay untouched by this proposal")


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration and accounting proposal."""
    assert_anchors()
    recon = completion_reconciliation()
    dispositions = marker_unit_disposition()
    return {
        "proposal_status": PROPOSAL_STATUS,
        "token": PROPOSAL_TOKEN,
        "applied": APPLIED,
        "shared_face_files_touched": [],
        "wave": {
            "layer": "MARKER_COMPLETION_WAVE_D",
            "contract": "service-policy-density-completion-v1",
            "wave_token": COMPLETION_WAVE_TOKEN,
            "prior_wave_token": R1_WAVE_TOKEN,
            "completion_leaves": COMPLETION_LEAF_COUNT,
            "ledger_gap": LEDGER_GAP,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
        },
        "envelope": {
            "command": ENVELOPE_COMMAND,
            "case_id": CASE_ID,
            "new_registration_command": NEW_REGISTRATION_COMMAND,
            "reuse_statement": (
                "the fifteen completion leaves bind twelve of the nineteen "
                "reviewed station operations already registered on the dual "
                "service policy density envelope; parser, dispatch and "
                "registration stay byte-identical"),
            "cli_required": False,
            "case_command_registration_required": False,
        },
        "executor_migration": {
            "row_index": EXECUTOR_ROW_INDEX,
            "row_id": EXECUTOR_ROW_ID,
            "notes_before": [R1_WAVE_TOKEN],
            "notes_append_after": [R1_WAVE_TOKEN, COMPLETION_WAVE_TOKEN],
            "placement": (
                "append the Wave-D token to the row notes list AFTER the "
                "existing R1 token; the row id, mode DUAL, preprod_verdict "
                "NOT_RUN and gates list stay unchanged"),
            "proposed_row_note": PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE,
        },
        "inventory": {
            "declaration": "EXECUTOR_MIGRATION_NOTES",
            "note_counts_declaration": "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
            "proposed_count": 1,
            "statement": (
                "one new token entry with count 1; PARSED_OBLIGATION_COUNT and "
                "reviewed-release declarations stay untouched"),
        },
        "coverage_map": {
            "row": COVERAGE_MAP_ROW,
            "case_id": EXECUTOR_ROW_ID,
            "gap_kind_on_file": "COUNT_GAP+DEFECT?",
            "parsed_minus_obligations_on_file": 15,
            "residual_count_gap_after_wave": RESIDUAL_COUNT_GAP,
            "queue_basis_on_file": "delta=15×2；形态差核对+P4 关联",
            "form_difference_disposition": (
                "registered-not-rejudged: named item " + FORM_DIFFERENCE_ITEM_ID
                + " rides the coverage-map increment; the queue basis "
                "形态差核对 is discharged as registration plus P4 material-gate "
                "association, never as a silent verdict change"),
            "material_gap_member": COVERAGE_MAP_MATERIAL_GAP,
            "proposed_row_note": PROPOSED_COVERAGE_MAP_ROW_NOTE,
        },
        "material_gate": dict(MATERIAL_GATE),
        "registered_exception": dict(FORM_DIFFERENCE_NAMED_ITEM),
        "gap_ledger": {key: dict(value) for key, value in
                       (("GAP-SP-D-01", recon["gap_ledger"]["GAP-SP-D-01"]),
                        ("GAP-SP-D-02", recon["gap_ledger"]["GAP-SP-D-02"]))},
        "accounting": {
            "marker_charged_checks": MARKER_CHARGED_CHECKS,
            "flat_wrapper_obligations": 8,
            "ledger_gap": LEDGER_GAP,
            "completion_leaves": COMPLETION_LEAF_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "marker_unit_disposition_counts": recon[
                "marker_unit_disposition_counts"],
            "contested_unit": recon["contested_site"],
            "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
            "basis_counts": {"frozen-anchor": 14, "documented-exception": 1},
        },
        "source_freeze": {
            "file": SOURCE_FILE,
            "shell_file": STORY_SH_FILE,
            "story_py_sha256": STORY_PY_SHA256,
            "story_sh_sha256": STORY_SH_SHA256,
        },
        "red_lines": [
            "executor-migration.json (proposal note only; apply is an "
            "authorized wave action)",
            "coverage-map/coverage-map-20261031.json (proposal note only)",
            "bcs_e2e/inventory.py (proposal note only)",
            "bcs_e2e/cli.py",
            "bcs_e2e/case_command_registration.py",
            "bcs_e2e/workspace-migration-contract.json",
            "bcs_e2e/webhook_subscription*/**",
            "bcs_e2e/slow_judge/**",
            "bcs_e2e/edge_revoke_lifecycle/**",
            "bcs_e2e/delivery_density/**",
            "other parallel-session write domains",
        ],
        "user_actions": [
            "observe the P4 provider live-admission landing (" + P4_GATE_ID + ")",
            "authorize an admitted preprod station observation of PATCH "
            "/groups/{group_id}/settings under explicit user authorization "
            "(env names BCS_E2E_HUMAN_COOKIE / BCS_E2E_DRIVER_TOKEN only)",
            "disposition the M11 service-policy 410 sim/station form difference "
            "through product",
            "land the notes increments above in an authorized wave after the "
            "focused completion tests re-run green",
        ],
        "non_goals": [
            "this proposal modifies no shared face file (executor-migration."
            "json, coverage-map/*, inventory.py, cli.py, "
            "case_command_registration.py all untouched this wave)",
            "this proposal registers no new CLI command and reuses the landed "
            "dual-service-policy-density-run envelope",
            "this proposal never re-judges the frozen 410 station form or the "
            "flat/R1 rows; the sim observation of 200 stays registered "
            "evidence",
            "this proposal triggers no transport, resolves no credential, and "
            "grants no release credit",
            "this proposal fabricates no preprod PASS: the completion preprod "
            "leg stays the honest NOT_RUN gap " + COMPLETION_GAP_ID,
        ],
        "case_scope": {
            "case_id": CASE_ID,
            "parent_id": EXECUTOR_ROW_ID,
            "completion_leaves": COMPLETION_LEAF_COUNT,
            "leaf_ids": ["SERVICE-POLICY.F" + f"{index:02d}"
                         for index in range(33, 33 + COMPLETION_LEAF_COUNT)],
        },
        "drop_dead_checks": {
            "leaves_built": len(build_service_policy_density_completion_obligations()),
            "dispositions_built": len(dispositions),
            "upstream_sha256_frozen": True,
        },
    }

"""Describe-only mainline integration proposal for the density backfill.

The Wave-D1 density backfill needs **no new registered command**: the 14
parsed clauses ride the existing ``dual-direct-chat-run`` envelope and its
DualDirectChatAdapter journey, because the clause roster (S13) observes
exactly the four main-journey wire operations the DM case already records.
This module freezes the exact integration text a future authorized wave can
apply, plus the honest accounting increments for the red-lined shared faces
(``executor-migration.json``, ``coverage-map/*``, ``inventory.py`` and the
``migration_inventory`` planning declarations) — **nothing here is applied**;
``applied`` stays False and ``shared_face_files_touched`` stays empty.

Red-line discipline of this wave: ``bcs_e2e/cli.py``,
``bcs_e2e/case_command_registration.py``, ``bcs_e2e/inventory.py``,
``bcs_e2e/migration_inventory.py`` and the coverage map stay untouched; the
projection itself lives entirely in this offline subpackage and its focused
tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from .contract import CASE_ID, DENSITY_ASSERTION_COUNT, PARENT

REPO_ROOT = Path(__file__).resolve().parents[6]
PROPOSAL_STATUS = "DESCRIBE_ONLY"
APPLIED = False
ENVELOPE_COMMAND = "dual-direct-chat-run"

# Read-only anchors that must exist verbatim in the current tree; a drift here
# means the proposal text needs re-review, not silent rebasing.
ANCHORS: dict[str, tuple[str, ...]] = {
    "apps/bcs/scripts/e2e-runner/bcs_e2e/direct_chat_cli_case_run.py": (
        "from .case_executor import CaseExecution, StepOutcome, record_error_detail",
        'projections["ping_terminal"] = ping_terminal.data',
        '        "resource_disposition": _CASES[case_key]["fixture_effect"],',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/cli.py": (
        "from .direct_chat_cli_case_run import dual_direct_chat_case_run",
        'elif args.command == "dual-direct-chat-run":',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/case_command_registration.py": (
        '    direct_chat_case = commands.add_parser(',
        '        "dual-direct-chat-run",',
    ),
}

HUNK_A_IMPORTS = (
    "from .direct_conversation_density.run import (\n"
    "    run_direct_conversation_density,\n"
    "    blocked_direct_density_receipt,\n"
    ")\n"
    "from .direct_conversation_density.transport import ProjectionReplayTransport"
)

HUNK_A_BODY = (
    "        # Wave-D1 density backfill (describe-only proposal this wave):\n"
    "        # evaluate the 14 parsed S13 clauses against the recorded DM\n"
    "        # journey projections; the strict DM admission stays untouched.\n"
    "        if error_type is None:\n"
    "            density_receipt = run_direct_conversation_density(\n"
    "                profile=adapter.profile,\n"
    "                transport=ProjectionReplayTransport(projections),\n"
    "            )\n"
    "        else:\n"
    "            density_receipt = blocked_direct_density_receipt(\n"
    "                profile=adapter.profile,\n"
    "                blocked_by=(\n"
    "                    'DM journey fail-closed before density evaluation: '\n"
    "                    + str(error_type)),\n"
    "            )\n"
)

HUNK_A_STEP = (
    "    def step(requirement_id: str, method: str, data: object,\n"
    "             operations: tuple[str, ...],\n"
    "             extra_evidence: dict | None = None) -> StepOutcome:\n"
    "        ...\n"
    "            evidence={\n"
    '                "requests": [dict(records[operation]) for operation in operations],\n'
    '                "fixture_effect": adapter.fixture_effect,\n'
    '                "remote_validation": "NOT_RUN",\n'
    '                "grants_release_credit": False,\n'
    "                **(extra_evidence or {}),\n"
    "            },\n"
    "            ...\n"
)

HUNK_A_TERMINAL_CALL = (
    '        step(DIRECT_CHAT_REQUIREMENTS[4], "read_main_terminal_run",\n'
    "             main_terminal_data if error_type is None else None,\n"
    "             (DIRECT_CHAT_MAIN_TERMINAL_OPERATION, DIRECT_CHAT_PING_CANCEL_OPERATION,\n"
    "              DIRECT_CHAT_PING_TERMINAL_OPERATION),\n"
    '             extra_evidence={"direct_conversation_density": density_receipt}),\n'
)

HUNK_A_CAVEATS = (
    "Additive evidence key: StepOutcome evidence dicts gain one key only on the "
    "DM.main-terminal step; baseline mock-adapter tests in "
    "tests/test_direct_chat_cli_case_run.py assert evidence shapes and an "
    "authorized wave must re-run them before applying.  "
    "adapter.profile is the existing DualDirectChatAdapter attribute; no new "
    "CLI flag, no new command, no parser change."
)

OPTIONAL_NEW_COMMAND_HUNKS = {
    "disposition": (
        "OPTIONAL AND NOT RECOMMENDED - envelope reuse above covers the "
        "registration need; recorded only so a future separate-command request "
        "has an exact shape"
    ),
    "case_command_registration.py": (
        "case_command_registration.py: append to the dual command block after "
        "the direct-chat parser:\n"
        "    direct_density_case = commands.add_parser(\n"
        '        "dual-direct-conversation-density-run",\n'
        '        help="Project the 14 parsed Direct-chat density clauses offline")\n'
        "    direct_density_case.add_argument(\"--profile\", type=Path, required=True)\n"
        "    direct_density_case.add_argument(\"--state-dir\", type=Path, required=True)\n"
        "    direct_density_case.add_argument(\"--run-id\", required=True)\n"
        "    direct_density_case.add_argument(\"--confirm-run-id\", required=True)\n"
        "    direct_density_case.add_argument(\"--allow-write\", action=\"store_true\")\n"
        "    ... dispatch to a wrapper that only calls "
        "run_direct_conversation_density with an injected owned-local transport"
    ),
    "cli.py": (
        "cli.py: import the wrapper and add one elif branch mirroring the "
        "L786 dual-direct-chat-run dispatch; note cli.py and "
        "case_command_registration.py are shared faces outside this wave's "
        "write domain - proposal only"
    ),
}

PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE = (
    "density-projection(direct_conversation_density): parsed 14/14 anchored "
    "(stories.sh L782-L804 incl. L789 guard; wave3 clause map S13); DM "
    "obligations 5 + density 14 = family 19 (obligation accounting 5->19 "
    "proposed); "
    "owned-local offline defect-faithful rehearsal 14/14 PASS under "
    "DM-TERMINAL-CANCEL-PROJECTION gate disclosure; preprod honest NOT_RUN "
    "receipt; GAP-DC-01 idempotency not fabricated; remote_validation=NOT_RUN; "
    "grants_release_credit=false; preprod_verdict stays BLOCKED_INVENTORIED"
)

PROPOSED_COVERAGE_MAP_INCREMENT = {
    "row": 13,
    "case_id": PARENT,
    "obligations": {"before": 5, "after": 19, "basis": "5 DM requirements + 14 DIRECT_DENSITY clause rows"},
    "parsed": {"snapshot_parsed_count": 14, "covered_by_density_rows": 14},
    "gap_kind": "COUNT_GAP+DEFECT",
    "gap_detail": (
        "count layer closed by the 14-row density projection (delta 9 absorbed: "
        "status clauses reinforce the DM parents, field/guard clauses close "
        "run/session id read-back, read-back state, cancel ack and terminal "
        "chain); execution gap and the cancel terminal projection defect stay "
        "open (GAP-DC-02/03)"
    ),
    "machine_totals": {
        "obligations_before": 674,
        "obligations_after": 688,
        "basis": "committed anchor 674 + 14 density child rows (proposal only)",
    },
    "verdict_unchanged": "DUAL / BLOCKED_INVENTORIED (r8 preprod BLOCKED 0/5 RunStateError)",
}

MATERIALS = [
    {
        "id": "M-DC-1",
        "item": "DM-TERMINAL-CANCEL-PROJECTION disposition: user decision on the "
                "DM-DIAG parked options (a) keep strict admission / (b) disclosed "
                "contract surgery for downstream_error-bearing CANCELLED terminals "
                "(queued_runtime.rs:85 design semantics)",
        "owner": "user sign-off + product",
        "blocks": "DM.main-cancel / DM.main-terminal preprod PASS",
    },
    {
        "id": "M-DC-2",
        "item": "authorized preprod rerun of dual-direct-chat-run "
                "(credential window refresh; env names BCS_E2E_HUMAN_COOKIE and "
                "BCS_E2E_DRIVER_TOKEN only — values never touched)",
        "owner": "user authorization",
        "blocks": "all 14 density clauses on the station tier (GAP-DC-03)",
    },
    {
        "id": "M-DC-3",
        "item": "cancel idempotency probe authorization (second POST cancel on "
                "the same Run); if executed, the result is a new gap-closing "
                "observation, NOT a parsed-roster change (GAP-DC-01)",
        "owner": "user authorization",
        "blocks": "idempotency evidence only",
    },
    {
        "id": "M-DC-4",
        "item": "apply HUNK A inside direct_chat_cli_case_run.py behind the "
                "baseline mock-adapter test suite (additive evidence key caveat)",
        "owner": "authorized landing wave",
        "blocks": "live density projection riding the DM receipt",
    },
]


def assert_anchors() -> None:
    """Fail closed when a proposal anchor no longer matches the tree."""
    for relative, needles in ANCHORS.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RunStateError(f"registration anchor file missing: {relative}")
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise RunStateError(f"registration anchor drifted in {relative}: {needle!r}")


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration and accounting proposal."""
    assert_anchors()
    return {
        "proposal_status": PROPOSAL_STATUS,
        "applied": APPLIED,
        "shared_face_files_touched": [],
        "finding": (
            "no new registered command is structurally required: the 14 parsed "
            "S13 clauses observe exactly the four main-journey wire operations "
            "the dual-direct-chat-run envelope already records; the density "
            "projection rides that envelope as an additive receipt field"
        ),
        "envelope": {
            "command": ENVELOPE_COMMAND,
            "case_id": CASE_ID,
            "reuse_statement": (
                "density obligations hang under the existing dual direct-chat "
                "envelope; parser/dispatch/registration stay byte-identical"
            ),
            "case_command_registration_required": False,
            "cli_required": False,
        },
        "primary_hunk": {
            "id": "HUNK-A",
            "file": "apps/bcs/scripts/e2e-runner/bcs_e2e/direct_chat_cli_case_run.py",
            "placement": (
                "inside execute_direct_chat_case, after the seven adapter "
                "projections are recorded and before the StepOutcome tuple is "
                "assembled; the step() closure gains an additive extra_evidence "
                "parameter merged into the terminal step evidence"
            ),
            "imports": HUNK_A_IMPORTS,
            "body": HUNK_A_BODY,
            "step_closure_shape": HUNK_A_STEP,
            "terminal_step_call": HUNK_A_TERMINAL_CALL,
            "caveats": HUNK_A_CAVEATS,
            "disposition": "PROPOSED_ONLY",
        },
        "optional_new_command_hunks": OPTIONAL_NEW_COMMAND_HUNKS,
        "accounting_increments": {
            "executor_migration_row_note": PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE,
            "executor_migration_gates_proposal": [
                {
                    "name": "density_clause_projection_offline",
                    "requires": (
                        "bcs_e2e.direct_conversation_density 14-clause roster "
                        "anchored; owned-local offline rehearsal PASS disclosed "
                        "under the DM-TERMINAL-CANCEL-PROJECTION gate"),
                    "status": "PROPOSED",
                },
                {
                    "name": "preprod_density_execution",
                    "requires": (
                        "authorized dual-direct-chat-run preprod rerun after "
                        "GAP-DC-02 disposition and credential window refresh"),
                    "status": "PROPOSED",
                },
            ],
            "coverage_map_increment": PROPOSED_COVERAGE_MAP_INCREMENT,
            "migration_inventory_note": (
                "migration_inventory.migration_plan direct-chat parent row "
                "required_assertion_count basis stays the frozen contract "
                "requirements (5); the 14 density rows are child clause "
                "projections, not re-planned parent obligations — proposal "
                "keeps the planning layer byte-identical"
            ),
            "inventory_case_count_delta": 0,
        },
        "materials": MATERIALS,
        "user_actions": [
            "disposition the DM-TERMINAL-CANCEL-PROJECTION defect (DM-DIAG parked options a/b)",
            "authorize the preprod dual-direct-chat-run rerun (credential window refresh, env names BCS_E2E_HUMAN_COOKIE / BCS_E2E_DRIVER_TOKEN only) and the idempotency probe",
            "land HUNK-A in an authorized wave after the baseline mock-adapter tests re-run green",
        ],
        "non_goals": [
            "this proposal modifies no shared face (cli.py, "
            "case_command_registration.py, inventory.py, migration_inventory.py, "
            "coverage-map/*, executor-migration.json all untouched this wave)",
            "this proposal triggers no transport and resolves no credential",
            "this proposal does not promote the offline rehearsal to preprod "
            "validation and grants no release credit",
            "this proposal does not fabricate the cancel idempotency clause "
            "(GAP-DC-01 stays out of the parsed roster)",
        ],
        "case_scope": {
            "case_id": CASE_ID,
            "parent_id": PARENT,
            "density_assertion_count": DENSITY_ASSERTION_COUNT,
        },
    }

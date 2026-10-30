"""Defect registry and gap ledger for the Direct-conversation density slice.

The upstream parsed 14 clauses execute against a station whose designed
CANCELLED terminal projection carries ``error_message="downstream_error"``.
The frozen DM lifecycle contract fail-closes on that shape (strict convergence
admission), which is why the DUAL-DIRECT-CHAT-01 preprod leg stays BLOCKED
(r3/r4/r5/r8 live receipts, remote_validation NOT_RUN).  This module freezes
that named defect and the three honest gaps of this density wave so no row can
be promoted into PASS credit it does not own:

* ``GAP-DC-01`` — cancel idempotency (second cancel of the same Run) has NO
  upstream parsed clause; it is not fabricated into the roster and needs an
  authorized probe.
* ``GAP-DC-02`` — the DM-TERMINAL-CANCEL-PROJECTION product defect on file.
* ``GAP-DC-03`` — the preprod execution gap: all 14 clauses stay NOT_RUN on
  the station tier until an authorized rerun.

Nothing here executes a transport or resolves a credential.
"""

from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import DENSITY_ASSERTION_COUNT

SCHEMA_VERSION = 1
DEFECT_STATUS = "PRODUCT_DEFECT_ON_FILE"
DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID = "DM-TERMINAL-CANCEL-PROJECTION"

# The station-designed abort product marker observed on the preprod CANCELLED
# terminal (queued_runtime.rs:85 semantics; DM-DIAG 2026-10-29).  The defect-
# faithful offline replay deliberately carries this value so the projection
# never launders the station shape.
DOWNSTREAM_ERROR_MARKER = "downstream_error"

# DM.main-cancel / DM.main-terminal journey gate: the strict convergence
# admission in direct_chat_cli_case_run raises RunStateError when the
# converged cancelled terminal carries any error_message.  The cancel and
# terminal-read density rows (C09-C14) are therefore defect-gated: they
# evaluate the raw parsed clauses truthfully, but the DM parent obligations
# keep their own fail-closed admission and gain no credit from this slice.
DEFECT_GATED_CLAUSES = ("C09", "C10", "C11", "C12", "C13", "C14")
DEFECT_GATED_ASSERTION_IDS = tuple(
    f"DIRECT_DENSITY.{clause}" for clause in DEFECT_GATED_CLAUSES
)

DEFECT_REGISTRY: dict[str, dict[str, Any]] = {
    DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID: {
        "defect_id": DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
        "status": DEFECT_STATUS,
        "parent": "story_user_has_direct_agent_conversation",
        "case_id": "DUAL-DIRECT-CHAT-01",
        "gated_assertion_ids": list(DEFECT_GATED_ASSERTION_IDS),
        "observed_detail_prefix": (
            "Direct chat main Run cancel converged to a non-cancelled terminal "
            "projection"
        ),
        "observed_rounds": [
            "20261029-regression-direct-chat",
            "20261029-direct-chat-preprod-r4",
            "20261030-regression-direct-chat",
            "20261031-regression-r8-preprod",
        ],
        "observation": (
            "preprod DM.main-cancel convergence journaled a CANCELLED terminal "
            'with state="cancelled", is_terminal=true, version=14, '
            'error_message="downstream_error", response.content="", '
            "completed_at_ms set; the strict DM convergence admission "
            "(converged error_message is not None -> RunStateError) rejects the "
            "station's designed abort product (queued_runtime.rs:85 semantics) "
            "and the whole case fail-closes BLOCKED 0/5"
        ),
        "station_semantics": (
            "queued_runtime.rs:85 designs the aborted Run to carry "
            "downstream_error on its cancelled terminal projection"
        ),
        "strict_admission": (
            "direct_chat_cli_case_run DM convergence gate: a cancelled terminal "
            "projection with error_message set fails closed as a non-cancelled "
            "terminal projection; DM.main-cancel / DM.main-terminal obligations "
            "stay NOT_RUN/BLOCKED on the station tier"
        ),
        "closure_contradiction": (
            "the raw parsed clauses C10/C11/C13/C14 evaluate truthfully against "
            "the recorded station shape (cancelled ack, cancelled state, "
            "is_terminal true), while the strict DM admission still fail-closes; "
            "this projection evaluates the raw clauses only and claims zero DM "
            "obligation credit"
        ),
        "parked_disposition": (
            "DM-DIAG 2026-10-29 parked the user decision: (a) keep the strict "
            "contract and report direct-chat BLOCKED as-is, or (b) approve "
            "disclosed contract surgery accepting cancelled terminals whose only "
            "defect is error_message=downstream_error; surgery requires explicit "
            "user sign-off and is not performed by this wave"
        ),
        "evidence_relative_paths": [
            "~/.codex/bcs-e2e/preprod-live-evidence/20261029-regression-direct-chat/DM-DIAG-findings.md",
            "~/.codex/bcs-e2e/preprod-live-evidence/20261029-regression-direct-chat/diag-manual/probe-ping-run-status.json",
            "~/.codex/bcs-e2e/preprod-live-evidence/20261029-regression-direct-chat/diag-manual/probe-ping-run-terminal.json",
            "~/.codex/bcs-e2e/preprod-live-evidence/20261031-regression-r8-preprod/direct-chat/direct-chat-console.txt",
            "~/.codex/bcs-e2e/e2e-runner-diag/tests/test_cancellation_fence.py",
            "~/.codex/bcs-e2e/probes/dm_r4_cancel_race.py",
        ],
        "dm_obligation_credit_claimed": False,
    },
}

GAP_LEDGER: dict[str, dict[str, Any]] = {
    "GAP-DC-01": {
        "gap_id": "GAP-DC-01",
        "scope": "cancel idempotency (second cancel of the same main Run)",
        "kind": "EXECUTION_GAP_NO_UPSTREAM_CLAUSE",
        "status": "OPEN",
        "basis": (
            "the parsed S13 roster (14 clauses, stories.sh L776-805) contains no "
            "second-cancel clause; the DM journey cancels the main Run once and "
            "the ping Run once; a duplicate POST /chat/runs/{run_id}/cancel "
            "observation must not be fabricated as station semantics"
        ),
        "closure_requirement": (
            "authorized owned-local Singlebox journey extension or an admitted "
            "preprod probe recording the raw second-cancel response; until then "
            "the clause stays OUT of the parsed roster and out of the density "
            "obligation count"
        ),
        "blocks": "cancel-idempotency evidence only; not a parsed-count blocker",
        "parsed_clause_claimed": False,
    },
    "GAP-DC-02": {
        "gap_id": "GAP-DC-02",
        "scope": "DM cancel/terminal strict admission vs station abort product",
        "kind": "PRODUCT_DEFECT_ON_FILE",
        "status": "PRODUCT_DEFECT_ON_FILE",
        "basis": (
            "defect " + DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID + ": cancelled "
            "terminal carries error_message=downstream_error (queued_runtime."
            "rs:85); r3/r4/r5/r8 preprod receipts stay BLOCKED with "
            "remote_validation=NOT_RUN"
        ),
        "closure_requirement": (
            "product disposition of DM-DIAG parked option (a) strict / (b) "
            "disclosed surgery with user sign-off, then authorized preprod rerun"
        ),
        "blocks": "DM.main-cancel / DM.main-terminal preprod PASS; density rows stay raw-clause evaluators",
        "parsed_clause_claimed": False,
    },
    "GAP-DC-03": {
        "gap_id": "GAP-DC-03",
        "scope": "preprod execution of the 14 density clauses",
        "kind": "EXECUTION_GAP_NOT_RUN",
        "status": "NOT_RUN_RECEIPT_ONLY",
        "basis": (
            "no preprod network call is authorized for this wave; the preprod "
            "arm is an honest NOT_RUN gap receipt (all rows NOT_RUN, "
            "remote_validation=NOT_RUN, grants_release_credit=false)"
        ),
        "closure_requirement": (
            "authorized dual-direct-chat-run preprod rerun after credential "
            "window refresh and GAP-DC-02 disposition; the density projection "
            "then evaluates against real station observations"
        ),
        "blocks": "all 14 clauses on the preprod tier",
        "parsed_clause_claimed": True,
    },
}

DOCUMENTED_GAPS = ("GAP-DC-01", "GAP-DC-02", "GAP-DC-03")


def defect_gate_descriptor() -> dict[str, Any]:
    """Canonical defect gate block embedded in every density receipt."""
    entry = DEFECT_REGISTRY[DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID]
    return {
        "defect_id": entry["defect_id"],
        "status": entry["status"],
        "gated_assertion_ids": list(DEFECT_GATED_ASSERTION_IDS),
        "raw_clause_evaluation": (
            "density rows evaluate the frozen parsed clauses against raw "
            "observations; a PASS on a gated row is a clause-level truth about "
            "the observation, never DM obligation credit"
        ),
        "dm_obligation_credit_claimed": False,
        "strict_admission_unchanged": (
            "bcs_e2e/direct_chat_cli_case_run.py convergence admission is not "
            "modified by this slice"
        ),
    }


def gap_ledger_descriptor() -> dict[str, Any]:
    """Canonical gap ledger block embedded in every density receipt."""
    return {
        gap_id: {
            "gap_id": entry["gap_id"],
            "scope": entry["scope"],
            "kind": entry["kind"],
            "status": entry["status"],
            "blocks": entry["blocks"],
            "parsed_clause_claimed": entry["parsed_clause_claimed"],
        }
        for gap_id, entry in GAP_LEDGER.items()
    }


def assert_defect_gap_alignment() -> None:
    """Fail closed when the frozen roster/gates drift apart."""
    if len(DEFECT_GATED_ASSERTION_IDS) != 6:
        raise RunStateError("defect gate must cover exactly the six cancel/terminal rows")
    expected = tuple(f"DIRECT_DENSITY.C{index:02d}" for index in range(9, 15))
    if DEFECT_GATED_ASSERTION_IDS != expected:
        raise RunStateError("defect-gated clause ids drifted from the frozen roster")
    if len(GAP_LEDGER) != 3 or set(GAP_LEDGER) != set(DOCUMENTED_GAPS):
        raise RunStateError("gap ledger drifted from the three documented gaps")
    if DENSITY_ASSERTION_COUNT != 14:
        raise RunStateError("density clause count drifted from the parsed 14 roster")

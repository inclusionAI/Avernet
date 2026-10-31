"""Describe-only serial integration proposal for row #1 delivery-policy density.

Nothing here executes, writes, registers, or mutates any file.  The proposal
carries the exact note text the owner session integrates against the 41-row
executor face: executor-migration.json row
``story_human_manages_delivery_policy`` notes 0 -> 1 (notes-only), and
``bcs_e2e/inventory.py`` fail-close membership plus
``_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS`` counter closure.  The density
runner ``dual-delivery-policy-density-run`` (committed at 6be8d9363), its
projection contract, the family suite and the dual-delivery-policy-run
envelope with its r5/r16 PASS receipts all stay untouched.
"""
from __future__ import annotations

from typing import Any

from .contract import (
    CASE_ID,
    DENSITY_FIELD_COUNT,
    DENSITY_PARENT_COUNT,
    DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
    PROJECTION_CONTRACT,
    REPORT_CONTRACT,
)
from .obligations import (
    COARSE_PARENT_COUNT,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    SOURCE_FILE,
    delivery_density_reconciliation,
)

NOTE_TOKEN = "DELIVERY_POLICY_DENSITY_26_FIELD_OBLIGATIONS_20261101"
RUNNER_COMMIT = "6be8d9363"
UPSTREAM_ISSUE = None
NOTE = (
    "row #1 story_human_manages_delivery_policy: register the 26 field-anchored "
    "density obligations under the retained DELIVERY.* coarse parent envelope "
    "(36-point parsed projection = 10 legacy coarse parents + 26 field atoms "
    "projected from 13 anchored upstream field sites in stories.sh; "
    "residual_count_gap=0 on the projection caliber; the committed coverage-map "
    "snapshot caliber 22 and the migration-queue score 36 reconcile by explicit "
    "disclosure, never a silent rebase; shell probes without an assertion role "
    "stay NOT_CHARGED_SOURCE_PROBES and are never counted as coverage); field "
    "atoms carry status-envelope and value comparators with per-row stories.sh "
    "source anchors and live anchor-drift fail-close; density runner "
    "dual-delivery-policy-density-run (case DUAL-DELIVERY-POLICY-DENSITY-01, "
    "contract delivery-density-projection-v1) committed at 6be8d9363 with its "
    "focused family suite green; notes-only integration, the "
    "dual-delivery-policy-run envelope and its r5/r16 PASS receipts stay "
    "untouched; committed verdict stays DUAL/PASS with notes 0 to 1; "
    "standing-roster execution enrollment of the density command and its fresh "
    "dual receipts are the next regression round and are not claimed here; "
    "remote_validation=NOT_RUN and grants_release_credit=false"
)
MD_ROW = (
    "| 1 | story_human_manages_delivery_policy | projection 36 = 10 coarse "
    "DELIVERY.* parents + 26 field atoms (13 anchored sites); residual gap 0; "
    "runner dual-delivery-policy-density-run (case DUAL-DELIVERY-POLICY-DENSITY-01) @6be8d9363 family suite green; "
    "notes-only registration closure 20261101; verdict stays DUAL/PASS; "
    "execution enrollment next round |"
)
INTEGRATION_PLEDGE = (
    "notes-only integration against the 41-row executor face; "
    "executor-migration.json row notes 0 to 1 and inventory fail-close closure; "
    "mode, verdict, gates and every other row untouched; no coverage-map overlay "
    "in this wave (parallel-session in-flight dirty on coverage-map-20261031.*); "
    "no wrapper or standing-roster change; remote_validation=NOT_RUN; "
    "grants_release_credit=false"
)


def proposal_facts() -> dict[str, Any]:
    """Return the frozen calibers a reviewer can recompute mechanically."""
    reconciliation = delivery_density_reconciliation()
    return {
        "note_token": NOTE_TOKEN,
        "parent": PARENT,
        "source_file": SOURCE_FILE,
        "coarse_parent_count": COARSE_PARENT_COUNT,
        "field_site_count": FIELD_SITE_COUNT,
        "field_obligation_count": FIELD_OBLIGATION_COUNT,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "density_parent_count": DENSITY_PARENT_COUNT,
        "density_field_count": DENSITY_FIELD_COUNT,
        "case_id": CASE_ID,
        "projection_contract": PROJECTION_CONTRACT,
        "report_contract": REPORT_CONTRACT,
        "dual_density_command": DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
        "runner_commit": RUNNER_COMMIT,
        "upstream_issue": UPSTREAM_ISSUE,
        "not_charged_source_probes": len(NOT_CHARGED_SOURCE_PROBES),
        "reconciliation": reconciliation,
    }

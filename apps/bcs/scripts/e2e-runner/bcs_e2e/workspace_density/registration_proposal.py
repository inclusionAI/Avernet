"""Describe-only serial integration proposal for row #7 group-workspace density.

Nothing here executes, writes, registers, or mutates any file.  The proposal
carries the exact note text the owner session integrates against the 41-row
executor face: executor-migration.json row
``story_user_operates_group_workspace`` notes 0 -> 1 (notes-only), and
``bcs_e2e/inventory.py`` fail-close membership plus
``_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS`` counter closure.  The density
executor face ``dual-workspace-field-run`` (committed at 205715228), the
frozen 63-atom group-workspace-density oracle seam (6114dbfbc), the five-plane
contract split (63c2a3a52), the ``dual-workspace-run`` envelope family and its
r5/r6 receipts all stay untouched.
"""
from __future__ import annotations

from typing import Any

from .frozen import (
    ATOM_COUNT,
    ATOM_FAMILY_COUNTS,
    CASE_ID,
    COMMITTED_FIELD_BASELINE,
    COMMITTED_FIELD_ROWS,
    COMMITTED_FIELD_TOTAL,
    CONTRACT,
    EXCLUSION_COUNT,
    EXCLUSION_FAMILY_COUNTS,
    FIELD_ORACLE_CONTRACT,
    FIELD_ORACLE_SHA256,
    GROUP_SH_SHA256,
    GROUP_SOURCE_FILE,
    LEAF_COUNT,
    LEAF_FAMILY_COUNTS,
    MIGRATION_CONTRACT,
    MIGRATION_SHA256,
    ORACLE_CONTRACT,
    ORACLE_SHA256,
    PARENT,
    PARENT_COUNT,
    PARSED_COUNT,
    PLUMBING_TOTAL,
    RESIDUAL_COUNT_GAP,
    STORIES_SH_SHA256,
    STORIES_SOURCE_FILE,
    STORY_LINE_END,
    STORY_LINE_START,
    TAIL_LINE_END,
    TAIL_LINE_START,
)

NOTE_TOKEN = "WORKSPACE_DENSITY_59_PARSED_63_ATOMS_20261101"
RUNNER_COMMIT = "205715228"
SEAM_COMMIT = "6114dbfbc"
SPLIT_COMMIT = "63c2a3a52"
WORKSPACE_FIELD_COMMAND = "dual-workspace-field-run"
UPSTREAM_ISSUE = None
NOTE = (
    "row #7 story_user_operates_group_workspace: register the workspace density "
    "closure (59 parsed = 17 retained Workspace.* parents + 42 charged leaves; "
    "63 source-anchored atoms = 42 leaves + 21 disclosed exclusions; "
    "residual_count_gap=0 under both calibers: 59 = 17 + 42 and 63 = 42 + 21; "
    "atom families api26/cli11/tail26, charged leaf families api19/cli7/tail16, "
    "disclosed exclusion families api7/cli4/tail10, each family closing "
    "leaves+exclusions=atoms as 26=19+7, 11=7+4, 26=16+10); the 17 parents are "
    "the legacy WORKSPACE.* requirement roll-ups from migration-contracts S07 "
    "projected onto their verbatim story step calls at stories.sh L408-427 plus "
    "the group.sh and stories.sh tail L429-488 sources, SHA-256-frozen with live "
    "anchor-drift fail-close; the 42 charged field leaves are the content atoms "
    "carrying method, line range, verbatim quote, address and expectation; the "
    "21 exclusions are 19 same-parent success-liveness riders (status_eq/"
    "exit_zero) charged once to the nearest following content leaf plus 2 "
    "deprecated /groups/{id}/workspace 404-envelope clauses charged to the "
    "WORKSPACE.workspace parent envelope, the retired face keeping its verbatim "
    "observed_form retired_410/legacy_service_spec and never a fabricated PASS "
    "leaf; the 44-entry plumbing register is disclosed outside every "
    "denominator; the frozen P0 determination is LANDED (63-atom density oracle "
    "seam 6114dbfbc, workspace-field runner 205715228, five-plane contract split "
    "63c2a3a52); density executor face dual-workspace-field-run (case "
    "DUAL-WORKSPACE-DENSITY-01, contracts workspace-density-v1, "
    "group-workspace-density-v1 oracle, workspace-field-obligations-v1 field "
    "oracle, migration-contracts-v1) committed at 205715228 with its focused "
    "family suite green; honesty rules: missing or drifted inputs fail closed to "
    "NOT_RUN and the retired face observed_form is disclosed, never silently "
    "re-based to PASS; notes-only integration, the dual-workspace-run envelope "
    "family and its r5/r6 receipts stay untouched; committed verdict stays "
    "DUAL/PASS with notes 0 to 1; execution enrollment of the density command "
    "and its fresh dual receipts are the next regression round and are not "
    "claimed here; the live coverage-map-20261031 row 7 stays the parallel "
    "session's write domain with its snapshot parsed 59 - legacy 17 = 42 lens "
    "reconciled by this closure, never a silent rebase; "
    "remote_validation=NOT_RUN and grants_release_credit=false"
)
MD_ROW = (
    "| 7 | story_user_operates_group_workspace | workspace density closure "
    "registered (notes-only): 59 parsed = 17 retained Workspace.* parents + 42 "
    "field leaves; 63 atoms = 42 leaves + 21 exclusions; residual gap 0 under "
    "both calibers; leaf families api19/cli7/tail16, exclusion families "
    "api7/cli4/tail10, atom families api26/cli11/tail26; P0 LANDED (seam "
    "6114dbfbc, field runner 205715228, split 63c2a3a52); face "
    "dual-workspace-field-run (DUAL-WORKSPACE-DENSITY-01, "
    "workspace-density-v1) family suite green; notes-only registration closure "
    "20261101; verdict stays DUAL/PASS; execution enrollment next round |"
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
    return {
        "note_token": NOTE_TOKEN,
        "parent": PARENT,
        "source_files": {GROUP_SOURCE_FILE: GROUP_SH_SHA256,
                         STORIES_SOURCE_FILE: STORIES_SH_SHA256},
        "story_line_span": [STORY_LINE_START, STORY_LINE_END],
        "tail_line_span": [TAIL_LINE_START, TAIL_LINE_END],
        "atom_count": ATOM_COUNT,
        "atom_family_counts": dict(ATOM_FAMILY_COUNTS),
        "leaf_count": LEAF_COUNT,
        "leaf_family_counts": dict(LEAF_FAMILY_COUNTS),
        "exclusion_count": EXCLUSION_COUNT,
        "exclusion_family_counts": dict(EXCLUSION_FAMILY_COUNTS),
        "parent_count": PARENT_COUNT,
        "parsed_count": PARSED_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "committed_field_total": COMMITTED_FIELD_TOTAL,
        "committed_field_baseline": COMMITTED_FIELD_BASELINE,
        "committed_field_rows": COMMITTED_FIELD_ROWS,
        "plumbing_register_entries": PLUMBING_TOTAL,
        "case_id": CASE_ID,
        "contract": CONTRACT,
        "oracle_contract": ORACLE_CONTRACT,
        "field_oracle_contract": FIELD_ORACLE_CONTRACT,
        "migration_contract": MIGRATION_CONTRACT,
        "oracle_sha256": ORACLE_SHA256,
        "field_oracle_sha256": FIELD_ORACLE_SHA256,
        "workspace_field_command": WORKSPACE_FIELD_COMMAND,
        "runner_commit": RUNNER_COMMIT,
        "seam_commit": SEAM_COMMIT,
        "split_commit": SPLIT_COMMIT,
        "upstream_issue": UPSTREAM_ISSUE,
    }

"""Describe-only serial integration proposal for wave E2b row #40.

Nothing here executes, writes, registers, or mutates any file.  The proposal
is the complete text the owner session needs to integrate the 75 weighted
draft-group launch density leaves into executor-migration.json (notes-only),
bcs_e2e/inventory.py (fail-close membership and counter closure) and
coverage-map (overlay arithmetic), while the dual-draft-group-run envelope,
its committed DUAL/BLOCKED_INVENTORIED verdict and its gates stay untouched.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..dynamic_family import dynamic_family_contract
from ..inventory import (EXECUTOR_MIGRATION_NOTES, _EXPECTED_EXECUTOR_MIGRATION_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
                         load_executor_migration)
from ..run_state import RunStateError
from .obligations import (
    COVERAGE_ROW_NO, DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT, DUAL_COMMAND,
    EXECUTOR_ROW_INDEX, FAMILY, MIGRATION_CONTRACT, PARENT, PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP, SOURCE_FILE, SOURCE_SHA256,
    STORY_LINE_SPAN, ZERO_ATOM_PARENTS, density_plan)

EXPECTED_ROW_FACE = {"mode": "DUAL", "preprod_verdict": "BLOCKED_INVENTORIED", "gates": [], "notes": []}
PROPOSAL_TOKEN = "DRAFT_GROUP_DENSITY_75_OBLIGATIONS_20261031"
STORY_ID = "story_collaboration_draft_group_launch"
UPSTREAM_ISSUE = "#26"
COMPANION_DIGESTS = {
    "indentation-regression.json": "23e0a22ea7f14f632b1af54a34aaab5bb6679f48580cd99240d36dd6943569e5",
    "http_provider_judge_mock.py": "fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4",
}
CONTRACT_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"

NOTE = (
    "row #40 story_collaboration_draft_group_launch: register 75 source-anchored "
    "density obligations under the retained LOCAL-DRAFT-GROUP.* parent envelope "
    "(92 parsed = 17 legacy parents + 75 weighted atoms; residual_count_gap=0); the "
    "owned-local executor catalog ASSERTION_IDS (75 ordered weighted sites) equals "
    "the independent AST rebuild verbatim and every site keeps exactly one frozen "
    "parent owner; LOCAL-DRAFT-GROUP.group-launch-input stays the disclosed "
    "zero-atom parent (story lines 162-172 only build the launch body dict; "
    "enforcement is observed by the browser-context-submission-guards atoms "
    "DGL.F49..DGL.F51 and the group-launch-binding atoms DGL.F56..DGL.F64); the "
    "terminal marker print stays parent-charged; notes-only integration, the "
    "dual-draft-group-run envelope and its LOCAL_DRAFT_GROUP_PASS owned-local "
    "conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED "
    "with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file; "
    "remote_validation=NOT_RUN and grants_release_credit=false")

MD_ROW = (
    "| 26 | `story_collaboration_draft_group_launch` | 0绿/1（parsed 92） | NOT_RUN "
    "0/92 叶（obl 92） | LOCAL_ONLY | NOT_RUN(—) | parsed 92 − 义务 92 = 0（密度闭合 "
    "DRAFT_GROUP_DENSITY_75_OBLIGATIONS_20261031：17 契约父 + 75 加权原子 "
    "DGL.F01..DGL.F75；group-launch-input 零原子父披露；V1 预览字节保真与共享校验 "
    "绑定、403/401 负矩阵、201 启动绑定与授权、Draft 不可变、撤销与清理均在案）；"
    "执行缺口 92；预发无腿（设计 owned-local，REMOTE_UNSUPPORTED）+ 4 具名 PREPROD_* "
    "gap；⏳P5 密度执行 |")

QUEUE_BASIS = ("执行缺口按密度口径 75（parsed 92−legacy 17）×1；密度 delta=75；"
               "LOCAL_ONLY 车道；owned-local 族回放全绿（75/75 有序恒等）")

NAMED_GAP_DETAILS = {
    "PREPROD_DRAFT_GROUP_CLI_FACE_NOT_EXECUTED_OFFLINE": (
        "the browser CLI face of the draft group launch is not executed by this "
        "offline package; the launch atoms stay owned-local only"),
    "PREPROD_BROWSER_IDENTITY_CHAIN_UNVERIFIED": (
        "the three-hop browser identity chain (Human auth chain to expected-user "
        "header to originator selection) needs a live authorized run; it stays "
        "owned-local only"),
    "PREPROD_THREE_BOT_AUTHORIZATION_MATRIX_UNVERIFIED": (
        "the author/driver/reviewer three-bot authorization matrix needs a live "
        "authorized run; the grant and submission guard atoms stay owned-local only"),
    "PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE": (
        "this offline density package opens no transport and executes no story child; "
        "the full story stays NOT_RUN by design"),
}

RED_LINES = [
    "describe-only: no executor-migration.json row is created or rewritten here",
    "no edits to inventory.py, cli.py, artifact.py, or case_command_registration.py",
    "no edits to coverage-map/*, webhook_subscription*, slow_judge/**, "
    "edge_revoke_lifecycle/, delivery_density/**, workspace-migration-contract.json, "
    "workspace_density/, service_policy_density/, channel_density/, authority_density/, "
    "provider_publishes/, collaboration_draft_density/, or the frozen legacy contracts",
    "preprod stays NOT_RUN with remote_validation=NOT_RUN and grants_release_credit=false",
    "SHA-256 froze the story, the incident fixture and the mock; any drift fails "
    "closed before the plan is usable",
]


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration proposal; nothing is executed."""
    plan = density_plan()
    family = dynamic_family_contract(FAMILY)
    if plan["parsed_obligation_count"] != PARSED_OBLIGATION_COUNT:
        raise ValueError("draft-group density proposal caliber changed")
    # Inventory fail-close: the proposal is only usable against the live
    # committed face; a parallel wave note, a verdict change, or a repeat
    # registration all fail closed before any increment text is produced.
    statuses = load_executor_migration()
    face = statuses.get(STORY_ID)
    if face != EXPECTED_ROW_FACE:
        raise RunStateError("draft-group executor-migration face changed")
    if NOTE in EXECUTOR_MIGRATION_NOTES:
        raise RunStateError("draft-group density note is already registered")
    expected_note_counts = Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
    current_total_notes = sum(expected_note_counts.values())
    if len(EXECUTOR_MIGRATION_NOTES) != current_total_notes:
        raise RunStateError("draft-group inventory note totals are inconsistent")
    return {
        "status": "DESCRIBE_ONLY",
        "token": PROPOSAL_TOKEN,
        "contract": DENSITY_CONTRACT,
        "parent_story": STORY_ID,
        "upstream_issue": UPSTREAM_ISSUE,
        "executor_row_index": EXECUTOR_ROW_INDEX,
        "coverage_row_no": COVERAGE_ROW_NO,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "source": {
            "file": SOURCE_FILE, "sha256": SOURCE_SHA256,
            "story_line_span": list(STORY_LINE_SPAN),
            "companion_sources": dict(COMPANION_DIGESTS),
            "migration_contract": MIGRATION_CONTRACT,
            "contract_source_revision": CONTRACT_REVISION},
        "family": {
            "family": family.family, "command": family.command,
            "dual_command": DUAL_COMMAND, "legacy_id": family.legacy_id,
            "dual_case_id": family.case_id, "source_case_id": family.source_case_id,
            "dual_suite": family.suite, "source_suite": family.source_suite,
            "profile_contract": family.profile_contract,
            "report_contract": family.report_contract,
            "local_executor": family.local_executor,
            "local_status": family.local_status,
            "canonical_status": "LOCAL_SUITE_PASS",
            "preprod_initial_verdict": family.preprod_initial_verdict},
        "zero_atom_parents": dict(ZERO_ATOM_PARENTS),
        "not_charged_source_probes": list(plan["not_charged_source_probes"]),
        "named_preprod_gaps": [
            {"id": gap, "status": "NAMED_DESIGN_GAP", "detail": NAMED_GAP_DETAILS[gap],
             "blocks": "preprod PASS credit"} for gap in family.preprod_gaps],
        "inventory_increment": {
            "target_file": "bcs_e2e/inventory.py",
            "integration_mode": "owner session applies; this package writes nothing",
            "operation": "fail-close membership and counter closure",
            "adds_to_EXECUTOR_MIGRATION_NOTES": [NOTE],
            "adds_to_expected_note_counts": {NOTE: 1},
            "note_counts_replacement": {
                "current_total_notes": current_total_notes,
                "after_total_notes": current_total_notes + 1,
                "this_family_delta": 1},
            "unchanged": {
                "state_counts": {
                    f"{mode}/{verdict}": count
                    for (mode, verdict), count in _EXPECTED_EXECUTOR_MIGRATION_COUNTS.items()
                },
                "gate_counts": dict(_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS),
                "row_face": {
                    "id": STORY_ID, "mode": "DUAL",
                    "preprod_verdict": "BLOCKED_INVENTORIED",
                    "gates": [], "notes_delta": "+1 (this proposal note only)"}}},
        "coverage_map_increment": {
            "targets": ["coverage-map/coverage-map-20261031.json",
                        "coverage-map/coverage-map-20261031.md"],
            "integration_mode": "owner session applies; proposal text only",
            "row": {
                "no": COVERAGE_ROW_NO, "case_id": STORY_ID,
                "before": {"obligations": PARENT_OBLIGATION_COUNT,
                           "snapshot_parsed_count": 1,
                           "gap_parsed_minus_obligations": -16,
                           "queue": {"rank": 20, "gap_qty": 17, "feasibility": 1,
                                     "score": 17, "basis": "obl 17×1；LOCAL_ONLY 车道"}},
                "after": {
                    "obligations": PARSED_OBLIGATION_COUNT,
                    "snapshot_parsed_count": PARSED_OBLIGATION_COUNT,
                    "gap_kind": "COUNT_GAP_CLOSED+EXECUTION_GAP",
                    "gap_parsed_minus_obligations": 0,
                    "gap_detail": ("parsed 92 − 义务 92 = 0（密度闭合 "
                                   + PROPOSAL_TOKEN + "）；执行缺口 92；预发无腿"
                                   "（设计 owned-local，REMOTE_UNSUPPORTED）；⏳P5 密度执行"),
                    "queue": {"rank": 20, "gap_qty": DENSITY_OBLIGATION_COUNT,
                              "feasibility": 1, "score": DENSITY_OBLIGATION_COUNT,
                              "basis": QUEUE_BASIS}},
                "md_row": MD_ROW},
            "committed_totals": {
                "obligations_committed_sum": 674,
                "upstream_parsed_known_sum": 638,
                "disposition": "unchanged by this proposal; the owner landing commit "
                               "rebases the two row overlay fields only"}},
        "upstream_roster": {"roster_verdict": "0绿/1", "parsed_green": 0,
                            "parsed_total": 1,
                            "note": "upstream roster greenness is unaffected by the "
                                    "obligation-layer backfill"},
        "replay_caliber": {
            "local": "92/92 PASS against the frozen LOCAL-DRAFT-GROUP-01 receipt "
                     "(weighted slots: http_status plus cache_control and "
                     "envelope_code on envelope sites plus error_code on frozen "
                     "error literals; zero provider webhooks to the mock sink)",
            "preprod": "92/92 NOT_RUN with BLOCKED_INVENTORIED overall and the four "
                       "named gaps; authorized injection stays capped"},
        "red_lines": list(RED_LINES),
        "new_registration_command": False,
        "write_domain": ["bcs_e2e/draft_group_density/**",
                         "tests/test_draft_group_density.py"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"],
    }


def describe_only_mainline_fragment() -> str:
    """Return the text a mainline integrator may apply; this module writes nothing."""
    proposal = registration_proposal()
    gaps = ", ".join(proposal["named_preprod_gaps"][index]["id"]
                     for index in range(len(proposal["named_preprod_gaps"])))
    return (
        f"{PROPOSAL_TOKEN}: {DENSITY_CONTRACT} registers 17 retained "
        f"LOCAL-DRAFT-GROUP.* contract parents plus 75 source-anchored weighted "
        f"density obligations for row #40 {STORY_ID} (executor row index 40, "
        f"coverage-map row no=26, upstream {UPSTREAM_ISSUE}; 92 parsed = 17 legacy "
        f"parents + 75 atoms; residual_count_gap=0; ordered identity DGL catalog "
        f"75/75; zero-atom parent LOCAL-DRAFT-GROUP.group-launch-input disclosed; the "
        f"terminal marker print stays parent-charged); executor-migration.json "
        f"notes-only increment; bcs_e2e/inventory.py gains exactly one "
        f"EXECUTOR_MIGRATION_NOTES member and one _EXPECTED_EXECUTOR_MIGRATION_NOTE_"
        f"COUNTS entry (states and gates counters unchanged; committed verdict stays "
        f"DUAL/BLOCKED_INVENTORIED); coverage-map overlay rebases row no=26 "
        f"obligations 17 to 92 and snapshot_parsed_count 1 to 92 with queue gap_qty "
        f"17 to 75 (执行缺口按密度口径 75（parsed 92−legacy 17）×1) while committed "
        f"totals 674/638 stay unchanged; local replay projects 92/92 PASS against "
        f"the frozen LOCAL-DRAFT-GROUP-01 receipt; preprod stays NOT_RUN with the "
        f"four named gaps {gaps}; remote_validation=NOT_RUN and "
        f"grants_release_credit=false; New registration command: none "
        f"(dual-draft-group-run envelope reused); write domain is "
        f"bcs_e2e/draft_group_density/** and tests/test_draft_group_density.py only.")

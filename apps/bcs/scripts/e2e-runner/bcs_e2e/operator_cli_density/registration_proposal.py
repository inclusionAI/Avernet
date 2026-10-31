"""Describe-only serial integration proposal for wave idx9 row #9.

Nothing here executes, writes, registers, or mutates any file.  The proposal
is the complete text the owner session needs to integrate the 15
source-anchored operator CLI density leaves into executor-migration.json
(notes-only), bcs_e2e/inventory.py (fail-close membership and counter
closure) and coverage-map (overlay arithmetic), while the
dual-operator-cli-run envelope, its committed DUAL/BLOCKED_INVENTORIED
verdict and its gates stay untouched.

The operator_cli family deliberately stays outside
bcs_e2e.dynamic_family DYNAMIC_FAMILIES (fixed_loop, nested_fixed_loop,
result_queue, draft_group): its dual registration face is the executor
migration row plus the frozen dual-operator-cli-profile-v1 /
dual-operator-cli-case-run-v1 contracts, so this proposal mirrors the family
face statically from bcs_e2e.operator_cli_density.contract instead of
resolving it through dynamic_family_contract (which would fail closed with
"unknown dynamic family: operator_cli").  That divergence from the RQ/DGL
templates is disclosed, not hidden.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..inventory import (EXECUTOR_MIGRATION_NOTES,
                         _EXPECTED_EXECUTOR_MIGRATION_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
                         load_executor_migration)
from ..run_state import RunStateError
from .contract import (
    ADAPTER_METHODS, CANONICAL_STATUS, CASE_ID, CLI_OPERATIONS, COMPANION_SOURCES,
    CONTRACT_REVISION, COVERAGE_ROW_NO, DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT,
    DUAL_CASE_ID, DUAL_COMMAND, DUAL_SUITE, EXECUTOR_ROW_INDEX, FAMILY,
    HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV, LOCAL_EXECUTOR, LOCAL_STATUS, MIGRATION_CONTRACT,
    PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT, PARENT_STORY, PREPROD_GAPS,
    PREPROD_INITIAL_VERDICT, PROFILE_CONTRACT, REQUIREMENTS, RESIDUAL_COUNT_GAP,
    REPORT_CONTRACT, SOURCE_FILE, SOURCE_SHA256, STORY_LINE_SPAN, SUITE,
    ZERO_ATOM_PARENTS)
from .density import (ASSERTION_IDS, EXPECTED_LEAF_COUNTS, SITE_IDS, density_plan,
                      obligations)

EXPECTED_ROW_FACE = {"mode": "DUAL", "preprod_verdict": "BLOCKED_INVENTORIED", "gates": [], "notes": []}
PROPOSAL_TOKEN = "OPERATOR_CLI_DENSITY_15_OBLIGATIONS_20261031"
STORY_ID = PARENT_STORY
UPSTREAM_ISSUE = None
UPSTREAM_ISSUE_DISCLOSURE = (
    "no upstream issue reference exists for story_operator_coordinates_with_cli in "
    "the committed migration bookkeeping (executor-migration.json rows carry no "
    "issue field, next-story-migrations.md lists no operator-cli pending issue, and "
    "the coverage-map carries no upstream issue pointer); UPSTREAM_ISSUE=None is "
    "disclosed instead of fabricating an issue id")
CONTRACT_REVISION_FROZEN = CONTRACT_REVISION
# Frozen coverage-map row-9 overlay fact (read-only disk basis): the upstream
# snapshot already parsed 28 while obligations stood at 13, so the material
# gap is the 15-atom density delta this wave registers.
SNAPSHOT_PARSED_BEFORE = PARSED_OBLIGATION_COUNT
SNAPSHOT_PARSED_AFTER = PARSED_OBLIGATION_COUNT

NOTE = (
    "row #9 story_operator_coordinates_with_cli: register 15 source-anchored density "
    "obligations under the retained OPERATOR-CLI.* parent envelope (28 parsed = 13 "
    "legacy parents + 15 atoms; residual_count_gap=0); the frozen shell span catalog "
    "OPCLI.F01..OPCLI.F15 (site envelope OPERATOR-CLI.*) rebuilds every parent call "
    "line (stories.sh 1786-1798) and every atom quote verbatim from the SHA-256-frozen "
    "cli.sh/cli-stories.sh bytes in story order, each leaf keeping exactly one frozen "
    "parent owner; OPERATOR-CLI.create-group stays the disclosed zero-atom parent "
    "(its group.sh helper surface lives outside the stories.sh/cli.sh/cli-stories.sh "
    "upstream parse face; field enforcement stays parent-charged with the frozen "
    "creator-scoped exact-delete disposition as evidence); the exit-zero fail guards, "
    "TESTS_* bookkeeping, ensure_cli_token skip guards, the visibility original-value "
    "capture guard, the upstream register prerequisite legs and the chat prerequisite "
    "in-case friendship admissions stay parent-charged, not leaf-charged; "
    "UPSTREAM_ISSUE=None disclosed (no committed issue pointer found); notes-only "
    "integration, the dual-operator-cli-run envelope and its "
    "LOCAL_OPERATOR_CLI_PASS owned-local conclusion stay untouched; committed verdict "
    "stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named "
    "PREPROD_* gaps on file (five-bot health leg BLOCKED, r5 leaf-BLOCKED lineage, "
    "reviewed duo credentials unreviewed, full story not executed offline); "
    "remote_validation=NOT_RUN and grants_release_credit=false")

MD_ROW = (
    "| 9 | `story_operator_coordinates_with_cli` | 23绿/26（parsed 28） | PASS "
    "13/13 父（obl 28；15 密度叶为离线镜像投影） | DUAL | BLOCKED(r8) | parsed 28 "
    "− 义务 28 = 0（密度闭合 OPERATOR_CLI_DENSITY_15_OBLIGATIONS_20261031：13 契约父 "
    "+ 15 源锚定壳原子 OPCLI.F01..OPCLI.F15；create-group 零原子父披露；退出零守卫/"
    "TESTS_* 簿记/ensure_cli_token/可见性原值捕获/直登 register 先决腿/聊天同案友谊"
    "准入均父级承担）；执行缺口 28；预发 r8 近空收据（duo 凭据未审查 + 5-bot "
    "health 腿物料门控）+ 4 具名 PREPROD_* gap；⏳P5 密度执行 |")

QUEUE_BASIS = ("密度 delta=15×2；5-bot actor token 套件+duo 凭据+health 先绿；"
               "owned-local 族回放全绿（15/15 有序恒等）")

NAMED_GAP_DETAILS = {
    "PREPROD_OPERATOR_CLI_FIVE_BOT_HEALTH_LEG_BLOCKED": (
        "r5 20261031-regression-r5-operator-cli receipt is BLOCKED 0/13 with "
        "OperatorCliRunnerError: the OPERATOR-CLI.health step is the first failing "
        "leaf (NOT_RUN/OperatorCliRunnerError) and the five trusted-team-Bot actor "
        "token suite material never reached the health leg, so no membership "
        "assertion was admissible; the material gate stays on file"),
    "PREPROD_OPERATOR_CLI_R5_LEAF_BLOCKED_LINEAGE": (
        "r5b/r5c/r5f operator-cli receipts advanced exactly 1/13: OPERATOR-CLI.health "
        "PASS (credential-free exit-zero leg) while OPERATOR-CLI.list stays NOT_RUN "
        "with OperatorCliRunnerError; the health-green/list-leaf-BLOCKED lineage is "
        "disclosed as a preprod material gap, not counted as density coverage"),
    "PREPROD_OPERATOR_CLI_DUAL_CREDENTIALS_UNREVIEWED": (
        "r8 20261031-regression-r8-preprod/operator-cli produced a near-empty "
        "receipt (binding/profile only; console JSON carries "
        "ready_to_execute=false and the reviewed-duo-credentials requirement); the "
        "dual face human-cookie plus owned-driver-bot-token pair stays unreviewed"),
    "PREPROD_OPERATOR_CLI_FULL_STORY_NOT_EXECUTED_OFFLINE": (
        "this offline density package opens no transport and executes no story "
        "child; the full story stays NOT_RUN by design"),
}

RED_LINES = [
    "describe-only: no executor-migration.json row is created or rewritten here",
    "no edits to inventory.py, cli.py, artifact.py, or case_command_registration.py",
    "no edits to coverage-map/*, webhook_subscription*, slow_judge/**, "
    "edge_revoke_lifecycle/, delivery_density/**, fixed_loop_density/, "
    "workspace_density/, workspace-migration-contract.json, agent_network_density/, "
    "edge_permission_backlog/, provider_publishes/, channel_density/, "
    "authority_density/, service_policy_density/, collaboration_*density, "
    "direct_conversation_density, group_context_density, group_workspace_density.py, "
    "or the frozen legacy contracts",
    "preprod stays NOT_RUN with remote_validation=NOT_RUN and grants_release_credit=false",
    "SHA-256 froze stories.sh and the four companions; any drift fails closed "
    "before the plan is usable",
]


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration proposal; nothing is executed."""
    plan = density_plan()
    rows = obligations()
    if plan["parsed_obligation_count"] != PARSED_OBLIGATION_COUNT:
        raise ValueError("operator-cli density proposal caliber changed")
    if plan["assertion_ids"] != list(ASSERTION_IDS) or plan["site_ids"] != list(
            SITE_IDS):
        raise ValueError("operator-cli density ordered identity changed")
    if len(rows) != PARSED_OBLIGATION_COUNT or len({row.obligation_id
                                                    for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise ValueError("operator-cli density rebuild count changed")
    if plan["per_parent_atom_counts"] != dict(EXPECTED_LEAF_COUNTS):
        raise ValueError("operator-cli per-parent atom counts changed")
    # Inventory fail-close: the proposal is only usable against the live
    # committed face; a parallel wave note, a verdict change, or a repeat
    # registration all fail closed before any increment text is produced.
    statuses = load_executor_migration()
    face = statuses.get(STORY_ID)
    if face != EXPECTED_ROW_FACE:
        raise RunStateError("operator-cli executor-migration face changed")
    if NOTE in EXECUTOR_MIGRATION_NOTES:
        raise RunStateError("operator-cli density note is already registered")
    expected_note_counts = Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
    current_total_notes = sum(expected_note_counts.values())
    if len(EXECUTOR_MIGRATION_NOTES) != current_total_notes:
        raise RunStateError("operator-cli inventory note totals are inconsistent")
    family = {
        "family": FAMILY, "command": DUAL_COMMAND, "dual_command": DUAL_COMMAND,
        "legacy_id": STORY_ID, "dual_case_id": DUAL_CASE_ID,
        "source_case_id": CASE_ID, "dual_suite": DUAL_SUITE,
        "source_suite": SUITE, "profile_contract": PROFILE_CONTRACT,
        "report_contract": REPORT_CONTRACT, "local_executor": LOCAL_EXECUTOR,
        "local_status": LOCAL_STATUS, "canonical_status": CANONICAL_STATUS,
        "preprod_initial_verdict": PREPROD_INITIAL_VERDICT,
        "adapter_methods": list(ADAPTER_METHODS),
        "cli_operations": list(CLI_OPERATIONS),
        "cli_operations_count": len(CLI_OPERATIONS),
        "dynamic_family_registration": (
            "operator_cli stays outside bcs_e2e.dynamic_family DYNAMIC_FAMILIES by "
            "design; the dual-operator-cli-run envelope registers through the "
            "executor-migration row and the frozen dual-operator-cli-profile-v1 / "
            "dual-operator-cli-case-run-v1 contracts instead of a dynamic family "
            "entry; the static mirror above is re-checked live by the density "
            "obligations builder")}
    return {
        "status": "DESCRIBE_ONLY",
        "token": PROPOSAL_TOKEN,
        "contract": DENSITY_CONTRACT,
        "parent_story": STORY_ID,
        "upstream_issue": UPSTREAM_ISSUE,
        "upstream_issue_disclosure": UPSTREAM_ISSUE_DISCLOSURE,
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
            "companion_sources": dict(COMPANION_SOURCES),
            "migration_contract": MIGRATION_CONTRACT,
            "contract_source_revision": CONTRACT_REVISION_FROZEN},
        "family": family,
        "parent_requirement_order": list(REQUIREMENTS),
        "zero_atom_parents": dict(ZERO_ATOM_PARENTS),
        "not_charged_source_probes": list(plan["not_charged_source_probes"]),
        "named_preprod_gaps": [
            {"id": gap, "status": "NAMED_DESIGN_GAP",
             "detail": NAMED_GAP_DETAILS[gap],
             "blocks": "preprod PASS credit"} for gap in PREPROD_GAPS],
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
                "before": {
                    "obligations": PARENT_OBLIGATION_COUNT,
                    "snapshot_parsed_count": SNAPSHOT_PARSED_BEFORE,
                    "gap_kind": "COUNT_GAP+MATERIAL",
                    "gap_parsed_minus_obligations": DENSITY_OBLIGATION_COUNT,
                    "local_projection": "NOT_RUN 0/1 叶（obl 13）",
                    "preprod_latest": {"verdict": "BLOCKED", "round": "r8"},
                    "queue": {"rank": 10, "gap_qty": DENSITY_OBLIGATION_COUNT,
                              "feasibility": 2, "score": 30,
                              "basis": ("密度 delta=15×2；5-bot actor token 套件+duo "
                                        "凭据+health 先绿")}},
                "after": {
                    "obligations": PARSED_OBLIGATION_COUNT,
                    "snapshot_parsed_count": SNAPSHOT_PARSED_AFTER,
                    "gap_kind": "COUNT_GAP_CLOSED+MATERIAL",
                    "gap_parsed_minus_obligations": 0,
                    "gap_detail": ("parsed 28 − 义务 28 = 0（密度闭合 "
                                   + PROPOSAL_TOKEN + "：13 契约父 + 15 源锚定壳原子 "
                                   "OPCLI.F01..OPCLI.F15；create-group 零原子父披露；"
                                   "退出零守卫/TESTS_* 簿记/ensure_cli_token/可见性原值"
                                   "捕获/直登 register 先决腿/聊天同案友谊准入均父级"
                                   "承担）；执行缺口 28；预发 r8 近空收据（duo 凭据未"
                                   "审查 + 5-bot health 腿物料门控）+ 4 具名 "
                                   "PREPROD_* gap；⏳P5 密度执行"),
                    "local_projection": "PASS 13/13 父（obl 28；15 密度叶为离线镜像投影）",
                    "queue": {"rank": 10, "gap_qty": DENSITY_OBLIGATION_COUNT,
                              "feasibility": 2, "score": 30, "basis": QUEUE_BASIS}},
                "md_row": MD_ROW,
                "upstream_roster": {"roster_verdict": "23绿/26", "parsed_green": 23,
                                    "parsed_total": 26}},
            "committed_totals": {
                "obligations_committed_sum": 674,
                "upstream_parsed_known_sum": 638,
                "disposition": "unchanged by this proposal; the owner landing commit "
                               "rebases the row overlay fields only"}},
        "upstream_roster": {"roster_verdict": "23绿/26", "parsed_green": 23,
                            "parsed_total": 26,
                            "note": "upstream roster greenness is unaffected by the "
                                    "obligation-layer backfill"},
        "replay_caliber": {
            "local": ("28/28 PASS against the deterministic owned-local mirror of the "
                      "frozen LOCAL-OPERATOR-CLI-01 receipt (suite "
                      "owned-local-operator-cli-v1; 13 legacy story stages plus 15 "
                      "shell assertion atoms; comparator-aware predicate/equals/"
                      "contains/contains_any/exit_zero)"),
            "preprod": ("28/28 NOT_RUN with BLOCKED_INVENTORIED overall and the four "
                        "named gaps; authorized injection stays capped (the overall "
                        "verdict stays BLOCKED_INVENTORIED even under injected "
                        "observations)")},
        "red_lines": list(RED_LINES),
        "new_registration_command": False,
        "write_domain": ["bcs_e2e/operator_cli_density/**",
                         "tests/test_operator_cli_density.py"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }


def describe_only_mainline_fragment() -> str:
    """Return the text a mainline integrator may apply; this module writes nothing."""
    proposal = registration_proposal()
    gaps = ", ".join(entry["id"] for entry in proposal["named_preprod_gaps"])
    return (
        f"{PROPOSAL_TOKEN}: {DENSITY_CONTRACT} registers 13 retained OPERATOR-CLI.* "
        f"contract parents plus 15 source-anchored density obligations for row #9 "
        f"{STORY_ID} (executor row index 9, coverage-map row no=9, upstream issue "
        f"reference None per disclosure: {UPSTREAM_ISSUE_DISCLOSURE}; 28 parsed = 13 "
        f"legacy parents + 15 atoms; residual_count_gap=0; ordered identity OPCLI "
        f"catalog 15/15 with the site envelope OPERATOR-CLI.*; zero-atom parent "
        f"OPERATOR-CLI.create-group disclosed; exit-zero guards, TESTS_* "
        f"bookkeeping, ensure_cli_token skip guards, the visibility original-value "
        f"capture guard, the upstream register prerequisite legs and the chat "
        f"prerequisite friendship admissions stay parent-charged); "
        f"executor-migration.json notes-only increment; bcs_e2e/inventory.py gains "
        f"exactly one EXECUTOR_MIGRATION_NOTES member and one "
        f"_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS entry (state and gate counters "
        f"unchanged; committed verdict stays DUAL/BLOCKED_INVENTORIED); coverage-map "
        f"overlay rebases row no=9 obligations 13 to 28 with snapshot_parsed_count "
        f"28 unchanged and gap 15 to 0 (COUNT_GAP_CLOSED+MATERIAL) while the queue "
        f"stays rank 10 / gap_qty 15 / feasibility 2 / score 30 (执行缺口按密度口径 "
        f"15×2；owned-local 族回放全绿（15/15 有序恒等）) and committed totals "
        f"674/638 stay unchanged; local replay projects 28/28 PASS against the "
        f"frozen LOCAL-OPERATOR-CLI-01 receipt mirror; preprod stays NOT_RUN with "
        f"the four named gaps {gaps}; remote_validation=NOT_RUN and "
        f"grants_release_credit=false; New registration command: none "
        f"(dual-operator-cli-run envelope reused); write domain is "
        f"bcs_e2e/operator_cli_density/** and tests/test_operator_cli_density.py only.")

"""Describe-only serial integration proposal for wave E2b row #20.

Nothing here executes, writes, registers, or mutates any file.  The proposal
is the complete text the owner session needs to integrate the 34 result-queue
density leaves into executor-migration.json (notes-only), bcs_e2e/inventory.py
(fail-close membership and counter closure) and coverage-map (overlay
arithmetic), while the dual-result-queue-run envelope, its committed
DUAL/BLOCKED_INVENTORIED verdict and its gates stay untouched.
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
PROPOSAL_TOKEN = "RESULT_QUEUE_DENSITY_34_OBLIGATIONS_20261031"
STORY_ID = "story_state_machine_result_queue"
UPSTREAM_ISSUE = "#25"
COMPANION_DIGESTS = {
    "fixed_loop_story.py": "8fc6daeace717e1e030478c398ed1a770df192c69fcd7622cd34cbdca11d17d5",
    "http_provider_judge_mock.py": "fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4",
}
CONTRACT_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"

NOTE = (
    "row #20 story_state_machine_result_queue: register 34 source-anchored density "
    "obligations under the retained RESULT-QUEUE.* parent envelope (52 parsed = 18 "
    "legacy parents + 34 atoms; residual_count_gap=0); the owned-local executor "
    "catalog ASSERTION_IDS (34 ordered sites) equals the independent AST rebuild "
    "verbatim and every site keeps exactly one frozen parent owner; "
    "RESULT-QUEUE.delivery-policy-replacement stays the disclosed zero-atom parent "
    "(story lines 41-46 only mutate the in-memory policy dict; enforcement is "
    "observed by the scheduled-result-delivery atoms RQ.F21..RQ.F23); the seven "
    "dynamic wait predicates and the terminal marker print stay parent-charged, "
    "not leaf-charged; notes-only integration, the dual-result-queue-run envelope "
    "and its LOCAL_RESULT_QUEUE_PASS owned-local conclusion stay untouched; "
    "committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod "
    "keeps the four named PREPROD_* gaps on file; remote_validation=NOT_RUN and "
    "grants_release_credit=false")

MD_ROW = (
    "| 25 | `story_state_machine_result_queue` | 0绿/1（parsed 52） | NOT_RUN 0/52 叶 "
    "（obl 52） | LOCAL_ONLY | NOT_RUN(—) | parsed 52 − 义务 52 = 0（密度闭合 "
    "RESULT_QUEUE_DENSITY_34_OBLIGATIONS_20261031：18 契约父 + 34 AST 原子 "
    "RQ.F01..RQ.F34；delivery-policy-replacement 零原子父披露；七个 wait 谓词与终端 "
    "marker print 父级承担）；执行缺口 52；预发无腿（设计 owned-local，"
    "REMOTE_UNSUPPORTED）+ 4 具名 PREPROD_* gap；⏳P5 密度执行 |")

QUEUE_BASIS = ("执行缺口按密度口径 34（parsed 52−legacy 18）×1；密度 delta=34；"
               "LOCAL_ONLY 车道；owned-local 族回放全绿（34/34 有序恒等）")

NAMED_GAP_DETAILS = {
    "PREPROD_ADMIN_MESSAGE_DELIVERY_POLICY_ROUTE_UNREGISTERED": (
        "admin message-delivery policy route is not registered on the preprod station "
        "face; the policy baseline and CAS atoms stay owned-local only"),
    "PREPROD_PROVIDER_CALLBACK_HARNESS_UNREGISTERED": (
        "the Provider callback harness route is not registered on the preprod station "
        "face; the scheduled-callback admission atoms stay owned-local only"),
    "PREPROD_RESULT_DELIVERY_LEDGER_FACE_VERIFICATION_NOT_RUN": (
        "the durable result-delivery ledger face needs a live authorized run; the "
        "ledger and durable-rows atoms stay owned-local only"),
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
    "SHA-256 froze the story and both companions; any drift fails closed before the "
    "plan is usable",
]


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration proposal; nothing is executed."""
    plan = density_plan()
    family = dynamic_family_contract(FAMILY)
    if plan["parsed_obligation_count"] != PARSED_OBLIGATION_COUNT:
        raise ValueError("result-queue density proposal caliber changed")
    # Inventory fail-close: the proposal is only usable against the live
    # committed face; a parallel wave note, a verdict change, or a repeat
    # registration all fail closed before any increment text is produced.
    statuses = load_executor_migration()
    face = statuses.get(STORY_ID)
    if face != EXPECTED_ROW_FACE:
        raise RunStateError("result-queue executor-migration face changed")
    if NOTE in EXECUTOR_MIGRATION_NOTES:
        raise RunStateError("result-queue density note is already registered")
    expected_note_counts = Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
    current_total_notes = sum(expected_note_counts.values())
    if len(EXECUTOR_MIGRATION_NOTES) != current_total_notes:
        raise RunStateError("result-queue inventory note totals are inconsistent")
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
                    "id": STORY_ID, "mode": "DUAL", "preprod_verdict": "BLOCKED_INVENTORIED",
                    "gates": [], "notes_delta": "+1 (this proposal note only)"}}},
        "coverage_map_increment": {
            "targets": ["coverage-map/coverage-map-20261031.json",
                        "coverage-map/coverage-map-20261031.md"],
            "integration_mode": "owner session applies; proposal text only",
            "row": {
                "no": COVERAGE_ROW_NO, "case_id": STORY_ID,
                "before": {"obligations": PARENT_OBLIGATION_COUNT,
                           "snapshot_parsed_count": 1,
                           "gap_parsed_minus_obligations": -17,
                           "queue": {"rank": 19, "gap_qty": 18, "feasibility": 1,
                                     "score": 18, "basis": "obl 18×1；LOCAL_ONLY 车道"}},
                "after": {
                    "obligations": PARSED_OBLIGATION_COUNT,
                    "snapshot_parsed_count": PARSED_OBLIGATION_COUNT,
                    "gap_kind": "COUNT_GAP_CLOSED+EXECUTION_GAP",
                    "gap_parsed_minus_obligations": 0,
                    "gap_detail": ("parsed 52 − 义务 52 = 0（密度闭合 "
                                   + PROPOSAL_TOKEN + "）；执行缺口 52；预发无腿"
                                   "（设计 owned-local，REMOTE_UNSUPPORTED）；⏳P5 密度执行"),
                    "queue": {"rank": 19, "gap_qty": DENSITY_OBLIGATION_COUNT,
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
            "local": "52/52 PASS against the frozen LOCAL-RESULT-QUEUE-01 receipt "
                     "(mock mirror; three scheduled provider webhooks observed, "
                     "event-webhook and judge surfaces untouched)",
            "preprod": "52/52 NOT_RUN with BLOCKED_INVENTORIED overall and the four "
                       "named gaps; authorized injection stays capped"},
        "red_lines": list(RED_LINES),
        "new_registration_command": False,
        "write_domain": ["bcs_e2e/result_queue_density/**",
                         "tests/test_result_queue_density.py"],
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
        f"{PROPOSAL_TOKEN}: {DENSITY_CONTRACT} registers 18 retained RESULT-QUEUE.* "
        f"contract parents plus 34 source-anchored density obligations for row #20 "
        f"{STORY_ID} (executor row index 20, coverage-map row no=25, upstream "
        f"{UPSTREAM_ISSUE}; 52 parsed = 18 legacy parents + 34 atoms; "
        f"residual_count_gap=0; ordered identity RQ catalog 34/34; zero-atom parent "
        f"RESULT-QUEUE.delivery-policy-replacement disclosed; seven wait predicates "
        f"plus the terminal marker print stay parent-charged); executor-migration.json "
        f"notes-only increment; bcs_e2e/inventory.py gains exactly one "
        f"EXECUTOR_MIGRATION_NOTES member and one _EXPECTED_EXECUTOR_MIGRATION_NOTE_"
        f"COUNTS entry (states and gates counters unchanged; committed verdict stays "
        f"DUAL/BLOCKED_INVENTORIED); coverage-map overlay rebases row no=25 "
        f"obligations 18 to 52 and snapshot_parsed_count 1 to 52 with queue gap_qty "
        f"18 to 34 (执行缺口按密度口径 34（parsed 52−legacy 18）×1) while committed "
        f"totals 674/638 stay unchanged; local replay projects 52/52 PASS against the "
        f"frozen LOCAL-RESULT-QUEUE-01 receipt; preprod stays NOT_RUN with the four "
        f"named gaps {gaps}; remote_validation=NOT_RUN and grants_release_credit=false; "
        f"New registration command: none (dual-result-queue-run envelope reused); "
        f"write domain is bcs_e2e/result_queue_density/** and "
        f"tests/test_result_queue_density.py only.")

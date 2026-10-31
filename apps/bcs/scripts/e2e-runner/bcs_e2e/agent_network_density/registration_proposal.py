"""Describe-only serial integration proposal for wave idx1 row #1.

Nothing here executes, writes, registers, or mutates any file.  The proposal
is the complete text the owner session needs to integrate the 3
source-anchored network density leaves into executor-migration.json
(notes-only; the committed DUAL/PASS r7 face stays untouched), bcs_e2e/
inventory.py (fail-close membership and counter closure), and coverage-map
(overlay arithmetic), while the dual-network read/register/status envelopes
and their committed conclusions stay untouched.  All before-faces are
live-read from disk and fail closed on drift or repeat registration.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..inventory import (EXECUTOR_MIGRATION_NOTES, _EXPECTED_EXECUTOR_MIGRATION_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
                         load_executor_migration)
from ..run_state import RunStateError
from .obligations import (
    ACTOR_SHA256, COMMON_FILE, COMMON_SHA256, COVERAGE_ROW_NO, DENSITY_CONTRACT,
    DENSITY_OBLIGATION_COUNT, DUAL_COMMANDS, EXECUTOR_ROW_INDEX, PARENT,
    PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT, READ_CASE_KEYS,
    RESIDUAL_COUNT_GAP, RUNNER_FILE, RUNNER_SHA256, SOURCE_FILE, SOURCE_SHA256,
    STATIC_TOTALS, STORY_FUNCTION, NAMED_PREPROD_GAP_DETAILS,
    WAVE_SCOPE_UNREACHED_PARENTS, density_plan)

EXPECTED_ROW_FACE = {"mode": "DUAL", "preprod_verdict": "PASS", "gates": [],
                     "notes": []}
PROPOSAL_TOKEN = "AGENT_NETWORK_DENSITY_3_OBLIGATIONS_20261031"
UPSTREAM_ISSUE = None
CONTRACT_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"
CONTRACT_SOURCES = {"stories.sh": SOURCE_SHA256, "actor.sh": ACTOR_SHA256}

NOTE = (
    "row #1 story_user_prepares_agent_network: register 3 source-anchored "
    "density obligations under the retained NETWORK.* parent envelope "
    "(11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms; "
    "residual_count_gap=0); the 8 leg-PASS snapshot records align verbatim "
    "onto the first eight platform statements (identical description literals "
    "at stories.sh:269-280) and are absorbed one per leg by NETWORK.platform "
    "under the leg-verdict proxy caliber that the committed assertion-gap "
    "matrix itself labels approximate; the FAIL record and the two WARN "
    "records are one upstream manifest-asset-404 abort event anchored as "
    "AN.F01..AN.F03 at stories.sh:295 (emit chain common.sh assert_status "
    "104-107 > assert_eq 68 > fail print 74), stories.sh:37 (require_status "
    "failure-branch response warn) and e2e.sh:210 (runner stopped warn); the "
    "33 unreached actor/register static statements and the 11 unreached "
    "platform statements stay parent-charged with no fabricated leaves; "
    "notes-only integration, the dual-network-read-run/register-run/status-run "
    "envelopes and the committed DUAL/PASS r7 face stay untouched; committed "
    "verdict stays DUAL/PASS with notes 0 to 1; preprod density tier keeps "
    "the three named PREPROD_* gaps on file; remote_validation=NOT_RUN and "
    "grants_release_credit=false")

MD_ROW = (
    "| 2 | `story_user_prepares_agent_network` | 全绿†（parsed 11） | "
    "PASS 8/8 父镜像（obl 11；密度叶 NOT_RUN 0/3） | DUAL | PASS(r7) | "
    "parsed 11 − 义务 8 = 3 → 0（密度闭合 "
    "AGENT_NETWORK_DENSITY_3_OBLIGATIONS_20261031：8 条 leg-PASS 记录与 "
    "stories.sh:269-280 八语句 desc 逐字恒等，由 NETWORK.platform 父按腿判决"
    "代理口径吸收；FAIL+2 WARN 同源锚 AN.F01..AN.F03 于 stories.sh:295 → "
    "common.sh:74 fail 打印 / stories.sh:37 response warn / e2e.sh:210 "
    "stopped warn，为同一资产 404 中止事件的三层发射；故事中止后 actor/register "
    "33 条静态语句无 parsed 记录不建叶，platform 段余 11 条未到达语句父级承担；"
    "静态目录 platform 20/register 20/actor 13 仅披露）；密度执行缺口 11；"
    "预发密度层初始判定 NOT_RUN（设计 owned-local 离线镜像）+ 3 具名 "
    "PREPROD_* gap；committed verdict 维持 PASS(r7) 不动，notes 0→1；⏳P5 "
    "密度执行 |")

QUEUE_BASIS = ("执行缺口按密度口径 11（parsed 11 全量：8 父吸收 leg-PASS 记录 "
               "+ 3 密度叶镜像 NOT_RUN）；密度 delta=3×feasibility 3；dual trio "
               "committed r7 8/8 PASS 仅作镜像引用不重执行；零新物料")

RED_LINES = [
    "describe-only: no executor-migration.json row is created or rewritten here",
    "no edits to inventory.py, cli.py, artifact.py, case_command_registration.py, "
    "or tests/test_migration_inventory.py",
    "no edits to coverage-map/*, webhook_subscription*, slow_judge/**, "
    "edge_revoke_lifecycle/, delivery_density/**, workspace_density/, "
    "fixed_loop_density/, nested_fixed_loop_density/, "
    "workspace-migration-contract.json, edge_permission_backlog/, "
    "provider_publishes/, authority_density/, channel_density/, the landed "
    "density families, or the frozen network migration contract",
    "preprod density tier stays NOT_RUN with remote_validation=NOT_RUN and "
    "grants_release_credit=false; the committed DUAL/PASS r7 face is cited as "
    "mirror evidence only and is never re-executed by this package",
    "SHA-256 froze stories.sh, actor.sh and the emission-chain companions "
    "common.sh/e2e.sh; any drift fails closed before the proposal is usable",
]


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration proposal; nothing is executed."""
    plan = density_plan()
    if plan["parsed_obligation_count"] != PARSED_OBLIGATION_COUNT:
        raise ValueError("agent-network density proposal caliber changed")
    # Inventory fail-close: the proposal is only usable against the live
    # committed face; a verdict change, a parallel wave note, or a repeat
    # registration all fail closed before any increment text is produced.
    statuses = load_executor_migration()
    face = statuses.get(STORY_FUNCTION)
    if face != EXPECTED_ROW_FACE:
        raise RunStateError("agent-network executor-migration face changed")
    if NOTE in EXECUTOR_MIGRATION_NOTES:
        raise RunStateError("agent-network density note is already registered")
    expected_note_counts = Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
    current_total_notes = sum(expected_note_counts.values())
    if len(EXECUTOR_MIGRATION_NOTES) != current_total_notes:
        raise RunStateError("agent-network inventory note totals are inconsistent")
    return {
        "status": "DESCRIBE_ONLY",
        "token": PROPOSAL_TOKEN,
        "contract": DENSITY_CONTRACT,
        "parent_story": STORY_FUNCTION,
        "upstream_issue": UPSTREAM_ISSUE,
        "upstream_issue_basis": "no separate upstream issue anchor was supplied "
                                "to this wave; the row is cited by executor "
                                "row idx=1 and coverage-map row no=2",
        "executor_row_index": EXECUTOR_ROW_INDEX,
        "coverage_row_no": COVERAGE_ROW_NO,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "source": {
            "file": SOURCE_FILE, "sha256": SOURCE_SHA256,
            "contract_sources": dict(CONTRACT_SOURCES),
            "companion_sources": {"actor.sh": ACTOR_SHA256,
                                  "common.sh": COMMON_SHA256,
                                  "e2e.sh": RUNNER_SHA256},
            "emission_chain_sources": {"common.sh": COMMON_FILE,
                                       "e2e.sh": RUNNER_FILE},
            "story_line_span": [255, 265],
            "function_span": {"platform": [267, 322],
                              "register": [324, 373]},
            "migration_contract": "network-migration-contract.json",
            "contract_source_revision": CONTRACT_REVISION},
        "family": {
            "family": "agent_network (static mirror; not a dynamic_family "
                      "registration)",
            "dual_commands": list(DUAL_COMMANDS),
            "read_case_keys": list(READ_CASE_KEYS),
            "legacy_id": PARENT,
            "source_case_id": "LOCAL-NETWORK-01",
            "source_suite": "owned-local-network-v1",
            "local_executor": "bcs_e2e.local_network_run.local_network_run",
            "local_status": "LOCAL_NETWORK_PASS",
            "canonical_status": "LOCAL_SUITE_PASS",
            "completion_contract": "owned-local-network-completion-v1",
            "committed_preprod_face": "PASS(r7) 8/8 leaves; notes-only wave "
                                      "keeps the face",
            "registered_in_dynamic_family_map": False,
            "registered_triads": ["fixed_loop", "nested_fixed_loop",
                                  "result_queue", "draft_group"]},
        "parsed_absorption": dict(plan["parsed_absorption"]),
        "snapshot_provenance": dict(plan["snapshot_provenance"]),
        "static_statement_catalog": dict(plan["static_statement_catalog"]),
        "static_totals": dict(STATIC_TOTALS),
        "wave_scope_unreached_parents": dict(WAVE_SCOPE_UNREACHED_PARENTS),
        "not_charged_source_probes": list(plan["not_charged_source_probes"]),
        "emission_chain_disclosure": dict(plan["emission_chain_disclosure"]),
        "named_preprod_gaps": [
            {"id": gap, "status": "NAMED_DESIGN_GAP",
             "detail": NAMED_PREPROD_GAP_DETAILS[gap],
             "blocks": "preprod PASS credit"} for gap in plan["named_preprod_gaps"]],
        "inventory_increment": {
            "target_file": "bcs_e2e/inventory.py",
            "integration_mode": "owner session applies; this package writes nothing",
            "operation": "fail-close membership and counter closure",
            "anchors": {
                "notes_membership": "append the NOTE member to the "
                                    "EXECUTOR_MIGRATION_NOTES frozenset after "
                                    "the row #40 draft-group note (the entry "
                                    "before the closing '})'), keeping the "
                                    "frozenset face valid",
                "note_counts": "append {NOTE: 1} to "
                               "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS after "
                               "the row #40 entry (the entry before the "
                               "closing '}'), so load_executor_migration's "
                               "Counter face check stays green",
                "unchanged_counters": "state and gate counts stay "
                                      "untouched; the row face keeps "
                                      "mode=DUAL, preprod_verdict=PASS, "
                                      "gates=[], notes 0 to 1"},
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
                    "id": STORY_FUNCTION, "mode": "DUAL",
                    "preprod_verdict": "PASS", "gates": [],
                    "notes_delta": "+1 (this proposal note only)"}}},
        "coverage_map_increment": {
            "targets": ["coverage-map/coverage-map-20261031.json",
                        "coverage-map/coverage-map-20261031.md"],
            "integration_mode": "owner session applies; proposal text only",
            "row": {
                "no": COVERAGE_ROW_NO, "case_id": STORY_FUNCTION,
                "before": _coverage_before(),
                "after": {
                    "obligations": PARSED_OBLIGATION_COUNT,
                    "snapshot_parsed_count": PARSED_OBLIGATION_COUNT,
                    "gap_kind": "COUNT_GAP_CLOSED+EXECUTION_GAP",
                    "gap_parsed_minus_obligations": 0,
                    "gap_detail": ("parsed 11 − 义务 11 = 0（密度闭合 "
                                   + PROPOSAL_TOKEN + "：8 条 leg-PASS 记录与 "
                                   "stories.sh:269-280 八语句 desc 逐字恒等，由 "
                                   "NETWORK.platform 父按腿判决代理口径吸收；"
                                   "FAIL+2 WARN 同源锚 AN.F01..AN.F03 于 "
                                   "stories.sh:295 → common.sh:74 fail 打印 / "
                                   "stories.sh:37 response warn / e2e.sh:210 "
                                   "stopped warn，为同一资产 404 中止事件的三层"
                                   "发射；故事中止后 actor/register 33 条静态语句"
                                   "无 parsed 记录不建叶，platform 段余 11 条未"
                                   "到达语句父级承担；静态目录 platform 20/"
                                   "register 20/actor 13 仅披露）；密度执行缺口 "
                                   "11；预发密度层初始判定 NOT_RUN（设计 "
                                   "owned-local 离线镜像）+ 3 具名 PREPROD_* "
                                   "gap；committed verdict 维持 PASS(r7)，"
                                   "notes 0→1；⏳P5 密度执行"),
                    "queue": {"rank": 26, "gap_qty": PARSED_OBLIGATION_COUNT,
                              "feasibility": 3,
                              "score": PARSED_OBLIGATION_COUNT * 3,
                              "basis": QUEUE_BASIS}},
                "md_row": MD_ROW}},
        "committed_totals": {
            "obligations_committed_sum": 674,
            "upstream_parsed_known_sum": 638,
            "disposition": "unchanged by this proposal; the owner landing "
                           "commit rebases the two row overlay fields only"},
        "upstream_roster": dict(plan["upstream_roster"]),
        "replay_caliber": {
            "local": "8/8 parent leg citations mirrored PASS (committed r7 "
                     "face plus the owned-local LOCAL-NETWORK-01 conclusion); "
                     "3/3 density leaves mirror the frozen upstream records "
                     "and project NOT_RUN by design (record mirrors are not "
                     "probes); the offline receipt overall stays NOT_RUN and "
                     "never mints PASS",
            "preprod": "11/11 NOT_RUN with the three named gaps; even an "
                       "authorized injected observation set stays capped at "
                       "NOT_RUN overall"},
        "red_lines": list(RED_LINES),
        "new_registration_command": False,
        "write_domain": ["bcs_e2e/agent_network_density/**",
                         "tests/test_agent_network_density.py",
                         "tests/test_agent_network_density_contract.py",
                         "tests/test_agent_network_density_replay.py",
                         "tests/test_agent_network_density_registration_proposal.py",
                         "tests/test_agent_network_density_hygiene.py",
                         "tests/agent_network_density_test_support.py"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"],
    }


def _coverage_before() -> dict[str, Any]:
    """Live-read the committed coverage-map row no=2 face; fail on drift."""
    path = (Path(__file__).resolve().parents[2] / "coverage-map"
            / "coverage-map-20261031.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    row = next(item for item in value["rows"] if item["no"] == COVERAGE_ROW_NO)
    if (row["case_id"] != PARENT
            or row["local_projection"]["obligations"] != PARENT_OBLIGATION_COUNT
            or row["local_projection"]["leaves"] != "PASS 8/8 叶"
            or not row["local_projection"]["obligations_basis"].startswith(
                "contract:network-migration-contract.json")
            or row["upstream"]["snapshot_parsed_count"] != PARSED_OBLIGATION_COUNT
            or row["gap"]["parsed_minus_obligations"] != DENSITY_OBLIGATION_COUNT
            or "COUNT_GAP" not in row["gap"]["kind"]
            or row["queue"] != {"rank": 26, "gap_qty": 3, "feasibility": 3,
                                "score": 9, "basis": "密度 delta=3×3；零物料"}):
        raise RunStateError("agent-network committed coverage-map face changed")
    return row


def describe_only_mainline_fragment() -> str:
    """Return the text a mainline integrator may apply; this module writes nothing."""
    proposal = registration_proposal()
    gaps = ", ".join(gap["id"] for gap in proposal["named_preprod_gaps"])
    return (
        f"{PROPOSAL_TOKEN}: {DENSITY_CONTRACT} registers 8 retained NETWORK.* "
        f"contract parents plus 3 source-anchored density obligations for "
        f"executor row idx=1 (row #1) {STORY_FUNCTION} (coverage-map row no=2; "
        f"11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms; "
        f"residual_count_gap=0); the 8 leg-PASS records align verbatim onto "
        f"the first eight platform statements and are absorbed by the "
        f"NETWORK.platform parent; the FAIL record and the two WARN records "
        f"are one upstream manifest-asset-404 abort event anchored as "
        f"AN.F01..AN.F03 at stories.sh:295 (common.sh assert chain fail print "
        f"at common.sh:74), stories.sh:37 and e2e.sh:210; the 33 unreached "
        f"actor/register static statements and the 11 unreached platform "
        f"statements stay parent-charged (wave-scope disclosure, no fabricated "
        f"leaves); executor-migration.json row idx=1 notes-only increment "
        f"(face stays DUAL/PASS, notes 0 to 1); bcs_e2e/inventory.py gains "
        f"exactly one EXECUTOR_MIGRATION_NOTES member and one "
        f"_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS entry (state and gate "
        f"counters unchanged); coverage-map overlay rebases row no=2 "
        f"obligations 8 to 11 and closes the COUNT_GAP 3 to 0 (queue gap_qty "
        f"3 to 11, feasibility 3 unchanged, score 9 to 33 keeping the "
        f"committed score=gap_qty×feasibility identity) while committed totals "
        f"674/638 stay unchanged; dual envelopes reused (dual-network-read-run "
        f"with its six read case keys, dual-network-register-run, "
        f"dual-network-status-run; no new registration command); local replay "
        f"mirrors 8 committed leg PASS citations plus 3 frozen record mirrors "
        f"as NOT_RUN (offline overall never PASS); preprod density tier stays "
        f"NOT_RUN with the three named gaps {gaps}; the committed 全绿† roster "
        f"face is cited with null parsed-greenness, not fabricated; "
        f"remote_validation=NOT_RUN and grants_release_credit=false; write "
        f"domain is bcs_e2e/agent_network_density/** and "
        f"tests/test_agent_network_density.py only. umbrella split charter: "
        f"the single frozen test charter was split byte-verbatim into the "
        f"contract/replay/registration_proposal/hygiene test modules plus a "
        f"shared agent_network_density_test_support module to satisfy the "
        f"repository 1000-line budget; every frozen assertion stays verbatim "
        f"in its split module")

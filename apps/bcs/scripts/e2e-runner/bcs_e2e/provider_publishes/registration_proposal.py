"""Describe-only mainline integration proposal for the provider-publishes density wave.

This module closes the density gap of executor-migration row index 7
(gap-matrix case #4, ``story_provider_operator_publishes_agent``): the frozen
dual contract parses 98 upstream receipt obligations while the committed
executor B-tree registered only the 32 legacy ``PROVIDER.*`` operations.  The
wave lands the 98-leaf source-anchored roster (``density.py``) and its offline
owned-local replay projector (``density_run.py``) inside the
``provider_publishes`` family write domain; mainline integration is
**notes-only** and nothing in this module is applied:

* the 98 leaves ride the already-registered ``dual-provider-publishes-run``
  envelope (``cli.py`` and ``case_command_registration.py`` stay
  byte-identical; ``new_registration_command`` stays false);
* the executor-migration row keeps mode DUAL and preprod_verdict PASS bytes
  untouched - that PASS rests on the r6 legacy-42 preprod slice (commit
  02297df18) and this proposal only appends a note disclosing the density
  closure, the offline replay basis and the honest preprod NOT_RUN posture;
* the inventory fail-close declaration needs exactly three edits (frozenset
  token, expected-counts entry, and nothing else);
* the coverage-map row #4 increment is a text-only proposal into a red-zone
  shared face; the on-file parsed_minus_obligations already reads 0 via the
  committed frozen contract, so the increment registers the wave, the +66
  delta versus the legacy 32 B-tree caliber, and the persisting 98-row
  execution gap behind the P4 material gate;
* every preprod gap is registered by name, led by the r8-recorded live
  blocker verbatim (PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE,
  credential_resolution NOT_ATTEMPTED, zero network requests,
  NO_REMOTE_RESOURCES_CREATED) and the frozen dual-family seam gaps.

All anchors below are verified read-only against the current tree; a drift
here means the proposal text needs re-review, never silent rebasing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from .contract import CONTRACT_SHA256, PARENT, REQUIREMENTS
from .density import (BRIEF_SPAN_CLAIM, DENSITY_LEAF_COUNT, FIELD_RECEIPT_COUNT,
                      HELPER_SPAN, PARENT_LEAF_COUNT, PARENT_SPAN, PARSED_OBLIGATION_COUNT,
                      SLUG_ATOM_COUNT, SLUG_FIELD_ATOM_COUNT, SLUG_STATUS_ATOM_COUNT,
                      STORIES_SHA256, build_provider_publishes_density_obligations,
                      provider_publishes_density_reconciliation)
from .density_run import R8_OPERATOR_STATEMENT, R8_REASON_CLASS, WAVE_TOKEN
from .dual_case_run import COMMAND, NAMED_GAPS

REPO_ROOT = Path(__file__).resolve().parents[6]
PROPOSAL_STATUS = "DESCRIBE_ONLY"
APPLIED = False
NEW_REGISTRATION_COMMAND = False
PROPOSAL_TOKEN = WAVE_TOKEN
ENVELOPE_COMMAND = COMMAND
EXECUTOR_ROW_INDEX = 7
EXECUTOR_ROW_1BASED = 8
GAP_MATRIX_CASE_NO = 4
COVERAGE_MAP_ROW = 4
ROW_ID = PARENT
RESIDUAL_COUNT_GAP = 0
OBLIGATIONS_ON_FILE_BEFORE = 32
WAVE_DELTA = DENSITY_LEAF_COUNT

ANCHORS: dict[str, tuple[str, ...]] = {
    "apps/bcs/scripts/e2e-runner/executor-migration.json": (
        '      "id": "story_provider_operator_publishes_agent",',
        '        "mode": "DUAL",',
        '        "preprod_verdict": "PASS",',
        '        "notes": []',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/cli.py": (
        "from .provider_publishes.dual_case_run import dual_provider_publishes_case_run",
        'elif args.command == "dual-provider-publishes-run":',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/case_command_registration.py": (
        "from .provider_publishes.dual_case_run import (COMMAND as PROVIDER_PUBLISHES_COMMAND,",
        "register_provider_publishes_dual_case_commands(commands)",
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/inventory.py": (
        "    'AUTHORITY_DENSITY_42_OBLIGATIONS_20261031',",
        "    'AUTHORITY_DENSITY_42_OBLIGATIONS_20261031': 1,",
    ),
    "apps/bcs/scripts/e2e-runner/coverage-map/coverage-map-20261031.md": (
        "| 4 | `story_provider_operator_publishes_agent` | 全绿†（parsed 98） |",
        "密度闭合 98/98；执行缺口 98（凭证门：人类 cookie 2026-10-29 过期 + provider 物料）",
    ),
    "apps/bcs/scripts/e2e-runner/coverage-map/coverage-map-20261031.json": (
        '"case_id": "story_provider_operator_publishes_agent"',
        '"snapshot_parsed_count": 98',
        '"obligations_basis": "contract:provider-publishes-migration-contract.json requirements=98（committed 96bb3ab9e',
        '"parsed_minus_obligations": 0',
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/provider_publishes/dual_case_run.py": (
        "COMMAND = 'dual-provider-publishes-run'",
        "PREPROD_PROVIDER_PUBLISHES_JOURNEY_SEAM_PENDING",
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/provider_publishes/contract.py": (
        "CONTRACT_SHA256 = '" + CONTRACT_SHA256 + "'",
    ),
    "apps/bcs/scripts/e2e-test/stories.sh": (
        "story_provider_operator_publishes_agent() {",
        "_story_provider_manages_organization() {",
    ),
    "apps/bcs/scripts/e2e-runner/bcs_e2e/provider_publishes/README.md": (
        "The frozen map contains **98 receipt obligations**",
    ),
}

PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE = (
    "density-projection(provider_publishes density wave E1 20261031): parsed 98 = "
    "obligations 98 closed (32 retained legacy PARENT envelopes + 66 density "
    "leaves: 44 legacy-inline field receipts + 22 slug atoms [10 slug station "
    "statuses + 12 slug fields]); residual_count_gap=0; upstream anchor "
    "stories.sh parent 1422-1607 + helper 1609-1724 (brief claim " + BRIEF_SPAN_CLAIM
    + " disclosed as superset) frozen at SHA " + STORIES_SHA256[:16] + "...; offline "
    "owned-local replay PASS 98/98 (42 status leaves truly re-checked against frozen "
    "expected_status + 56 field leaves recorded-verdict replay of the authoritative "
    "w3a attempt-3 local receipt 20261031-w3a-provider-publishes); preprod leg honest "
    "NOT_RUN fail-close: r8 live leg BLOCKED-at-entry 0/98 " + R8_REASON_CLASS
    + " (credential_resolution NOT_ATTEMPTED, network_requests 0, "
    "NO_REMOTE_RESOURCES_CREATED; operator statement: canonical 2026-10-29 cookie "
    "expired/USER_NOT_LOGIN, no re-probe authorized); named gaps register the frozen "
    "dual-family seam trio (" + "/".join(sorted(NAMED_GAPS)) + "), the P4 material "
    "gate (admission code landed 3c6d6f374; human cookie refresh and provider "
    "principal material need explicit user authorization; provider_admin_token env "
    "reference; frozen S04 local-form contract) and the sealed-v1/v2 profile posture; "
    "verdict lineage: the committed row preprod_verdict PASS bytes rest on the r6 "
    "legacy-42 preprod slice (02297df18) and stay byte-identical this wave; "
    "remote_validation=NOT_RUN; grants_release_credit=false; envelope reuse "
    + ENVELOPE_COMMAND + " (no new CLI command; cli.py and "
    "case_command_registration.py byte-identical)")

PROPOSED_COVERAGE_MAP_ROW_NOTE = (
    "密度波 E1（20261031）闭合：parsed 98 = 义务 98（32 保留 legacy PARENT 信封 + "
    "66 密度叶：44 legacy 内联字段收据 + 22 slug 原子[10 站状态 + 12 字段]）；"
    "residual_count_gap=0；源锚 stories.sh 父函数 1422-1607 + 辅助 1609-1724 "
    "（brief 声称 1422-1737 为超集披露）SHA " + STORIES_SHA256[:16] + "... 冻结；"
    "owned-local 离线 replay 收据 PASS 98/98（42 状态叶真值重查 + 56 字段叶按 "
    "w3a attempt-3 权威本地 PASS 记录裁决重放，不伪造响应体）；预发腿诚实 NOT_RUN "
    "fail-close：r8 实弹 BLOCKED-at-entry 0/98（" + R8_REASON_CLASS + "，凭证未尝试"
    "解析、零网络请求、未建远程资源；操作员原文：2026-10-29 cookie 过期/"
    "USER_NOT_LOGIN，无再探授权）；具名 gap 登记 P4 物料门控（3c6d6f374 准入代码"
    "已落地；cookie 刷新需用户显式授权；provider principal signing key 与 "
    "provider_admin_token 环境引用；frozen S04 本地形态契约）+ sealed-v1/v2 姿态 + "
    "冻结 seam 三件套；已提交 row preprod_verdict PASS 字节谱系（r6 legacy-42 预发"
    "切片 02297df18）本波不动；remote_validation=NOT_RUN；grants_release_credit="
    "false；执行缺口 98 维持登记，待 P4 物料门与用户授权后走已注册 dual 信封实弹")


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
    """Return the propose-only integration text an authorized wave may apply."""
    return (
        "DESCRIBE_ONLY token=" + PROPOSAL_TOKEN + "\n"
        "- executor-migration.json row index " + str(EXECUTOR_ROW_INDEX) + " (1-based row "
        + str(EXECUTOR_ROW_1BASED) + ") " + ROW_ID + ": append note " + PROPOSAL_TOKEN
        + " to the empty notes list; mode DUAL, preprod_verdict PASS and gates [] stay "
        "byte-identical; the PASS byte rests on the r6 legacy-42 preprod slice and this "
        "note only discloses the density closure\n"
        "- inventory.py: append the token to EXECUTOR_MIGRATION_NOTES and add its "
        "count entry (1) to _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS; three edits "
        "total, nothing else\n"
        "- coverage-map row #4: append the proposed row note (density closure + "
        "offline replay basis + named preprod gaps; parsed_minus_obligations stays 0; "
        "the persisting execution gap 98 stays registered behind the P4 material gate)\n"
        "- envelope reuse: the 98 leaves ride " + ENVELOPE_COMMAND + "; cli.py and "
        "case_command_registration.py stay byte-identical this wave; no new parser, "
        "dispatch or registration is proposed\n"
        "- env names BCS_E2E_HUMAN_COOKIE / BCS_E2E_DRIVER_TOKEN only; values never "
        "touched by this proposal or the offline projector\n"
        "- red lines: this proposal writes nothing; every shared face file listed in "
        "red_lines stays untouched")


def registration_proposal() -> dict[str, Any]:
    """Return the describe-only integration and accounting proposal."""
    assert_anchors()
    rows = build_provider_publishes_density_obligations()
    recon = provider_publishes_density_reconciliation()
    return {
        "proposal_status": PROPOSAL_STATUS,
        "token": PROPOSAL_TOKEN,
        "applied": APPLIED,
        "new_registration_command": NEW_REGISTRATION_COMMAND,
        "shared_face_files_touched": [],
        "wave": {
            "layer": "PROVIDER_PUBLISHES_DENSITY_WAVE_E1",
            "roster_contract": "provider-publishes-density-v1",
            "wave_token": WAVE_TOKEN,
            "parent": PARENT,
            "gap_matrix_case_no": GAP_MATRIX_CASE_NO,
            "executor_row_index": EXECUTOR_ROW_INDEX,
            "executor_row_1based": EXECUTOR_ROW_1BASED,
            "parsed_upstream_steps": PARSED_OBLIGATION_COUNT,
            "obligations_on_file_before": OBLIGATIONS_ON_FILE_BEFORE,
            "obligations_on_file_before_basis": (
                "legacy provider-migration-contract.json requirements=32 (ids "
                "PROVIDER.<op>); gap-matrix row #4 obligations=32 caliber"),
            "roster_leaves": len(rows),
            "roster_delta_vs_btree": WAVE_DELTA,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        },
        "envelope": {
            "command": ENVELOPE_COMMAND,
            "report_contract": "dual-provider-publishes-case-run-v1",
            "suite": "dual-provider-publishes-v1",
            "case_id": "DUAL-PROVIDER-PUBLISHES-01",
            "cli_required": False,
            "case_command_registration_required": False,
            "reuse_statement": (
                "the 98 density leaves reuse the committed dual envelope as a strict "
                "key superset; the offline projector never registers a parser, "
                "dispatch or CLI command and opens no socket"),
        },
        "executor_migration": {
            "row_index": EXECUTOR_ROW_INDEX,
            "row_id": ROW_ID,
            "state_on_file": {"mode": "DUAL", "preprod_verdict": "PASS",
                              "gates": [], "notes": []},
            "notes_before": [],
            "notes_append_after": [PROPOSAL_TOKEN],
            "placement": (
                "append the wave token to row notes AFTER the existing (empty) list; "
                "no mode, verdict, gate or other field changes; the committed PASS "
                "byte keeps its r6 lineage disclosure inside the note text"),
            "verdict_lineage": (
                "committed preprod_verdict PASS rests on the r6 preprod slice "
                "20261031-regression-r6-provider-provider-publishes (legacy 42-step "
                "envelope: frozen_core 32 + source_api_get_discovery 10, transport "
                "PRODUCT_HTTP, credential_kind "
                "preprod_human_cookie_with_minted_provider_credentials, resource "
                "disposition PROVIDER_BOT_RETIRED_PROVIDER_AND_ORGANIZATION_RETAINED, "
                "remote_validation NOT_RUN, grants_release_credit false; commit "
                "02297df18 promoted it as the 19th dual preprod PASS slice); the r8 "
                "98-envelope preprod leg never executed (BLOCKED at entry); this "
                "proposal rewrites no verdict byte"),
            "proposed_row_note": PROPOSED_EXECUTOR_MIGRATION_ROW_NOTE,
        },
        "inventory": {
            "declaration": "EXECUTOR_MIGRATION_NOTES",
            "note_counts_declaration": "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
            "edits": [
                "executor-migration.json rows index 7 notes []: append " + PROPOSAL_TOKEN,
                "inventory.py EXECUTOR_MIGRATION_NOTES: append the token literal",
                "inventory.py _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS: add "
                "{token: 1}",
            ],
            "proposed_count": 1,
            "statement": (
                "exactly three edits land the notes-only integration; the "
                "reviewed-release parent set, PARSED_OBLIGATION_COUNT declarations "
                "and every other row stay untouched; load_executor_migration "
                "Counter equality must hold after the append"),
        },
        "coverage_map": {
            "row": COVERAGE_MAP_ROW,
            "case_id": ROW_ID,
            "parsed_on_file": PARSED_OBLIGATION_COUNT,
            "obligations_on_file": PARSED_OBLIGATION_COUNT,
            "parsed_minus_obligations_on_file": 0,
            "residual_count_gap_after_wave": RESIDUAL_COUNT_GAP,
            "wave_delta_vs_legacy_btree": WAVE_DELTA,
            "execution_gap_on_file": PARSED_OBLIGATION_COUNT,
            "execution_gap_posture": (
                "执行缺口 98 维持登记：密度闭合不影响执行门；r8 实弹腿 BLOCKED-at-entry"),
            "proposed_row_note": PROPOSED_COVERAGE_MAP_ROW_NOTE,
        },
        "named_preprod_gaps": {
            "r8_recorded_live_blocker": {
                "reason_class": R8_REASON_CLASS,
                "operator_statement_verbatim": R8_OPERATOR_STATEMENT,
                "recorded_run_id": "20261031-regression-r8-preprod-provider-publishes-provider-publishes",
                "credential_resolution": "NOT_ATTEMPTED",
                "network_requests": 0,
                "resource_disposition": "NO_REMOTE_RESOURCES_CREATED",
                "state_dir": recon["preprod_recorded_evidence"]["r8_state_dir"],
            },
            "frozen_seam_gaps": {name: detail for name, detail in NAMED_GAPS.items()},
            "p4_material_gate": {
                "gate": "P4",
                "nature": (
                    "material/credential gate at preprod entry, not a code or "
                    "transport defect; admission code landed as commit 3c6d6f374 "
                    "(read-only honest admission surface) while r8 predates it"),
                "materials": [
                    "人类 cookie 刷新（用户授权物料）",
                    "provider principal signing key 环境引用",
                    "provider_admin_token 环境引用",
                    "frozen S04 本地形态契约",
                ],
            },
            "profile_posture": (
                "sealed v1 profile is a blocked-no-network seal; the v2 gateway "
                "admission profile resolves credential references by NAME only and "
                "probes read-only faces; neither posture executes the "
                "provider-admin write journey offline"),
            "density_transport_gap": (
                "the offline projector itself registers "
                "PROVIDER_PUBLISHES_DENSITY_PREPROD_TRANSPORT_GAP and fail-closes "
                "the preprod tier to NOT_RUN with zero network requests"),
        },
        "red_lines": [
            "this proposal applies nothing: applied=false, shared_face_files_touched=[]",
            "executor-migration.json, coverage-map/*, bcs_e2e/inventory.py, "
            "bcs_e2e/cli.py, bcs_e2e/case_command_registration.py, "
            "bcs_e2e/artifact.py and tests/test_migration_inventory.py are "
            "owner-integration write domains; this wave only proposes note text",
            "provider_publishes family existing files stay byte-identical; the wave "
            "only adds density.py, density_run.py and registration_proposal.py",
            "parallel-session write domains stay untouched: webhook_subscription*, "
            "slow_judge/**, edge_revoke_lifecycle/, delivery_density/**, "
            "workspace_density/, service_policy_density/, channel_density/, "
            "authority_density/, edge_permission_backlog/, workspace-migration-contract.json",
            "no credential value is resolved, stored or echoed; env names "
            "BCS_E2E_HUMAN_COOKIE / BCS_E2E_DRIVER_TOKEN only",
            "no preprod execution, no network, no subprocess, no release credit",
        ],
        "user_actions": [
            "close the P4 material gate: obtain explicit user authorization for the "
            "human cookie refresh and provision provider principal material "
            "(signing key and provider_admin_token environment references)",
            "execute the preprod leg through the registered " + ENVELOPE_COMMAND
            + " envelope under the sealed-v1/v2 admission stages after the material "
            "gate closes",
            "after the focused density tests re-run green, an authorized mainline "
            "wave may land the three inventory edits and the two notes-only "
            "increments above",
            "keep the r8 verdict bytes and the coverage-map lag registration "
            "(登记器字段未随 r8 实弹收据更新) visible until the preprod leg actually executes",
        ],
        "non_goals": [
            "no preprod PASS is fabricated: the preprod leg stays honest NOT_RUN "
            "with named gaps and remote_validation=NOT_RUN",
            "no new CLI command, parser or registration is proposed",
            "no verdict byte of the committed executor row is rewritten",
            "no upstream 全绿† aggregate color is merged into local obligation truth",
            "no response body is fabricated offline; field leaves replay recorded "
            "verdict truth or evaluate caller-labeled injected observations only",
        ],
        "drop_dead_checks": {
            "leaves_built": len(rows),
            "parent_leaves": PARENT_LEAF_COUNT,
            "density_leaves": DENSITY_LEAF_COUNT,
            "field_receipts": FIELD_RECEIPT_COUNT,
            "slug_atoms": SLUG_ATOM_COUNT,
            "slug_status_atoms": SLUG_STATUS_ATOM_COUNT,
            "slug_field_atoms": SLUG_FIELD_ATOM_COUNT,
            "requirement_ids_equal_frozen_order": [r.obligation_id for r in rows] == list(REQUIREMENTS),
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "upstream_sha256_frozen": STORIES_SHA256,
            "anchors_verified_read_only": True,
        },
        "source_freeze": {
            "source_file": "apps/bcs/scripts/e2e-test/stories.sh",
            "stories_sha256": STORIES_SHA256,
            "parent_function_span": list(PARENT_SPAN),
            "helper_function_span": list(HELPER_SPAN),
            "leaf_span_bounds": [1429, 1723],
            "brief_span_claim": BRIEF_SPAN_CLAIM,
            "contract_sha256": CONTRACT_SHA256,
        },
        "reconciliation": recon,
    }

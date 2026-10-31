"""Describe-only serial integration proposal for wave E2a rows #18/#19.

Nothing here executes, writes, registers, or mutates any file.  The proposal
is the complete text the owner session needs to integrate the two dynamic
owned-local fixed-Loop density families into executor-migration.json
(notes-only, rows #18 and #19), bcs_e2e/inventory.py (fail-close membership
and counter closure) and coverage-map (overlay arithmetic for rows no 23/24),
while the dual-fixed-loop-run / dual-nested-fixed-loop-run envelopes, their
committed DUAL / BLOCKED_INVENTORIED verdicts and their gates stay untouched.

Every anchor is read live from the committed faces and fails closed before
any increment text is produced: the two executor-migration row faces, the
inventory note counters, the two coverage-map rows (json and md), the two
manifest baseline commits, the two frozen migration contracts, the dynamic
family command registrations in cli.py and case_command_registration.py, and
the SHA-frozen story bytes through the density contract itself.  The preprod
posture is registered by design: both stories run on a runner-owned dynamic
owned-local runtime, the nested migration contract freezes preprod as
REMOTE_UNSUPPORTED, and neither family ever counts a dual preprod leg.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..case_adapters import FIXED_LOOP_ADAPTER_METHOD_NAMES
from ..inventory import (EXECUTOR_MIGRATION_NOTES, _EXPECTED_EXECUTOR_MIGRATION_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS,
                         _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
                         load_executor_migration)
from ..run_state import RunStateError
from .catalog import (
    COMMON_SH_SHA256, DENSITY_CONTRACT, DRIVER_TOKEN_ENV, FAMILY_CHARGED_ROW_COUNT,
    FAMILY_FIXED, FAMILY_LEAF_COUNT, FAMILY_LEDGER_SITE_COUNT, FAMILY_NESTED,
    FAMILY_PARENT_COUNT, FAMILY_ROSTER_ROW_COUNT, FAMILY_SITE_COUNT,
    FIXED_ASSERT_SITE_COUNT, FIXED_CHARGED_ROW_COUNT, FIXED_CONTRACT_FILE,
    FIXED_CONTRACT_JSON_PATH, FIXED_CONTRACT_JSON_SHA256, FIXED_FACE_GAP,
    FIXED_FACE_OBLIGATIONS, FIXED_FACE_PARSED, FIXED_LEAF_COUNT, FIXED_LEDGER_SITE_COUNT,
    FIXED_PARENT_COUNT, FIXED_PY_FILE, FIXED_PY_SHA256, FIXED_ROSTER_ROW_COUNT,
    FIXED_SH_ASSERT_LINE, FIXED_SH_FILE, FIXED_SH_SHA256, FIXED_SH_STORY_SPAN,
    FIXED_SITE_COUNT, FIXED_TRANSPORT_SITE_COUNT, FIXED_WRAPPER_LEAF, GATE_ID_FIXED,
    GATE_ID_NESTED, NESTED_ASSERT_SITE_COUNT, NESTED_CHARGED_ROW_COUNT,
    NESTED_CONTRACT_FILE, NESTED_CONTRACT_JSON_PATH, NESTED_CONTRACT_JSON_SHA256,
    NESTED_FACE_GAP, NESTED_FACE_OBLIGATIONS, NESTED_FACE_PARSED, NESTED_LEAF_COUNT,
    NESTED_LEDGER_SITE_COUNT, NESTED_PARENT_COUNT, NESTED_PY_FILE, NESTED_PY_SHA256,
    NESTED_ROSTER_ROW_COUNT, NESTED_SH_ASSERT_LINE, NESTED_SH_STORY_SPAN, NESTED_SITE_COUNT,
    NESTED_TRANSPORT_SITE_COUNT, NESTED_WRAPPER_LEAF, PROVIDER_JUDGE_MOCK_SHA256,
    RESIDUAL_COUNT_GAP, RUNNER_ROOT, SCHEMA_VERSION, SOURCE_REVISION,
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, STORY_FIXED,
    STORY_NESTED
)
from .contract import (
    family_reconciliation, fixed_loop_density_plan, nested_fixed_loop_density_plan,
    obligations, wrapper_gate_envelope
)
from .obligations import (FIXED_ARM_LEG_RULES, FIXED_CONDITIONAL_LEAVES,
                          FIXED_DEFAULT_LEGS, NESTED_CONDITIONAL_LEAVES,
                          NESTED_MARKER_LEGS, NESTED_SITE_FREE_PARENT_COUNT)
from .replay import PREPROD_BY_DESIGN, UPSTREAM_ROSTER, mock_observations, project
from .registrations import (
    DUAL_FACE_DIVERGENCE_ID, FACE_UNDERCOUNT_ID, FAMILY_TOKEN, FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL,
    FIXED_PREPROD_GAP_ID, FIXED_ROW_NOTE_PROPOSAL, FIXED_ROW_TOKEN, NAMED_REGISTRATIONS,
    NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL, NESTED_PREPROD_GAP_ID, NESTED_ROW_NOTE_PROPOSAL,
    NESTED_ROW_TOKEN)

EXPECTED_ROW_FACE = {"mode": "DUAL", "preprod_verdict": "BLOCKED_INVENTORIED",
                     "gates": [], "notes": []}

FIXED_EXECUTOR_ROW_INDEX = 18
NESTED_EXECUTOR_ROW_INDEX = 19
FIXED_COVERAGE_ROW_NO = 23
NESTED_COVERAGE_ROW_NO = 24
UPSTREAM_ISSUES = {STORY_FIXED: "#23", STORY_NESTED: "#24"}

REPO_ROOT = Path(__file__).resolve().parents[6]
COVERAGE_MAP_JSON = RUNNER_ROOT / "coverage-map" / "coverage-map-20261031.json"
COVERAGE_MAP_MD = RUNNER_ROOT / "coverage-map" / "coverage-map-20261031.md"
MANIFEST_JSON = RUNNER_ROOT / "manifest.json"
RETAINED_MANIFEST_JSON = RUNNER_ROOT / "retained-manifest.json"
DYNAMIC_FAMILY_CONTRACT_PY = (REPO_ROOT / "apps" / "bcs" / "scripts" / "e2e-runner"
                              / "bcs_e2e" / "dynamic_family" / "contract.py")
CLI_PY = (REPO_ROOT / "apps" / "bcs" / "scripts" / "e2e-runner"
          / "bcs_e2e" / "cli.py")
CASE_COMMAND_REGISTRATION_PY = (REPO_ROOT / "apps" / "bcs" / "scripts" / "e2e-runner"
                                / "bcs_e2e" / "case_command_registration.py")

NESTED_CONTRACT_ENVIRONMENT = {"local": "LOCAL_EXECUTOR_ONLY",
                               "preprod": "REMOTE_UNSUPPORTED"}
NESTED_STALE_SOURCE_REVISIONS = {
    "nested_fixed_loop_story.py": "c6f00120949078f14cb0d3602fb8301f2ccb1803",
    "fixed_loop_story.py": "c6f00120949078f14cb0d3602fb8301f2ccb1803",
    "http_provider_judge_mock.py": "c86bc262ea2513d9eb6d13c1f0725ed0ace204d5",
}
FIXED_SANDBOX_REQUIREMENT_ID = "FIXED_LOOP.sandbox-isolation"
FIXED_SANDBOX_REQUIREMENT_PATH = "${OWNED_FIXED_LOOP_SANDBOX}"
NESTED_REQUIRED_STORY_ARGUMENTS = ("--base-url", "--mock-url", "--cli", "--driver",
                                   "--human")

FIXED_DUAL_COMMAND = "dual-fixed-loop-run"
NESTED_DUAL_COMMAND = "dual-nested-fixed-loop-run"
FIXED_CLI_DISPATCH_NEEDLE = f'args.command == "{FIXED_DUAL_COMMAND}"'
NESTED_CLI_DISPATCH_NEEDLE = f'args.command == "{NESTED_DUAL_COMMAND}"'
FIXED_COMMAND_REGISTRATION_NEEDLE = f'"{FIXED_DUAL_COMMAND}"'
NESTED_COMMAND_REGISTRATION_NEEDLE = f'"{NESTED_DUAL_COMMAND}"'

# Mirror of the replay executor-binding extension for the nested family; the
# proposal re-derives it live from the local replay receipt and fails closed
# when the committed receipt shape drifts from this frozen registry.
BLACK_BOX_CHILD_KEYS = (
    "child_exit_code", "mock_child_ready", "mock_child_exit_code",
    "mock_child_redaction_gate", "required_assertion_count", "assertions_all_pass",
    "story_markers_observed", "observed_marker_checks", "zero_traffic",
    "mock_child_control_planes", "resource_disposition", "credential_memory")

DISCLOSED_FACE_OBSERVATIONS = [
    ("nested-fixed-loop-migration-contract.json still freezes the stale "
     "source_file_revisions c6f00120949078f14cb0d3602fb8301f2ccb1803 for both story "
     "modules and c86bc262ea2513d9eb6d13c1f0725ed0ace204d5 for the Provider/Judge mock, "
     "while the on-disk SHA-256 freeze of this wave reads "
     + NESTED_PY_SHA256[:12] + " (nested story), " + FIXED_PY_SHA256[:12]
     + " (base story) and " + PROVIDER_JUDGE_MOCK_SHA256[:12] + " (mock); the density "
     "projection anchors the on-disk bytes fail-close and registers the stale contract "
     "revision block verbatim, never silently rebased"),
    ("coverage-map row no 23 detail carries the explicit clause that the preprod leg must "
     "not be counted as a dual leg, while row no 24 detail lacks that clause even though "
     "both rows share the LOCAL_ONLY / DYNAMIC_OWNED_LOCAL posture; both committed rows "
     "stay byte-identical this wave and the asymmetry is disclosed here"),
    ("the executor-migration face rows #18/#19 carry mode DUAL with an empty notes list "
     "while coverage-map records dual.status LOCAL_ONLY with executor_migration_mode "
     "DYNAMIC_OWNED_LOCAL; the two committed faces disagree on whether a preprod dual leg "
     "exists and the divergence is registered by name, never normalized"),
]

RED_LINES = [
    "describe-only: no executor-migration.json row face is created or rewritten here; "
    "rows #18/#19 stay byte-identical and their notes lists stay empty until the owner "
    "session applies the two proposal notes",
    "no edits to inventory.py, cli.py, artifact.py, case_command_registration.py, "
    "executor-migration.json or coverage-map/* inside this package; every increment is "
    "text proposal material only",
    "no writes into coverage-map/*, webhook_subscription*, slow_judge/**, "
    "edge_revoke_lifecycle/, delivery_density/**, workspace_density/, "
    "service_policy_density/, channel_density/, authority_density/, provider_publishes/, "
    "result_queue_density or draft_group_density domains; those belong to other waves",
    "preprod legs stay honest NOT_RUN under a BLOCKED_INVENTORIED overall cap: the nested "
    "family is contractually REMOTE_UNSUPPORTED and both families execute on a "
    "runner-owned dynamic owned-local runtime, so the preprod leg is never counted as a "
    "dual preprod leg",
    "SHA-256 froze the shell wrapper, both python stories, both migration-contract JSONs, "
    "common.sh and the Provider/Judge mock; any drift fails closed before any increment "
    "text is produced",
    "no credential value appears anywhere; the driver-token face is the environment "
    "variable name only and the provider downlink quote stays a disk-assembled runtime "
    "quote, never a source literal",
    "remote_validation=NOT_RUN and grants_release_credit=false in every delivered shape; "
    "nothing in this wave grants release credit",
]

NON_GOALS = [
    "this package does not execute the owned BCS runtime, the story child, the "
    "Provider/Judge mock child, any CLI, any transport, any socket or any subprocess",
    "this package does not register the wave tokens, the row notes or the coverage overlay; "
    "the owner session applies them",
    "this package does not rebase the two committed face rows, the 41 executor-migration "
    "rows, or the coverage-map committed totals",
    "the two density calibers (site caliber 67/47 and charged-row caliber 42/41) are "
    "disclosed side by side and never merged",
    "transport checkpoints are disclosed as parent-internal checkpoints only and never "
    "charge as density rows",
]

USER_ACTIONS = [
    "owner session appends FIXED_ROW_NOTE_PROPOSAL and NESTED_ROW_NOTE_PROPOSAL to "
    "EXECUTOR_MIGRATION_NOTES and adds one counter entry each to "
    "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS (35 to 37 closure)",
    "owner session rebases coverage-map rows no 23/24 to the proposed after overlay "
    "(obligations 42/41, snapshot_parsed_count 42/41, queue gap_qty and score 42/41) "
    "while the committed totals stay unchanged",
    "owner session keeps executor-migration rows #18/#19 faces byte-identical (DUAL "
    "bookkeeping unchanged; each face notes list grows 0 to 1)",
    "owner session lands the two row tokens riding the family token in the shared faces "
    "exactly as proposed",
    "owner session re-runs the focused unittest and the case guard before committing",
]

DROP_DEAD_CHECKS = [
    "registration_proposal() fail-closes on its own anchors: assert_anchors re-reads both "
    "executor faces, both coverage rows (json and md), both manifests, both migration "
    "contracts, the dual command registrations and the note-counter closure before any "
    "increment text is produced",
    "the local replay receipt re-derives the 85-row roster from SHA-frozen disk bytes on "
    "every call; any story edit fails closed",
    "contract.obligations() compares every one of the 85 anchored quotes against disk "
    "bytes; drifted bytes drift-close",
    "the replay preprod tier stays capped at BLOCKED_INVENTORIED even under authorized "
    "injected observation sets",
    "named registration scopes are recomputed from the charged roster rows "
    "(83/83/42/41), never hardcoded into the receipts",
]


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunStateError(f"fixed-loop density proposal cannot read {label}: {error}") from error


def _coverage_row(number: int) -> dict[str, Any]:
    data = _load_json(COVERAGE_MAP_JSON, "coverage-map json")
    for row in data["rows"]:
        if row.get("no") == number:
            return row
    raise RunStateError(f"coverage-map row no {number} disappeared")


def _md_row(number: int) -> str:
    text = COVERAGE_MAP_MD.read_text(encoding="utf-8")
    needle = f"| {number} |"
    for line in text.splitlines():
        if line.startswith(needle):
            return line
    raise RunStateError(f"coverage-map md row no {number} disappeared")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RunStateError(message)


def assert_anchors() -> dict[str, Any]:
    """Read every committed face live; any drift fails closed with a name."""
    rows = obligations()
    _require(len(rows) == FAMILY_ROSTER_ROW_COUNT, "density roster caliber changed")
    _require(sum(1 for row in rows if row.charged) == FAMILY_CHARGED_ROW_COUNT,
             "density charged caliber changed")

    statuses = load_executor_migration()
    _require(len(statuses) == 41, "executor-migration row count changed")
    for story, row_index in ((STORY_FIXED, FIXED_EXECUTOR_ROW_INDEX),
                             (STORY_NESTED, NESTED_EXECUTOR_ROW_INDEX)):
        face = statuses.get(story)
        _require(face == dict(EXPECTED_ROW_FACE),
                 f"executor-migration row #{row_index} face changed: {face}")
    if any(token in note for token in (FAMILY_TOKEN, FIXED_ROW_TOKEN, NESTED_ROW_TOKEN)
           for note in EXECUTOR_MIGRATION_NOTES):
        raise RunStateError("fixed-loop density wave token is already registered")
    for proposal_note in (FIXED_ROW_NOTE_PROPOSAL, NESTED_ROW_NOTE_PROPOSAL):
        if proposal_note in EXECUTOR_MIGRATION_NOTES:
            raise RunStateError("fixed-loop density row note is already registered")
    note_total = sum(Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS).values())
    _require(len(EXECUTOR_MIGRATION_NOTES) == note_total,
             "inventory executor-migration note counter closure broke")
    _require(note_total >= 35,
             "inventory executor-migration note total regressed below the E2a plan snapshot")

    fixed_row = _coverage_row(FIXED_COVERAGE_ROW_NO)
    nested_row = _coverage_row(NESTED_COVERAGE_ROW_NO)
    _require(fixed_row["case_id"] == STORY_FIXED and nested_row["case_id"] == STORY_NESTED,
             "coverage-map row case ids drifted")
    for row, obligations_before, gap in ((fixed_row, FIXED_FACE_OBLIGATIONS, FIXED_FACE_GAP),
                                         (nested_row, NESTED_FACE_OBLIGATIONS, NESTED_FACE_GAP)):
        _require(row["upstream"]["snapshot_parsed_count"] == 1,
                 f"coverage-map row no {row['no']} parsed snapshot changed")
        _require(row["local_projection"]["obligations"] == obligations_before,
                 f"coverage-map row no {row['no']} obligations changed")
        _require(row["gap"]["parsed_minus_obligations"] == gap,
                 f"coverage-map row no {row['no']} gap arithmetic changed")
        _require(row["dual"]["status"] == "LOCAL_ONLY",
                 f"coverage-map row no {row['no']} dual status changed")
        _require(row["dual"]["executor_migration_mode"] == "DYNAMIC_OWNED_LOCAL",
                 f"coverage-map row no {row['no']} executor migration mode changed")
        _require(row["preprod_latest"]["verdict"] == STATUS_NOT_RUN,
                 f"coverage-map row no {row['no']} preprod verdict changed")
    _require("预发不应按 dual 计" in fixed_row["preprod_latest"]["detail"],
             "coverage-map row no 23 preprod dual-count clause disappeared")
    fixed_md = _md_row(FIXED_COVERAGE_ROW_NO)
    nested_md = _md_row(NESTED_COVERAGE_ROW_NO)
    _require(STORY_FIXED in fixed_md and "obl 8" in fixed_md
             and "（parsed 1）" in fixed_md,
             "coverage-map md row 23 drifted")
    _require(STORY_NESTED in nested_md and "obl 16" in nested_md,
             "coverage-map md row 24 drifted")

    for path in (MANIFEST_JSON, RETAINED_MANIFEST_JSON):
        manifest = _load_json(path, path.name)
        _require(manifest.get("baseline_commit") == SOURCE_REVISION,
                 f"{path.name} baseline commit drifted")

    nested_contract = _load_json(NESTED_CONTRACT_JSON_PATH, "nested migration contract")
    _require(nested_contract["environment"] == NESTED_CONTRACT_ENVIRONMENT,
             "nested migration contract environment posture changed")
    _require(nested_contract["remote_validation"] == STATUS_NOT_RUN
             and nested_contract["grants_release_credit"] is False,
             "nested migration contract honesty posture changed")
    _require(nested_contract.get("source_file_revisions") == NESTED_STALE_SOURCE_REVISIONS,
             "nested migration contract stale revision block drifted")
    _require(nested_contract["source_revision"] == SOURCE_REVISION,
             "nested migration contract source revision drifted")
    _require(len(nested_contract["requirements"]) == NESTED_PARENT_COUNT,
             "nested migration contract requirement count changed")
    _require(tuple(nested_contract["required_story_arguments"])
             == NESTED_REQUIRED_STORY_ARGUMENTS,
             "nested migration contract required story arguments changed")
    fixed_contract = _load_json(FIXED_CONTRACT_JSON_PATH, "fixed migration contract")
    _require(fixed_contract["source_revision"] == SOURCE_REVISION,
             "fixed migration contract source revision drifted")
    _require(len(fixed_contract["requirements"]) == FIXED_PARENT_COUNT,
             "fixed migration contract requirement count changed")
    _require(fixed_contract["sources"] == {
        "fixed_loop_story.sh": FIXED_SH_SHA256, "fixed_loop_story.py": FIXED_PY_SHA256,
        "common.sh": COMMON_SH_SHA256}, "fixed migration contract sources block drifted")
    sandbox = next((row for row in fixed_contract["requirements"]
                    if row["id"] == FIXED_SANDBOX_REQUIREMENT_ID), None)
    _require(sandbox is not None and sandbox["path"] == FIXED_SANDBOX_REQUIREMENT_PATH
             and sandbox["kind"] == "fixture" and sandbox["method"] == "NONE"
             and sandbox["actor"] == "LOCAL_CONTROL",
             "fixed migration contract sandbox-isolation requirement drifted")

    dynamic_text = DYNAMIC_FAMILY_CONTRACT_PY.read_text(encoding="utf-8")
    _require(FIXED_DUAL_COMMAND in dynamic_text and NESTED_DUAL_COMMAND in dynamic_text,
             "dynamic family contract lost a dual command registration")
    cli_text = CLI_PY.read_text(encoding="utf-8")
    _require(f'args.command == "{FIXED_DUAL_COMMAND}"' in cli_text
             and f'args.command == "{NESTED_DUAL_COMMAND}"' in cli_text,
             "cli.py dispatch lost a dual dynamic-family command")
    registration_text = CASE_COMMAND_REGISTRATION_PY.read_text(encoding="utf-8")
    _require(f'"{FIXED_DUAL_COMMAND}"' in registration_text
             and f'"{NESTED_DUAL_COMMAND}"' in registration_text,
             "case_command_registration.py lost a dual dynamic-family command")

    return {
        "executor_rows": {"row_count": len(statuses),
                          "faces": {STORY_FIXED: dict(statuses[STORY_FIXED]),
                                    STORY_NESTED: dict(statuses[STORY_NESTED])}},
        "executor_migration_note_total": note_total,
        "coverage_map": {"json": COVERAGE_MAP_JSON.name, "md": COVERAGE_MAP_MD.name,
                         "row_23": fixed_row, "row_24": nested_row,
                         "md_row_23": fixed_md, "md_row_24": nested_md},
        "committed_totals": {"obligations_committed_sum":
                              _load_json(COVERAGE_MAP_JSON, "coverage-map json")
                              ["totals"]["obligations_committed_sum"],
                             "upstream_parsed_known_sum":
                              _load_json(COVERAGE_MAP_JSON, "coverage-map json")
                              ["totals"]["upstream_parsed_known_sum"]},
        "inventory_state_counts": {f"{mode}/{verdict}": count
                                   for (mode, verdict), count
                                   in _EXPECTED_EXECUTOR_MIGRATION_COUNTS.items()},
        "inventory_gate_counts": dict(_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS),
        "baseline_commits": {path.name: _load_json(path, path.name)["baseline_commit"]
                             for path in (MANIFEST_JSON, RETAINED_MANIFEST_JSON)},
        "nested_contract_environment": dict(NESTED_CONTRACT_ENVIRONMENT),
    }


def _replay_receipts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Project the frozen completion gates offline; cap honesty fail-closes."""
    local_receipt = project(mock_observations())
    _require(local_receipt["status"] == STATUS_PASS, "local replay projection is not green")
    _require(local_receipt["counts"] == {STATUS_PASS: FAMILY_ROSTER_ROW_COUNT,
                                         STATUS_FAIL: 0, STATUS_NOT_RUN: 0},
             "local replay caliber changed")
    _require(local_receipt["charged_row_count"] == FAMILY_CHARGED_ROW_COUNT,
             "local replay charged caliber changed")
    preprod_receipt = project(tier="preprod")
    _require(preprod_receipt["status"] == STATUS_BLOCKED_INVENTORIED,
             "preprod replay verdict lost its honest cap")
    _require(preprod_receipt["counts"] == {STATUS_PASS: 0, STATUS_FAIL: 0,
                                           STATUS_NOT_RUN: FAMILY_ROSTER_ROW_COUNT},
             "preprod replay tier is not fully NOT_RUN")
    _require(preprod_receipt["remote_validation"] == STATUS_NOT_RUN
             and preprod_receipt["grants_release_credit"] is False,
             "preprod replay honesty posture changed")
    return local_receipt, preprod_receipt


def _named_registration_scopes(rows: tuple) -> list[dict[str, Any]]:
    applies_to: dict[str, int] = {registration_id: 0 for registration_id in NAMED_REGISTRATIONS}
    for row in rows:
        if row.charged:
            for registration_id in row.named_registrations:
                applies_to[registration_id] += 1
    expected = {FACE_UNDERCOUNT_ID: FAMILY_CHARGED_ROW_COUNT,
                DUAL_FACE_DIVERGENCE_ID: FAMILY_CHARGED_ROW_COUNT,
                FIXED_PREPROD_GAP_ID: FIXED_CHARGED_ROW_COUNT,
                NESTED_PREPROD_GAP_ID: NESTED_CHARGED_ROW_COUNT}
    for registration_id, scope in expected.items():
        _require(applies_to[registration_id] == scope,
                 f"named registration scope broke: {registration_id}")
    return [{**NAMED_REGISTRATIONS[registration_id],
             "applies_to_charged_rows": applies_to[registration_id]}
            for registration_id in (FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID,
                                    FIXED_PREPROD_GAP_ID, NESTED_PREPROD_GAP_ID)]


def _coverage_gap_detail(story_token: str, family_token: str, charged: int, leaves: int,
                          transport: int, parsed: int, dual_clause: bool) -> str:
    detail = (
        f"parsed {charged} − 义务 {charged} = 0（密度闭合 {story_token}，族令牌 "
        f"{family_token}；站点口径 {parsed} = {leaves} 叶 + {transport} 交通检查点单独披露，"
        f"双口径永不合并）；执行缺口 {charged}；预发腿诚实 NOT_RUN（动态 owned-local "
        f"runtime 设计如此，预发永不按 dual 计；双口径站点/义务行并列披露）")
    if dual_clause:
        detail += "；committed 行 23 detail 原含「预发不应按 dual 计」条款"
    else:
        detail += "；committed 行 24 detail 无双计条款，不对称在 disclosed_face_observations 披露"
    return detail


def _coverage_queue_basis(story_token: str, family_token: str, charged: int, parents: int,
                          leaves: int, transport: int, parsed: int) -> str:
    return (
        f"E2a 密度闭合 {story_token} / {family_token}：obl {charged}×1 = {parents} 迁移合同"
        f"父信封原样复用 + {leaves} 源锚叶（1 shell 门面叶 + {leaves - 1} python assert 原子）；"
        f"站点口径 {parsed} = {leaves} 叶 + {transport} 交通检查点单独披露（双口径永不合并）；"
        f"identity {charged} = {parents} + {leaves}；owned-local 族回放 {charged}/{charged} "
        f"有序恒等；LOCAL_ONLY 车道")


def _md_row_after(md_before: str, charged: int, note: str) -> str:
    cells = md_before.split("|")
    _require(len(cells) >= 8, "coverage-map md row lost its column shape")
    _require("（parsed 1）" in cells[3], "coverage-map md parsed cell drifted")
    _require("叶（obl " in cells[4],
             "coverage-map md leaf cell drifted")
    cells[3] = f" 0绿/1（parsed {charged}）"
    cells[4] = f" NOT_RUN 0/{charged} 叶（obl {charged}）"
    cells[-2] = f" {note} "
    return "|".join(cells)


def _coverage_overlay(story: str, charged: int, leaves: int, parents: int, transport: int,
                      parsed: int, assert_sites: int, story_token: str,
                      live_row: dict[str, Any], live_md: str, note: str,
                      dual_clause: bool) -> dict[str, Any]:
    before_queue = live_row["queue"]
    gap_detail = _coverage_gap_detail(story_token, FAMILY_TOKEN, charged, leaves, transport,
                                      parsed, dual_clause)
    queue_basis = _coverage_queue_basis(story_token, FAMILY_TOKEN, charged, parents, leaves,
                                        transport, parsed)
    after = {
        "obligations": charged,
        "snapshot_parsed_count": charged,
        "gap_kind": "COUNT_GAP_CLOSED+EXECUTION_GAP",
        "gap_parsed_minus_obligations": RESIDUAL_COUNT_GAP,
        "gap_detail": gap_detail,
        "queue": {"rank": before_queue["rank"], "gap_qty": charged,
                  "feasibility": before_queue["feasibility"], "score": charged,
                  "basis": queue_basis},
    }
    return {
        "no": live_row["no"], "case_id": story,
        "before": {"obligations": live_row["local_projection"]["obligations"],
                   "snapshot_parsed_count": live_row["upstream"]["snapshot_parsed_count"],
                   "gap_parsed_minus_obligations": live_row["gap"]["parsed_minus_obligations"],
                   "gap_kind": live_row["gap"]["kind"],
                   "queue": dict(before_queue)},
        "after": after,
        "md_row_before": live_md,
        "md_row_after": _md_row_after(live_md, charged, note),
        "committed_overlay_fields_unchanged": [
            "upstream.upstream_file", "upstream.roster_verdict", "upstream.parsed_green",
            "upstream.parsed_total", "local_projection.obligations_basis", "dual",
            "preprod_latest.verdict", "materials", "pending_landing", "source_contracts"],
        "site_caliber_disclosure": (
            f"sites {parsed} = {leaves} leaves + {transport} parent-internal transport "
            f"checkpoints + 1 shell facade; assert sites {assert_sites}; the site caliber and "
            f"the charged-row caliber are disclosed side by side and never merged"),
    }


def _wrapper_facade_block() -> dict[str, Any]:
    fixed_envelope = wrapper_gate_envelope(FAMILY_FIXED)
    nested_envelope = wrapper_gate_envelope(FAMILY_NESTED)
    return {
        "face_gate_ids": {STORY_FIXED: GATE_ID_FIXED, STORY_NESTED: GATE_ID_NESTED},
        "charged_facade_leaves": [FIXED_WRAPPER_LEAF, NESTED_WRAPPER_LEAF],
        "families": {STORY_FIXED: fixed_envelope, STORY_NESTED: nested_envelope},
        "face_undercount": {
            STORY_FIXED: {"face_parsed": FIXED_FACE_PARSED,
                          "face_obligations": FIXED_FACE_OBLIGATIONS,
                          "face_gap": FIXED_FACE_GAP},
            STORY_NESTED: {"face_parsed": NESTED_FACE_PARSED,
                           "face_obligations": NESTED_FACE_OBLIGATIONS,
                           "face_gap": NESTED_FACE_GAP}},
        "disclosure": (
            "the face parsed snapshot (1 per story) counts the shell facade alone, which is "
            "the registered FACE_UNDERCOUNT_ID undercount; the facade leaf charges once as "
            "FLOOP.F01 / NFLOOP.F01 under the disclosed face-derived auxiliary gate "
            "FIXED_LOOP.story-gate / NESTED-LOOP.story-gate (envelope_source "
            "face_wrapper_gate, uncharged, excluded from the 24 charged contract parents); "
            "the gate mirrors the shell facade exit-0 assertion and never normalizes the "
            "face mode to a preprod dual leg"),
    }


def _source_freeze_block() -> dict[str, Any]:
    fixed_envelope = wrapper_gate_envelope(FAMILY_FIXED)
    nested_envelope = wrapper_gate_envelope(FAMILY_NESTED)
    return {
        "shell_wrapper": {"file": FIXED_SH_FILE, "sha256": FIXED_SH_SHA256,
                          "story_spans": {STORY_FIXED: list(FIXED_SH_STORY_SPAN),
                                          STORY_NESTED: list(NESTED_SH_STORY_SPAN)},
                          "facade_assert_lines": {STORY_FIXED: FIXED_SH_ASSERT_LINE,
                                                  STORY_NESTED: NESTED_SH_ASSERT_LINE},
                          "facade_assert_messages": {
                              STORY_FIXED: fixed_envelope["facade_assert_message"],
                              STORY_NESTED: nested_envelope["facade_assert_message"]}},
        "python_stories": {
            FAMILY_FIXED: {"file": FIXED_PY_FILE, "sha256": FIXED_PY_SHA256},
            FAMILY_NESTED: {"file": NESTED_PY_FILE, "sha256": NESTED_PY_SHA256}},
        "migration_contracts": {
            FAMILY_FIXED: {"file": FIXED_CONTRACT_FILE,
                           "sha256": FIXED_CONTRACT_JSON_SHA256},
            FAMILY_NESTED: {"file": NESTED_CONTRACT_FILE,
                            "sha256": NESTED_CONTRACT_JSON_SHA256}},
        "source_revision": SOURCE_REVISION,
        "supporting_freezes": {"common.sh": COMMON_SH_SHA256,
                               "http_provider_judge_mock.py": PROVIDER_JUDGE_MOCK_SHA256},
        "ledger_parity": {
            FAMILY_FIXED: {"ledger_sites": FIXED_LEDGER_SITE_COUNT,
                           "assert_sites": FIXED_ASSERT_SITE_COUNT,
                           "transport_sites": FIXED_TRANSPORT_SITE_COUNT},
            FAMILY_NESTED: {"ledger_sites": NESTED_LEDGER_SITE_COUNT,
                            "assert_sites": NESTED_ASSERT_SITE_COUNT,
                            "transport_sites": NESTED_TRANSPORT_SITE_COUNT}},
        "credential_reference": DRIVER_TOKEN_ENV,
        "credential_reference_basis": (
            "the shell wrapper sets the driver token through the environment variable name "
            "only; the python stories read it via the base Story; the provider downlink "
            "assert row keeps a disk-assembled runtime quote and no credential value is "
            "ever stored, quoted or resolved by this package"),
    }


def _envelope_reuse_block(family: str, plan: dict[str, Any]) -> dict[str, Any]:
    reuse = plan["envelope_reuse"]
    command = reuse["command"]
    dual = (command == FIXED_DUAL_COMMAND)
    return {
        "dual_command": command,
        "cli_dispatch": "bcs_e2e/cli.py",
        "cli_dispatch_needle": (FIXED_CLI_DISPATCH_NEEDLE if dual
                                else NESTED_CLI_DISPATCH_NEEDLE),
        "command_registration": "bcs_e2e/case_command_registration.py",
        "registration_needle": (FIXED_COMMAND_REGISTRATION_NEEDLE if dual
                                else NESTED_COMMAND_REGISTRATION_NEEDLE),
        "edits": [],
        "statement": (
            "the charged leaves ride the already-registered dual dynamic-family command "
            + command + "; cli.py dispatch and case_command_registration.py stay "
            "byte-identical this wave; no new parser, no new subcommand, no new envelope"),
    }


def registration_proposal() -> dict[str, Any]:
    """Return the DESCRIBE_ONLY integration proposal; nothing is executed or written."""
    snapshot = assert_anchors()
    rows = obligations()
    fixed_rows = [row for row in rows if row.story == STORY_FIXED]
    nested_rows = [row for row in rows if row.story == STORY_NESTED]
    local_receipt, preprod_receipt = _replay_receipts()

    fixed_plan = fixed_loop_density_plan()
    nested_plan = nested_fixed_loop_density_plan()
    _require(fixed_plan["site_free_parents"] == [],
             "fixed family site-free parent caliber changed")
    _require(len(nested_plan["site_free_parents"]) == NESTED_SITE_FREE_PARENT_COUNT,
             "nested family site-free parent caliber changed")
    fixed_binding, nested_binding = (
        local_receipt["families"][FAMILY_FIXED]["executor_binding"],
        local_receipt["families"][FAMILY_NESTED]["executor_binding"])
    _require(fixed_binding["adapter_seams"] == list(FIXED_LOOP_ADAPTER_METHOD_NAMES),
             "fixed adapter seam registry drifted from the replay receipt")
    _require(nested_binding["black_box_child_keys"] == list(BLACK_BOX_CHILD_KEYS),
             "nested black-box child key registry drifted from the replay receipt")
    receipt_named = {entry["id"]: entry["applies_to_charged_rows"]
                     for entry in local_receipt["named_registrations"]}
    _require(receipt_named == {FACE_UNDERCOUNT_ID: FAMILY_CHARGED_ROW_COUNT,
                               DUAL_FACE_DIVERGENCE_ID: FAMILY_CHARGED_ROW_COUNT,
                               FIXED_PREPROD_GAP_ID: FIXED_CHARGED_ROW_COUNT,
                               NESTED_PREPROD_GAP_ID: NESTED_CHARGED_ROW_COUNT},
             "replay named registration scope drifted from the roster")

    coverage = snapshot["coverage_map"]
    fixed_overlay = _coverage_overlay(
        STORY_FIXED, FIXED_CHARGED_ROW_COUNT, FIXED_LEAF_COUNT, FIXED_PARENT_COUNT,
        FIXED_TRANSPORT_SITE_COUNT, FIXED_SITE_COUNT, FIXED_ASSERT_SITE_COUNT,
        FIXED_ROW_TOKEN, coverage["row_23"], coverage["md_row_23"],
        FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL, True)
    nested_overlay = _coverage_overlay(
        STORY_NESTED, NESTED_CHARGED_ROW_COUNT, NESTED_LEAF_COUNT, NESTED_PARENT_COUNT,
        NESTED_TRANSPORT_SITE_COUNT, NESTED_SITE_COUNT, NESTED_ASSERT_SITE_COUNT,
        NESTED_ROW_TOKEN, coverage["row_24"], coverage["md_row_24"],
        NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL, False)

    families = {
        FAMILY_FIXED: {
            "executor_row_index": FIXED_EXECUTOR_ROW_INDEX,
            "coverage_row_no": FIXED_COVERAGE_ROW_NO,
            "story": STORY_FIXED,
            "upstream_issue": UPSTREAM_ISSUES[STORY_FIXED],
            "roster_rows": FIXED_ROSTER_ROW_COUNT,
            "charged_rows": FIXED_CHARGED_ROW_COUNT,
            "leaves": FIXED_LEAF_COUNT,
            "contract_parents": FIXED_PARENT_COUNT,
            "parsed_sites": FIXED_SITE_COUNT,
            "ledger_sites": FIXED_LEDGER_SITE_COUNT,
            "transport_checkpoints": FIXED_TRANSPORT_SITE_COUNT,
            "assert_sites": FIXED_ASSERT_SITE_COUNT,
            "wrapper_facade_sites": 1,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "face": fixed_plan["face_delta"],
            "recalibrated": {"charged_rows": FIXED_CHARGED_ROW_COUNT,
                             "snapshot_parsed_count": FIXED_CHARGED_ROW_COUNT,
                             "gap": RESIDUAL_COUNT_GAP},
            "row_note": FIXED_ROW_NOTE_PROPOSAL,
            "row_token": FIXED_ROW_TOKEN,
            "preprod_named_gap_id": FIXED_PREPROD_GAP_ID,
            "wrapper_facade": fixed_plan["wrapper_facade"],
            "adapter_seams": list(FIXED_LOOP_ADAPTER_METHOD_NAMES),
            "envelope_reuse": _envelope_reuse_block(FAMILY_FIXED, fixed_plan),
            "executor_binding": fixed_binding,
            "named_registrations": fixed_plan["named_registrations"],
            "internal_checkpoint_catalog": fixed_plan["internal_checkpoint_catalog"],
            "transport_checkpoint_union": fixed_plan["transport_checkpoint_union"],
            "site_free_parents": fixed_plan["site_free_parents"],
            "inherited_base_anchors": fixed_plan["inherited_base_anchors"],
            "conditional_leaves": dict(FIXED_CONDITIONAL_LEAVES),
            "arm_leg_rules": {str(line): list(legs)
                              for line, legs in FIXED_ARM_LEG_RULES.items()},
            "default_legs": list(FIXED_DEFAULT_LEGS),
            "density_plan": fixed_plan,
            "coverage_map": {
                "targets": [COVERAGE_MAP_JSON.name, COVERAGE_MAP_MD.name],
                "integration_mode": "owner session applies; proposal text only",
                "committed_totals": dict(snapshot["committed_totals"]),
                "overlay": fixed_overlay},
            "replay_receipt_counts": {
                "local": local_receipt["families"][FAMILY_FIXED]["counts"],
                "preprod": preprod_receipt["families"][FAMILY_FIXED]["counts"]},
        },
        FAMILY_NESTED: {
            "executor_row_index": NESTED_EXECUTOR_ROW_INDEX,
            "coverage_row_no": NESTED_COVERAGE_ROW_NO,
            "story": STORY_NESTED,
            "upstream_issue": UPSTREAM_ISSUES[STORY_NESTED],
            "roster_rows": NESTED_ROSTER_ROW_COUNT,
            "charged_rows": NESTED_CHARGED_ROW_COUNT,
            "leaves": NESTED_LEAF_COUNT,
            "contract_parents": NESTED_PARENT_COUNT,
            "parsed_sites": NESTED_SITE_COUNT,
            "ledger_sites": NESTED_LEDGER_SITE_COUNT,
            "transport_checkpoints": NESTED_TRANSPORT_SITE_COUNT,
            "assert_sites": NESTED_ASSERT_SITE_COUNT,
            "wrapper_facade_sites": 1,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "face": nested_plan["face_delta"],
            "recalibrated": {"charged_rows": NESTED_CHARGED_ROW_COUNT,
                             "snapshot_parsed_count": NESTED_CHARGED_ROW_COUNT,
                             "gap": RESIDUAL_COUNT_GAP},
            "row_note": NESTED_ROW_NOTE_PROPOSAL,
            "row_token": NESTED_ROW_TOKEN,
            "preprod_named_gap_id": NESTED_PREPROD_GAP_ID,
            "wrapper_facade": nested_plan["wrapper_facade"],
            "black_box_child_keys": list(BLACK_BOX_CHILD_KEYS),
            "envelope_reuse": _envelope_reuse_block(FAMILY_NESTED, nested_plan),
            "executor_binding": nested_binding,
            "named_registrations": nested_plan["named_registrations"],
            "internal_checkpoint_catalog": nested_plan["internal_checkpoint_catalog"],
            "transport_checkpoint_union": nested_plan["transport_checkpoint_union"],
            "site_free_parents": nested_plan["site_free_parents"],
            "inherited_base_anchors": nested_plan["inherited_base_anchors"],
            "conditional_leaves": dict(NESTED_CONDITIONAL_LEAVES),
            "marker_legs": list(NESTED_MARKER_LEGS),
            "density_plan": nested_plan,
            "coverage_map": {
                "targets": [COVERAGE_MAP_JSON.name, COVERAGE_MAP_MD.name],
                "integration_mode": "owner session applies; proposal text only",
                "committed_totals": dict(snapshot["committed_totals"]),
                "overlay": nested_overlay},
            "replay_receipt_counts": {
                "local": local_receipt["families"][FAMILY_NESTED]["counts"],
                "preprod": preprod_receipt["families"][FAMILY_NESTED]["counts"]},
        },
    }

    return {
        "status": "DESCRIBE_ONLY",
        "applied": False,
        "new_registration_command": False,
        "shared_face_files_touched": [],
        "write_domain": ["bcs_e2e/fixed_loop_density/**",
                         "tests/test_fixed_loop_density.py"],
        "token": FAMILY_TOKEN,
        "wave": "E2a",
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
        "credential_references": [DRIVER_TOKEN_ENV],
        "families": families,
        "family_totals": {
            "roster_rows": FAMILY_ROSTER_ROW_COUNT,
            "charged_rows": FAMILY_CHARGED_ROW_COUNT,
            "leaves": FAMILY_LEAF_COUNT,
            "contract_parents": FAMILY_PARENT_COUNT,
            "auxiliary_gate_envelopes": 2,
            "wrapper_facade_leaves": 2,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_sites": FAMILY_SITE_COUNT,
            "executor_ledger_sites": FAMILY_LEDGER_SITE_COUNT,
            "shell_facade_sites": 2,
            "transport_checkpoints": FIXED_TRANSPORT_SITE_COUNT + NESTED_TRANSPORT_SITE_COUNT,
            "assert_sites": FIXED_ASSERT_SITE_COUNT + NESTED_ASSERT_SITE_COUNT,
            "charged_identity": (
                f"{FAMILY_CHARGED_ROW_COUNT} charged rows = {FAMILY_PARENT_COUNT} contract "
                f"parents + {FAMILY_LEAF_COUNT} leaves"),
            "site_identity": (
                f"{FAMILY_SITE_COUNT} parsed sites = {FAMILY_LEAF_COUNT} leaves + "
                f"{FIXED_TRANSPORT_SITE_COUNT + NESTED_TRANSPORT_SITE_COUNT} checkpoints + "
                f"2 shell facades; ledger {FAMILY_LEDGER_SITE_COUNT} python sites + 2 facades"),
            "uncharged_auxiliary_gates": 2,
        },
        "inventory_increment": {
            "target_file": "bcs_e2e/inventory.py",
            "integration_mode": "owner session applies; this package writes nothing",
            "operation": "fail-close membership and counter closure",
            "adds_to_EXECUTOR_MIGRATION_NOTES": [FIXED_ROW_NOTE_PROPOSAL,
                                                 NESTED_ROW_NOTE_PROPOSAL],
            "adds_to_expected_note_counts": {FIXED_ROW_NOTE_PROPOSAL: 1,
                                             NESTED_ROW_NOTE_PROPOSAL: 1},
            "note_counts_replacement": {
                "current_total_notes": snapshot["executor_migration_note_total"],
                "after_total_notes": snapshot["executor_migration_note_total"] + 2,
                "this_family_delta": 2},
            "unchanged": {
                "state_counts": dict(snapshot["inventory_state_counts"]),
                "gate_counts": dict(snapshot["inventory_gate_counts"]),
                "executor_rows": snapshot["executor_rows"]["row_count"],
                "row_faces": {STORY_FIXED: {
                                  "mode": EXPECTED_ROW_FACE["mode"],
                                  "preprod_verdict": EXPECTED_ROW_FACE["preprod_verdict"],
                                  "gates": [], "notes_delta": "+1 (this proposal note only)"},
                              STORY_NESTED: {
                                  "mode": EXPECTED_ROW_FACE["mode"],
                                  "preprod_verdict": EXPECTED_ROW_FACE["preprod_verdict"],
                                  "gates": [], "notes_delta": "+1 (this proposal note only)"}},
                "dual_posture": {
                    "mode": "DUAL", "preprod_verdict": "BLOCKED_INVENTORIED",
                    "statement": (
                        "the DUAL face mode is executor-migration bookkeeping only; the "
                        "density projection charges the owned-local lane and never counts "
                        "a dual preprod leg; the committed face rows stay byte-identical")}},
        },
        "coverage_map_increment": {
            "targets": ["coverage-map/coverage-map-20261031.json",
                        "coverage-map/coverage-map-20261031.md"],
            "integration_mode": "owner session applies; proposal text only",
            "rows": [fixed_overlay, nested_overlay],
            "committed_totals": {
                "obligations_committed_sum":
                    snapshot["committed_totals"]["obligations_committed_sum"],
                "upstream_parsed_known_sum":
                    snapshot["committed_totals"]["upstream_parsed_known_sum"],
                "disposition": (
                    "unchanged by this proposal; the owner landing commit rebases the two "
                    "row overlay fields only and the committed sums stay 674/638")},
        },
        "envelope_reuse": {
            FAMILY_FIXED: _envelope_reuse_block(FAMILY_FIXED, fixed_plan),
            FAMILY_NESTED: _envelope_reuse_block(FAMILY_NESTED, nested_plan),
            "statement": (
                "both dual commands stay byte-identical this wave; the density leaves ride "
                "the registered dual-fixed-loop-run / dual-nested-fixed-loop-run envelopes "
                "and their committed DUAL / BLOCKED_INVENTORIED verdicts and gates stay "
                "untouched"),
        },
        "wrapper_facade": _wrapper_facade_block(),
        "named_registrations": _named_registration_scopes(tuple(rows)),
        "preprod_by_design": PREPROD_BY_DESIGN,
        "replay_caliber": {
            "local": (
                f"{FAMILY_ROSTER_ROW_COUNT}/{FAMILY_ROSTER_ROW_COUNT} PASS (fixed "
                f"{FIXED_ROSTER_ROW_COUNT} roster rows / {FIXED_CHARGED_ROW_COUNT} charged; "
                f"nested {NESTED_ROSTER_ROW_COUNT} roster rows / {NESTED_CHARGED_ROW_COUNT} "
                f"charged) against the frozen completion gates mirrored from "
                f"local_fixed_loop_run.py LOCAL_FIXED_LOOP_PASS and "
                f"local_nested_fixed_loop_run.py LOCAL_NESTED_FIXED_LOOP_PASS"),
            "preprod": (
                f"{FAMILY_ROSTER_ROW_COUNT}/{FAMILY_ROSTER_ROW_COUNT} NOT_RUN with the "
                f"overall verdict capped at BLOCKED_INVENTORIED and the four named "
                f"registrations; an authorized injected observation set still projects "
                f"per-row PASS but can never convert the receipt into preprod PASS credit"),
        },
        "upstream_issues": dict(UPSTREAM_ISSUES),
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "disclosed_face_observations": list(DISCLOSED_FACE_OBSERVATIONS),
        "source_freeze": _source_freeze_block(),
        "reconciliation": family_reconciliation(),
        "red_lines": list(RED_LINES),
        "non_goals": list(NON_GOALS),
        "user_actions": list(USER_ACTIONS),
        "drop_dead_checks": list(DROP_DEAD_CHECKS),
        "live_anchor_snapshot": snapshot,
    }


def describe_only_mainline_fragment() -> str:
    """Return the text a mainline integrator may apply; this module writes nothing."""
    live_note_total = sum(Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS).values())
    return (
        f"{FAMILY_TOKEN}: {DENSITY_CONTRACT} registers the wave E2a fixed-Loop density "
        f"recalibration for rows #18/#19 ({STORY_FIXED} upstream {UPSTREAM_ISSUES[STORY_FIXED]}, "
        f"coverage-map row no {FIXED_COVERAGE_ROW_NO}; {STORY_NESTED} upstream "
        f"{UPSTREAM_ISSUES[STORY_NESTED]}, coverage-map row no {NESTED_COVERAGE_ROW_NO}); "
        f"{FAMILY_ROSTER_ROW_COUNT} roster rows = {FAMILY_CHARGED_ROW_COUNT} charged "
        f"({FAMILY_PARENT_COUNT} migration-contract parents + {FAMILY_LEAF_COUNT} leaves "
        f"{FIXED_WRAPPER_LEAF}/{NESTED_WRAPPER_LEAF} shell facades plus 57 python assert atoms) + 2 "
        f"uncharged face-derived auxiliary gates; site caliber {FAMILY_SITE_COUNT} = "
        f"{FAMILY_LEDGER_SITE_COUNT} executor-ledger python sites + 2 shell facades = "
        f"{FAMILY_LEAF_COUNT} leaves + {FIXED_TRANSPORT_SITE_COUNT + NESTED_TRANSPORT_SITE_COUNT} "
        f"parent-internal transport checkpoints, residual_count_gap=0 under both calibers; "
        f"face deltas parsed 1 to obligations 8/16 recalibrated to charged rows 42/41 with "
        f"gap 0 (FACE_UNDERCOUNT_ID recalibration disclosed, never silently rebased); "
        f"executor-migration.json rows #18/#19 stay byte-identical with notes-only "
        f"increments ({FIXED_ROW_TOKEN}, {NESTED_ROW_TOKEN}; inventory note total {live_note_total} to {live_note_total + 2}, "
        f"states and gates counters unchanged); bcs_e2e/inventory.py gains exactly the two "
        f"proposal notes as fail-close membership; coverage-map overlay rebases rows no "
        f"{FIXED_COVERAGE_ROW_NO}/{NESTED_COVERAGE_ROW_NO} obligations 8/16 to 42/41 and "
        f"snapshot_parsed_count 1 to 42/41 with queue gap_qty and score 8/16 to 42/41 while "
        f"committed totals stay 674/638; dual-fixed-loop-run / dual-nested-fixed-loop-run "
        f"envelopes, cli.py dispatch and case_command_registration.py stay byte-identical "
        f"(new registration command: none); local owned-offline credential-free replay "
        f"projects {FAMILY_ROSTER_ROW_COUNT}/{FAMILY_ROSTER_ROW_COUNT} PASS with the frozen "
        f"completion gates; preprod stays honest NOT_RUN with the overall verdict capped at "
        f"BLOCKED_INVENTORIED (REMOTE_UNSUPPORTED-by-design for the nested family, dynamic "
        f"owned-local runtime for both, the preprod leg is never counted as a dual leg) and "
        f"the four named registrations "
        f"{FACE_UNDERCOUNT_ID}, {DUAL_FACE_DIVERGENCE_ID}, {FIXED_PREPROD_GAP_ID}, "
        f"{NESTED_PREPROD_GAP_ID}; remote_validation=NOT_RUN; grants_release_credit=false; "
        f"write domain bcs_e2e/fixed_loop_density/** and tests/test_fixed_loop_density.py "
        f"only; describe-only, nothing applied")


__all__ = [
    "BLACK_BOX_CHILD_KEYS", "DISCLOSED_FACE_OBSERVATIONS", "DROP_DEAD_CHECKS",
    "EXPECTED_ROW_FACE", "FIXED_CLI_DISPATCH_NEEDLE", "FIXED_COMMAND_REGISTRATION_NEEDLE",
    "FIXED_COVERAGE_ROW_NO", "FIXED_DUAL_COMMAND", "FIXED_EXECUTOR_ROW_INDEX",
    "NESTED_CLI_DISPATCH_NEEDLE", "NESTED_COMMAND_REGISTRATION_NEEDLE",
    "NESTED_COVERAGE_ROW_NO", "NESTED_DUAL_COMMAND", "NESTED_EXECUTOR_ROW_INDEX",
    "NON_GOALS", "RED_LINES", "UPSTREAM_ISSUES", "USER_ACTIONS",
    "assert_anchors", "describe_only_mainline_fragment", "registration_proposal",
]

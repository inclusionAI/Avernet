"""Describe-only registration proposal for the Wave-D3 density overlay.

The overlay changes no shared face and applies nothing.  The five density
parents are already registered DUAL with preprod verdict PASS on their
committed executor-migration rows; the frozen families already carry the case
keys through dual-edge-friend-mutation-run and dual-edge-read-run.  What this
module proposes, without touching any shared face, is:

* the honest annotation delta on the five executor-migration rows (gates and
  notes are empty today), citing the recorded r5/r6/r7 receipts and the wpc
  owned-local round, while the committed verdict stays PASS;
* the gate vocabulary a true density closure still awaits;
* the inventory parsed-count far-side proposal marked pending-landing, with the
  committed accounting left untouched;
* the optional describe-only command delta for
  ``dual-edge-permission-density-run``;
* the material and user-action list the ENROLLMENT flip depends on.
"""
from __future__ import annotations

from typing import Any

from .density_contract import (
    DENSITY_CASE_KEYS,
    OPTIONAL_DENSITY_COMMAND,
    PARENT_BY_CASE_KEY,
    SUITE,
)
from .density_obligations import (
    ADDED_BY_PARENT,
    EXISTING_BY_PARENT,
    PARSED_BY_PARENT,
    PARENTS,
)
from .density_preprod_arm import (
    CITED_RECEIPT_SPEC,
    READ_R5_ROUND,
    READ_R7_RELATIVE,
    R7_PREPROD_ROUND,
)

PROPOSAL_STATUS = "DESCRIBE_ONLY"
APPLIED = False
SUITE_NAME = SUITE

# executor-migration.json row anchors (committed file, read-only reference)
ROW_LINE_BY_PARENT = {
    "test_ep_friend_connections_friend_request_and_list": 218,
    "test_ep_friend_connections_friend_accept_reject_cancel": 227,
    "test_ep_friend_connections_mutual_auto_approve": 272,
    "test_ep_error_and_admission_branches": 290,
    "test_ep_connect_private_and_discover_branches": 299,
}

_RECEIPT_CITATIONS = {
    "test_ep_error_and_admission_branches": (
        "r5 双腿收据 20261031-regression-r5-edge-mutation/"
        "error-admission-branches-stdout.json（status=PASS，remote_validation=NOT_RUN，"
        "grants_release_credit=false）；wpc 本地轮 "
        "20261031-migration-wpc-local-owned-edge-3 junit 行 "
        "LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01 PASS 1/1"),
    "test_ep_connect_private_and_discover_branches": (
        "r5 双腿收据 20261031-regression-r5-edge-mutation/"
        "connect-private-discover-stdout.json（status=PASS，remote_validation=NOT_RUN）；"
        "wpc 本地轮 junit 行 LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01 PASS 1/1；"
        "产品在案 PD-PRIVATE-CREATE-500（preprod 500 vs 契约 403）与 "
        "PD-DISCOVER-BRANCH-DRIFT（org 分支 403 vs 契约 400、role 包装 "
        "invalid_operation）在密度分类中如实披露"),
    "test_ep_friend_connections_friend_request_and_list": (
        "r5 读腿收据 20261031-regression-r5-edge-read/friend-connections-stdout.json "
        "与 r7 状态腿 20261031-regression-r7-preprod/edge-read/"
        "friend-connections-state/edge-friend-connections-run.json（公共 face "
        "PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01，contract "
        "dual-edge-read-case-run-v1，status=PASS，remote_validation=NOT_RUN）；"
        "本地读轮留存在本族为 LEG_ABSENT（读片走 dual-edge-read-run 族）；"
        "annex 叶跨族引证 r5 mutation 轮 error-admission 收据 create 行"
        "（friend_connection_mutation.create 6 行中 3 行 201 且 request id 回显）"),
    "test_ep_friend_connections_friend_accept_reject_cancel": (
        "r5 双腿收据 20261031-regression-r5-edge-mutation/"
        "accept-reject-cancel-stdout.json（status=PASS，remote_validation=NOT_RUN）；"
        "wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01 PASS 1/1"),
    "test_ep_friend_connections_mutual_auto_approve": (
        "r5 与 r6 双腿收据 20261031-regression-r5-edge-mutation/ 与 "
        "20261031-regression-r6-edge-mutation/ 下 mutual-auto-approve-stdout.json"
        "（双轮 status=PASS，remote_validation=NOT_RUN；r5/r6 peer bot 不同，"
        "口径 一致）；wpc 本地轮 junit 行 "
        "LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01 PASS 1/1；身份交叉核对 "
        "member=20261010_gekng5hk:39464 出现在双轮 admissions 方向键"),
}

_EXECUTOR_NOTES = {
    parent: (
        "Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen "
        + str(EXISTING_BY_PARENT[parent]) + " 义务 + "
        + str(ADDED_BY_PARENT[parent]) + " 密度叶 == parsed "
        + str(PARSED_BY_PARENT[parent]) + "（" + SUITE_NAME + "）；"
        + _RECEIPT_CITATIONS[parent] + "；22 叶族内 ENROLLMENT=NOT_ENROLLED "
        "fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS "
        "现状不变，笔记增量仅补水注记；⏳pending-landing"
    )
    for parent in PARENTS
}

PROPOSED_EXECUTOR_MIGRATION_NOTES = _EXECUTOR_NOTES


def registration_proposal() -> dict:
    """Return the describe-only proposal payload."""
    row_delta = {}
    for parent in PARENTS:
        row_delta[parent] = {
            "executor_migration": {
                "mode": "DUAL",
                "preprod_verdict": "PASS",
                "gates": [
                    {
                        "name": "dual_envelope_offline_closure",
                        "requires": (
                            "bcs_e2e.edge_permission_backlog 密度双信封离线闭合："
                            "owned-local 四柱 LOCAL_MODEL_READY（绑定/seam 存在/"
                            "seam literal/wpc 轮核验）+ 预发双腿收据重读 ATTACHED "
                            "且 envelope_verified；22 密度叶 ENROLLMENT=NOT_ENROLLED "
                            "fail-close"),
                        "status": "PROPOSED",
                    },
                    {
                        "name": "preprod_defect_disposition",
                        "requires": (
                            "产品在案核对 DUP-CREATE-ID-ECHO（行幂等成立/请求 id "
                            "回显分歧）、PD-PRIVATE-CREATE-500（preprod 500 vs 契约 "
                            "403）、PD-DISCOVER-BRANCH-DRIFT（org 分支 403 vs 400、"
                            "role 包装 invalid_operation）；授权预发重跑 "
                            "dual-edge-friend-mutation-run 与 dual-edge-read-run "
                            "后 remote_validation 由 NOT_RUN 翻 RUN，并按叶投影键 "
                            "补给收据（M-DENSITY-RECEIPT-LEAFKEYS）"),
                        "status": "PROPOSED",
                    },
                ],
                "notes": [PROPOSED_EXECUTOR_MIGRATION_NOTES[parent]],
            },
            "json_pointer_anchor": (
                "apps/bcs/scripts/e2e-runner/executor-migration.json rows[id=="
                + parent + "]（committed 快照行约 L"
                + str(ROW_LINE_BY_PARENT[parent]) + "，落地前复核行号）"),
        }
    inventory_proposal = []
    for parent in PARENTS:
        inventory_proposal.append({
            "parent_id": parent,
            "committed_local_obligation_count": EXISTING_BY_PARENT[parent],
            "parsed_snapshot_denominator": PARSED_BY_PARENT[parent],
            "density_overlay_leaves": ADDED_BY_PARENT[parent],
            "projected_superset": EXISTING_BY_PARENT[parent]
            + ADDED_BY_PARENT[parent],
            "landing_status": "pending-landing",
            "basis": (
                "coverage-map-20261031.md section 2 rows #32-#35/#39 与 "
                "assertion-gap-matrix-20261031.md A2 同口径快照；committed "
                "计数口径本提案不动"),
        })
    return {
        "proposal_status": PROPOSAL_STATUS,
        "applied": APPLIED,
        "suite": SUITE_NAME,
        "shared_face_files_touched": [],
        "finding": (
            "登记面已完整：5 个密度父在 committed executor-migration 行均为 "
            "mode=DUAL/preprod_verdict=PASS，case key 已由 "
            "dual-edge-friend-mutation-run（4 写父）与 dual-edge-read-run "
            "（读父，含公共别名）携带；执行面本波次不新增命令；唯一登记面滞后是 "
            "5 行 gates/notes 空注记与本 overlay 的 parsed 计数口径未登记"),
        "required_delta": {
            "executor_migration_rows": row_delta,
            "json_pointer_basis": (
                "apps/bcs/scripts/e2e-runner/executor-migration.json rows[id==<parent>]"
                ".executor_migration"),
            "disposition": "PROPOSED_ONLY",
        },
        "inventory_count_proposal": {
            "rows": inventory_proposal,
            "total_parsed_denominator": sum(PARSED_BY_PARENT.values()),
            "total_frozen_existing": sum(EXISTING_BY_PARENT.values()),
            "total_density_leaves": sum(ADDED_BY_PARENT.values()),
            "disposition": "pending-landing; committed coverage-map counts unchanged",
        },
        "optional_delta": {
            "family_command": OPTIONAL_DENSITY_COMMAND,
            "registration_points": [
                "bcs_e2e/case_command_registration.py：EDGE 家族块追加 parser"
                "（--profile/--case/--state-dir/--run-id/--confirm-run-id/"
                "--allow-write/--evidence-root，offline 选择守卫同 "
                "dual-edge-permission-backlog-run）",
                "bcs_e2e/cli.py：dispatch 追加同名命令",
            ],
            "disposition": (
                "OPTIONAL — describe-only；密度叶当前 NOT_ENROLLED，命令登记"
                "不带来任何预发执行或信用"),
        },
        "materials": [
            {
                "id": "M-DENSITY-RECEIPT-LEAFKEYS",
                "item": (
                    "runner 主线补给 dual 收据的每叶投影键：visibility writes、"
                    "pre-decision 方向 listing、mutual pair、per-direction friend "
                    "listings、all-direction decided-row union、public discovery "
                    "per-entry 形（bot_uuid/visibility/is_friend）；读片本地轮留"
                    "存缺口（LEG_ABSENT）按 dual-edge-read-run 族口径 narration"),
                "owner": "runner mainline",
                "blocks": "per-leaf ENROLLMENT 分类信用",
            },
            {
                "id": "M-PREREQ",
                "item": (
                    "授权预发重跑 dual-edge-friend-mutation-run（4 写父）与 "
                    "dual-edge-read-run（读父）后密度叶才从 NOT_ENROLLED 翻 "
                    "ENROLLED；凭证种类 human_cookie+registered_bot_token_presence，"
                    "仅引用环境变量名 BCS_E2E_HUMAN_COOKIE 与 "
                    "BCS_E2E_STATION_BOT_TOKEN，不落任何值；cookie 刷新需用户"
                    "授权（coverage-map M4 口径）"),
                "owner": "user authorization",
                "blocks": "preprod ENROLLMENT flip",
            },
        ],
        "user_actions": [
            "复核本提案的 5 行 executor-migration notes/gates 增量并决定是否落地",
            "产品在案缺陷核对（DUP-CREATE-ID-ECHO / PD-PRIVATE-CREATE-500 / "
            "PD-DISCOVER-BRANCH-DRIFT）",
            "授权并执行预发双腿重跑，收据 remote_validation 由 NOT_RUN 翻 RUN，"
            "密度叶按叶投影键分类后翻 ENROLLED",
            "上游 roster 密度口径（parsed 分母分解规则）与 coverage-map "
            "committed 计数解耦核对",
        ],
        "non_goals": [
            "本提案不修改任何 shared face（case_command_registration.py/cli.py/"
            "inventory.py/executor-migration.json/coverage-map 均未动）",
            "本提案不触发任何本地或预发 transport",
            "本提案不把本地模型投影或记录收据行提升为预发验证或发布信用",
            "本提案不改变 5 行 committed verdict（维持 PASS 现状）",
        ],
        "case_scope": {
            PARENT_BY_CASE_KEY[case_key]: {
                "case_key": case_key,
                "case_id": _case_id_of(case_key),
                "frozen_requirement_ids": _frozen_reqs(case_key),
                "density_leaves": ADDED_BY_PARENT[PARENT_BY_CASE_KEY[case_key]],
                "enrollment": "NOT_ENROLLED",
            }
            for case_key in DENSITY_CASE_KEYS
        },
        "cited_receipts": {
            "rounds": [
                "20261031-regression-r5-edge-mutation",
                "20261031-regression-r6-edge-mutation",
                "20261031-regression-r5-edge-read",
                R7_PREPROD_ROUND,
                "20261031-migration-wpc-local/owned-local",
            ],
            "cross_family_citation": {
                "round": CITED_RECEIPT_SPEC[0],
                "file": CITED_RECEIPT_SPEC[1],
                "read_round": READ_R5_ROUND,
                "r7_relative": READ_R7_RELATIVE,
            },
        },
    }


def _case_id_of(case_key: str) -> str:
    from .density_contract import density_case_row

    return density_case_row(case_key)["case_id"]


def _frozen_reqs(case_key: str) -> list[str]:
    from .density_contract import density_case_row

    return list(density_case_row(case_key)["frozen_requirement_ids"])

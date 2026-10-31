"""Named registrations and wave tokens for the fixed-Loop density projection.

Every registration is a disclosed, named gap or divergence with an explicit
disposition.  None of them normalizes an unexecuted leg to PASS and none of
them grants release credit: the dynamic owned-local fixed-Loop families have
no authorized preprod station by design, the two committed face surfaces
(executor-migration mode DUAL versus coverage-map LOCAL_ONLY) disagree about
whether a preprod dual leg exists, and the face parsed snapshot undercounts
the frozen executor ledger.  The three wave tokens name this density wave in
the shared faces exactly as an authorized mainline wave would append them.
"""
from __future__ import annotations

STORY_FIXED = "story_fixed_loop_human_editor_workflow"
STORY_NESTED = "story_nested_fixed_loop_workflows"

DUAL_FACE_DIVERGENCE_ID = "FIXED-LOOP-DENSITY-DUAL-FACE-DIVERGENCE"
FACE_UNDERCOUNT_ID = "FIXED-LOOP-DENSITY-FACE-PARSED-UNDERCOUNT"
FIXED_PREPROD_GAP_ID = "FIXED-LOOP-DENSITY-PREPROD-DYNAMIC-OWNED-LOCAL-GAP"
NESTED_PREPROD_GAP_ID = "NESTED-LOOP-DENSITY-PREPROD-REMOTE-UNSUPPORTED-GAP"

FAMILY_TOKEN = "FIXED_LOOP_DENSITY_59_OBLIGATIONS_20261031"
FIXED_ROW_TOKEN = "FIXED_LOOP_DENSITY_34_OBLIGATIONS_20261031"
NESTED_ROW_TOKEN = "NESTED_FIXED_LOOP_DENSITY_25_OBLIGATIONS_20261031"

NAMED_REGISTRATIONS = {
    FIXED_PREPROD_GAP_ID: {
        "id": FIXED_PREPROD_GAP_ID,
        "status": "NAMED_REGISTRATION",
        "category": "preprod_owned_local_runtime_by_design_gap",
        "scope": f"row #18 {STORY_FIXED}: all 42 charged rows",
        "observation": (
            "Face row #18 carries mode DUAL with preprod_verdict BLOCKED_INVENTORIED and empty "
            "notes, while the family executes on a runner-owned dynamic owned-local runtime "
            "(fresh per-Run Provider/Judge sandbox, deterministic scripted outcomes, no shared "
            "control-plane reset). The coverage-map row no 23 records the preprod leg as NOT_RUN "
            "with the explicit note that the preprod leg must not be counted as a dual leg. The "
            "dual-registered command dual-fixed-loop-run replays the owned-local leg and emits "
            "this preprod gap receipt; it never mounts a preprod station."),
        "evidence": (
            "executor-migration.json row index 18; coverage-map-20261031 rows no 23; "
            "bcs_e2e/dynamic_family/contract.py dual-fixed-loop-run; "
            "bcs_e2e/local_fixed_loop_run.py permission gates; bcs_e2e/local_fixed_loop_case.py "
            "verify_shared_reset_replacement"),
        "disposition": (
            "REMOTE_UNSUPPORTED-by-design registered by name: the preprod leg stays honest "
            "NOT_RUN; the DUAL face mode is executor-migration bookkeeping and is never read "
            "as a dual preprod leg; nothing is normalized to PASS."),
        "blocks": "preprod PASS credit for the fixed-Loop family",
    },
    NESTED_PREPROD_GAP_ID: {
        "id": NESTED_PREPROD_GAP_ID,
        "status": "NAMED_REGISTRATION",
        "category": "preprod_remote_unsupported_by_design",
        "scope": f"row #19 {STORY_NESTED}: all 41 charged rows",
        "observation": (
            "nested-fixed-loop-migration-contract.json freezes environment {local: "
            "LOCAL_EXECUTOR_ONLY, preprod: REMOTE_UNSUPPORTED} with remote_validation NOT_RUN "
            "and grants_release_credit false. The story needs the runner-owned BCS runtime plus "
            "the Provider/Judge mock child and a fresh temporary definition directory; no "
            "authorized preprod station exists for it by contract."),
        "evidence": (
            "nested-fixed-loop-migration-contract.json environment/remote_validation/"
            "grants_release_credit blocks; bcs_e2e/local_nested_fixed_loop_run.py owned-runtime "
            "and mock-child lifecycle"),
        "disposition": (
            "REMOTE_UNSUPPORTED-by-design disclosed at the migration-contract level: every "
            "nested density row keeps preprod NOT_RUN with this named gap; the contract value "
            "is quoted verbatim from the frozen JSON, never re-judged."),
        "blocks": "preprod PASS credit for the nested fixed-Loop family",
    },
    DUAL_FACE_DIVERGENCE_ID: {
        "id": DUAL_FACE_DIVERGENCE_ID,
        "status": "NAMED_REGISTRATION",
        "category": "dual_face_vs_owned_local_lane_divergence",
        "scope": f"face rows #18/#19 and coverage-map rows no 23/24 ({STORY_FIXED}, {STORY_NESTED})",
        "observation": (
            "The committed executor-migration rows #18/#19 carry mode DUAL (preprod "
            "BLOCKED_INVENTORIED) and the dual commands dual-fixed-loop-run / "
            "dual-nested-fixed-loop-run are registered in cli.py dispatch and in "
            "case_command_registration.py (landed with the four dual dynamic-family runners), "
            "while coverage-map rows no 23/24 carry dual.status LOCAL_ONLY with "
            "executor_migration_mode DYNAMIC_OWNED_LOCAL and the note that the preprod leg "
            "must not be counted as a dual leg. The two committed faces disagree on whether a "
            "preprod dual leg exists for these families."),
        "evidence": (
            "executor-migration.json rows 18/19; coverage-map-20261031.json/.md rows no 23/24; "
            "bcs_e2e/cli.py dual-fixed-loop-run and dual-nested-fixed-loop-run dispatch; "
            "bcs_e2e/case_command_registration.py parsers; dynamic family runners commit "
            "2f941f2ab"),
        "disposition": (
            "DUAL_FACE_DIVERGENCE_REGISTERED: the density projection charges the owned-local "
            "lane only; the preprod leg is never counted as a dual preprod leg; the "
            "divergence is registered for an authorized mainline disposition, never "
            "normalized, and the committed face rows stay byte-identical this wave."),
        "blocks": "preprod dual-leg credit and any face normalization",
    },
    FACE_UNDERCOUNT_ID: {
        "id": FACE_UNDERCOUNT_ID,
        "status": "NAMED_REGISTRATION",
        "category": "face_parsed_undercount_recalibration",
        "scope": f"face rows #18/#19 parsed snapshot (1) versus frozen site ledgers 67/47",
        "observation": (
            "The face rows carry snapshot parsed 1 (the shell facade only) and local "
            "obligations 8/16 (the coarse migration-contract requirements). The frozen "
            "executor ledgers parse 66/46 upstream python sites (33 assert + 33 transport "
            "and 24 assert + 22 transport) plus 2 shell facades = 67/47 sites. This wave "
            "registers 34/25 leaves and keeps the 33/22 transport stations as parent-internal "
            "checkpoints, closing the charged-row caliber at 42/41 with residual gap 0."),
        "evidence": (
            "bcs_e2e/local_fixed_loop_contract.py ASSERTION_IDS parity; "
            "bcs_e2e/local_nested_fixed_loop_contract.py ASSERTION_IDS parity; "
            "coverage-map-20261031 rows no 23/24; this package plan accounting"),
        "disposition": (
            "RECALIBRATION_DISCLOSED: the site caliber (67/47) and the charged-row caliber "
            "(42/41) are both disclosed and never merged; the face undercount is registered, "
            "not silently rebased; the recalibration changes no committed face value."),
        "blocks": "nothing locally; recalibrates the parsed denominator honestly",
    },
}

# The two executor-migration row notes this wave proposes (DESCRIBE_ONLY).
# Each is one single-line note naming its family token, riding the wave
# family token, and closing with the honest preprod posture; inventory.py
# declares both strings absent today (fail-close precondition asserted by
# the focused tests and by registration_proposal.assert_anchors).
FIXED_ROW_NOTE_PROPOSAL = (
    FIXED_ROW_TOKEN + " | " + FAMILY_TOKEN
    + ": E2a fixed-Loop density recalibration for row #18 story_fixed_loop_human_editor_workflow"
    " - frozen executor ledger 66 sites (33 assert + 33 transport) + 1 shell facade = 67 parsed"
    " sites (face snapshot parsed 1 recalibrated and disclosed, never silently rebased); roster"
    " 8 FIXED_LOOP.* migration-contract parent envelopes reused verbatim + 34 charged leaves"
    " FLOOP.F01-F34 (F01 shell facade exit-0 leaf under the disclosed face-derived auxiliary"
    " gate FIXED_LOOP.story-gate, F02-F34 python assert atoms with frozen comparators and"
    " expected values) = 42 charged rows; 33 transport stations ride their owning parent envelopes"
    " as internal checkpoints (union equals the transport ledger, never double-charged);"
    " residual_count_gap=0 under both calibers (sites 67 = 34 leaves + 33 checkpoints; rows"
    " 42 = 8 parents + 34 leaves); owned-local offline replay is station-form only with frozen"
    " completion gates (judge revise/approved/revise/revise, receipt partitions 4/4 per arm,"
    " outputs 6 per arm, one-shot session running, chat publication 1, exact deletions groups 2"
    " + provider_bot 1, non-target state 0); preprod leg honest NOT_RUN as named gap"
    " FIXED-LOOP-DENSITY-PREPROD-DYNAMIC-OWNED-LOCAL-GAP (dynamic owned-local runtime by"
    " design; the DUAL face mode is bookkeeping and never counts a dual preprod leg;"
    " FIXED-LOOP-DENSITY-DUAL-FACE-DIVERGENCE and FIXED-LOOP-DENSITY-FACE-PARSED-UNDERCOUNT"
    " registered); remote_validation=NOT_RUN; grants_release_credit=false; pending-landing"
)
NESTED_ROW_NOTE_PROPOSAL = (
    NESTED_ROW_TOKEN + " | " + FAMILY_TOKEN
    + ": E2a nested fixed-Loop density recalibration for row #19 story_nested_fixed_loop_workflows"
    " - frozen executor ledger 46 sites (24 assert + 22 transport) + 1 shell facade = 47 parsed"
    " sites (face snapshot parsed 1 recalibrated and disclosed, never silently rebased); roster"
    " 16 NESTED-LOOP.* migration-contract parent envelopes (story-span anchored) + 25 charged"
    " leaves NFLOOP.F01-F25 (F01 shell facade exit-0 leaf under the disclosed face-derived"
    " auxiliary gate NESTED-LOOP.story-gate, F02-F25 python assert atoms) = 41 charged rows;"
    " 22 transport stations ride their owning parent envelopes as internal checkpoints;"
    " residual_count_gap=0 under both calibers (sites 47 = 25 leaves + 22 checkpoints; rows"
    " 41 = 16 parents + 25 leaves); the nested child inherits the base finish_bot assert family"
    " and cleanup stations as a NON-additive cross-family annex (FLOOP finish_bot asserts"
    " 202/204-207/216 and cleanup stations 293/295/297) disclosed per owned-parent envelope;"
    " owned-local offline replay station-form only with frozen completion gates (both story"
    " markers true, observed marker checks 2, redaction gate clean, post-story zero traffic,"
    " child exit 0, mock child ready/exit 0/redaction clean); preprod leg honest NOT_RUN as"
    " REMOTE_UNSUPPORTED-by-design named gap NESTED-LOOP-DENSITY-PREPROD-REMOTE-UNSUPPORTED-GAP"
    " (nested-fixed-loop-migration-contract.json environment preprod REMOTE_UNSUPPORTED;"
    " dynamic owned-local runtime; never counted as a dual preprod leg;"
    " FIXED-LOOP-DENSITY-DUAL-FACE-DIVERGENCE and FIXED-LOOP-DENSITY-FACE-PARSED-UNDERCOUNT"
    " registered jointly with the fixed family under the shared family-scope registration ids);"
    " remote_validation=NOT_RUN; grants_release_credit=false; pending-landing"
)

# Coverage-map recalibration increments (proposal text only; the shared
# coverage-map files stay byte-identical this wave).
FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL = (
    "E2a 密度重校准：parsed 1→42 义务行口径（站点口径 67 = 34 叶 + 33 交通检查点单独披露，"
    "双口径永不合并）；obligation 8→42（8 个 FIXED_LOOP.* 迁移合同父信封原样复用 + 34 收费叶"
    " FLOOP.F01-F34：F01 shell 门面 exit-0 叶挂在披露的 face 派生辅助门 FIXED_LOOP.story-gate"
    " 下，F02-F34 为 33 条 python assert 原子）；gap −7→0，residual_count_gap=0；交通站仅骑父"
    "信封披露为 internal checkpoints 不重复收费；owned-local 离线回放仅站面口径（冻结完成门："
    "judge revise/approved/revise/revise、per-arm 分区 4/4、每臂 outputs 6、one-shot session"
    " running、chat 发布 1、精确删除 groups 2 + provider_bot 1、非目标态 0）；preprod 腿维持"
    " NOT_RUN 具名 gap（动态 owned-local runtime 设计如此，DUAL face 模式是登记簿记永不按"
    " dual 预发腿计；具名登记 FIXED-LOOP-DENSITY-PREPROD-DYNAMIC-OWNED-LOCAL-GAP / "
    "FIXED-LOOP-DENSITY-DUAL-FACE-DIVERGENCE / FIXED-LOOP-DENSITY-FACE-PARSED-UNDERCOUNT）；"
    "dual.status LOCAL_ONLY 与 executor_migration_mode DYNAMIC_OWNED_LOCAL 维持不改；"
    "remote_validation=NOT_RUN；grants_release_credit=false；pending-landing"
)
NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL = (
    "E2a 嵌套密度重校准：parsed 1→41 义务行口径（站点口径 47 = 25 叶 + 22 交通检查点单独披露，"
    "双口径永不合并）；obligation 16→41（16 个 NESTED-LOOP.* 迁移合同父信封按 story_lines span "
    "锚定复用 + 25 收费叶 NFLOOP.F01-F25：F01 shell 门面 exit-0 叶挂在披露的 face 派生辅助门"
    " NESTED-LOOP.story-gate 下，F02-F25 为 24 条 python assert 原子）；gap −15→0，"
    "residual_count_gap=0；子进程继承基类 finish_bot 断言族与清理站作为 NON-additive 跨族 annex"
    "（FLOOP 202/204-207/216 与 293/295/297）随属主父信封披露，nested 账本保持 46 不膨胀；"
    "owned-local 离线回放仅站面口径（冻结完成门：双 story marker 全真、observed checks 2、"
    "redaction gate 清洁、事后零流量、child exit 0、mock child ready/exit 0/redaction 清洁）；"
    "preprod 腿维持 contractual REMOTE_UNSUPPORTED NOT_RUN 具名 gap"
    " NESTED-LOOP-DENSITY-PREPROD-REMOTE-UNSUPPORTED-GAP（动态 owned-local runtime，"
    "永不按 dual 预发腿计）；dual.status LOCAL_ONLY 与 executor_migration_mode"
    " DYNAMIC_OWNED_LOCAL 维持不改；remote_validation=NOT_RUN；grants_release_credit=false；"
    "pending-landing"
)

__all__ = [
    "DUAL_FACE_DIVERGENCE_ID", "FACE_UNDERCOUNT_ID", "FAMILY_TOKEN",
    "FIXED_COVERAGE_MAP_ROW_NOTE_PROPOSAL", "FIXED_ROW_NOTE_PROPOSAL",
    "FIXED_ROW_TOKEN", "FIXED_PREPROD_GAP_ID", "NAMED_REGISTRATIONS",
    "NESTED_COVERAGE_MAP_ROW_NOTE_PROPOSAL", "NESTED_ROW_NOTE_PROPOSAL",
    "NESTED_ROW_TOKEN", "NESTED_PREPROD_GAP_ID", "STORY_FIXED", "STORY_NESTED",
]

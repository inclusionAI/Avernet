"""Run-frozen Case inventory and executor artifact identity.

Checksums detect drift, not malicious rewrites by someone who owns the Run files.
Historical reports can be read offline; executing with changed code is refused.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from .artifact import process_digest
from .effect_policy import validate_effect_policy
from .profile import ConfigError, fingerprint, identifier
from .run_state import MAX_STATE_BYTES, RunStateError, read

ROOT = Path(__file__).resolve().parents[1]


def validate_manifest(value: dict) -> dict:
    try:
        if (set(value) != {'schema_version', 'baseline_commit', 'suite', 'legacy_inventory', 'cases'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['baseline_commit'], str)
                or len(value['baseline_commit']) != 40
                or any(c not in '0123456789abcdef' for c in value['baseline_commit'])):
            raise ValueError
        identifier(value['suite'], 'manifest suite')
        cases, legacy = value['cases'], value['legacy_inventory']
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise ValueError
        if not isinstance(legacy, list) or not 1 <= len(legacy) <= 500:
            raise ValueError
        for items in (cases, legacy):
            ids = [identifier(item['id'], 'manifest item ID') for item in items]
            if len(set(ids)) != len(ids): raise ValueError
        for case in cases:
            if type(case['version']) is not int or case['version'] < 1: raise ValueError
            for field in ('assertions', 'effects', 'steps', 'roles', 'required_evidence'):
                items = case[field]
                if not isinstance(items, list) or not 1 <= len(items) <= 1000: raise ValueError
                if any(not isinstance(item, str) or not item or len(item) > 512 for item in items): raise ValueError
                if len(set(items)) != len(items): raise ValueError
            if any(not item.startswith(case['id'] + '.') for item in case['assertions']): raise ValueError
        return json.loads(json.dumps(value, allow_nan=False))
    except (KeyError, TypeError, ValueError, ConfigError):
        raise RunStateError('manifest schema, versions or inventory are invalid') from None


KNOWN_UNMAPPED_LEGACY_IDS = frozenset({
    # Pre-existing five, unmapped since the reviewed-release cut:
    'story_cli_operator_manages_group_context',
    'story_nested_fixed_loop_workflows',
    'story_state_machine_result_queue',
    'test_ep_dept_scope_parses_object_friend_ext_entries',
    'test_ep_tc_friend_auth_sync_readdressed_principal',
    # Long-unregistered upstream stories (harness-defined before the freeze,
    # never part of the reviewed release denominator; registered 2026-10-29
    # for migration with the fresh re-freeze, see migration-freeze-review-20261029.md):
    'story_provider_owner_isolation',
    'story_session_owner_permissions',
    'story_provider_operator_manages_subbots',
    'story_collaboration_draft_delivery',
    'story_collaboration_draft_group_launch',
})
REVIEWED_RELEASE_PARENT_COUNT = 31

EXECUTOR_MIGRATION_PATH = ROOT / 'executor-migration.json'
EXECUTOR_MIGRATION_MODES = frozenset({
    'DUAL', 'LOCAL_ONLY', 'DYNAMIC_OWNED_LOCAL', 'EDGE_OBLIGATION_IN_FAMILY',
})
PREPROD_VERDICTS = frozenset({
    'PASS', 'BLOCKED_INVENTORIED', 'NOT_RUN', 'NOT_APPLICABLE',
})
EXECUTOR_MIGRATION_GATES = frozenset({'MOCK'})
EXECUTOR_MIGRATION_NOTES = frozenset({
    'ROSTER_LAG', 'STATION_DEFECT_R5_CAVEAT', 'FRESH_FIXTURE_R5G',
    'DUAL_STRUCTURED_DENSITY_R12_PREPROD_PASS_20261031',
    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031',
    'SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031',
    'DIRECT_DENSITY_14_OBLIGATIONS_20261031',
    'CUSTOM_COLLAB_DENSITY_37_OBLIGATIONS_20261031',
    'DUAL_SUBBOTS_DENSITY_D4_131_LEAVES',
    'STRUCTURED_COLLAB_DENSITY_26_OBLIGATIONS_20261031',
    'DRAFT_DELIVERY_DENSITY_24_OBLIGATIONS_20261031',
    'COLLAB_TEAM_DENSITY_22_OBLIGATIONS_20261031',
    'GROUP_CONTEXT_DENSITY_9_OBLIGATIONS_20261031',
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 6 密度叶 == parsed 7（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/error-admission-branches-stdout.json（status=PASS，remote_validation=NOT_RUN，grants_release_credit=false）；wpc 本地轮 20261031-migration-wpc-local-owned-edge-3 junit 行 LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing',
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/connect-private-discover-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01 PASS 1/1；产品在案 PD-PRIVATE-CREATE-500（preprod 500 vs 契约 403）与 PD-DISCOVER-BRANCH-DRIFT（org 分支 403 vs 契约 400、role 包装 invalid_operation）在密度分类中如实披露；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing',
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 2 义务 + 1 密度叶 == parsed 3（dual-edge-permission-density-v1）；r5 读腿收据 20261031-regression-r5-edge-read/friend-connections-stdout.json 与 r7 状态腿 20261031-regression-r7-preprod/edge-read/friend-connections-state/edge-friend-connections-run.json（公共 face PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01，contract dual-edge-read-case-run-v1，status=PASS，remote_validation=NOT_RUN）；本地读轮留存在本族为 LEG_ABSENT（读片走 dual-edge-read-run 族）；annex 叶跨族引证 r5 mutation 轮 error-admission 收据 create 行（friend_connection_mutation.create 6 行中 3 行 201 且 request id 回显）；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing',
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 1 密度叶 == parsed 2（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/accept-reject-cancel-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing',
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 与 r6 双腿收据 20261031-regression-r5-edge-mutation/ 与 20261031-regression-r6-edge-mutation/ 下 mutual-auto-approve-stdout.json（双轮 status=PASS，remote_validation=NOT_RUN；r5/r6 peer bot 不同，口径 一致）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01 PASS 1/1；身份交叉核对 member=20261010_gekng5hk:39464 出现在双轮 admissions 方向键；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing',
    'AUTHORITY_DENSITY_42_OBLIGATIONS_20261031',
    'row #10 story_user_validates_external_channel_setup: register 7 source-anchored density obligations under the retained CHANNEL.* parent envelope (13 parsed = 6 legacy parents + 7 atoms; residual_count_gap=0); notes-only integration, the dual-channel-bindings-run envelope and its r8 conclusions stay untouched',
    'row #11 story_cli_operator_validates_channel_management: register 7 source-anchored density obligations under the retained CHANNEL-CLI.* parent envelope (16 parsed = 9 legacy parents + 7 atoms; residual_count_gap=0); preprod keeps BLOCKED_INVENTORIED with the disabled-bridge divergence registered as a true business judgment (not a defect) and the sim DingTalk design red disclosed; remote_validation=NOT_RUN and grants_release_credit=false',
    'WPB_SESSION_SHARING_87_OBLIGATIONS_20261031',
    'WPB_SESSION_SERVICE_44_OBLIGATIONS_20261031',
    'WPB_SESSION_FILE_12_REQUIREMENTS_20261031',
    "WP-B2 边缘读族密度闭合注记：义务 2 == parsed 2（committed 契约 edge-admission-migration-contract.json requirements=2，EDGE-ADMISSION.human-actor/actor-kind，源冻结 8559a7a5）；预发实弹 PASS 双轮——r5 preprod-live-evidence/20261031-regression-r5-edge-read/admission-stdout.json PUBLIC-EDGE-ADMISSION-01 status=PASS，r7 r7-progress.tsv edge-read/admission rc=0 PASS；committed verdict 维持 PASS。",
    "WP-B2 族内义务与缺陷披露注记：族内义务 1（edge_friend_mutation_case 九义务分派，LOCAL-EDGE-FRIEND-REVOKE-01 owned-local 回放全绿，migration-coverage-matrix-20261030.tsv 记 REVIEWED-30-SLICED）；产品在案缺陷 E9 revoke 空边（assertion-gap-matrix-20261031.md：r5 日志 revoked_edges=[]）登记不改判；预发 r5 edge-mutation/revoke-stdout.json status=BLOCKED（RunStateError fail-close 准入窗口，非静默跳过）；预发 dual 腿 station bot token 环境变量引用 GAP 在案（⏳P6 用户决策项）；committed verdict 维持 BLOCKED_INVENTORIED。",
    "WP-B2 边缘读族密度闭合注记：义务 3 ≥ parsed 1（超集 2 条如实登记，不调减分母）；committed 契约 edge-friend-requests-list-migration-contract.json requirements=3（源冻结 8559a7a5）；预发实弹 PASS 双轮——r5 edge-read/friend-requests-stdout.json PUBLIC-EDGE-FRIEND-REQUESTS-LIST-01 status=PASS，r7 r7-progress.tsv edge-read/friend-requests rc=0 PASS；committed verdict 维持 PASS。",
    "WP-B2 族内近似口径如实注：mode 维持 EDGE_OBLIGATION_IN_FAMILY 不变；owned-local 族内分派 per-case 义务 1，回放 LOCAL-EDGE-ENSURE-BOT-01 全绿（migration-coverage-matrix-20261030.tsv 记 REVIEWED-30-SLICED 全绿）；预发 r7 edge-read 家族 rc=0 PASS（admission 叶族内近似覆盖，非独立精确腿）；本行不增资、不改判、不从族近似冒领独立腿信用。",
    "WP-B2 COUNT_GAP 如实披露不闭合：parsed 7 − 族内义务 1 = 6 条未逐条义务化（不伪造密度）；产品在案缺陷 E10 full-lifecycle 连带（r5 日志 friendship was not revoked）；预发 r5 edge-mutation/full-lifecycle-stdout.json status=BLOCKED（RunStateError fail-close）；owned-local 族回放全绿（tsv REVIEWED-30-SLICED）；station bot token GAP ⏳P6；committed verdict 维持 BLOCKED_INVENTORIED。",
    "WP-B2 边缘搜索密度闭合注记：义务 13 == parsed 13（快照义务 12 为漂移旧值，committed 契约 edge-bot-search-migration-contract.json requirements=13 已追平，漂移 resolved 注记保留、不改 NONE 计数口径）；预发实弹 PASS 双轮——r5 edge-read/bot-search-gw-stdout.json PUBLIC-EDGE-BOT-SEARCH-CANDIDATES-01 status=PASS，r7 r7-progress.tsv edge-read/bot-search-gw rc=0 PASS；committed verdict 维持 PASS。",
    "WP-B2 backlog 密度闭合（契约层）：committed tc-friend-auth-sync-migration-contract.json obligations=5（requirements=1 wrapper；98e893e87 E12 切片 1→2：EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept/principal）≥ parsed 2；owned-local 家族 13/13 断言 PASS 在案（98e893e87：family obligations 9→13、focused tests 47/47）；预发 r8 双收据 fail-close NOT_RUN（preprod-live-evidence/20261031-regression-r8-preprod/edge-permission-backlog/tc-friend-auth-sync-local-console.txt 与 tc-friend-auth-sync-preprod-console.txt：offline dual receipt，remote_validation=NOT_RUN，grants_release_credit=false）；98e893e87 登记 PREPROD_SERVICE_SCOPE_GAP 物料缺口；station bot token 环境变量引用 GAP ⏳P6；committed verdict 维持 NOT_RUN。",
    "WP-B2 backlog 密度闭合（契约层）：committed dept-scope-friend-ext-migration-contract.json obligations=7（requirements=1 wrapper；98e893e87 E13 切片 1→4：mixed object/string/garbage dept-scope 解析与隔离）≥ roster parsed 6 ≥ snapshot 4（双口径如实登记）；owned-local 家族闭合同 tc 形态（13/13 断言 PASS、focused tests 47/47，98e893e87）；预发 r8 双收据 fail-close NOT_RUN（dept-scope-friend-ext-local-console.txt 与 dept-scope-friend-ext-preprod-console.txt：offline dual receipt，remote_validation=NOT_RUN，grants_release_credit=false）；PREPROD_SERVICE_SCOPE_GAP 在案；station bot token GAP ⏳P6；committed verdict 维持 NOT_RUN。",
    'PROVIDER_PUBLISHES_DENSITY_98_OBLIGATIONS_20261031',
    "row #20 story_state_machine_result_queue: register 34 source-anchored density obligations under the retained RESULT-QUEUE.* parent envelope (52 parsed = 18 legacy parents + 34 atoms; residual_count_gap=0); the owned-local executor catalog ASSERTION_IDS (34 ordered sites) equals the independent AST rebuild verbatim and every site keeps exactly one frozen parent owner; RESULT-QUEUE.delivery-policy-replacement stays the disclosed zero-atom parent (story lines 41-46 only mutate the in-memory policy dict; enforcement is observed by the scheduled-result-delivery atoms RQ.F21..RQ.F23); the seven dynamic wait predicates and the terminal marker print stay parent-charged, not leaf-charged; notes-only integration, the dual-result-queue-run envelope and its LOCAL_RESULT_QUEUE_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false",
    "row #40 story_collaboration_draft_group_launch: register 75 source-anchored density obligations under the retained LOCAL-DRAFT-GROUP.* parent envelope (92 parsed = 17 legacy parents + 75 weighted atoms; residual_count_gap=0); the owned-local executor catalog ASSERTION_IDS (75 ordered weighted sites) equals the independent AST rebuild verbatim and every site keeps exactly one frozen parent owner; LOCAL-DRAFT-GROUP.group-launch-input stays the disclosed zero-atom parent (story lines 162-172 only build the launch body dict; enforcement is observed by the browser-context-submission-guards atoms DGL.F49..DGL.F51 and the group-launch-binding atoms DGL.F56..DGL.F64); the terminal marker print stays parent-charged; notes-only integration, the dual-draft-group-run envelope and its LOCAL_DRAFT_GROUP_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false",
    "row #35 story_provider_owner_isolation: register the 24 source-anchored obligation roster (PROVIDER-OWNER.*; contract provider_owner_contract.py; upstream provider_scope_story.py frozen at f181ba448a8bd5844c9517d8543ff3b6bb9d66bb with story_checks=45; shell step map provider-owner-isolation-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-provider-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/provider-owner-stdout.json status=BLOCKED completed 0/24 error_type=LOCAL_PROVIDER_OWNER_BLOCKED (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_principal case DUAL-PROVIDER-OWNER-01 contract dual-provider-owner-case-run-v1 all 24 steps NOT_RUN): the story child registers provider prv_59ca9519 and bot bot_e38133ac, bootstraps the foreign Human, POST /providers/{id}/bots answers 200, then story request 8 PUT /bots/bot_09741ff7/visibility expects 200 and receives 403, the child raises AssertionError at provider_scope_story.py L117 and exits 1, the executor fail-closes at 0/24, and the full stderr traceback is retained as bounded evidence while the receipt error_detail stays null by design; this is the contract-frozen drift gap OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT (provider registration records created_by but mints no bcs-edge-permission authority edges on the owned-local runtime; the story frozen at f181ba44 predates the bot-authority model c6f001209 #2625; poolab preprod green there because the preprod runtime seeds the edges; story source apps/bcs/scripts/e2e-test/provider_scope_story.py exists locally and not on upstream dev; the BLOCKED receipt is the contract expectation, not a regression); preprod stays REMOTE_UNSUPPORTED by design (PREPROD_PROVIDER_ADMIN_FACE_ABSENT, WORKBENCH_WS_MOCK_ADMISSION) and the wp2 preprod disclosure verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/provider-owner/20261031-migration-wp2-provider-owner-preprod-disclosure-verdict.json stays BLOCKED (PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED, credential_admission BLOCKED PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE); the r1 root holds no provider-owner local leg receipt; notes-only integration, the dual-provider-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1; remote_validation=NOT_RUN and grants_release_credit=false",
    "row #36 story_session_owner_permissions: register the 9 source-anchored obligation roster (SESSION-OWNER.*; contract session_owner/session_owner_contract.py; story_checks=60 plus cleanup_checks=4, frozen required assertion inventory 78 = run_http 60 + cleanup_http 4 + shell 1 + structural_python 13; shell step map session-owner-permissions-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r1 receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r1/session-owner-stdout.json is the preprod-tier probe leg and is NOT a local live leg (tier=preprod id=dual-session-owner-preprod-r7 entrypoint=gateway api_face=public-v1 credential_kind=preprod_human_cookie_route_probe run_id 20261031-owner-isolation-local-live-r1-session-owner): status=BLOCKED completed 0/9 error PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED, preprod probe BLOCKED PREPROD_PROBE_BASELINE_UNEXPECTED, four named gaps PREPROD_STATION_BOOTSTRAP_FACE_ABSENT PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY PREPROD_PARTICIPANT_ROUTES_UNBOUND, mirrored by wp2 verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/session-owner/20261031-owner-isolation-local-live-r1-session-owner-verdict.json (BLOCKED, remote_validation=NOT_RUN, grants_release_credit=false); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-session-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/session-owner-stdout.json status=PASS completed 9/9 LOCAL_SESSION_OWNER_PASS (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_gateway_principals case DUAL-SESSION-OWNER-01 contract dual-session-owner-case-run-v1): journal observed_assertion_count=64 against the frozen required inventory 78 (run_http 60 + cleanup_http 4 + shell 1 + structural_python 13 per contract UPSTREAM_ASSERTIONS), requirement_statuses 9/9 PASS, cleanup disposition SESSION_GROUP_AND_OWNED_BOTS_DELETED with zero process leak; notes-only integration, the dual-session-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1 (preprod verdict stays NOT_RUN honestly; neither leg grants release credit); remote_validation=NOT_RUN and grants_release_credit=false",
    "row #9 story_operator_coordinates_with_cli: register 15 source-anchored density obligations under the retained OPERATOR-CLI.* parent envelope (28 parsed = 13 legacy parents + 15 atoms; residual_count_gap=0); the frozen shell span catalog OPCLI.F01..OPCLI.F15 (site envelope OPERATOR-CLI.*) rebuilds every parent call line (stories.sh 1786-1798) and every atom quote verbatim from the SHA-256-frozen cli.sh/cli-stories.sh bytes in story order, each leaf keeping exactly one frozen parent owner; OPERATOR-CLI.create-group stays the disclosed zero-atom parent (its group.sh helper surface lives outside the stories.sh/cli.sh/cli-stories.sh upstream parse face; field enforcement stays parent-charged with the frozen creator-scoped exact-delete disposition as evidence); the exit-zero fail guards, TESTS_* bookkeeping, ensure_cli_token skip guards, the visibility original-value capture guard, the upstream register prerequisite legs and the chat prerequisite in-case friendship admissions stay parent-charged, not leaf-charged; UPSTREAM_ISSUE=None disclosed (no committed issue pointer found); notes-only integration, the dual-operator-cli-run envelope and its LOCAL_OPERATOR_CLI_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file (five-bot health leg BLOCKED, r5 leaf-BLOCKED lineage, reviewed duo credentials unreviewed, full story not executed offline); remote_validation=NOT_RUN and grants_release_credit=false",
    "row #1 story_user_prepares_agent_network: register 3 source-anchored density obligations under the retained NETWORK.* parent envelope (11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms; residual_count_gap=0); the 8 leg-PASS snapshot records align verbatim onto the first eight platform statements (identical description literals at stories.sh:269-280) and are absorbed one per leg by NETWORK.platform under the leg-verdict proxy caliber that the committed assertion-gap matrix itself labels approximate; the FAIL record and the two WARN records are one upstream manifest-asset-404 abort event anchored as AN.F01..AN.F03 at stories.sh:295 (emit chain common.sh assert_status 104-107 > assert_eq 68 > fail print 74), stories.sh:37 (require_status failure-branch response warn) and e2e.sh:210 (runner stopped warn); the 33 unreached actor/register static statements and the 11 unreached platform statements stay parent-charged with no fabricated leaves; notes-only integration, the dual-network-read-run/register-run/status-run envelopes and the committed DUAL/PASS r7 face stay untouched; committed verdict stays DUAL/PASS with notes 0 to 1; preprod density tier keeps the three named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false",
})
_VALID_EXECUTOR_MIGRATION_STATES = frozenset({
    ('DUAL', 'PASS'),
    ('DUAL', 'BLOCKED_INVENTORIED'),
    ('DUAL', 'NOT_RUN'),
    ('LOCAL_ONLY', 'NOT_RUN'),
    ('DYNAMIC_OWNED_LOCAL', 'NOT_APPLICABLE'),
    ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'),
})
_EXPECTED_EXECUTOR_MIGRATION_COUNTS = {
    ('DUAL', 'PASS'): 19,
    ('DUAL', 'BLOCKED_INVENTORIED'): 11,
    ('DUAL', 'NOT_RUN'): 10,
    ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1,
}
_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS = {'MOCK': 2}
_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS = {
    'STATION_DEFECT_R5_CAVEAT': 1,
    'FRESH_FIXTURE_R5G': 1,
    'ROSTER_LAG': 1,
    'DUAL_STRUCTURED_DENSITY_R12_PREPROD_PASS_20261031': 1,
    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031': 1,
    'SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031': 1,
    'DIRECT_DENSITY_14_OBLIGATIONS_20261031': 1,
    'CUSTOM_COLLAB_DENSITY_37_OBLIGATIONS_20261031': 1,
    'DUAL_SUBBOTS_DENSITY_D4_131_LEAVES': 1,
    'STRUCTURED_COLLAB_DENSITY_26_OBLIGATIONS_20261031': 1,
    'DRAFT_DELIVERY_DENSITY_24_OBLIGATIONS_20261031': 1,
    'COLLAB_TEAM_DENSITY_22_OBLIGATIONS_20261031': 1,
    'GROUP_CONTEXT_DENSITY_9_OBLIGATIONS_20261031': 1,
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 6 密度叶 == parsed 7（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/error-admission-branches-stdout.json（status=PASS，remote_validation=NOT_RUN，grants_release_credit=false）；wpc 本地轮 20261031-migration-wpc-local-owned-edge-3 junit 行 LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing': 1,
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/connect-private-discover-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01 PASS 1/1；产品在案 PD-PRIVATE-CREATE-500（preprod 500 vs 契约 403）与 PD-DISCOVER-BRANCH-DRIFT（org 分支 403 vs 契约 400、role 包装 invalid_operation）在密度分类中如实披露；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing': 1,
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 2 义务 + 1 密度叶 == parsed 3（dual-edge-permission-density-v1）；r5 读腿收据 20261031-regression-r5-edge-read/friend-connections-stdout.json 与 r7 状态腿 20261031-regression-r7-preprod/edge-read/friend-connections-state/edge-friend-connections-run.json（公共 face PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01，contract dual-edge-read-case-run-v1，status=PASS，remote_validation=NOT_RUN）；本地读轮留存在本族为 LEG_ABSENT（读片走 dual-edge-read-run 族）；annex 叶跨族引证 r5 mutation 轮 error-admission 收据 create 行（friend_connection_mutation.create 6 行中 3 行 201 且 request id 回显）；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing': 1,
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 1 密度叶 == parsed 2（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/accept-reject-cancel-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing': 1,
    'Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 与 r6 双腿收据 20261031-regression-r5-edge-mutation/ 与 20261031-regression-r6-edge-mutation/ 下 mutual-auto-approve-stdout.json（双轮 status=PASS，remote_validation=NOT_RUN；r5/r6 peer bot 不同，口径 一致）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01 PASS 1/1；身份交叉核对 member=20261010_gekng5hk:39464 出现在双轮 admissions 方向键；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing': 1,
    'AUTHORITY_DENSITY_42_OBLIGATIONS_20261031': 1,
    'row #10 story_user_validates_external_channel_setup: register 7 source-anchored density obligations under the retained CHANNEL.* parent envelope (13 parsed = 6 legacy parents + 7 atoms; residual_count_gap=0); notes-only integration, the dual-channel-bindings-run envelope and its r8 conclusions stay untouched': 1,
    'row #11 story_cli_operator_validates_channel_management: register 7 source-anchored density obligations under the retained CHANNEL-CLI.* parent envelope (16 parsed = 9 legacy parents + 7 atoms; residual_count_gap=0); preprod keeps BLOCKED_INVENTORIED with the disabled-bridge divergence registered as a true business judgment (not a defect) and the sim DingTalk design red disclosed; remote_validation=NOT_RUN and grants_release_credit=false': 1,
    'WPB_SESSION_SHARING_87_OBLIGATIONS_20261031': 1,
    'WPB_SESSION_SERVICE_44_OBLIGATIONS_20261031': 1,
    'WPB_SESSION_FILE_12_REQUIREMENTS_20261031': 1,
    "WP-B2 边缘读族密度闭合注记：义务 2 == parsed 2（committed 契约 edge-admission-migration-contract.json requirements=2，EDGE-ADMISSION.human-actor/actor-kind，源冻结 8559a7a5）；预发实弹 PASS 双轮——r5 preprod-live-evidence/20261031-regression-r5-edge-read/admission-stdout.json PUBLIC-EDGE-ADMISSION-01 status=PASS，r7 r7-progress.tsv edge-read/admission rc=0 PASS；committed verdict 维持 PASS。": 1,
    "WP-B2 族内义务与缺陷披露注记：族内义务 1（edge_friend_mutation_case 九义务分派，LOCAL-EDGE-FRIEND-REVOKE-01 owned-local 回放全绿，migration-coverage-matrix-20261030.tsv 记 REVIEWED-30-SLICED）；产品在案缺陷 E9 revoke 空边（assertion-gap-matrix-20261031.md：r5 日志 revoked_edges=[]）登记不改判；预发 r5 edge-mutation/revoke-stdout.json status=BLOCKED（RunStateError fail-close 准入窗口，非静默跳过）；预发 dual 腿 station bot token 环境变量引用 GAP 在案（⏳P6 用户决策项）；committed verdict 维持 BLOCKED_INVENTORIED。": 1,
    "WP-B2 边缘读族密度闭合注记：义务 3 ≥ parsed 1（超集 2 条如实登记，不调减分母）；committed 契约 edge-friend-requests-list-migration-contract.json requirements=3（源冻结 8559a7a5）；预发实弹 PASS 双轮——r5 edge-read/friend-requests-stdout.json PUBLIC-EDGE-FRIEND-REQUESTS-LIST-01 status=PASS，r7 r7-progress.tsv edge-read/friend-requests rc=0 PASS；committed verdict 维持 PASS。": 1,
    "WP-B2 族内近似口径如实注：mode 维持 EDGE_OBLIGATION_IN_FAMILY 不变；owned-local 族内分派 per-case 义务 1，回放 LOCAL-EDGE-ENSURE-BOT-01 全绿（migration-coverage-matrix-20261030.tsv 记 REVIEWED-30-SLICED 全绿）；预发 r7 edge-read 家族 rc=0 PASS（admission 叶族内近似覆盖，非独立精确腿）；本行不增资、不改判、不从族近似冒领独立腿信用。": 1,
    "WP-B2 COUNT_GAP 如实披露不闭合：parsed 7 − 族内义务 1 = 6 条未逐条义务化（不伪造密度）；产品在案缺陷 E10 full-lifecycle 连带（r5 日志 friendship was not revoked）；预发 r5 edge-mutation/full-lifecycle-stdout.json status=BLOCKED（RunStateError fail-close）；owned-local 族回放全绿（tsv REVIEWED-30-SLICED）；station bot token GAP ⏳P6；committed verdict 维持 BLOCKED_INVENTORIED。": 1,
    "WP-B2 边缘搜索密度闭合注记：义务 13 == parsed 13（快照义务 12 为漂移旧值，committed 契约 edge-bot-search-migration-contract.json requirements=13 已追平，漂移 resolved 注记保留、不改 NONE 计数口径）；预发实弹 PASS 双轮——r5 edge-read/bot-search-gw-stdout.json PUBLIC-EDGE-BOT-SEARCH-CANDIDATES-01 status=PASS，r7 r7-progress.tsv edge-read/bot-search-gw rc=0 PASS；committed verdict 维持 PASS。": 1,
    "WP-B2 backlog 密度闭合（契约层）：committed tc-friend-auth-sync-migration-contract.json obligations=5（requirements=1 wrapper；98e893e87 E12 切片 1→2：EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept/principal）≥ parsed 2；owned-local 家族 13/13 断言 PASS 在案（98e893e87：family obligations 9→13、focused tests 47/47）；预发 r8 双收据 fail-close NOT_RUN（preprod-live-evidence/20261031-regression-r8-preprod/edge-permission-backlog/tc-friend-auth-sync-local-console.txt 与 tc-friend-auth-sync-preprod-console.txt：offline dual receipt，remote_validation=NOT_RUN，grants_release_credit=false）；98e893e87 登记 PREPROD_SERVICE_SCOPE_GAP 物料缺口；station bot token 环境变量引用 GAP ⏳P6；committed verdict 维持 NOT_RUN。": 1,
    "WP-B2 backlog 密度闭合（契约层）：committed dept-scope-friend-ext-migration-contract.json obligations=7（requirements=1 wrapper；98e893e87 E13 切片 1→4：mixed object/string/garbage dept-scope 解析与隔离）≥ roster parsed 6 ≥ snapshot 4（双口径如实登记）；owned-local 家族闭合同 tc 形态（13/13 断言 PASS、focused tests 47/47，98e893e87）；预发 r8 双收据 fail-close NOT_RUN（dept-scope-friend-ext-local-console.txt 与 dept-scope-friend-ext-preprod-console.txt：offline dual receipt，remote_validation=NOT_RUN，grants_release_credit=false）；PREPROD_SERVICE_SCOPE_GAP 在案；station bot token GAP ⏳P6；committed verdict 维持 NOT_RUN。": 1,
    'PROVIDER_PUBLISHES_DENSITY_98_OBLIGATIONS_20261031': 1,
    "row #20 story_state_machine_result_queue: register 34 source-anchored density obligations under the retained RESULT-QUEUE.* parent envelope (52 parsed = 18 legacy parents + 34 atoms; residual_count_gap=0); the owned-local executor catalog ASSERTION_IDS (34 ordered sites) equals the independent AST rebuild verbatim and every site keeps exactly one frozen parent owner; RESULT-QUEUE.delivery-policy-replacement stays the disclosed zero-atom parent (story lines 41-46 only mutate the in-memory policy dict; enforcement is observed by the scheduled-result-delivery atoms RQ.F21..RQ.F23); the seven dynamic wait predicates and the terminal marker print stay parent-charged, not leaf-charged; notes-only integration, the dual-result-queue-run envelope and its LOCAL_RESULT_QUEUE_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false": 1,
    "row #40 story_collaboration_draft_group_launch: register 75 source-anchored density obligations under the retained LOCAL-DRAFT-GROUP.* parent envelope (92 parsed = 17 legacy parents + 75 weighted atoms; residual_count_gap=0); the owned-local executor catalog ASSERTION_IDS (75 ordered weighted sites) equals the independent AST rebuild verbatim and every site keeps exactly one frozen parent owner; LOCAL-DRAFT-GROUP.group-launch-input stays the disclosed zero-atom parent (story lines 162-172 only build the launch body dict; enforcement is observed by the browser-context-submission-guards atoms DGL.F49..DGL.F51 and the group-launch-binding atoms DGL.F56..DGL.F64); the terminal marker print stays parent-charged; notes-only integration, the dual-draft-group-run envelope and its LOCAL_DRAFT_GROUP_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false": 1,
    "row #35 story_provider_owner_isolation: register the 24 source-anchored obligation roster (PROVIDER-OWNER.*; contract provider_owner_contract.py; upstream provider_scope_story.py frozen at f181ba448a8bd5844c9517d8543ff3b6bb9d66bb with story_checks=45; shell step map provider-owner-isolation-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-provider-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/provider-owner-stdout.json status=BLOCKED completed 0/24 error_type=LOCAL_PROVIDER_OWNER_BLOCKED (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_principal case DUAL-PROVIDER-OWNER-01 contract dual-provider-owner-case-run-v1 all 24 steps NOT_RUN): the story child registers provider prv_59ca9519 and bot bot_e38133ac, bootstraps the foreign Human, POST /providers/{id}/bots answers 200, then story request 8 PUT /bots/bot_09741ff7/visibility expects 200 and receives 403, the child raises AssertionError at provider_scope_story.py L117 and exits 1, the executor fail-closes at 0/24, and the full stderr traceback is retained as bounded evidence while the receipt error_detail stays null by design; this is the contract-frozen drift gap OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT (provider registration records created_by but mints no bcs-edge-permission authority edges on the owned-local runtime; the story frozen at f181ba44 predates the bot-authority model c6f001209 #2625; poolab preprod green there because the preprod runtime seeds the edges; story source apps/bcs/scripts/e2e-test/provider_scope_story.py exists locally and not on upstream dev; the BLOCKED receipt is the contract expectation, not a regression); preprod stays REMOTE_UNSUPPORTED by design (PREPROD_PROVIDER_ADMIN_FACE_ABSENT, WORKBENCH_WS_MOCK_ADMISSION) and the wp2 preprod disclosure verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/provider-owner/20261031-migration-wp2-provider-owner-preprod-disclosure-verdict.json stays BLOCKED (PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED, credential_admission BLOCKED PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE); the r1 root holds no provider-owner local leg receipt; notes-only integration, the dual-provider-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1; remote_validation=NOT_RUN and grants_release_credit=false": 1,
    "row #36 story_session_owner_permissions: register the 9 source-anchored obligation roster (SESSION-OWNER.*; contract session_owner/session_owner_contract.py; story_checks=60 plus cleanup_checks=4, frozen required assertion inventory 78 = run_http 60 + cleanup_http 4 + shell 1 + structural_python 13; shell step map session-owner-permissions-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r1 receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r1/session-owner-stdout.json is the preprod-tier probe leg and is NOT a local live leg (tier=preprod id=dual-session-owner-preprod-r7 entrypoint=gateway api_face=public-v1 credential_kind=preprod_human_cookie_route_probe run_id 20261031-owner-isolation-local-live-r1-session-owner): status=BLOCKED completed 0/9 error PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED, preprod probe BLOCKED PREPROD_PROBE_BASELINE_UNEXPECTED, four named gaps PREPROD_STATION_BOOTSTRAP_FACE_ABSENT PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY PREPROD_PARTICIPANT_ROUTES_UNBOUND, mirrored by wp2 verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/session-owner/20261031-owner-isolation-local-live-r1-session-owner-verdict.json (BLOCKED, remote_validation=NOT_RUN, grants_release_credit=false); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-session-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/session-owner-stdout.json status=PASS completed 9/9 LOCAL_SESSION_OWNER_PASS (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_gateway_principals case DUAL-SESSION-OWNER-01 contract dual-session-owner-case-run-v1): journal observed_assertion_count=64 against the frozen required inventory 78 (run_http 60 + cleanup_http 4 + shell 1 + structural_python 13 per contract UPSTREAM_ASSERTIONS), requirement_statuses 9/9 PASS, cleanup disposition SESSION_GROUP_AND_OWNED_BOTS_DELETED with zero process leak; notes-only integration, the dual-session-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1 (preprod verdict stays NOT_RUN honestly; neither leg grants release credit); remote_validation=NOT_RUN and grants_release_credit=false": 1,
    "row #9 story_operator_coordinates_with_cli: register 15 source-anchored density obligations under the retained OPERATOR-CLI.* parent envelope (28 parsed = 13 legacy parents + 15 atoms; residual_count_gap=0); the frozen shell span catalog OPCLI.F01..OPCLI.F15 (site envelope OPERATOR-CLI.*) rebuilds every parent call line (stories.sh 1786-1798) and every atom quote verbatim from the SHA-256-frozen cli.sh/cli-stories.sh bytes in story order, each leaf keeping exactly one frozen parent owner; OPERATOR-CLI.create-group stays the disclosed zero-atom parent (its group.sh helper surface lives outside the stories.sh/cli.sh/cli-stories.sh upstream parse face; field enforcement stays parent-charged with the frozen creator-scoped exact-delete disposition as evidence); the exit-zero fail guards, TESTS_* bookkeeping, ensure_cli_token skip guards, the visibility original-value capture guard, the upstream register prerequisite legs and the chat prerequisite in-case friendship admissions stay parent-charged, not leaf-charged; UPSTREAM_ISSUE=None disclosed (no committed issue pointer found); notes-only integration, the dual-operator-cli-run envelope and its LOCAL_OPERATOR_CLI_PASS owned-local conclusion stay untouched; committed verdict stays DUAL/BLOCKED_INVENTORIED with notes 0 to 1; preprod keeps the four named PREPROD_* gaps on file (five-bot health leg BLOCKED, r5 leaf-BLOCKED lineage, reviewed duo credentials unreviewed, full story not executed offline); remote_validation=NOT_RUN and grants_release_credit=false": 1,
    "row #1 story_user_prepares_agent_network: register 3 source-anchored density obligations under the retained NETWORK.* parent envelope (11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms; residual_count_gap=0); the 8 leg-PASS snapshot records align verbatim onto the first eight platform statements (identical description literals at stories.sh:269-280) and are absorbed one per leg by NETWORK.platform under the leg-verdict proxy caliber that the committed assertion-gap matrix itself labels approximate; the FAIL record and the two WARN records are one upstream manifest-asset-404 abort event anchored as AN.F01..AN.F03 at stories.sh:295 (emit chain common.sh assert_status 104-107 > assert_eq 68 > fail print 74), stories.sh:37 (require_status failure-branch response warn) and e2e.sh:210 (runner stopped warn); the 33 unreached actor/register static statements and the 11 unreached platform statements stay parent-charged with no fabricated leaves; notes-only integration, the dual-network-read-run/register-run/status-run envelopes and the committed DUAL/PASS r7 face stay untouched; committed verdict stays DUAL/PASS with notes 0 to 1; preprod density tier keeps the three named PREPROD_* gaps on file; remote_validation=NOT_RUN and grants_release_credit=false": 1,

}


def load_executor_migration() -> dict[str, dict]:
    """Load the single 41-row executor migration status source by story ID."""
    try:
        value = read(EXECUTOR_MIGRATION_PATH)
    except RunStateError:
        raise RunStateError(
            'executor migration status source is missing or unreadable') from None
    try:
        if (set(value) != {'schema_version', 'rows'}
                or not isinstance(value['rows'], list)
                or len(value['rows']) != 41):
            raise ValueError
        statuses = {}
        for item in value['rows']:
            if not isinstance(item, dict) or set(item) != {'id', 'executor_migration'}:
                raise ValueError
            story_id = identifier(item['id'], 'executor migration ID')
            if story_id in statuses:
                raise ValueError
            status = item['executor_migration']
            if (not isinstance(status, dict)
                    or set(status) != {'mode', 'preprod_verdict', 'gates', 'notes'}):
                raise ValueError
            mode, verdict = status['mode'], status['preprod_verdict']
            if ((mode, verdict) not in _VALID_EXECUTOR_MIGRATION_STATES
                    or mode not in EXECUTOR_MIGRATION_MODES
                    or verdict not in PREPROD_VERDICTS):
                raise ValueError
            gates, notes = status['gates'], status['notes']
            if (not isinstance(gates, list) or len(set(gates)) != len(gates)
                    or any(gate not in EXECUTOR_MIGRATION_GATES for gate in gates)
                    or not isinstance(notes, list) or len(set(notes)) != len(notes)
                    or any(note not in EXECUTOR_MIGRATION_NOTES for note in notes)):
                raise ValueError
            statuses[story_id] = {
                'mode': mode, 'preprod_verdict': verdict,
                'gates': list(gates), 'notes': list(notes),
            }
        states = Counter(
            (status['mode'], status['preprod_verdict']) for status in statuses.values())
        gates = Counter(
            gate for status in statuses.values() for gate in status['gates'])
        notes = Counter(
            note for status in statuses.values() for note in status['notes'])
        if (states != Counter(_EXPECTED_EXECUTOR_MIGRATION_COUNTS)
                or gates != Counter(_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS)
                or notes != Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)):
            raise ValueError
        return statuses
    except (KeyError, TypeError, ValueError, ConfigError):
        raise RunStateError(
            'executor migration status source is invalid or drifted') from None


def legacy_inventory_with_executor_migration(manifest: dict | None = None) -> list[dict]:
    """Return every legacy row with its executor_migration object joined by ID."""
    value = current_manifest() if manifest is None else manifest
    try:
        legacy = value['legacy_inventory']
        if not isinstance(legacy, list) or any(
                not isinstance(row, dict) or 'id' not in row for row in legacy):
            raise ValueError
        statuses = load_executor_migration()
        legacy_ids = [row['id'] for row in legacy]
        if len(legacy_ids) != len(set(legacy_ids)):
            raise ValueError
        missing = set(legacy_ids) - set(statuses)
        extra = set(statuses) - set(legacy_ids)
        if missing or extra:
            raise RunStateError(
                'executor migration status join is incomplete: '
                f'missing={len(missing)} extra={len(extra)}')
        rows = []
        for row in legacy:
            status = statuses[row['id']]
            enriched = dict(row)
            enriched['executor_migration'] = {
                'mode': status['mode'],
                'preprod_verdict': status['preprod_verdict'],
                'gates': list(status['gates']),
                'notes': list(status['notes']),
            }
            rows.append(enriched)
        return rows
    except (KeyError, TypeError, ValueError):
        raise RunStateError('executor migration status join is invalid') from None


def reviewed_release_inventory(manifest: dict | None = None) -> list[dict]:
    """Return the reviewed 31-Story release denominator from the current 41-row legacy inventory.

    Ten legacy parents sit outside the reviewed release denominator: the
    pre-existing backlog rows plus five registered 2026-10-29 during the
    harness re-freeze (long-unregistered upstream stories). They are known
    backlog, not silently missing or absorbed into this release denominator.
    """
    value = current_manifest() if manifest is None else manifest
    legacy = legacy_inventory_with_executor_migration(value)
    ids = [row['id'] for row in legacy]
    reviewed = [row for row in legacy if row['id'] not in KNOWN_UNMAPPED_LEGACY_IDS]
    if len(ids) != len(set(ids)) or len(reviewed) != REVIEWED_RELEASE_PARENT_COUNT:
        raise RunStateError(
            'reviewed release denominator changed; review the known '
            'unmapped stories separately')
    return reviewed


def binding_matches_reviewed_release(binding_inventory: object) -> bool:
    """Guard: a frozen binding inventory covers the reviewed 31-story denominator.

    Bindings frozen before the reviewed cut carry the whole 41-row manifest
    snapshot, so any id set between the reviewed denominator and the full
    manifest is admitted; every row id must come from the manifest and be
    distinct. The ten known-unmapped backlog rows are exempt from drift
    detection by design; the reviewed rows are the enforced surface.
    """
    try:
        if not isinstance(binding_inventory, list) or not binding_inventory:
            return False
        ids = [identifier(row['id'], 'binding legacy inventory row ID') for row in binding_inventory]
        if len(ids) != len(set(ids)):
            return False
        manifest_ids = {row['id'] for row in current_manifest()['legacy_inventory']}
        reviewed_ids = {row['id'] for row in reviewed_release_inventory()}
        return reviewed_ids <= set(ids) <= manifest_ids
    except (KeyError, TypeError, ValueError, ConfigError, RunStateError):
        return False


def binding_freezes_reviewed_release_rows(binding_inventory: object) -> bool:
    """Guard: the reviewed rows inside a frozen binding match the manifest verbatim.

    Stronger than the id-coverage guard: each reviewed row (all fields, in
    manifest order) must be byte-equal to the manifest snapshot. Backlog rows
    stay exempt so pre-cut bindings remain readable; reviewed drift fails closed.
    """
    try:
        if not isinstance(binding_inventory, list) or not binding_inventory:
            return False
        ids = [identifier(row['id'], 'binding legacy inventory row ID') for row in binding_inventory]
        if len(ids) != len(set(ids)):
            return False
        manifest = current_manifest()['legacy_inventory']
        manifest_ids = {row['id'] for row in manifest}
        reviewed_ids = {row['id'] for row in reviewed_release_inventory()}
        mapping = {row['id']: row for row in binding_inventory}
        if not reviewed_ids <= set(ids) <= manifest_ids:
            return False
        base = [row for row in manifest if row['id'] in reviewed_ids]
        return [mapping[row['id']] for row in base] == base
    except (KeyError, TypeError, ValueError, ConfigError, RunStateError):
        return False


def assert_reviewed_parent(parent_id: str, *, family: str) -> str:
    """Fail closed unless a dual parent is a reviewed release parent.

    Known unmapped backlog parents stay executable and are named explicitly;
    anything outside the reviewed release denominator or the unmapped backlog
    is rejected before any binding is frozen.
    """
    reviewed = {row['id'] for row in reviewed_release_inventory()}
    if not isinstance(parent_id, str) or (
            parent_id not in reviewed and parent_id not in KNOWN_UNMAPPED_LEGACY_IDS):
        raise RunStateError(
            family + ' parent is outside the reviewed release denominator')
    return parent_id


def current_manifest(suite: str = 'preprod-pilot-v1') -> dict:
    paths = {'preprod-pilot-v1': 'manifest.json', 'preprod-retained-v1': 'retained-manifest.json',
             'preprod-retained-file-v1': 'retained-file-manifest.json',
             'owned-local-trusted-team-v1': 'trusted-team-manifest.json'}
    if suite not in paths:
        raise RunStateError('unsupported executor suite')
    manifest = validate_manifest(read(ROOT / paths[suite]))
    if manifest['suite'] != suite:
        raise RunStateError('executor manifest has a different suite identity')
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    return manifest


def executor_digest() -> str:
    try:
        return process_digest()
    except (ValueError, OSError):
        raise RunStateError('executor artifact differs from the process-import snapshot') from None


def freeze_manifest(state_dir: Path, manifest: dict) -> str:
    manifest = validate_manifest(manifest)
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    path = state_dir / 'manifest.json'
    digest = fingerprint(manifest)
    if path.exists() or path.is_symlink():
        if fingerprint(validate_manifest(read(path))) != digest:
            raise RunStateError('existing Run manifest differs; never overwrite its inventory')
        return digest
    payload = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if len(payload) > MAX_STATE_BYTES:
        raise RunStateError('manifest exceeds the supported size limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return digest


def frozen_manifest(state_dir: Path, state: dict) -> dict:
    manifest = validate_manifest(read(state_dir / 'manifest.json'))
    if state.get('manifest_digest') != fingerprint(manifest):
        raise RunStateError('Run manifest digest is missing or mismatched')
    artifact = state.get('executor_digest')
    if not isinstance(artifact, str) or len(artifact) != 64 or any(c not in '0123456789abcdef' for c in artifact):
        raise RunStateError('Run executor binding is missing or invalid')
    return manifest


def executable_case(state_dir: Path, state: dict, case_id: str) -> dict:
    manifest = frozen_manifest(state_dir, state)
    if fingerprint(current_manifest(manifest['suite'])) != state['manifest_digest']:
        raise RunStateError('Case inventory changed after prepare; start a new Run')
    if executor_digest() != state['executor_digest']:
        raise RunStateError('executor artifact changed after prepare; start a new Run')
    for case in manifest['cases']:
        if case['id'] == case_id:
            return case
    raise RunStateError('Case is absent from the frozen Run inventory')


# --- Finite evidence contracts (consolidated from evidence_policy.py) ---
# Admission only: declarations are not observation receipts or qualification.
# Historical snapshots remain readable without applying today's execution policy.

_REQUIRED = {
    'owned-local-trusted-team-v1': {
        'LOCAL-TRUST-API-CLI-01': {'api', 'cli', 'sqlite'},
    },
    'preprod-pilot-v1': {
        'PRE-01': {'api', 'ob'},
        'PRE-02': {'api', 'websocket', 'ob'},
        'PRE-03': {'api', 'bytes', 'ob'},
    },
    'preprod-retained-v1': {
        'RETAIN-GROUP-01': {'api', 'ob'},
        'RETAIN-SESSION-01': {'api', 'websocket', 'ob'},
    },
    'preprod-retained-file-v1': {
        'RETAIN-FILE-01': {'api', 'bytes', 'ob'},
    },
}
_DIAGNOSTIC = {'request_correlation', 'logs'}


def validate_evidence_policy(manifest: dict) -> None:
    """Reject unsupported new execution, not historical schema or rendering.

    ``ob`` names the semantic persistence requirement in the shared inventory;
    the owned-local adapter explicitly substitutes SQLite, never remote OB.
    New mandatory sources require an executor/preflight contract, not just JSON.
    """
    contracts = _REQUIRED.get(manifest['suite'])
    if contracts is None:
        raise RunStateError('unsupported suite evidence contract')
    for case in manifest['cases']:
        required = set(case['required_evidence'])
        diagnostic = case.get('diagnostic_evidence')
        if (case['id'] not in contracts or required != contracts[case['id']]):
            raise RunStateError('Case required evidence differs from its executor contract')
        if (not isinstance(diagnostic, list) or len(diagnostic) > len(_DIAGNOSTIC)
                or any(not isinstance(item, str) or item not in _DIAGNOSTIC for item in diagnostic)
                or len(set(diagnostic)) != len(diagnostic)
                or required.intersection(diagnostic)):
            raise RunStateError('Case diagnostic evidence is invalid or unsupported')

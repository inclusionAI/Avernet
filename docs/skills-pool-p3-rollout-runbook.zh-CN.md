# Skills Pool P3 灰度发布与验收手册

本文描述 #378 提供的运维入口和人工晋级规则。所有接口均位于
`/api/ops/skills-pool`，仅 operator 可访问，并且只操作 Backend 当前部署环境；
调用方不能通过参数跨环境写配置。

## 发布前置

生产环境不由 Backend 自动建表。发布新 Backend 前，必须先通过 OceanBase
数据库变更流程创建或升级 `ac_skill_migration_quarantine` 和
`ac_skills_pool_rollout_audit`，并分别执行 `SELECT 1 ... LIMIT 1` 作为发布
preflight；任一表不存在或不可读写时禁止开启 rollout。Avernet 仓库只维护
ORM，数据库变更单需使用以下与 ORM 一致的 DDL：

```sql
CREATE TABLE ac_skill_migration_quarantine (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT
    COMMENT '自增主键',
  env VARCHAR(20) NOT NULL
    COMMENT '部署环境，如 pre、prod',
  entity_id VARCHAR(512) NOT NULL
    COMMENT 'Bot 所属实体或用户标识',
  bot_id VARCHAR(128) NOT NULL
    COMMENT 'Bot 唯一标识',
  migration_generation VARCHAR(64) NOT NULL
    COMMENT '本次 Skills Pool 迁移代际标识',
  engine VARCHAR(64) NOT NULL
    COMMENT 'Bot 使用的引擎类型',
  path VARCHAR(1024) NOT NULL
    COMMENT '运行时返回的隔离目录物理路径',
  status VARCHAR(32) NOT NULL DEFAULT 'retained'
    COMMENT '隔离记录状态',
  source_evidence TEXT NOT NULL
    COMMENT 'Pool cutover 时记录的隔离证据 JSON',
  pool_activated_at TIMESTAMP NULL DEFAULT NULL
    COMMENT 'Bot 成功进入 POOL_ACTIVE 的时间',
  runtime_reconciled_at TIMESTAMP(6) NULL DEFAULT NULL
    COMMENT 'Pool 激活后运行时完成 reconciliation 的时间',
  runtime_reconciliation_status VARCHAR(16) NULL
    COMMENT '运行时 reconciliation 结果',
  runtime_evidence TEXT NULL
    COMMENT '运行时 reconciliation 证据 JSON',
  cleaned_at TIMESTAMP NULL DEFAULT NULL
    COMMENT '隔离目录完成清理的时间',
  cleanup_evidence TEXT NULL
    COMMENT '隔离目录清理结果证据 JSON',
  cleanup_lease_owner VARCHAR(128) NULL
    COMMENT '当前持有清理租约的 Worker 标识',
  cleanup_lease_expires_at TIMESTAMP NULL DEFAULT NULL
    COMMENT '清理任务租约过期时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    COMMENT '记录创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP
    COMMENT '记录最后修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_skill_migration_quarantine_scope_generation
    (env, entity_id, bot_id, migration_generation) GLOBAL,
  KEY idx_skill_migration_quarantine_cleanup
    (env, status, pool_activated_at) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'Skills Pool 迁移隔离目录生命周期及清理证据';

CREATE TABLE ac_skills_pool_rollout_audit (
  id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT
    COMMENT '自增主键',
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT '数据隔离租户；既有内部数据归属 teamclaw',
  env VARCHAR(20) NOT NULL
    COMMENT 'Rollout 配置所属部署环境',
  config_id BIGINT(20) UNSIGNED NOT NULL
    COMMENT '关联的 ac_common_config 配置记录 ID',
  action VARCHAR(128) NOT NULL
    COMMENT '运维操作类型',
  batch_id VARCHAR(128) NULL
    COMMENT '关联的灰度批次标识',
  operator VARCHAR(128) NOT NULL
    COMMENT '执行本次操作的人员标识',
  reason VARCHAR(512) NOT NULL
    COMMENT '执行本次操作的原因',
  based_on_config_version VARCHAR(64) NULL
    COMMENT '修改前配置版本',
  effective_config_version VARCHAR(64) NOT NULL
    COMMENT '修改后生效的配置版本',
  evidence TEXT NULL
    COMMENT '批次验收报告或其他操作证据 JSON',
  effective_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    COMMENT '本次配置变更的业务生效时间',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    COMMENT '审计记录的数据库创建时间',
  gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP
    COMMENT '审计记录的数据库修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_skills_pool_rollout_audit_tenant_revision
    (avernet_tenant, env, effective_config_version) GLOBAL,
  KEY idx_skills_pool_rollout_audit_batch
    (env, batch_id, id) GLOBAL
) DEFAULT CHARSET = utf8mb4
  COMMENT = 'Skills Pool 灰度配置变更追加式审计记录';

-- 已存在上述两表的环境不得重复 CREATE。只有 rollout audit 是 tenant
-- 隔离表：发布读取该列的 Backend 前，按以下顺序升级 audit 表。
ALTER TABLE ac_skills_pool_rollout_audit
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT '数据隔离租户；既有内部数据归属 teamclaw';

ALTER TABLE ac_skills_pool_rollout_audit
  ADD UNIQUE KEY uk_skills_pool_rollout_audit_tenant_revision
    (avernet_tenant, env, effective_config_version) GLOBAL;

ALTER TABLE ac_skills_pool_rollout_audit
  DROP INDEX uk_skills_pool_rollout_audit_revision;

SELECT 1 FROM ac_skill_migration_quarantine LIMIT 1;
SELECT 1 FROM ac_skills_pool_rollout_audit LIMIT 1;
SELECT COUNT(*) AS null_tenant_rows
  FROM ac_skills_pool_rollout_audit WHERE avernet_tenant IS NULL;
SHOW INDEX FROM ac_skill_migration_quarantine;
SHOW INDEX FROM ac_skills_pool_rollout_audit;
```

数据库变更顺序固定为：建表并 preflight → 发布 Backend → 保持 feature disabled
→ 验证只读运维接口 → 开始 OpenClaw 白名单。代码回滚不删除审计表。

## 灰度顺序

1. 通过 `GET /rollout` 核对当前环境、Policy schema、revision、feature、
   Engine Admission Switch 和当前 Bot/Owner/Environment 规则。
2. 首次从 v1 切换前保存原始配置；新 Policy 的第一次成功写入会把当前
   v1 精确 Bot 条目解析为 Bot 当前 Engine，并在同一个 CAS 事务升级成 v2。
3. 通过 `POST /rollout/feature` 启用全局 feature；请求必须携带刚刚读取的
   `expected_revision`（配置不存在时显式传 `null`）。
4. 通过 `PUT /rollout/engines/openclaw/admission` 打开 OpenClaw Engine
   Admission Switch。该开关优先于所有 allow 规则；关闭只阻止新 claim，
   不取消已 claim Bot。
5. 使用下列互相独立、全部按 Engine 隔离的规则逐步扩大：
   - `PUT|DELETE /rollout/bots/{bot_id}/allow`：精确 Bot；
   - `PUT|DELETE /rollout/bots/{bot_id}/exclude`：最高优先级精确排除；
   - `PUT|DELETE /rollout/owners/{owner_id}`：Owner + Engine；
   - `PUT|DELETE /rollout/environments/{engine}`：当前环境的指定 Engine。
6. 每次写入后重新 `GET /rollout` 获取新 revision；不能用旧 revision 连续写。
7. 容器生命周期事件会正常唤醒迁移；需要主动触发时使用
   `POST /bots/{bot_id}/wake`。只有已持久化为可重试失败的 Bot 才使用
   `/retry`。
8. 通过 `GET /bots/{bot_id}` 检查单 Bot 的 Engine、claim/layout、
   `policy_revision` 和 `admission_reason`。历史
   `GET /batches/{batch_id}?engine=...` 只用于旧数据排障，不参与新扩量决策。

示例：为员工 `168944` 开启预发 OpenClaw Owner rollout：

```json
{
  "engine": "openclaw",
  "expected_revision": "<GET /rollout returned revision>",
  "reason": "OpenClaw owner validation passed in pre"
}
```

该请求只写当前 Backend 所属环境的 `owner_rollouts`。它不会立即扫描并
重启员工名下所有 Bot；后续创建、重启、ARCA alive、BaaS publish-completed
或人工 wake 事件会触发首次认领。

移除 allow 规则后，未认领 Bot 将不再开始迁移；已经认领或已经 Pool-active
的 Bot 保持同一 `migration_generation` 前滚，不会因策略收缩而回退。

所有配置写请求必须附带非空 `reason` 和 `expected_revision`。发生并发修改时
返回 `409 POLICY_REVISION_CONFLICT`，操作者必须重新读取。每次成功变更继续在
`ac_skills_pool_rollout_audit` 追加 action、Engine/目标 evidence、操作者、原因、
前后 revision 和生效时间；新事件 `batch_id` 为空。配置与审计事件同事务提交。

## 历史 Batch 兼容

- 新 Backend 兼容读取 v1/v2，但所有新写只产生 v2。
- `POST /rollout/promote|full|whitelist|whitelist/remove|owners|batches/accept|controls`
  统一返回 `410 ROLLOUT_BATCH_API_RETIRED`，不翻译、不双写。
- `GET /batches/{batch_id}` 暂时只读保留。确认运维脚本和 Postman Collection
  已迁移、连续一个发布周期无旧 API 流量后再删除。
- Backend 如需回滚到不识别 v2 的旧版本，必须同步恢复切换前备份的 v1 配置；
  只收缩业务放量时使用 Engine Admission Switch 或删除规则，不回滚代码。

## 人工恢复

- `/retry`：只重新投递 `last_failure_retryable=true` 的已认领迁移。
- `/repair`：提交 migration generation、人工核验结论和非空备注，复用
  `SkillsPoolRecoveryService` 恢复同一迁移代际。
- `/rollback`：提交 rollback generation 和非空备注，复用
  `SkillsPoolRollbackService` 从当前 Pool 内容显式重建 Legacy。

这些入口都先用当前环境中的精确 owner + Bot 解析真实
`(env, entity_id, bot_id)` scope；人工唤醒通过持久化任务队列交接。

## 兼容性证据

| 发布场景 | 自动化证据 |
|---|---|
| 新 Backend + 旧镜像无 marker，保持 Legacy | `test_reconcile_service.py::test_non_ready_runtime_keeps_legacy_without_data_plane_changes` |
| 新镜像已有 marker、未命中白名单，不认领 | `test_claim_service.py::test_ineligible_bot_does_not_persist_layout_state` |
| 精确命中后认领，移出白名单仍前滚 | `test_claim_service.py::test_claim_is_sticky_after_whitelist_removal` |
| Owner + Engine 规则覆盖未来新建与后续重启，且不影响其他 Owner/Engine | `test_rollout_gate.py` 的 v2 Engine-scoped allow cases 与 `test_operations.py::test_owner_environment_and_exclusion_rules_are_independent` |
| ONLINE Legacy 服务不原地迁移 | `test_claim_service.py::test_published_service_and_teclaw_do_not_claim` |
| 四个文件型引擎使用各自 Pool 路径和结构桥 | `test_reconcile_service.py::test_ready_claimed_bot_completes_pool_activation`、`::test_claude_code_uses_its_own_pool_paths_for_full_activation`、`::test_aicoding_uses_its_own_pool_paths_for_full_activation`、`::test_hermes_h0_ready_uses_its_own_pool_paths_for_full_activation` |
| 新旧镜像对应的不同 Bot 独立收敛：新镜像可激活，旧镜像保持 Legacy | `test_reconcile_service.py::test_mixed_image_bots_reconcile_independently_in_one_environment` |
| Teclaw 不进入文件系统迁移 | `test_rollout_gate.py` 与 `test_claim_service.py` 的 Teclaw no-op 测试 |
| 服务发布固定草稿布局并向容器传递 | `test_arca_snapshot_producer.py::test_pool_build_freezes_the_draft_layout_into_one_versioned_artifact` 与 `::test_release_translates_frozen_layout_into_container_env` |
| 服务重启、回滚和扩容继承冻结制品布局 | `test_publish_flow_service.py::test_restart_and_recreate_preserve_frozen_pool_layout`、`::test_execute_rollback_with_config_artifact` 与 `::test_scale_bot_success_prefers_bot_ext_device_count` |
| Engine Admission Switch 优先于所有 allow 规则 | `test_rollout_gate.py::test_v2_engine_switch_precedes_every_allow_rule` |
| 精确 exclusion 优先于 Bot、Owner 和 Environment allow | `test_rollout_gate.py::test_v2_exclusion_precedes_exact_owner_and_environment_rules` |
| v1 首次新写升级 v2，且不保留 Batch 门禁 | `test_operations.py::test_first_v2_write_converts_v1_without_batch_gates` |
| 旧 Batch 写 API 明确退出，新 Policy API 维持 operator-only | `test_skills_pool_ops_router.py::test_batch_write_routes_are_gone` 与 endpoint coverage |

镜像 preparation 脚本、Hermes H0 和各引擎 companion 的验收由对应镜像/引擎
仓库 CI 承担；本 Backend 报告只读取已提交的控制面与运行时证据，不推断容器
文件系统事实。

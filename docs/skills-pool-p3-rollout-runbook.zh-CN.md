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
  avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT '数据隔离租户；既有内部数据归属 teamclaw',
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
  UNIQUE KEY uk_skill_migration_quarantine_tenant_scope_generation
    (avernet_tenant, env, entity_id, bot_id, migration_generation) GLOBAL,
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

-- 已存在上述两表的环境不得重复 CREATE。发布读取 avernet_tenant 的 Backend
-- 前，按以下顺序升级：先加列，再建 tenant 前导唯一键，最后删除旧唯一键。
ALTER TABLE ac_skill_migration_quarantine
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT '数据隔离租户；既有内部数据归属 teamclaw';
ALTER TABLE ac_skills_pool_rollout_audit
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT '数据隔离租户；既有内部数据归属 teamclaw';

ALTER TABLE ac_skill_migration_quarantine
  ADD UNIQUE KEY uk_skill_migration_quarantine_tenant_scope_generation
    (avernet_tenant, env, entity_id, bot_id, migration_generation) GLOBAL;
ALTER TABLE ac_skills_pool_rollout_audit
  ADD UNIQUE KEY uk_skills_pool_rollout_audit_tenant_revision
    (avernet_tenant, env, effective_config_version) GLOBAL;

ALTER TABLE ac_skill_migration_quarantine
  DROP INDEX uk_skill_migration_quarantine_scope_generation;
ALTER TABLE ac_skills_pool_rollout_audit
  DROP INDEX uk_skills_pool_rollout_audit_revision;

SELECT 1 FROM ac_skill_migration_quarantine LIMIT 1;
SELECT 1 FROM ac_skills_pool_rollout_audit LIMIT 1;
SELECT COUNT(*) AS null_tenant_rows
  FROM ac_skill_migration_quarantine WHERE avernet_tenant IS NULL;
SELECT COUNT(*) AS null_tenant_rows
  FROM ac_skills_pool_rollout_audit WHERE avernet_tenant IS NULL;
SHOW INDEX FROM ac_skill_migration_quarantine;
SHOW INDEX FROM ac_skills_pool_rollout_audit;
```

数据库变更顺序固定为：建表并 preflight → 发布 Backend → 保持 feature disabled
→ 验证只读运维接口 → 开始 OpenClaw 白名单。代码回滚不删除审计表。

## 灰度顺序

1. 通过 `GET /rollout` 核对当前环境、配置版本、feature 状态、已晋级引擎、
   精确白名单和对照样本。
2. 通过 `POST /rollout/feature` 启用 rollout。初始配置固定
   `enable_all=false`，不存在通配入口。
3. 通过 `POST /rollout/promote` 按
   OpenClaw、Claude Code、AICoding、Hermes 的顺序人工晋级引擎。
   除首个 OpenClaw 外，请求必须携带上一引擎已通过验收的
   `acceptance_batch_id`；Backend 只接受已冻结的批次验收记录。
4. 通过 `POST /rollout/controls` 为当前批次登记至少一个同环境、同引擎、
   未命中白名单的负对照，以及一个 Teclaw 对照。
5. 通过 `POST /rollout/whitelist` 添加精确 `(owner_id, bot_id)` 条目和
   `batch_id`。批次扩大仍逐 Bot 执行，不提供 `enable_all`。
6. 容器生命周期事件会正常唤醒迁移；需要主动触发时使用
   `POST /bots/{bot_id}/wake`。只有已持久化为可重试失败的 Bot 才使用
   `/retry`。
7. 通过 `GET /bots/{bot_id}` 检查单 Bot 证据，通过
   `GET /batches/{batch_id}?engine=...` 生成批次验收报告。
8. 只有批次报告 `promotion_ready=true` 且人工确认实际业务指标后，才调用
   `POST /rollout/batches/accept` 冻结该批次的验收报告。扩大批次时，新
   `batch_id` 必须引用当前引擎最近的 `acceptance_batch_id`；晋级下一引擎
   也必须引用上一引擎已验收批次。Backend 不会自动验收、扩大或晋级。
9. 如需在当前环境内按员工覆盖其存量重启和未来新建 Bot，调用
   `POST /rollout/owners`，传入精确 `owner_id`、`engine`、`enabled` 和该
   引擎最近一次已验收的 `acceptance_batch_id`。规则按
   `(owner_id, engine)` 隔离，不会越过引擎晋级顺序，也不会影响其他员工。
   精确负对照优先于 owner 全量并保持 Legacy；关闭规则只阻止尚未认领的
   Bot，已认领 Bot 继续前滚。

示例：预发 OpenClaw 验收完成后，为员工 `168944` 开启 owner 全量：

```json
{
  "owner_id": "168944",
  "engine": "openclaw",
  "enabled": true,
  "acceptance_batch_id": "openclaw-pre-canary-1",
  "reason": "OpenClaw canary accepted; enable all owner bots in pre"
}
```

该请求只写当前 Backend 所属环境的 `full_rollout_owners`。它不会立即扫描并
重启员工名下所有 Bot；后续创建、重启、ARCA alive、BaaS publish-completed
或人工 wake 事件会触发首次认领。

移出白名单会返回 `claimed_before` 和 `claimed_after`。未认领 Bot 将不再开始
迁移；已经认领或已经 Pool-active 的 Bot 保持同一
`migration_generation` 前滚，不会因移出白名单而回退。

所有配置写请求必须附带非空 `reason`。写入使用配置 revision CAS；发生并发
修改时返回 409，操作者必须重新读取配置。每次成功变更都会在配置
`ac_skills_pool_rollout_audit` 独立追加环境隐含范围内的 action、batch、
操作者、原因、前后 revision、生效时间及批次验收快照；配置与审计事件在同一
事务中提交。`ac_common_config.ext_info` 只保存当前 revision 和最近操作摘要，
不承担审计历史。

## 验收报告

批次报告包含：

- rollout 配置 ID、版本、feature 状态和引擎晋级状态；
- `eligible`、`attempted`、`claimed`、`preparing`、`active`、
  `rolling_back`、`failed`；
- `success_rate` 和 `failure_distribution`；
- 数据不一致失败、负对照与 Teclaw 对照健康状态；
- 隔离区清理资格和最终 `promotion_ready`。

`data_consistent=false` 表示批次存在 `DATA_INCONSISTENT`、
`ROLLBACK_DATA_INCONSISTENT`、`ACTIVE_ENTRY_CONFLICT` 或
`MAPPING_DATA_INVALID`。任何失败、缺少对照、对照被认领或引擎未晋级都会使
`promotion_ready=false`。

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
| owner + engine 全量覆盖未来新建与后续重启，且不影响其他 owner/engine | `test_rollout_gate.py::test_owner_full_rollout_admits_future_and_restarted_bots_for_that_engine` 与 `test_operations.py::test_owner_full_rollout_requires_and_audits_latest_engine_acceptance` |
| ONLINE Legacy 服务不原地迁移 | `test_claim_service.py::test_published_service_and_teclaw_do_not_claim` |
| 四个文件型引擎使用各自 Pool 路径和结构桥 | `test_reconcile_service.py::test_ready_claimed_bot_completes_pool_activation`、`::test_claude_code_uses_its_own_pool_paths_for_full_activation`、`::test_aicoding_uses_its_own_pool_paths_for_full_activation`、`::test_hermes_h0_ready_uses_its_own_pool_paths_for_full_activation` |
| 新旧镜像对应的不同 Bot 独立收敛：新镜像可激活，旧镜像保持 Legacy | `test_reconcile_service.py::test_mixed_image_bots_reconcile_independently_in_one_environment` |
| Teclaw 不进入文件系统迁移 | `test_rollout_gate.py` 与 `test_claim_service.py` 的 Teclaw no-op 测试 |
| 服务发布固定草稿布局并向容器传递 | `test_arca_snapshot_producer.py::test_pool_build_freezes_the_draft_layout_into_one_versioned_artifact` 与 `::test_release_translates_frozen_layout_into_container_env` |
| 服务重启、回滚和扩容继承冻结制品布局 | `test_publish_flow_service.py::test_restart_and_recreate_preserve_frozen_pool_layout`、`::test_execute_rollback_with_config_artifact` 与 `::test_scale_bot_success_prefers_bot_ext_device_count` |
| 批次必须有健康负对照、Teclaw 对照且无数据不一致 | `test_operational_query.py` |
| 同一引擎扩大批次和跨引擎晋级都必须引用已冻结验收 | `test_operations.py::test_next_batch_requires_latest_persisted_acceptance` 与 `::test_next_engine_requires_and_audits_a_passing_batch` |

镜像 preparation 脚本、Hermes H0 和各引擎 companion 的验收由对应镜像/引擎
仓库 CI 承担；本 Backend 报告只读取已提交的控制面与运行时证据，不推断容器
文件系统事实。

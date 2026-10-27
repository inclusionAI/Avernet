-- 036_bot_authority.sql — Bot owner/manager authority (spec §4).
-- 2026-10-10 Bot authority design: edge_grants owner/manager kinds,
-- bcs_bots.ownership_version, bot_ownership_transfers,
-- bot_authority_changes. Applied by the bcs-admin migrate tool
-- (ops/CI); never edit earlier migration files.
--
-- Identity columns on the NEW tables use the binary collation
-- utf8mb4_bin so distinct principals stay distinct (spec §3.2.6).
-- Existing-table actor columns keep their collation; the authority
-- reads compare byte-exactly in SQL (CAST ... AS BINARY), matching
-- the friend-list reader precedent. MySQL-only statements here are
-- CI-verified (feasibility record §5.4 U7: index length, generated
-- column collation behavior).

-- === bcs_bots.ownership_version ==========================================
-- 0 = uninitialized (denies Human management); 1 after first
-- initialization; +1 per accepted transfer. Clients never submit it.
ALTER TABLE bcs_bots
    ADD COLUMN ownership_version BIGINT NOT NULL DEFAULT 0;

-- === edge_grants grant_kind width =====================================
-- 014 sized grant_kind VARCHAR(16) for
-- 'permission_profile' | 'rules', but that value is 17
-- characters: MySQL strict mode rejects a real
-- permission_profile edge with ERROR 1406. The authority
-- kinds (owner / manager) write the same column, so widen it
-- before the new unique key below references it.
ALTER TABLE edge_grants
    MODIFY COLUMN grant_kind VARCHAR(32) NOT NULL
        COMMENT 'permission_profile | rules | owner | manager';

-- === edge_grants unique key ==============================================
-- The old key (from_id, to_id, env, grant_ref_id) cannot carry
-- owner/manager edges: both use grant_ref_id = 0 for the same
-- (from, to) pair, and owner/manager must coexist, so the key gains
-- grant_kind. The swap is ONE ALTER statement — MySQL applies the
-- DROP and ADD atomically under the same metadata lock, so no
-- concurrent old-writer can ever observe the unprotected window a
-- two-statement swap would open. Existing rows always satisfy the
-- new key: any 5-column duplicate is also an old-key duplicate,
-- which the old constraint never allowed.
ALTER TABLE edge_grants
    DROP INDEX uk_edge_from_to_env_ref,
    ADD UNIQUE KEY uk_edge_from_to_env_kind_ref
        (from_id, to_id, env, grant_kind, grant_ref_id);

-- === single approved owner per (env, to_id) ==============================
-- Nullable generated-column unique index (spec §4.1): non-NULL only
-- for approved owner edges, so every other row stays NULL and free.
-- The generated value is forced to the binary collation so owner
-- bot ids never case-fold together.
ALTER TABLE edge_grants
    ADD COLUMN authority_owner_log VARCHAR(256) COLLATE utf8mb4_bin
        GENERATED ALWAYS AS
            (CASE WHEN grant_kind = 'owner' AND status = 'approved'
                  THEN CONVERT(to_id USING utf8mb4) COLLATE utf8mb4_bin END) VIRTUAL,
    ADD UNIQUE KEY uk_edge_approved_owner (env, authority_owner_log);

-- Authority read indexes (role/roles_for/managers bounded reads;
-- EXPLAIN coverage is the plan §6 MySQL gate).
ALTER TABLE edge_grants
    ADD INDEX idx_edge_bot_kind_status (env, to_id, grant_kind, status);

-- === bot_ownership_transfers =============================================
-- Reusable current-state row per (env, bot_id, target_owner_actor_id)
-- (spec §4.3): one row per target holding its latest round; pending
-- uniqueness per bot via a nullable generated column.
CREATE TABLE bot_ownership_transfers (
  `id`                        BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK；自增 bigint',
  `env`                       VARCHAR(64)  NOT NULL COMMENT '环境标签',
  `bot_id`                    VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '物理 Bot ID',
  `target_owner_actor_id`     VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '目标 owner Human actor id（API 定位键之一）',
  `from_owner_actor_id`       VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '本轮发起时的 owner Human actor id',
  `expected_ownership_version` BIGINT      NOT NULL COMMENT '发起时读取的 ownership_version（ABA CAS 依据）',
  `status`                    VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending | accepted | rejected | cancelled | expired | invalidated',
  `requested_at`              timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本轮请求时间（DB 时间）',
  `expires_at`                timestamp    NOT NULL COMMENT 'requested_at + 7 天（DB 时间）',
  `updated_at`                timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次状态更新（DB 时间）',
  `decided_at`                timestamp    NULL DEFAULT NULL COMMENT '决议时刻；pending 为 NULL',
  `decided_by_kind`           VARCHAR(16)  NULL DEFAULT NULL COMMENT 'human | service | provider | system',
  `decided_by_id`             VARCHAR(256) NULL DEFAULT NULL COMMENT '实际决议者 ID',
  `reason`                    TEXT         NULL DEFAULT NULL COMMENT '决议理由',
  `result_ownership_version`  BIGINT       NULL DEFAULT NULL COMMENT 'accepted 后的版本；其他状态为 NULL',
  `bot_name_snapshot`         VARCHAR(1024) NULL DEFAULT NULL COMMENT '最小收件箱展示用 Bot 名快照（与 bcs_bots.name 同宽，写入路径原样复制不截断）',
  `pending_log`               VARCHAR(256) COLLATE utf8mb4_bin
        GENERATED ALWAYS AS
            (CASE WHEN status = 'pending'
                  THEN CONVERT(bot_id USING utf8mb4) COLLATE utf8mb4_bin END) VIRTUAL COMMENT 'pending 唯一约束的生成列',
  `gmt_create`                timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`              timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_transfer_target` (`env`, `bot_id`, `target_owner_actor_id`) COMMENT '同目标只有一行，保存最近一轮',
  UNIQUE KEY `uk_transfer_pending` (`env`, `pending_log`) COMMENT '每 (env, bot) 至多一条 pending',
  KEY `idx_transfer_received` (`env`, `target_owner_actor_id`, `status`, `requested_at`) COMMENT '收件箱：目标 Human/状态/时间',
  KEY `idx_transfer_sent` (`env`, `from_owner_actor_id`, `status`, `requested_at`) COMMENT '发件箱：发起 Human/状态/时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === bot_authority_changes ===============================================
-- Permission-scope audit only (spec §4.4): one row per change with a
-- server mutation id + monotonic sequence. Writers MUST chunk audit
-- VALUES to ≤99 rows per statement (SQLite 999-bind limit, F1) and
-- share one transaction with the data change.
CREATE TABLE bot_authority_changes (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'PK',
  `env`           VARCHAR(64)  NOT NULL COMMENT '环境标签',
  `bot_id`        VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '发生变更的 Bot',
  `mutation_id`   VARCHAR(64)  NOT NULL COLLATE utf8mb4_bin COMMENT '服务端生成的 mutation ID',
  `sequence`      BIGINT       NOT NULL COMMENT '同一 mutation 内的单调序号',
  `action`        VARCHAR(32)  NOT NULL COMMENT 'manager_grant | manager_revoke | manager_restore | manager_sync | transfer_request | transfer_refresh | transfer_decide | ownership_initialize | bot_retire | pending_close 等',
  `actor_kind`    VARCHAR(16)  NOT NULL COMMENT 'human | service | provider | system',
  `actor_id`      VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '实际操作者 ID（不构成授权来源）',
  `subject_id`    VARCHAR(256) NOT NULL COLLATE utf8mb4_bin COMMENT '变更主体（如 manager actor id / 目标 owner）',
  `before_state`  TEXT         NULL DEFAULT NULL COMMENT '必要的前状态（JSON 字符串）',
  `after_state`   TEXT         NULL DEFAULT NULL COMMENT '必要的后状态（JSON 字符串）',
  `gmt_create`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（DB 时间）',
  `gmt_modified`  timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_change_mutation_seq` (`env`, `mutation_id`, `sequence`) COMMENT '审计逐项还原键',
  KEY `idx_change_bot` (`env`, `bot_id`, `sequence`) COMMENT '按 Bot 的审计扫描'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

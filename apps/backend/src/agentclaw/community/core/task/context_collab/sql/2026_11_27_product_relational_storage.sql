-- Five business tables; additive migration, NOT an automatic startup action.
-- Stop all writers and back up before applying. Requires the L4 baseline,
-- Task migration and the single-context_spec Collet baseline. MySQL/InnoDB dialect.
-- If the existing product_profile has profile_id/profile_version/definition_json,
-- first verify the archive name is unused, then execute ONCE:
-- RENAME TABLE ac_context_collab_product_profile
--   TO ac_context_collab_product_profile_legacy_v1;
-- Fresh installs skip that rename. Never overwrite either incompatible schema.
-- This migration does not delete historical tables or authorize runtime cutover.
-- Relationships use indexed IDs only; integrity is enforced by application code.

CREATE TABLE ac_context_collab_product_profile (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'Profile 主键，自增',
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL COMMENT '租户标识',
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL COMMENT '环境标识',
    name VARCHAR(256) NOT NULL COMMENT 'Profile 名称',
    description VARCHAR(2048) NOT NULL DEFAULT '' COMMENT 'Profile 描述',
    config TEXT NOT NULL COMMENT '版本化完整 YAML，唯一执行配置来源',
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL DEFAULT 'DRAFT' COMMENT 'Profile 状态：DRAFT/ONLINE/OFFLINE',
    creator VARCHAR(1024) COLLATE utf8mb4_bin NULL COMMENT '创建人稳定标识；历史来源未知可空',
    modifer VARCHAR(1024) COLLATE utf8mb4_bin NULL COMMENT '最后修改人稳定标识；按约定拼写，历史未知可空',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    KEY idx_cc_profile_scope_name (avernet_tenant, env, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

ALTER TABLE ac_context_collab_collet
    ADD COLUMN content MEDIUMTEXT NULL COMMENT 'Collet 业务内容',
    ADD COLUMN status VARCHAR(32) COLLATE utf8mb4_bin NULL COMMENT 'Collet 业务状态',
    ADD COLUMN product_profile_id BIGINT NULL COMMENT '关联的 ProductProfile 主键',
    ADD COLUMN current_rollout_id BIGINT NULL COMMENT '当前 ColletRollout 主键，可为空',
    ADD KEY idx_cc_collet_profile_status (avernet_tenant, env, product_profile_id, status, id);

CREATE TABLE ac_context_collab_collet_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'ColletRollout 主键，自增',
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL COMMENT '租户标识',
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL COMMENT '环境标识',
    collet_id BIGINT UNSIGNED NOT NULL COMMENT '物理 Collet 主键',
    ordinal INT NOT NULL COMMENT '本 Collet 内稳定顺序，从 1 开始',
    predecessor_id BIGINT NULL COMMENT '前驱 ColletRollout ID，首轮为 NULL',
    content MEDIUMTEXT NOT NULL COMMENT '本 Rollout 的业务输入内容',
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL COMMENT 'ColletRollout 执行状态',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_cc_rollout_order (collet_id, ordinal),
    UNIQUE KEY uk_cc_rollout_collet_id (collet_id, id),
    KEY idx_cc_rollout_predecessor (collet_id, predecessor_id),
    KEY idx_cc_rollout_scope_collet (avernet_tenant, env, collet_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

ALTER TABLE ac_context_collab_collet
    ADD KEY idx_cc_collet_current (id, current_rollout_id);

CREATE TABLE ac_context_collab_stage_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'StageRollout 主键，自增',
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL COMMENT '租户标识',
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL COMMENT '环境标识',
    collet_rollout_id BIGINT NOT NULL COMMENT '所属 ColletRollout 主键',
    name VARCHAR(256) NOT NULL COMMENT 'Stage 名称，必须来自 Profile YAML',
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL COMMENT 'Stage 执行状态',
    assignee_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL DEFAULT '' COMMENT '执行者类型，未分配为空字符串',
    assignee_uuid VARCHAR(256) COLLATE utf8mb4_bin NOT NULL DEFAULT '' COMMENT '执行者 UUID，未分配为空字符串',
    `input` MEDIUMTEXT NOT NULL COMMENT 'Stage 输入内容',
    session_id VARCHAR(512) COLLATE utf8mb4_bin NOT NULL DEFAULT '' COMMENT '真实会话 ID，未绑定为空字符串',
    acceptances VARCHAR(8192) NOT NULL COMMENT '严格 JSON 数组字符串，序列化后最多 8192 字符，空集合写 []',
    error_msg TEXT NOT NULL COMMENT '错误消息，UTF-8 字节最多 65535，空值为空字符串',
    artifacts MEDIUMTEXT NOT NULL COMMENT '严格 JSON 数组，空集合写 []',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    KEY idx_cc_stage_scope_rollout (avernet_tenant, env, collet_rollout_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

ALTER TABLE ac_context_collab_event
    ADD COLUMN collet_rollout_id BIGINT NULL COMMENT '关联的 ColletRollout 主键，可为空',
    ADD COLUMN collet_stage_rollout_id BIGINT NULL COMMENT '关联的 StageRollout 主键，可为空',
    ADD KEY idx_cc_event_rollout (avernet_tenant, env, collet_rollout_id, id),
    ADD KEY idx_cc_event_stage (avernet_tenant, env, collet_stage_rollout_id, id),
    ADD KEY idx_cc_event_stage_parent (collet_rollout_id, collet_stage_rollout_id);

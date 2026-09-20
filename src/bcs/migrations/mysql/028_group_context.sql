-- 028_group_context.sql  ·  Group Context 持久化表（MySQL）
-- Applied externally (ops/CI); the bcs binary runs only SQLite migrations
-- （仓库惯例，见 migrations/mysql/014_edge_permission.sql:3）。
-- 与 migrations/sqlite/028_group_context.sql 同构，类型按 MySQL 方言。
-- 字段决策注释见 sqlite 版同文件头，此处不重复啰嗦。

-- ─── 1. entry ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bcs_group_context_entry (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    env                VARCHAR(64)  NOT NULL,
    context_id         VARCHAR(128) NOT NULL,
    unique_id          VARCHAR(512) NOT NULL,
    content            TEXT         NOT NULL,
    tenant_id          VARCHAR(128) NOT NULL,
    group_id           VARCHAR(128),
    session_id         VARCHAR(128),
    run_id             VARCHAR(128),
    actor_id           VARCHAR(128) NOT NULL,
    valid_from         TIMESTAMP    NOT NULL,
    valid_to           TIMESTAMP    NULL,
    tx_time            TIMESTAMP    NOT NULL,
    supersedes         VARCHAR(128),
    superseded_reason  TEXT,
    policy_version     VARCHAR(32)  NOT NULL,
    gmt_create         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_gc_entry_env_id (env, context_id),
    KEY idx_gc_entry_active (env, unique_id, valid_to),
    KEY idx_gc_entry_domain (env, tenant_id, group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- ─── 2. policy ──────────────────────────────────────────────────────────
-- 见 sqlite 版说明：本轮不加 uk(context_unique_id, version)。
CREATE TABLE IF NOT EXISTS bcs_group_context_policy (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    env                 VARCHAR(64)  NOT NULL,
    context_unique_id   VARCHAR(512) NOT NULL,
    version             VARCHAR(32)  NOT NULL,
    domain              VARCHAR(128) NOT NULL,
    granularity         VARCHAR(16)  NOT NULL,
    collect_from_json   JSON         NOT NULL,
    visible_to_json     JSON         NOT NULL,
    freshness_class     VARCHAR(16)  NOT NULL,
    revalidate_due      TIMESTAMP    NULL,
    obligations         TEXT,
    gmt_create          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_gc_policy_ctx    (env, context_unique_id),
    KEY idx_gc_policy_domain (env, domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- ─── 3. template ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bcs_group_context_template (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    env                VARCHAR(64)  NOT NULL,
    creator            VARCHAR(128) NOT NULL,
    modifier           VARCHAR(128) NOT NULL,
    template_id        VARCHAR(128) NOT NULL,
    tenant_id          VARCHAR(128) NOT NULL,
    group_id           VARCHAR(128) NOT NULL DEFAULT '',  -- 空串哨兵（见 sqlite 版）
    domain             VARCHAR(128) NOT NULL,
    granularity        VARCHAR(16)  NOT NULL,
    collect_from_json  JSON         NOT NULL,
    visible_to_json    JSON         NOT NULL,
    freshness_class    VARCHAR(16)  NOT NULL,
    revalidate_due     TIMESTAMP    NULL,
    obligations        TEXT,
    params             TEXT,
    description        TEXT         NOT NULL,
    gmt_create         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_gc_tpl_scope (env, tenant_id, group_id, domain, granularity),
    UNIQUE KEY uk_gc_tpl_id    (env, template_id),
    KEY idx_gc_tpl_scope (env, tenant_id, group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- ─── 4. audit ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bcs_group_context_audit (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    env                   VARCHAR(64)  NOT NULL,
    actor_id              VARCHAR(128) NOT NULL,
    tenant_id             VARCHAR(128) NOT NULL,
    group_id              VARCHAR(128),
    session_id            VARCHAR(128),
    run_id                VARCHAR(128),
    domain                VARCHAR(128) NOT NULL,
    hit_context_ids_json  JSON         NOT NULL,
    tx_time               TIMESTAMP    NOT NULL,
    gmt_create            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_gc_audit_actor (env, actor_id, domain),
    KEY idx_gc_audit_time  (env, tx_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

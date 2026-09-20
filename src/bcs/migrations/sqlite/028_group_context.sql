-- 028_group_context.sql  ·  Group Context 持久化表（SQLite）
--
-- 对应 specs/2026-09-09-group-context-api/store-plan.md §2 的三张表 + 第 5 项审计表。
-- 与 services/bcs-group-context-store/src/lib.rs 的 SQL 对齐。
-- 仓库惯例：每业务表带 env 多租户分区前导列 + gmt_create / gmt_modified。

-- ─── 1. entry：context 条目本体（数据面 + 治理面 lineage）───────────────────
-- 关键字段决策（review 时对照 store-plan 变更记录 2026-09-20）：
--   * context_id  : 面向调用方的业务 UUID，application 层生成。
--   * unique_id   : 版本链主键，同链所有版本共享，按 ScopeKey::unique_id 拼成。
--   * supersedes  : 指向被取代旧条目的 context_id（UUID），正向链。
--   * actor_id    : 写入者，权限/审计核心。
--   * 无 superseded_by / superseded_at 反向链（已去掉）。
CREATE TABLE IF NOT EXISTS bcs_group_context_entry (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    env                TEXT    NOT NULL,
    context_id         TEXT    NOT NULL,
    unique_id          TEXT    NOT NULL,
    content            TEXT    NOT NULL,
    tenant_id          TEXT    NOT NULL,
    group_id           TEXT,
    session_id         TEXT,
    run_id             TEXT,
    actor_id           TEXT    NOT NULL,
    valid_from         TEXT    NOT NULL,        -- YYYY-MM-DD HH:MM:SS.mmm
    valid_to           TEXT,                     -- NULL 表活跃；被 supersede 回填
    tx_time            TEXT    NOT NULL,
    supersedes         TEXT,                     -- 被取代旧条目的 context_id
    superseded_reason  TEXT,
    policy_version     TEXT    NOT NULL,
    gmt_create         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_gc_entry_env_id      ON bcs_group_context_entry (env, context_id);
-- 版本链定位主索引：同 unique_id 的活跃版本（valid_to IS NULL）≤1 靠应用层 + 事务保证。
CREATE INDEX        IF NOT EXISTS idx_gc_entry_active     ON bcs_group_context_entry (env, unique_id, valid_to);
CREATE INDEX        IF NOT EXISTS idx_gc_entry_domain     ON bcs_group_context_entry (env, tenant_id, group_id);

-- ─── 2. policy：策略面快照（创建时从 template 实例化冻结，写入后不可变）────
-- store-plan §2.2 的 uk(context_unique_id, version) 会与「supersede 时 policy
-- 不变仍 always-insert 新行」相冲突：policy_version 不变时该组合键重复。
-- 取舍：本轮不加 uk，用普通 idx；待 store 优化成「policy 不变则复用旧 policy
-- 行、不新插」后，再补 UNIQUE (env, context_unique_id, version) 收紧。
CREATE TABLE IF NOT EXISTS bcs_group_context_policy (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    env                 TEXT    NOT NULL,
    context_unique_id   TEXT    NOT NULL,        -- 关联 entry.unique_id（同链共享）
    version             TEXT    NOT NULL,
    domain              TEXT    NOT NULL,
    granularity         TEXT    NOT NULL,
    collect_from_json   TEXT    NOT NULL,
    visible_to_json     TEXT    NOT NULL,
    freshness_class     TEXT    NOT NULL,
    revalidate_due      TEXT,
    obligations         TEXT,
    gmt_create          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gc_policy_ctx     ON bcs_group_context_policy (env, context_unique_id);
CREATE INDEX IF NOT EXISTS idx_gc_policy_domain  ON bcs_group_context_policy (env, domain);

-- ─── 3. template：策略模板定义（管理员预设，bot 通过 template_id 创建）──────
-- group_id NOT NULL DEFAULT ''：tenant 级模板（跨 group）用空串哨兵占位。
-- 原因：SQLite 唯一约束对 NULL 不参与去重，允许 NULL 会让 tenant 级模板绕过
-- uk（env, tenant_id, group_id, domain, granularity）；空串让约束真正生效。
CREATE TABLE IF NOT EXISTS bcs_group_context_template (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    env               TEXT    NOT NULL,
    creator           TEXT    NOT NULL,
    modifier          TEXT    NOT NULL,
    template_id       TEXT    NOT NULL,
    tenant_id         TEXT    NOT NULL,
    group_id          TEXT    NOT NULL DEFAULT '',
    domain            TEXT    NOT NULL,
    granularity       TEXT    NOT NULL,
    collect_from_json TEXT    NOT NULL,
    visible_to_json   TEXT    NOT NULL,
    freshness_class   TEXT    NOT NULL,
    revalidate_due    TEXT,
    obligations       TEXT,
    params            TEXT,
    description       TEXT    NOT NULL,
    gmt_create        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_gc_tpl_scope ON bcs_group_context_template (env, tenant_id, group_id, domain, granularity);
CREATE UNIQUE INDEX IF NOT EXISTS uk_gc_tpl_id    ON bcs_group_context_template (env, template_id);
CREATE INDEX        IF NOT EXISTS idx_gc_tpl_scope ON bcs_group_context_template (env, tenant_id, group_id);

-- ─── 4. audit：retrieve 强制落审计（store-plan 第 5 项，独立审计表）────────
-- 即使 retrieve 返回空集也写一条（「查询过但无可见内容」）。
CREATE TABLE IF NOT EXISTS bcs_group_context_audit (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    env                      TEXT    NOT NULL,
    actor_id                 TEXT    NOT NULL,
    tenant_id                TEXT    NOT NULL,
    group_id                 TEXT,
    session_id               TEXT,
    run_id                   TEXT,
    domain                   TEXT    NOT NULL,
    hit_context_ids_json     TEXT    NOT NULL,    -- JSON array，可能为 "[]"
    tx_time                  TEXT    NOT NULL,
    gmt_create               TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gc_audit_actor ON bcs_group_context_audit (env, actor_id, domain);
CREATE INDEX IF NOT EXISTS idx_gc_audit_time  ON bcs_group_context_audit (env, tx_time);

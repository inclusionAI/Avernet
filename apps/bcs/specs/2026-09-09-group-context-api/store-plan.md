# Group Context Storage — Implementation Plan

- **Date:** 2026-09-20
- **Spec:** `specs/2026-09-09-group-context-api/spec.md`
- **API Plan:** `specs/2026-09-09-group-context-api/plan.md`

---

## 1. 交付范围

三张持久化表 + 存储层实现。

---

## 2. DDL

### 2.1 bcs_group_context_entry

Context 条目本体。数据面 + 治理面（lineage）。

```sql
create table if not exist bcs_group_context_entry (
    id               bigint not null primary key,
    gmt_create       date not null default current_timestamp,
    gmt_modified     date not null default current_timestamp,
    unique_id        string not null,
    content          text not null,
    tenant_id        string nullable,
    group_id         string nullable,
    session_id       string nullable,
    run_id           string nullable,
    actor_id           string nullable,
    valid_from       date not null,
    valid_to         date nullable,
    tx_time          date not null,
    supersedes       string nullable,
    superseded_reason string nullable,
    policy_version   string not null
) idx(unique_id, valid_to);
```

**unique_id 计算规则：**
- granularity=tenant  → `"{tenant_id}:{domain}"`
- granularity=group   → `"{tenant_id}:{group_id}:{domain}"`
- granularity=session → `"{tenant_id}:{group_id}:{session_id}:{domain}"`
- granularity=run     → `"{tenant_id}:{group_id}:{session_id}:{run_id}:{domain}"`

**supersedes 维护规则：**
写入新版本时，系统自动查同 unique_id 下 `valid_to IS NULL` 的活跃版本 → 回填旧条目 `valid_to = tx_time` → 新条目 `supersedes` 指向旧条目 `id`。

### 2.2 bcs_group_context_policy

策略面快照。创建时从 template 快照，与 entry 一并写入，写入后不可变。

```sql
create table if not exist bcs_group_context_policy (
    id                bigint not null primary key,
    gmt_create        date not null default current_timestamp,
    gmt_modified      date not null default current_timestamp,
    context_unique_id string not null,
    version           string not null,
    domain            string not null,
    granularity       string not null,
    collect_from_json string not null,
    visible_to_json   string not null,
    freshness_class   string not null,
    revalidate_due    date nullable,
    obligations       string nullable
) uk(context_unique_id, version),
  idx(domain);
```

**policy_version 自动维护规则：**
- 首次创建 → `policy_version = "v0.1"`
- 后续更新（supersede）→ 系统比对新旧 context 的 policy 字段（collect_from_json / visible_to_json / freshness_class / revalidate_due / obligations）
  - 完全相同 → version 不变，不写入 policy 记录
  - 有差异 → 写入新 policy，version 按整数位递增（v0.1 → v0.2, v1.0 → v1.1）

### 2.3 bcs_group_context_template

模板定义。管理员预设，bot 通过 `template_id` 创建 context。

```sql
create table if not exist bcs_group_context_template (
    id                bigint not null primary key,
    gmt_create        date not null default current_timestamp,
    gmt_modified      date not null default current_timestamp,
    creator           string not null,
    modifier          string not null,
    template_id       string not null,
    tenant_id         string not null,
    group_id          string not null default '',
    domain            string not null,
    granularity       string not null,
    collect_from_json string not null,
    visible_to_json   string not null,
    freshness_class   string not null,
    revalidate_due    date nullable,
    obligations       string nullable,
    params            string nullable,
    description       string not null
) uk(tenant_id, group_id, domain, granularity),
  uk(template_id),
  idx(tenant_id, group_id);
```

> **`group_id` 非空哨兵：** `group_id` 不允许 NULL，tenant 级模板（跨 group 共享）用空串 `''` 占位。
> 原因：SQLite / MySQL 的唯一约束对 NULL 各算各的、不参与去重，若允许 NULL 则
> `uk(tenant_id, group_id, domain, granularity)` 在 tenant 级模板上失效，允许重复模板。
> 用哨兵空串让唯一约束对所有粒度都真正生效。

---

## 3. 并发安全

`supersede` 的"查活跃版本 → 回填 valid_to → 写入新条目"三步操作通过 `DbPlugin::transaction` 实现原子性。同 unique_id 下的并发写入由 transaction 级别的行锁保护，防止产出两个 `valid_to IS NULL` 的活跃版本。

---

## 4. 实现

新建 `services/bcs-group-context-store` crate，依赖 `bcs-db-api` + `bcs-db-local`，通过 `DbSqlFlavor` 适配 SQLite / MySQL 方言差异。

首期交付 SQLite（`LocalSqliteDbPlugin`）。

---

## 5. 变更记录

本表在合并后仍可能随实现推进调整。任何对既有 DDL / 维护规则的改动，追加一行（日期 + 原因），不静默回填。

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-09-20 | 初版。entry 表加 `actor_id`；`supersedes` 指向旧条目 `id`（非 `unique_id`）；去除 `superseded_by` / `superseded_at` 反向链；template 表 `group_id` 改 NOT NULL 默认空串 | 与 plan.md 对齐：`change_reason` 落新条目 `superseded_reason`；正向链足够可追溯；NULL 在唯一约束中不参与去重，需哨兵空串保证 uk 生效 |
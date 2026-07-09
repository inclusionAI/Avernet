# 治理反馈数据 Online → Offline 反向同步设计

> **版本**: v1.0 | **日期**: 2026-06-29
> **状态**: 🟡 待实施
> **前置**: OCB `bot_governance` 模块上线（`ac_governance_notify_log` + `ac_governance_whitelist` 开始写入）
> **下游**: `01-governance-effectiveness-monitor-design.md` 宽表 2 Section 3.9 扩展

---

## 1. 背景与动机

治理效果监控宽表（`governance_effectiveness_monitor_di`）当前 3.9 节只有两个字段：

```sql
notification_deliverable  BIGINT   -- 计算: analysis_status IN ('success','success_with_warnings')
governance_tags           STRING   -- 治理标签
```

这只回答"能不能投"，**不回答"投了没有"和"用户怎么反馈"**。缺少闭环数据：

- 通知实际发了没有？
- Bot Owner 选了什么（已优化 / 需时间 / 不认可 / 申请加白）？
- 反馈是什么时候来的？
- 谁被加白了？

这些数据只存在于 OCB 在线库的 `ac_governance_notify_log` 和 `ac_governance_whitelist` 两张表中。需要将它们同步回 ODPS，供 Monitor 宽表 ETL JOIN 消费。

## 2. 数据流全景

```
                          ┌──── 离线侧 (ODPS) ────┐
                          │                         │
  economy_governance      │  governance_analysis_daily_di
  管线产出 ─────────────→ │  governance_task_rec_daily_di
  (T+1 凌晨)              │                         │
                          └────────┬────────────────┘
                                   │
                          ┌──── 在线侧 (OCB) ──────┐
                          │                         │
  POST /offline-batch ──→ │  ac_governance_analysis_daily    (OceanBase)
                          │  ac_governance_task_rec_daily    (OceanBase)
                          │                         │
  Cron 扫描 + 用户反馈 ──→ │  ac_governance_notify_log        (OceanBase)
                          │  ac_governance_whitelist          (OceanBase)
                          │                         │
                          └────────┬────────────────┘
                                   │
                          ZDAS → ODPS 同步（已有基础设施）
                                   │
                          ┌──── 离线侧 (ODPS) ────┐
                          │                         │
                          │  governance_notify_log_di       ← 新建
                          │  governance_whitelist_di         ← 新建
                          │                         │
                          │  JOIN 进入 Monitor ETL   │
                          │                         │
                          │  governance_effectiveness_monitor_di
                          │  (Section 3.9 扩展)      │
                          └─────────────────────────┘
```

**关键**：Online → Offline 复用 ZDAS → ODPS 的已有同步通道，不新建基础设施。OCB 侧只需让这两张表写入 ZDAS 即可。

## 3. 源表定义（在线侧）

### 3.1 `ac_governance_notify_log`

| 字段 | 类型 | 说明 | 同步到 ODPS |
|---|---|---|---|
| id | BigInteger | PK | ✅ |
| notification_id | String(64) | 通知唯一 ID | ✅ |
| bot_id | String(64) | Bot ID | ✅ |
| bot_name | String(128) | 冗余展示 | ✅ |
| owner_id | String(64) | Bot Owner | ✅ |
| worker_id | String(128) | user_id:bot_id | ✅ JOIN key |
| dt | String(8) | 首次创建时的数据日期 | ✅ |
| governance_decision | String(32) | actionable/observe/justified | ✅ |
| hit_dimensions | String(512) | 命中维度 JSON | ✅ |
| hit_dimensions_count | Integer | 命中维度数 | ✅ |
| expected_token_saving | BigInteger | 预估节省 Token | ✅ |
| saving_ratio | Float | 节省比例 | ✅ |
| governance_max_dimension | String(64) | 最大优先级维度 | ✅ |
| governance_max_priority | String(8) | P0/P1 | ✅ |
| notify_status | String(16) | pending/sent/failed/cancelled | ✅ |
| response | String(32) nullable | 用户反馈 | ✅ **核心** |
| response_at | DateTime nullable | 反馈时间 | ✅ **核心** |
| response_remark | Text nullable | 用户备注 | ✅ |
| response_source | String(32) nullable | card_callback/http_api/system_auto | ✅ |
| task_create_key | String(128) | ODPS 幂等键 | ✅ JOIN key |
| latest_dt | String(8) | 最新分析数据日期 | ✅ **核心** |
| data_refresh_count | Integer | 数据刷新次数 | ✅ |
| dry_run | Integer | 0/1 | ✅ 过滤用 |
| gmt_create | DateTime | 创建时间 | ✅ |
| gmt_modified | DateTime | 修改时间 | ✅ |

**不同步的字段**：`notification_md`（Markdown 正文，体积大且离线已有 `notification_md` 在 analysis 表）、`notification_structured`（JSON 正文，同上）、`analysis_ref`（仅在线查询用）、`notify_type`/`notify_source`（固定值，无分析价值）、`entity_id`（敏感字段，工号不落离线宽表）。

### 3.2 `ac_governance_whitelist`

| 字段 | 类型 | 说明 | 同步到 ODPS |
|---|---|---|---|
| id | BigInteger | PK | ✅ |
| bot_id | String(64) | Bot ID | ✅ |
| owner_id | String(64) | Bot Owner | ✅ |
| governance_source | String(64) | system/owner/admin/manual | ✅ |
| reason | String(512) | 加白原因 | ✅ |
| created_by | String(64) | 操作人 | ✅ |
| expires_at | DateTime nullable | 过期时间 | ✅ |
| gmt_create | DateTime | 创建时间 | ✅ |

## 4. 目标表定义（ODPS 侧）

### 4.1 `adm_sec_app_teamclaw_economy_governance_notify_log_di`

```sql
CREATE TABLE IF NOT EXISTS adm_sec_app_teamclaw_economy_governance_notify_log_di (
    id                      BIGINT COMMENT '在线库主键',
    notification_id         STRING COMMENT '通知唯一ID',
    worker_id               STRING COMMENT 'user_id:bot_id',
    bot_id                  STRING COMMENT 'Bot ID',
    bot_name                STRING COMMENT 'Bot名称',
    owner_id                STRING COMMENT 'Bot Owner',
    dt                      STRING COMMENT '首次创建时的数据日期 yyyymmdd',
    governance_decision     STRING COMMENT 'actionable/observe/justified',
    hit_dimensions          STRING COMMENT '命中维度JSON array',
    hit_dimensions_count    BIGINT COMMENT '命中维度数',
    expected_token_saving   BIGINT COMMENT '预估可节省Token',
    saving_ratio            DOUBLE COMMENT '节省比例',
    governance_max_dimension STRING COMMENT '最大优先级维度',
    governance_max_priority STRING COMMENT 'P0/P1',
    notify_status           STRING COMMENT 'pending/sent/failed/cancelled',
    response                STRING COMMENT '用户反馈: optimized/need_time/dispute/whitelist/resolved_by_system',
    response_at             STRING COMMENT '反馈时间',
    response_remark         STRING COMMENT '用户备注',
    response_source         STRING COMMENT 'card_callback/http_api/system_auto',
    task_create_key         STRING COMMENT 'ODPS幂等键',
    latest_dt               STRING COMMENT '最新分析数据日期',
    data_refresh_count      BIGINT COMMENT '数据刷新次数',
    dry_run                 BIGINT COMMENT '0/1',
    gmt_create              STRING COMMENT '创建时间',
    gmt_modified            STRING COMMENT '修改时间'
)
COMMENT '治理通知反馈日志（OCB在线库同步）'
PARTITIONED BY (ds STRING COMMENT '同步分区日期 yyyymmdd')
LIFECYCLE 365;
```

**分区策略**：`ds` = 同步跑批日期（非 `dt`），每次全量同步当天快照。
每次同步覆盖 `ds` 分区，不做增量合并——因为 `response` 等字段会被用户反馈更新，
需要拿最新快照。

### 4.2 `adm_sec_app_teamclaw_economy_governance_whitelist_di`

```sql
CREATE TABLE IF NOT EXISTS adm_sec_app_teamclaw_economy_governance_whitelist_di (
    id                  BIGINT COMMENT '在线库主键',
    bot_id              STRING COMMENT 'Bot ID',
    owner_id            STRING COMMENT 'Bot Owner',
    governance_source   STRING COMMENT 'system/owner/admin/manual',
    reason              STRING COMMENT '加白原因',
    created_by          STRING COMMENT '操作人',
    expires_at          STRING COMMENT '过期时间, 空=永久',
    gmt_create          STRING COMMENT '创建时间'
)
COMMENT '治理加白名单（OCB在线库同步）'
PARTITIONED BY (ds STRING COMMENT '同步分区日期 yyyymmdd')
LIFECYCLE 365;
```

**分区策略**：同上，`ds` = 同步跑批日期，每次全量快照覆盖。

## 5. 同步机制

### 5.1 方案：ZDAS → ODPS（复用已有通道）

OCB 在线库写入 OceanBase，OceanBase 通过 ZDAS 同步链路到 ODPS。这是团队已有基础设施，无需新建。

```
OCB 写入 OceanBase
  → ZDAS 实时/准实时同步（已有）
    → ODPS bridge 表（已有机制）
      → 建视图或直接引用
```

### 5.2 同步频率

| 维度 | 值 | 理由 |
|---|---|---|
| 同步频率 | T+1 日级 | Monitor ETL 是离线日级跑批，不需要实时 |
| 同步分区 | `ds = yyyyMMdd` | 每天覆盖昨天分区 |
| 历史回刷 | 首次上线需回刷已积累的通知数据 | OCB 上线后到 ODPS 建表前的增量 |

### 5.3 首次回刷

OCB `bot_governance` 上线后，若 ZDAS→ODPS 通道尚不通，通知数据会积压在 OceanBase。
首次回刷方案：

1. 从 OceanBase 导出 `ac_governance_notify_log` 和 `ac_governance_whitelist` 全量
2. 通过 `tunnel upload` 或 `DataX` 写入 ODPS 对应 `ds` 分区
3. 后续增量走 ZDAS → ODPS 正常通道

## 6. Monitor 宽表 ETL 扩展

在 `01-governance-effectiveness-monitor-design.md` 宽表 2 的 Section 3.9 后扩展：

### 6.1 新增字段

#### 3.9 通知投递

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `notification_deliverable` | BIGINT | 计算: analysis_status IN ('success','success_with_warnings') | 原 3.9 保留 |
| `governance_tags` | STRING | analysis | 原 3.9 保留 |
| `notification_sent` | BIGINT | notify_log: notify_status IN ('sent') | **新增** 实际已发送 |
| `notification_sent_at` | STRING | notify_log: gmt_modified (when sent) | **新增** 发送时间 |
| `notification_latest_dt` | STRING | notify_log: latest_dt | **新增** 通知使用的最新数据日期 |
| `notification_refresh_count` | BIGINT | notify_log: data_refresh_count | **新增** 数据刷新了几次 |

#### 3.10 用户反馈

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `owner_response` | STRING | notify_log: response | 用户反馈: optimized/need_time/dispute/whitelist/resolved_by_system |
| `owner_response_at` | STRING | notify_log: response_at | 反馈时间 |
| `owner_response_src` | STRING | notify_log: response_source | 反馈来源: card_callback/http_api/system_auto |
| `owner_response_remark` | STRING | notify_log: response_remark | 用户备注 |
| `response_latency_hours` | DOUBLE | 计算: response_at - gmt_create | 反馈延迟(小时)，衡量响应速度 |

#### 3.11 加白状态

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `is_whitelisted` | BIGINT | whitelist: 存在且未过期 → 1 | 是否加白 |
| `whitelist_source` | STRING | whitelist: governance_source | 加白来源: system/owner/admin |
| `whitelist_expires_at` | STRING | whitelist: expires_at | 加白过期时间 |

### 6.2 ETL SQL（关键 JOIN 片段）

```sql
-- 通知反馈 JOIN（取每个 worker_id 最近的未 dry_run 通知快照）
LEFT JOIN (
    SELECT
        worker_id,
        notify_status,
        latest_dt             AS notification_latest_dt,
        data_refresh_count    AS notification_refresh_count,
        response              AS owner_response,
        response_at           AS owner_response_at,
        response_source       AS owner_response_src,
        response_remark       AS owner_response_remark,
        gmt_create,
        gmt_modified
    FROM adm_sec_app_teamclaw_economy_governance_notify_log_di
    WHERE ds = '${ds}'
      AND dry_run = 0
      AND notify_status IN ('sent', 'pending')  -- 只取实际发了的
    GROUP BY worker_id, notify_status, latest_dt,
             data_refresh_count, response, response_at,
             response_source, response_remark,
             gmt_create, gmt_modified
) nl ON e.worker_id = nl.worker_id

-- 加白状态 JOIN
LEFT JOIN (
    SELECT
        bot_id,
        governance_source     AS whitelist_source,
        expires_at            AS whitelist_expires_at
    FROM adm_sec_app_teamclaw_economy_governance_whitelist_di
    WHERE ds = '${ds}'
      AND (expires_at IS NULL OR expires_at > CAST('${ds}' AS TIMESTAMP))
) wl ON e.bot_id = wl.bot_id   -- 注: whitelist 没有 worker_id，用 bot_id JOIN

-- 计算字段
CASE WHEN nl.notify_status = 'sent' THEN 1 ELSE 0 END AS notification_sent,
nl.gmt_modified                                       AS notification_sent_at,
nl.notification_latest_dt,
nl.notification_refresh_count,
nl.owner_response,
nl.owner_response_at,
nl.owner_response_src,
nl.owner_response_remark,
CASE WHEN nl.owner_response_at IS NOT NULL
     THEN (CAST(nl.owner_response_at AS TIMESTAMP) - CAST(nl.gmt_create AS TIMESTAMP)) * 24
     ELSE NULL END                                     AS response_latency_hours,
CASE WHEN wl.bot_id IS NOT NULL THEN 1 ELSE 0 END     AS is_whitelisted,
wl.whitelist_source,
wl.whitelist_expires_at
```

### 6.3 JOIN 时间语义说明

| 场景 | notify_log.dt | notify_log.latest_dt | Monitor 宽表 dt | JOIN 对齐方式 |
|---|---|---|---|---|
| 通知首次创建 | 20260628 | 20260628 | 20260628 | `nl.latest_dt = e.dt` |
| 数据刷新后 | 20260628 | 20260630 | 20260630 | `nl.latest_dt = e.dt` |
| 多次通知（已关闭再新建） | 20260628 → 20260701 | 各自 latest_dt | 各自 dt | 每条通知独立，不冲突 |

**注意**：同一 `worker_id` 在同一 `ds` 可能有多条通知（已关闭的历史通知 + 新建的未关闭通知）。ETL 中需要加过滤：只取最近一条 `notify_status IN ('sent', 'pending')` 且 `dry_run = 0` 的记录。已关闭通知（`response IS NOT NULL`）的反馈数据仍需保留，可在 `GROUP BY` 中用 `ROW_NUMBER()` 取最新。

## 7. 典型监控查询（扩展）

### 7.1 通知触达率

```sql
SELECT dt,
    COUNT(*) AS actionable_bots,
    SUM(notification_sent) AS notified,
    SUM(notification_sent) / COUNT(*) AS reach_rate
FROM adm_..._governance_effectiveness_monitor_di
WHERE dt = '${dt}'
  AND governance_decision = 'actionable'
GROUP BY dt;
```

### 7.2 反馈分布

```sql
SELECT owner_response,
    COUNT(*) AS cnt,
    AVG(response_latency_hours) AS avg_latency_h
FROM adm_..._governance_effectiveness_monitor_di
WHERE dt = '${dt}'
  AND owner_response IS NOT NULL
GROUP BY owner_response;
```

预期分布：optimized > need_time > dispute > whitelist

### 7.3 治理效果：反馈"已优化"的 Bot Token 变化

```sql
SELECT
    m.dt,
    SUM(CASE WHEN e.owner_response = 'optimized' THEN m.total_tokens ELSE 0 END) AS optimized_tokens,
    SUM(CASE WHEN e.owner_response IS NULL AND e.notification_sent = 1 THEN m.total_tokens ELSE 0 END) AS pending_tokens,
    SUM(m.total_tokens) AS total_tokens
FROM adm_..._economy_governance_monitor_di m
JOIN adm_..._governance_effectiveness_monitor_di e ON m.worker_id = e.worker_id AND m.dt = e.dt
WHERE m.dt BETWEEN '20260625' AND '20260630'
GROUP BY m.dt;
```

### 7.4 加白率

```sql
SELECT is_whitelisted, whitelist_source, COUNT(*)
FROM adm_..._governance_effectiveness_monitor_di
WHERE dt = '${dt}'
  AND governance_decision = 'actionable'
GROUP BY is_whitelisted, whitelist_source;
```

### 7.5 数据刷新影响

```sql
SELECT notification_refresh_count, COUNT(*), AVG(saving_ratio)
FROM adm_..._governance_effectiveness_monitor_di
WHERE dt = '${dt}'
  AND notification_sent = 1
GROUP BY notification_refresh_count;
```

衡量"催了几次才处理"和刷新后 saving_ratio 的变化。

## 8. 实施步骤

| # | 改动 | 谁做 | 依赖 |
|---|------|------|------|
| 1 | OCB 侧 `ac_governance_notify_log` + `ac_governance_whitelist` 上线写入 | agentclaw 团队 | bot_governance 模块上线 |
| 2 | OceanBase → ZDAS 映射配置（让这两张表可被 ZDAS 发现） | agentclaw + DBA | Step 1 |
| 3 | ODPS 建表 + ZDAS→ODPS 同步链路配置 | 数据平台 | Step 2 |
| 4 | 首次回刷（如有积压数据） | 数据平台 | Step 3 |
| 5 | Monitor 宽表 ETL 扩展 Section 3.9/3.10/3.11 | economy_governance 团队 | Step 3 |
| 6 | Dataphin 调度依赖更新 | economy_governance 团队 | Step 5 |

Step 5 与 Step 2-4 可并行设计，ETL 先写好等数据就绪后联调。

## 9. 数据安全

| 关注点 | 处理方式 |
|---|---|
| `owner_id` 脱敏 | 离线宽表中 `owner_id` 保留原始值（与分析表一致，用于 JOIN），不落地到面向外部的报表 |
| `response_remark` 敏感内容 | 用户备注可能含业务细节，ODPS 表中对内可见，外部报表不展示 |
| `entity_id`（工号） | **不落离线**，在源表定义中已排除 |

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| ZDAS 同步延迟导致 Monitor 看不到当天反馈 | Monitor ETL 依赖 `ds` 分区，ZDAS 同步每天跑一次即可；若当天未同步，次日分区会包含最新快照 |
| 同一 Bot 多条通知导致 JOIN 膨胀 | ETL 中用 `ROW_NUMBER()` 或 `GROUP BY worker_id` 取最新一条未关闭通知 |
| 加白表 `bot_id` JOIN 而非 `worker_id` | whitelist 表没有 `worker_id`，需通过 `bot_id` JOIN。若同一 `bot_id` 有多个 owner（罕见），加白记录也会多条，ETL 中 `GROUP BY bot_id` 取最新 |
| `dry_run` 通知混入 | ETL 中 `WHERE dry_run = 0` 过滤 |
| 首次积压数据体积大 | `ac_governance_notify_log` 预计日增 < 200 行，即使积压 3 个月也不到 2 万行，tunnel upload 可秒级完成 |

---

**文档版本**：v1.0
**创建时间**：2026-06-29
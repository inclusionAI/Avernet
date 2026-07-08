# Plan: Bot 治理通知 + 审计

## Approach

在 `economy` 模块下新增 `governance` 子模块（`core/economy/governance/`），
完整复用 `bot_dormant` 的架构模式。`economy` 与 `harness` 为并列一级模块，
后续还将接入 `incentive`（正向激励）子模块。

1. **Cron + 分布式锁** — `GovernanceBotLifecycle(LifecycleBase)` 每日 14:00 触发扫描，
   使用 `CachePlugin.acquire_lock` 防止多 Pod 重复执行。
2. **数据就绪检查** — 扫描开始时读取 `MAX(ac_governance_task_record_daily.last_sync_at)`，
   与上次扫描成功时间比较。若 `last_sync_at` 未更新（离线管线无新数据写入），
   则**跳过所有依赖离线数据新鲜度的操作**（不创建新通知、不执行状态追踪、
   不更新 `latest_decision` / `consecutive_normal_days` / `last_seen_at`），
   审计 `data_not_ready`，**不 return**——
   继续执行时间驱动操作（提醒到期处理、过期关闭、发送重试）。
   **原因**：通知创建和状态追踪都依赖离线数据新鲜度。`last_sync_at` 未变说明
   task_record_daily 仍是上一批次结果，用它创建通知会基于旧数据重复开单，
   用它更新状态会误判工单去向。
3. **离线→在线数据通路** — 离线治理管线调用
   `POST /api/economy/governance/records/offline-batch`
   将 ODPS 跑批结果 upsert 到 `ac_governance_task_record_daily` 在线表
   （参照 `harness/offline-batch` 模式，路径与
   `/api/harness/diagnose/records/offline-batch` 平行）。每次调用整批记录
   共享同一个 `last_sync_at = now()`，扫描通过 `MAX(last_sync_at)` 判断数据是否就绪。
   ODPS 侧的 `analysis_daily` 仅是分析过程数据，不透出到在线库。
4. **OceanBase 读取** — `OceanBaseGovernanceReader` 通过 `DatabasePlugin.orm_session()`
   读取在线库中的 `ac_governance_task_record_daily` 表。
5. **通知创建** — `GovernanceBotService.process_run()` 对 actionable Bot
   经过加白过滤、静默期过滤、冷却期检查后创建 `GovernanceNotifyLog` 记录，
   同时设置 `remind_at = gmt_create + 3天` 以触发提醒节奏。
6. **状态追踪 + 自动关闭** — `governance_status` 仅 4 态（`open` / `muted` / `closed` / `expired`），
   业务语义由 `response` + `close_reason` + `audit` 表达。扫描只处理 `open` 和 `muted`。
   对 `open` 通知：读取 decision 集合，更新 `latest_decision` 和
   `consecutive_normal_days`，`consecutive_normal_days >= 3` 时 → `closed + auto_resolved`。
   对 `muted` 通知：Bot 恢复 → `closed + auto_resolved`；静默期过仍 actionable → `expired + mute_expired`。
   不在治理范围也计入正常天数。
7. **提醒 + 过期关闭** — 一周为周期：Day 0 创建即时发送 → Day 3 提醒 →
   Day 7 仍无反馈 → `governance_status='expired'`, `close_reason='no_response_expired'`，
   下次扫描创建新记录（同一 `governance_cycle_id`，同一周期续催）携带最新数据和累积未反馈天数。
8. **用户反馈** — `GovernanceFeedbackService.resolve()` 接收 4 种 action，
   更新 `GovernanceNotifyLog` + `governance_status` 转换 + `close_reason` + 审计。
   `optimized`/`dispute`/`whitelist` → `closed` + 各自 `close_reason`；
   `need_time` → `muted`，必填 `repair_deadline`，系统计算 `mute_until`。
   `feedback_payload` 存储结构化反馈（按 `notification_structured.action_items[].index`）。
9. **Markdown 单向通知 + 单阶段发送** — Phase 1 只用 DingTalk `batchSend`
   (sampleMarkdown) 单向通知 + 深链接模式。消息内嵌治理详情页链接，
   用户点击链接跳转 OCB 前端页面进行反馈（HTTP API resolve）。
   不使用交互卡片（因交互卡片存在稳定性和复杂性问题，推迟到 Phase 2）。
   发送链路在扫描锁内直接完成：扫描创建通知后，内部循环读取 pending 通知
   并逐条 HTTP 发送。成功 → `sent`，失败 → 保持 `pending`，下次扫描重试。
   Phase 2 增加交互卡片反馈（卡片按钮 + HTTP 回调）。
10. **DI 绑定** — `EconomyGovernanceModule` 注册所有单例和协议桥接。

## Affected Components

### 新增文件

| 文件 | 说明 |
|---|---|
| `core/economy/__init__.py` | economy 模块声明（含 governance 子模块，后续接入 incentive） |
| `core/economy/governance/__init__.py` | governance 子模块声明 |
| `core/economy/governance/sqlite_models.py` | 4 张业务表（notify_log + audit + whitelist + task_record_daily） |
| `core/economy/governance/oceanbase_reader.py` | 在线库治理数据读取 |
| `core/economy/governance/offline_batch_service.py` | 离线批量写入服务（upsert 逻辑） |
| `core/economy/governance/protocols.py` | BotServiceProtocol 桥接（同 dormant） |
| `core/economy/governance/service.py` | 主扫描编排 + 状态追踪 + 提醒 + 过期关闭 |
| `core/economy/governance/feedback_service.py` | 用户反馈处理 + governance_status 转换 + mute_until |
| `core/economy/governance/whitelist_service.py` | 加白管理 |
| `core/economy/governance/internal_service.py` | 内部接口（手动触发扫描 + 紧急制动查询） + 发送服务（扫描锁内发送 pending 通知） |
| `core/economy/governance/emergency_service.py` | 紧急制动 |
| `core/economy/governance/templates.py` | Markdown 通知模板（单向 sampleMarkdown，含深链接） |
| `core/economy/governance/lifecycle.py` | Cron + 分布式锁 + 14:00 触发 |
| `adapters/http/economy/__init__.py` | |
| `adapters/http/economy/router.py` | 公开 + 内部路由（prefix: /api/economy/governance） |
| `adapters/http/economy/schemas.py` | Pydantic 模型 |
| `adapters/http/economy/auth.py` | 内部接口 Bearer token 鉴权 |
| `di/modules/economy_governance_module.py` | DI 绑定 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `di/config.py` | 新增 `GovernanceConfig` + `EconomyInternalToken` |
| `di/container.py` | 注册 `EconomyGovernanceModule` |
| `adapters/http/app.py` | 挂载公开路由 + 内部路由 |
| `plugins/local/database.py` | `bootstrap()` 中导入新 ORM 模型 |
| `specs/.../sql/governance_tables.sql` | `source` COMMENT 更新 |
| `contracts/models.py` | BotWhitelist.source 注释 + reason 注释同步 |
| `services/feedback_service.py` | `batch_add(source=source)` 替代 `source="owner"` |
| `repositories/whitelist_repo.py` | `reason` 应用层截断 `[:500]` |
| `adapters/http/economy/schemas.py` | WhitelistBatchRequest.source description 更新 |

## Data Model Changes

见 spec.md "Data Model Changes" 章节。4 张表：
- `ac_governance_notify_log` — 通知 + 反馈 + 工单生命周期
- `ac_governance_check_audit` — 审计日志 (append-only)
- `ac_bot_whitelist` — 统一白名单
- `ac_governance_task_record_daily` — 离线数据（ODPS 写入，在线读取），含 `last_sync_at`

### `ac_governance_notify_log` 关键字段

| 字段 | 说明 |
|---|---|
| `governance_status` | 系统处理态：open/muted/closed/expired（仅 4 值） |
| `governance_cycle_id` | 治理周期 ID。同一 Bot 未闭环期间共享，closed 后新周期用新 ID |
| `close_reason` | 关闭/过期原因：user_optimized/user_disputed/user_whitelisted/auto_resolved/mute_expired/no_response_expired/emergency_closed |
| `closed_at` | 关闭/过期时间 |
| `repair_deadline` | 用户承诺修复截止日期（need_time 时必填） |
| `mute_until` | 静默截止时间（need_time + repair_deadline + 7天宽限） |
| `expire_at` | 本轮过期时间（创建时 = gmt_create + expire_days） |
| `last_seen_at` | 静默期内扫描仍发现 actionable 的时间 |
| `remind_count` | 本轮已提醒次数 |
| `remind_at` | 下次提醒时间（创建时 = gmt_create + 3天，提醒后清空） |
| `feedback_payload` | 结构化用户反馈 JSON（对照 action_items index） |
| `cooldown_until` | 冷却截止时间。**仅 closed 时写入** = `closed_at + cooldown_days`。expired 不写 cooldown（非周期闭环，不阻断续催） |
| `latest_decision` | 最近扫描时该 Bot 的 decision |
| `consecutive_normal_days` | 连续非 actionable 天数 |
| `notify_status` | 通知投递态：pending/sent/cancelled（失败保持 pending，无 failed 态） |
| `sent_at` | 发送成功时间 |
| `send_attempt_count` | 发送尝试次数。初始 = 0，每次发送（不论成功失败）+1 |
| `last_send_at` | 最近一次发送尝试时间 |
| `last_send_error` | 最近一次发送错误信息 |
| `external_message_id` | DingTalk 返回的外部消息 ID |

### `governance_status` 状态机（4 态）

```
open  → closed    (response=optimized,    close_reason=user_optimized,      cooldown_until=closed_at+cooldown)  — 终态
open  → closed    (response=dispute,      close_reason=user_disputed,       cooldown_until=closed_at+cooldown)  — 终态
open  → closed    (response=whitelist,    close_reason=user_whitelisted,    cooldown_until=closed_at+cooldown)  — 终态
open  → muted     (response=need_time,    repair_deadline必填, 无cooldown)                                      — 活跃
open  → closed    (response=resolved_by_system, close_reason=auto_resolved, cooldown_until=closed_at+cooldown)  — 终态
open  → closed    (close_reason=emergency_closed,                          cooldown_until=closed_at+cooldown)  — 终态
open  → expired   (close_reason=no_response_expired,                       cooldown_until=NULL)                — 单条终态，非周期闭环
muted → closed    (response=resolved_by_system, close_reason=auto_resolved, cooldown_until=closed_at+cooldown)  — 终态
muted → expired   (close_reason=mute_expired,                              cooldown_until=NULL)                — 单条终态，非周期闭环
```

`open` 和 `muted` 是活跃态（扫描处理）；`closed` 和 `expired` 是终态（不再修改）。
expired 后下次扫描创建新记录（同一 `governance_cycle_id`，expired 不写 cooldown，不阻断续催）。

## API / Interface Changes

见 spec.md "API / Interface Changes" 章节。6 个公开端点 + 4 个内部端点。

## Key Design Details

### OceanBase 读取（oceanbase_reader.py）

```python
class OceanBaseGovernanceReader:
    def __init__(self, db: DatabasePlugin): ...

    def get_latest_dt_version(self, session) -> str:
        """SELECT MAX(dt_version) FROM ac_governance_task_record_daily
           WHERE dt_version <= today(YYYYMMDD)"""

    def get_actionable_bots(self, session, dt_version: str) -> list[GovernanceTaskRecordDaily]:
        """WHERE dt_version = :dt AND governance_decision = 'actionable'
           AND analysis_status IN ('completed', 'success', 'success_with_warnings')"""

    def get_completed_decisions(self, session, dt_version: str) -> dict[str, str]:
        """UK (worker_id, dt_version) 保证唯一，无需 GROUP BY。
           直接返回 {worker_id: governance_decision}。
           注意：不能用 MAX(governance_decision)——
           字典序 observe > justified > actionable 与业务优先级不一致。
           若未来 UK 放宽，需按业务优先级聚合：
           actionable > observe > justified（任一 actionable 则整体 actionable）"""
```

### 离线批量写入（offline_batch_service.py）

```python
class GovernanceOfflineBatchService:
    def __init__(self, db: DatabasePlugin): ...

    def batch_upsert_task_recs(self, records: list[dict]) -> dict:
        """逻辑 key: (worker_id, dt_version)
           update_fields: bot_id, bot_name, governance_decision, hit_dimensions,
             hit_dimensions_count, governance_max_priority, task_summary,
             expected_token_saving, saving_ratio, analysis_status,
             last_sync_at (整批共享 now())
           返回: {inserted: N, updated: N, errors: N}"""
```

### 主扫描（service.py）

```
GovernanceBotService.process_run(dry_run) -> RunSummary:
  1. orm_session()
  2. 检查紧急制动 → paused → 跳过扫描
  3. 数据就绪检查 → MAX(last_sync_at) 与上次扫描时间比较
     → 未更新 → 审计 data_not_ready，data_ready=False
        继续执行步骤 11-12（提醒/过期关闭），不 return
     → 已更新 → data_ready=True

  === 以下 4-10 仅在 data_ready=True 时执行 ===
  if not data_ready: goto step 11
  4. reader.get_latest_dt_version() → dt_version
  5. reader.get_actionable_bots(dt_version) → actionable_set

  === 通知创建 ===
  6. 加白过滤 → whitelist_set
  7. 对每个 actionable bot:
     a. 加白 → audit(whitelist_filtered), skip
     b. 存在 muted 通知 → 更新 last_seen_at, audit(muted), skip
     c. 已有 open 通知 → skip
     d. 冷却期 → 最近一条 closed 记录 cooldown_until > now() → audit(cooldown_filtered), skip
       （expired 不参与 cooldown 判断）
     e. 创建 GovernifyNotifyLog(pending, open)
        remind_at = now() + 3天, expire_at = now() + expire_days
        remind_count = 0
        governance_cycle_id = 新 UUID4 (或继承自同一 Bot 上次未闭环记录的 cycle_id)
        latest_decision = 'actionable', consecutive_normal_days = 0
        audit(enqueued)

  === 状态追踪（仅数据就绪时执行） ===
  8. reader.get_completed_decisions(dt_version) → decision_map

  9. 对 governance_status='open' 的通知:
     a. worker_id 在 decision_map:
        - actionable → latest_decision='actionable', consecutive_normal_days=0
        - observe/justified → latest_decision=decision, consecutive_normal_days += 1
     b. worker_id 不在 decision_map (不在治理范围):
        - consecutive_normal_days += 1 (不在治理范围 = 恢复正常)
        - audit(out_of_scope)
     c. consecutive_normal_days >= 3 →
        governance_status='closed', response='resolved_by_system',
        close_reason='auto_resolved', closed_at=now(),
        cooldown_until=now()+cooldown_days,
        audit(auto_resolved)

  10. 对 governance_status='muted' 的通知:
      a. Bot 已恢复 (非 actionable / 不在治理范围):
         → governance_status='closed', response='resolved_by_system',
           close_reason='auto_resolved', closed_at=now(),
           cooldown_until=now()+cooldown_days,
           audit(auto_resolved)
      b. Bot 仍 actionable, 静默期未过 (mute_until > now()):
         → 更新 last_seen_at
      c. Bot 仍 actionable, 静默期已过 (mute_until <= now()):
         → governance_status='expired',
           close_reason='mute_expired', closed_at=now(),
           cooldown_until=NULL（expired 不写 cooldown）,
           audit(mute_expired)

  === 提醒 + 过期关闭 ===
  11. 提醒通知（governance_status='open' 且 notify_status='sent' 且 remind_at <= now()）：
      发送提醒 Markdown：
      a. 发送前检查紧急制动 pause → 跳过提醒
      b. 成功 → remind_count += 1, remind_at = NULL, audit(reminded)
      c. 失败 → 保留 remind_at，下次扫描继续尝试
  12. 对 governance_status='open' 且 expire_at <= now()
      且 remind_count >= 1:
      → governance_status='expired',
        close_reason='no_response_expired', closed_at=now(),
        cooldown_until=NULL（expired 不写 cooldown）,
        audit(expired_unresolved)

  === 首发通知（扫描锁内） ===
  13. 对已 closed/expired 且 notify_status='pending' 的通知:
      → notify_status='cancelled'
  14. 查询 notify_status='pending' 且 governance_status='open'
      （muted 不进首发查询：用户已反馈 need_time，首发语义不适用）
      的通知，逐条发送 HTTP 通知（DingTalk Markdown）：
      a. 发送前检查紧急制动 pause → 跳过发送
      b. 成功 → notify_status='sent', sent_at=now(),
         external_message_id=返回值, send_attempt_count += 1,
         last_send_at=now(), last_send_error=NULL
      c. 失败 → notify_status 保持 'pending',
         send_attempt_count += 1, last_send_at=now(), last_send_error=error

  15. 结构化日志 + RunSummary
```

### 用户反馈（feedback_service.py）

```
GovernanceFeedbackService.resolve(notification_id, response, remark,
                                   user_id, source, repair_deadline=None,
                                   feedback_payload=None):
  1. 查 GovernanceNotifyLog by notification_id
  2. 校验 owner_id == user_id
  3. 幂等检查（已有正式 response → 返回已有结果）
  4. dispute 时 remark 必填
  5. need_time 时 repair_deadline 必填
  6. 更新 NotifyLog:
     - response + response_at + response_remark + response_source
     - governance_status + close_reason + cooldown_until 映射:
       optimized → closed, close_reason=user_optimized, closed_at=now(), cooldown_until=now()+cooldown_days
       dispute   → closed, close_reason=user_disputed, closed_at=now(), cooldown_until=now()+cooldown_days
       whitelist → closed, close_reason=user_whitelisted, closed_at=now(), cooldown_until=now()+cooldown_days
       need_time → muted (活跃态，不写 closed_at)
     - need_time 时: mute_until = repair_deadline + mute_grace_days
     - feedback_payload 直接存入（只校验合法 JSON）
  7. 分支:
     - whitelist → whitelist_service.batch_add()
  8. audit(user_resolved)
```

### 生命周期（lifecycle.py）

```python
class GovernanceBotLifecycle(LifecycleBase):
    target_hour: 14                           # 14:00 触发
    scan_lock: "governance_scan_lock:{env}"
    lock_ttl: 1800                           # 30 min
```

### 配置（di/config.py）

```python
@dataclass
class GovernanceConfig:
    dry_run: bool = True
    scan_hour: int = 14
    max_notify_per_run: int = 200
    cooldown_days: int = 14
    auto_resolve_threshold_days: int = 3
    mute_grace_days: int = 7          # need_time 静默宽限期
    expire_days: int = 7              # 未回应过期天数

@dataclass
class EconomyInternalToken:
    value: str = ""
```

### `close_reason` 枚举

| close_reason | governance_status | cooldown_until | 触发 |
|---|---|---|---|
| `user_optimized` | closed | closed_at + cooldown_days | response=optimized |
| `user_disputed` | closed | closed_at + cooldown_days | response=dispute |
| `user_whitelisted` | closed | closed_at + cooldown_days | response=whitelist |
| `auto_resolved` | closed | closed_at + cooldown_days | 连续N天非actionable / muted内恢复 |
| `emergency_closed` | closed | closed_at + cooldown_days | 紧急制动 cancel-pending / bulk-whitelist |
| `no_response_expired` | expired | NULL | 7天未回应（expired 不写 cooldown，不阻断续催） |
| `mute_expired` | expired | NULL | 静默期过仍actionable（expired 不写 cooldown） |

### feedback_payload 格式

```json
{
  "version": 1,
  "overall_action": "partial",
  "overall_remark": "整体接受，但部分建议有异议",
  "repair_deadline": "2026-07-15",
  "items": [
    {"index": 1, "action": "accepted", "remark": null},
    {"index": 2, "action": "rejected", "remark": "工具仍在使用中"},
    {"index": 3, "action": "partial", "remark": "只认30%的浪费"}
  ]
}
```

- `items[].index` 对照 `notification_structured.action_items[].index`
- `action`: accepted / rejected / partial
- 系统不解析 JSON 内容，只校验合法 JSON
- `version` 递增兼容，禁止删改已有字段

## DB 相关优化

### 优化 1：`ac_bot_whitelist.source` 注释补全 + 透传渠道

当前 DDL 注释只列 `system/owner/admin/manual/emergency`，遗漏了实际代码中已产生的来源。
更关键的是 `feedback_service.py` 在 `response=whitelist` 时硬编码 `source="owner"`，
丢失了触发渠道区分（`http_api` / `card_callback`）。

**变更**：
1. DDL COMMENT 更新为 `system/owner/admin/manual/emergency/card_callback/http_api/owner_feedback`
2. `feedback_service.py`：将 `resolve()` 接收到的 `source`（`http_api` / `card_callback`）透传给 `batch_add()`，替代硬编码 `"owner"`
3. ORM model + API schema 注释同步

source 仅记录来源，不影响查询/过滤逻辑，DDL 类型 `VARCHAR(64)` 足够容纳新值。

### 优化 2：`reason` 字段应用层截断

`reason VARCHAR(512)` 保持不变，应用层截断至 500 字。
留 12 字节余量防多字节字符截断溢出（utf8mb4 最长 4 字节/字符，500×4=2000 < 512×4=2048）。

同时修复 `entry.get("reason", "")` 在 `reason=None` 时返回 `None` 而非空字符串的问题：
改为 `(entry.get("reason") or "")[:500]`。

不改 DDL 类型（TEXT 在 OceanBase 上可能影响索引策略和查询计划，且批量加白场景无限制长度可能导致存储膨胀）。

### 优化 3：`admin close_all_open` 端到端验证

`GovernanceAdminService.close_all_open()` 已有单元测试覆盖，但缺少从数据注入到接口调用的完整端到端验证。
使用 `OpenclawSessionAnalysis` 的 upload 脚本注入 task_rec_daily 数据 → trigger-scan → close-all-open → 确认 open_count=0 + 审计已写。

## Dependencies

无新依赖。复用现有：
- `injector` (DI)
- `sqlalchemy` (ORM)
- `fastapi` (HTTP)
- `pydantic` (Schema)
- `DatabasePlugin` / `CachePlugin` / `SecretResolver`

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| ODPS 数据延迟导致无分区 | `get_latest_dt_version` 兜底 today-1；无分区时日志告警并跳过 |
| 通知量过大 | `max_notify_per_run` 限流；超出部分告警 |
| 加白表过期后仍被过滤 | 查询时 `expires_at IS NULL OR expires_at > now()` |
| 离线数据未导入就执行通知创建或状态追踪 | `last_sync_at` 就绪检查；未更新时跳过通知创建和状态追踪，审计 `data_not_ready`，但仍继续执行提醒/过期关闭/发送重试（不 return） |
| 不在治理范围误判为恢复 | 不在治理范围 = 恢复正常（设计决策），audit(out_of_scope) |
| muted 状态静默期过仍 actionable 无限轮回 | `mute_expired` → expired → 继承同一 `governance_cycle_id` 创建新记录续催；expired 不写 cooldown，同一周期持续催促直到 closed |
| governance_status 膨胀 | 强制 4 态，业务语义必须走 close_reason / audit，禁止加新 status |
| 卡片凭证未配置 | Phase 1 不用卡片，纯 Markdown 单向通知 + 扫描锁内单阶段发送 |
| 交互卡片稳定性和复杂度 | Phase 1 不使用交互卡片，用 Markdown 单向通知 + 深链接引导用户到前端页面反馈。交互卡片推迟到 Phase 2 |

## Test Strategy

- **单元测试** `tests/core/economy/governance/`：
  - `test_models.py` — ORM 模型建表 + UK 约束 + governance_status 默认值
  - `test_oceanbase_reader.py` — get_latest_dt_version / get_actionable_bots
  - `test_service_scan.py` — 主扫描 + 加白/静默期/冷却期过滤 + auto_resolved + out_of_scope
  - `test_feedback.py` — 4 种 resolve + need_time repair_deadline + governance_status 转换 + mute_until
  - `test_whitelist.py` — 批量加白 + 幂等 + 过期
  - `test_internal.py` — 扫描锁内发送 + auto-cancel + send_attempt_count
  - `test_emergency.py` — pause/resume + cancel_pending + governance_status='closed'
  - `test_templates.py` — 模板渲染
  - `test_lifecycle.py` — cron 触发 + 分布式锁
- **集成测试**：
  - 端到端 resolve + 加白后扫描跳过 + trigger-scan
- **预发验证**：
  - dry_run=True 扫描 → 确认筛选逻辑
  - dry_run=False → 确认通知创建
  - 扫描锁内发送 pending → 确认 DingTalk 发送
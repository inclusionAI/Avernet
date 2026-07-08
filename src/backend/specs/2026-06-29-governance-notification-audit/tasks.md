# Tasks: Bot 治理通知 + 审计

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: ORM 模型定义 `[ ]`

- **Goal:** 定义 4 张表（3 治理业务表 + 1 张离线数据表），作为所有后续任务的数据基础。
- **Files:** `core/economy/governance/__init__.py`, `core/economy/governance/sqlite_models.py`
- **Done when:**
  - [ ] `GovernanceNotifyLog(ac_governance_notify_log)` 定义完整，含 UK `(bot_id, dt_version)`
        + 反馈字段：response, response_at, response_remark, response_source
        + 工单生命周期字段：governance_status, mute_until, last_seen_at
        + 状态追踪字段：latest_decision, consecutive_normal_days
        + 提醒字段：remind_count, remind_at
        + 结构化反馈：feedback_payload
        + 发送字段：notify_status(pending/sent/cancelled), sent_at, send_attempt_count, last_send_at, last_send_error, external_message_id
        + 周期字段：governance_cycle_id（未闭环共享，closed 后新 ID）
        + 冷却字段：cooldown_until（仅 closed 时写入，expired 不写）
  - [ ] `GovernanceCheckAudit(ac_governance_check_audit)` 定义完整，含 `run_id`
  - [ ] `BotWhitelist(ac_bot_whitelist)` 统一白名单表，含 UK `(bot_id, owner_id, whitelist_type)` + `expires_at`
  - [ ] `GovernanceTaskRecordDaily(ac_governance_task_record_daily)` 定义完整，
        UK `(worker_id, dt_version)`，含 `last_sync_at`
        字段：worker_id, bot_id, dt_version, governance_decision, bot_name,
        hit_dimensions, hit_dimensions_count, governance_max_priority,
        expected_token_saving, saving_ratio, task_summary, analysis_status,
        last_sync_at, gmt_create, gmt_modified
  - [ ] 所有模型继承 `core.base.Base`，主键使用 `AutoIncrementBigInteger`
  - [ ] 单元测试 `tests/core/economy/governance/test_models.py` 验证建表和 UK 约束
- **Depends on:** —

## Task 2: OceanBase 读取器 `[ ]`

- **Goal:** 封装对在线库治理数据表的查询逻辑。
- **Files:** `core/economy/governance/oceanbase_reader.py`, `tests/core/economy/governance/test_oceanbase_reader.py`
- **Done when:**
  - [ ] `OceanBaseGovernanceReader.__init__(db: DatabasePlugin)`
  - [ ] `get_latest_dt_version(session) -> str` — 查 `ac_governance_task_record_daily` 最大 dt_version，兜底 today-1
  - [ ] `get_actionable_bots(session, dt_version) -> list[GovernanceTaskRecordDaily]` — actionable + completed
  - [ ] `get_completed_decisions(session, dt_version) -> dict[str, str]` — 读取完整 decision 集合
  - [ ] 无可用分区时返回空列表并记录 WARNING 日志
  - [ ] 单元测试覆盖：正常查询、空分区、兜底逻辑
- **Depends on:** Task 1

## Task 2b: 离线批量写入服务 `[ ]`

- **Goal:** 实现离线治理管线 → 在线库的 HTTP batch upsert 接口
  （参照 `harness_scan_repository.offline_batch()` 模式，
  端点路径 `/api/economy/governance/records/offline-batch` 与 harness 平行）。
- **Files:** `core/economy/governance/offline_batch_service.py`, `tests/core/economy/governance/test_offline_batch.py`
- **Done when:**
  - [ ] `GovernanceOfflineBatchService.__init__(db: DatabasePlugin)`
  - [ ] `batch_upsert_task_recs(records) -> {inserted, updated, errors}`：
    - 逻辑 key: `(worker_id, dt_version)`
    - 先批量查已有记录缓存到 dict
    - 已存在 → UPDATE（只更新非 None 字段），不存在 → INSERT
    - 整批记录共享 `last_sync_at = now()`
    - 支持自定义 `gmt_create`
  - [ ] 逐条 try/except 错误隔离，单条失败不回滚整批
  - [ ] 单元测试：insert、update（幂等）、混合批次、错误隔离、自定义 gmt_create、last_sync_at 更新
- **Depends on:** Task 1

## Task 3: 加白服务 `[ ]`

- **Goal:** 实现加白名单管理，供扫描过滤和用户 `whitelist` action 使用。
- **Files:** `core/economy/governance/whitelist_service.py`, `tests/core/economy/governance/test_whitelist.py`
- **Done when:**
  - [ ] `GovernanceWhitelistService.batch_add(entries, created_by) -> {inserted, skipped}`
  - [ ] `GovernanceWhitelistService.is_whitelisted(session, bot_id, owner_id) -> bool` — 含过期校验
  - [ ] `GovernanceWhitelistService.list_all(session) -> list` — 查询列表
  - [ ] UK `(bot_id, owner_id, whitelist_type)` 冲突时幂等 skip
  - [ ] 统一白名单表 `ac_bot_whitelist`，governance 使用 `whitelist_type='governance'`
  - [ ] **reason 应用层截断**：`batch_add()` 中 `reason=(entry.get("reason") or "")[:500]`，
        修复 `reason=None` 时返回 `None` 的问题，超长截断至 500 字符
  - [ ] 单元测试：批量加白、幂等 skip、过期不加白、reason 截断、reason=None 处理
- **Depends on:** Task 1

## Task 4: 主扫描服务 `[ ]`

- **Goal:** 实现每日治理扫描编排，连接读取器、加白过滤、静默期/冷却期检查、
  通知创建、状态追踪、提醒+过期关闭。
- **Files:** `core/economy/governance/service.py`, `core/economy/governance/protocols.py`, `tests/core/economy/governance/test_service_scan.py`
- **Done when:**
  - [ ] `GovernanceBotService.__init__(db, reader, whitelist_svc, cache, config, bot_service)` 使用 `@inject`
  - [ ] 数据就绪检查：`MAX(last_sync_at)` 与上次扫描时间比较，未更新则禁止离线数据依赖操作
  - [ ] 紧急制动检查：ZCache key 存在且 action=pause → 跳过扫描
  - [ ] `process_run(dry_run) -> RunSummary` 完整流程：
    1. 紧急制动检查
    2. 数据就绪检查
    3. `reader.get_latest_dt_version()`
    4. `reader.get_actionable_bots(dt_version)`
    5. 加白过滤、静默期过滤、冷却期检查（仅 closed 记录参与 cooldown 判断，expired 不阻断续催）
    6. 创建通知（含 remind_at = now+3天）
    7. 状态追踪（decision_map 驱动，不在治理范围计入正常天数）
    8. 提醒发送（remind_at 到期 → 发送提醒 Markdown，成功后 remind_count++、remind_at=NULL）
    9. 过期关闭（7天未回应）
  - [ ] `max_notify_per_run` 限流
  - [ ] per-candidate error isolation + session.rollback
  - [ ] 无分区时返回空 RunSummary 并日志告警
  - [ ] 单元测试覆盖：正常流程、加白跳过、静默期、冷却期（仅 closed）、expired 续催（不阻断）、auto_resolved、out_of_scope、
    数据未就绪、紧急制动、提醒、过期关闭
- **Depends on:** Task 2, Task 3

## Task 5: 用户反馈服务 `[ ]`

- **Goal:** 实现 resolve 接口，4 种 action 处理和联动加白。反馈直接写入
  `ac_governance_notify_log`（response/response_at/response_remark/response_source），
  不另建反馈表。
- **Files:** `core/economy/governance/feedback_service.py`, `tests/core/economy/governance/test_feedback.py`
- **Done when:**
  - [ ] `GovernanceFeedbackService.resolve(notification_id, response, remark, user_id, source, repair_deadline, feedback_payload) -> ResolveResult`
  - [ ] owner_id 校验（非 Owner 返回 403 信息）
  - [ ] 幂等：已有正式 response → 返回已有结果
  - [ ] `dispute` 时 remark 必填校验
  - [ ] `need_time` 时 repair_deadline 必填，计算 mute_until
  - [ ] `whitelist` → 调用 `whitelist_service.batch_add(source=source)` 透传 resolve() 的 source 参数（http_api / card_callback），不再硬编码 `"owner"`
  - [ ] governance_status 转换 + cooldown_until：optimized→closed+cooldown, dispute→closed+cooldown, whitelist→closed+cooldown, need_time→muted（无 cooldown）。expired 不写 cooldown
  - [ ] feedback_payload 直接存入（只校验合法 JSON）
  - [ ] 支持 pending→formal 升级（dispute_pending→dispute, whitelist_pending→whitelist）
  - [ ] 写 `GovernanceCheckAudit(action_taken='user_resolved')`
  - [ ] `list_pending(owner_id)` / `list_history(owner_id, limit)`
  - [ ] 单元测试：4 种 resolve、owner 校验、幂等、dispute remark 校验、whitelist 联动（source 透传验证）、
    need_time+mute_until、pending→formal 升级、feedback_payload
- **Depends on:** Task 1, Task 3

## Task 6: 发送服务 + 紧急制动 `[ ]`

- **Goal:** 实现 Phase 1 扫描锁内发送 pending 通知（DingTalk batchSend sampleMarkdown）
  + 紧急制动操作。Phase 1 不做交互卡片（因稳定性和复杂性问题推迟到 Phase 2），
  使用 Markdown 单向通知 + 深链接引导前端页面反馈。
  Phase 1 不做 claim/sending/send_attempt_id 机制，单实例在锁内直接发送。
- **Files:** `core/economy/governance/internal_service.py`, `core/economy/governance/emergency_service.py`, `core/economy/governance/templates.py`, `tests/core/economy/governance/test_internal.py`, `tests/core/economy/governance/test_emergency.py`, `tests/core/economy/governance/test_templates.py`
- **Done when:**
  - [ ] `GovernanceInternalService.send_pending_notifications(session)` — 查询 `notify_status='pending'` 且 `governance_status='open'` 的通知，逐条 HTTP 发送（DingTalk batchSend sampleMarkdown）
  - [ ] 发送成功 → `notify_status='sent'`, `sent_at=now()`, `external_message_id=返回值`, `send_attempt_count += 1`, `last_send_at=now()`, `last_send_error=NULL`
  - [ ] 发送失败 → 保持 `pending`, `send_attempt_count += 1`, `last_send_at=now()`, `last_send_error=error`
  - [ ] auto-cancel：已 closed/expired 且 notify_status='pending' 的通知标记 `cancelled`
  - [ ] 发送前检查紧急制动 pause → 跳过发送
  - [ ] `GOVERNANCE_NOTIFY_TEMPLATE` Markdown 模板（单向通知，含深链接引导前端页面反馈）
  - [ ] `REMIND_NOTIFY_TEMPLATE` 提醒模板（与首发相同结构，标题加超期提示前缀）
  - [ ] `render_governance_markdown(notification_structured, notification_id)` 渲染函数
  - [ ] DingTalk 发送配置 `GovernanceDingTalkConfig(app_key, app_secret, robot_code)`，
    DI 从环境变量或 SecretResolver 注入
  - [ ] `GovernanceEmergencyService.pause(reason, operator)` — 写 ZCache key（TTL 7天）
  - [ ] `GovernanceEmergencyService.resume(operator)` — 删除 ZCache key
  - [ ] `GovernanceEmergencyService.bulk_whitelist(bot_ids, reason, operator)` — 批量加白+取消pending
  - [ ] `GovernanceEmergencyService.cancel_pending(reason, operator)` — 取消所有pending通知
  - [ ] 单元测试：send_pending 成功/失败、auto-cancel、send_attempt_count、紧急制动检查、pause/resume、bulk_whitelist、cancel_pending、Markdown 模板渲染
- **Depends on:** Task 1

## Task 8: HTTP 回调端点 + 交互卡片分发器 `[ ]` (Phase 2 — 推迟)

- **Goal:** 实现 DingTalk 卡片回调的 HTTP 端点（验签+action解析+两阶段反馈处理），
  以及统一卡片/Markdown 降级的分发器。
  **Phase 1 因交互卡片稳定性和复杂性问题推迟此 Task**，Phase 2 实施。
- **Files:** `adapters/http/economy/router.py`（新增 card-callback 端点）,
  `core/economy/governance/notify_dispatcher.py`,
  `core/economy/governance/card_sender.py`,
  `tests/core/economy/governance/test_card_callback.py`,
  `tests/core/economy/governance/test_notify_dispatcher.py`
- **Done when:**
  - [ ] `POST /api/economy/governance/internal/card-callback` 端点：
    - 签名验证：`verify_dingtalk_signature(timestamp, sign, client_secret)` — HMAC-SHA256
    - 解析 `action`：`splitn(2, ':')` → `response` + `notification_id`
    - optimized/need_time → 直接 resolve（`response_source='card_callback'`）
    - dispute/whitelist → 写入 pending 态（dispute_pending/whitelist_pending）
    - 签名失败 → 401
    - 成功 → 返回卡片更新内容
  - [ ] `GovernanceNotifyDispatcher.__init__(card_sender | None, internal_service, config)`
  - [ ] `dispatch(notification) -> bool`：
    - card_sender 存在 → `send_card()`
    - card_sender 为 None → 通知保持 pending（等扫描锁内首发循环发送 Markdown）
    - send_card 异常 → 日志 warn，降级到 Markdown
  - [ ] 单元测试：签名验证/伪造拒绝、action 解析、两阶段反馈、卡片通道、Markdown 降级
- **Depends on:** Task 5, Task 6

## Task 10: HTTP 层 `[ ]`

- **Goal:** 公开路由 + 内部路由 + schemas + auth（economy 模块，governance 子路由）。
- **Files:** `adapters/http/economy/__init__.py`, `router.py`, `schemas.py`, `auth.py`
- **Done when:**
  - [ ] 公开路由 `/api/economy/governance` 6 个端点
    （notifications/history/detail/resolve + whitelist/batch+list）
  - [ ] 内部路由 `/api/economy/governance` 4 个端点（Bearer token 鉴权）：
    - `POST /records/offline-batch`（与 `/api/harness/diagnose/records/offline-batch` 平行）
    - `POST /internal/trigger-scan`
    - `POST /internal/emergency`
    - `GET /internal/emergency`
  - [ ] `GovernanceNotifyResolveRequest` schema 含 response + remark + repair_deadline + feedback_payload
  - [ ] `verify_economy_internal_token` auth 依赖（Bearer token 模式同 dormant）
  - [ ] `trigger-scan` 可选 `dry_run` 查询参数
  - [ ] 所有端点使用 `Injected()` 注入服务
- **Depends on:** Task 4, Task 5, Task 6

## Task 11: DI 绑定 + 配置 + 挂载 `[ ]`

- **Goal:** 将所有组件注册到 DI 容器并挂载路由。
- **Files:** `di/modules/economy_governance_module.py`, `di/config.py`, `di/container.py`, `adapters/http/app.py`, `plugins/local/database.py`
- **Done when:**
  - [ ] `GovernanceConfig(dry_run=True, scan_hour=14, max_notify_per_run=200, cooldown_days=14, auto_resolve_threshold_days=3, mute_grace_days=7, expire_days=7)` 在 `di/config.py`
  - [ ] `EconomyInternalToken` 在 `di/config.py`
  - [ ] `EconomyGovernanceModule` 注册所有单例 + provider
  - [ ] `_resolved_economy_token` 使用 `SecretResolver`（Mist secret 名称含 economy 前缀）
  - [ ] `container.py` 注册 `EconomyGovernanceModule`
  - [ ] `app.py` 挂载路由 `/api/economy/governance`
  - [ ] `plugins/local/database.py` bootstrap() 中导入 `core.economy.governance.sqlite_models`（仅可写表）
  - [ ] App 启动无报错，新路由可达
- **Depends on:** Task 10

## Task 12: 生命周期集成 `[ ]`

- **Goal:** 将扫描 Cron 统一在 Lifecycle 中管理。
- **Files:** `core/economy/governance/lifecycle.py`, `tests/core/economy/governance/test_lifecycle.py`
- **Done when:**
  - [ ] `GovernanceBotLifecycle(LifecycleBase)`:
    - `startup()`: 启动扫描 Cron daemon thread（`target_hour` 从 config 读取）
    - `shutdown()`: 停止 Cron thread
    - `_run_scan()`: 分布式锁 + `service.process_run()` + 结构化日志
    - `scan_lock = 'governance_scan_lock:{env}'`
    - `lock_ttl = 1800`
  - [ ] 单元测试：startup/shutdown、Cron 触发 + 分布式锁
- **Depends on:** Task 4

## Task 13: 端到端验证 `[ ]`

- **Goal:** 验证完整闭环：扫描 → 通知 → 反馈 → 审计（Markdown 通道 + 紧急制动）。
- **Files:** `tests/core/economy/governance/test_e2e.py` (集成测试)
- **Done when:**
  - [ ] mock ODPS 数据 + offline-batch 写入 → trigger-scan → 验证 notify_log 有 pending 记录
  - [ ] 扫描锁内自动发送 pending 通知 → notify_status 变为 sent + sent_at + external_message_id
  - [ ] HTTP resolve(optimized) → feedback 记录 + notify_log.response 更新 + audit 记录
  - [ ] resolve(whitelist) → 同上 + whitelist 表新增（whitelist_type='governance'）
  - [ ] resolve(dispute) → remark 为空时 400
  - [ ] resolve(need_time) → repair_deadline 为空时 400，mute_until 正确设置
  - [ ] 加白后 trigger-scan → 该 Bot 被跳过
  - [ ] closed 后冷却期内不创建新通知；expired 不写 cooldown，不阻断续催，继承 governance_cycle_id 创建新记录
  - [ ] 连续 N 天非 actionable → auto_resolved
  - [ ] 紧急制动 pause → scan 跳过，resume → scan 恢复
  - [ ] offline-batch 路径可达且 Bearer token 鉴权生效
  - [ ] dry_run=True → 扫描执行，通知不写入
  - [ ] Python backend 启动成功，无新增报错
- **Depends on:** Task 11, Task 12

---

## Groups

- **Group A — 数据层：** Tasks 1, 2, 2b, 3
  - 主题：ORM 模型(4表) + 在线库读取 + 离线批量写入 + 加白管理，为业务逻辑提供基础。

- **Group B — 业务逻辑：** Tasks 4, 5, 6
  - 主题：扫描编排 + 用户反馈 + Markdown单向通知发送/紧急制动，核心业务闭环。

- **Group C — 接入层：** Tasks 10, 11, 12
  - 主题：HTTP + DI + 生命周期，将业务逻辑接入系统。

- **Group D — 验证：** Task 13
  - 主题：端到端集成测试，确认全链路闭环（Markdown通知 + HTTP API反馈）。

- **Group E — Phase 2（推迟）：** Task 8
  - 主题：交互卡片 + HTTP 回调端点。因交互卡片稳定性和复杂性问题，推迟到 Phase 2 实施。
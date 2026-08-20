# task_discovery 后端定时发起 + Session 消息注入

## 概述

将 `task_discovery` 模块的定时调度从 `asyncio.sleep()` 改为 APScheduler BackgroundScheduler（线程级，非 asyncio）。session 创建从 `HttpSessionCreator`（直连 HTTP）改为 `CronRelaySessionInitiator`（经 relay 通道 + WebSocket `chat.send` 注入发现提示消息）。方向保持 backend → engine，engine 侧零改动。

**关联文档**：
- `design.md` — 技术设计 (HOW)
- `tasks.md` — TDD 任务清单
- 前置 spec：`2026-08-18-task-discovery`（模块落地）、`2026-08-18-task-discovery-autoinitiate-merge`（通知集成）

---

## 需求列表

### REQ-1: 调度器替换 — asyncio.sleep → APScheduler

- **描述**：删除 `TaskDiscoveryLifecycle`（asyncio.sleep while 循环），新增 `TaskDiscoveryScheduler` 使用 APScheduler BackgroundScheduler（线程级 cron 调度）
- **验收标准**:
  - `TaskDiscoveryScheduler` 继承 `LifecycleBase`，实现 `startup()` / `shutdown()`
  - `startup()` 读取 `TASK_DISCOVERY_AUTO_START`、`TASK_DISCOVERY_CRON`、`TASK_DISCOVERY_TIMEZONE` 环境变量
  - 调度在独立线程中运行，不占用 asyncio 事件循环
  - 定时触发时通过 `asyncio.run()` 调用 `DiscoveryService.discover_all_bots()`
  - `shutdown()` 正确停止 scheduler（`wait=False`）
  - DreamMode 开启 → `enable_for_bot()` 确保调度器运行
  - DreamMode 关闭 → `disable_for_bot()` 停止调度
- **场景**:
  - 当 backend startup 且 `TASK_DISCOVERY_AUTO_START=true`，则 scheduler 启动并按 cron 表达式调度
  - 当 `TASK_DISCOVERY_AUTO_START=false`，则 scheduler 不启动
  - 当 backend shutdown，则 scheduler 正确停止
  - 当 `POST /dream-mode?enabled=true`，则调度器确保运行
  - 当 `POST /dream-mode?enabled=false`，则调度器停止

### REQ-2: 模型字段扩展 — DiscoveredTask 增加 bot_id/owner_id/dt

- **描述**：`DiscoveredTask` 新增 `bot_id`、`owner_id`、`dt` 字段，支持按 (bot_id, owner_id, dt) 维度查询
- **验收标准**:
  - `DiscoveredTask` 新增字段：`bot_id: str`、`owner_id: str`、`dt: str`（YYYY-MM-DD）
  - `task_id` 格式改为 `discover_task_{bot_id}_{owner_id}_{dt}`
  - `to_session_ext_info()` 追加 `bot_id`/`owner_id`/`dt` 字段
  - 新增 `to_discovery_prompt()` — 生成给 bot 的发现提示消息
  - 新增 `to_notification_body(task_count)` — 生成通知消息体
  - 新增 `to_card_data()` — 生成交互卡片数据（通用，不绑定服务商）
- **场景**:
  - 当调用 `to_discovery_prompt()`，则返回包含项目名称、简介、业务场景、发现依据、确认引导的消息文本
  - 当调用 `to_notification_body(3)`，则返回包含「发现 3 件可能有意义的事情」摘要和 session 引导的通知文本

### REQ-3: DB Schema 改造 — 按 (bot_id, owner_id, dt) 查询

- **描述**：`discovered_tasks` 表增加 `bot_id`/`owner_id`/`dt` 列和索引，新增按维度查询方法
- **验收标准**:
  - DDL 包含 `bot_id TEXT NOT NULL`、`owner_id TEXT NOT NULL`、`dt TEXT NOT NULL`
  - 索引 `idx_discovered_tasks_bot_owner_dt` on `(bot_id, owner_id, dt)`
  - `SqliteTaskReader` 新增 `read_pending_tasks_for_bot(bot_id, owner_id, dt)` 方法
  - `init_discovered_tasks_db()` 支持写入含 `bot_id`/`owner_id`/`dt` 的数据
  - `read_pending_tasks()` / `read_discovered_tasks()` 保留向后兼容
- **场景**:
  - 当写入包含 bot_id/owner_id/dt 的 mock 数据并调用 `read_pending_tasks_for_bot("bot-001", "user-001", "2026-08-19")`，则返回匹配且状态为 `pending_confirmation` 的任务
  - 当查询不匹配的 bot_id，则返回空列表

### REQ-4: Session 创建+消息注入 — CronRelaySessionInitiator

- **描述**：删除 `HttpSessionCreator`，新增 `CronRelaySessionInitiator` 通过 relay 通道创建 session + WebSocket `chat.send` 注入发现提示消息
- **验收标准**:
  - `SessionInitiator` Protocol 定义 `initiate_session(tasks, bot_id, owner_id, agent_id, model) -> DiscoverySession`
  - `CronRelaySessionInitiator` Step 1 通过 `CronRelayService.forward_request(POST /api/sessions)` 创建 session
  - Step 2 解析 engine target 地址
  - Step 3 WebSocket 连 engine → `connect` 握手 (protocol v3) → `chat.send` 发送发现提示消息
  - 发现提示消息由 `_build_discovery_prompt(tasks)` 生成
  - 默认 `wait_for_reply=False`（发完即走）；测试中 `wait_for_reply=True` 等待 `state=final`
  - WebSocket 协议格式同 `test_create_session_e2e.py:128-179`
  - 消息注入失败仅 log warning，不抛异常 — session 已创建 = 主流程成功
  - 返回 `DiscoverySession`（含 session_id + session_url）
- **场景**:
  - 当 relay 创建 session 成功且 WS chat.send 成功，则返回含 session_id 的 DiscoverySession
  - 当 relay 创建 session 成功但 WS 注入失败（engine 离线），则返回 DiscoverySession，log warning
  - 当 relay 创建 session 失败，则抛 RuntimeError 记录于 `DiscoveryResult.error`
  - 当 `wait_for_reply=True` 且 bot 回复 `state=final`，则 WS 正常关闭

### REQ-5: DiscoveryService 改造

- **描述**：移除 `SessionCreator` 依赖，改用 `SessionInitiator`；新增 `discover_all_bots()`；通知消息改造
- **验收标准**:
  - `DiscoveryService.__init__()` 接受 `reader`、`session_initiator`、`notify_sender`
  - `discover_all_bots()` 遍历 BotService.list_bots() → 为每个 bot 执行发现
  - `discover(bot_id, owner_id, agent_id, model)` 为单个 bot 执行发现
  - `_discover_single()` 调用 `session_initiator.initiate_session()` → `_send_notification()`
  - `_send_notification()` 构造 `NotifyMessage`，body 为 `to_notification_body(task_count)`，`deep_link` 为 session_url
  - `NotifyMessage.extra` 包含 `channel`/`card_template_id`/`card_biz_id`/`card_data`/`session_url`
  - `card_template_id` 从环境变量 `TASK_DISCOVERY_CARD_TEMPLATE_ID` 读取（通用名，不含服务商前缀）
  - 代码中不出现任何具体服务商品牌名
- **场景**:
  - 当 scheduler 定时调用 `discover_all_bots()`，则遍历所有 bot，每个有任务的 bot 创建 session+注入消息+发通知
  - 当手动调用 `discover(bot_id, owner_id, agent_id)`，则为指定 bot 创建 session+注入+通知
  - 当 bot 无待确认任务，则返回空列表

### REQ-6: HTTP 端点改造

- **描述**：新增 `/scheduled-trigger`、`/dream-mode` 端点；`/discover` 改为直接调用
- **验收标准**:
  - `POST /api/public/task-discovery/scheduled-trigger` — 外部 scheduler 触发，调用 `discover_all_bots()`
  - `POST /api/public/task-discovery/discover` — 手动触发，调用 `discover(bot_id, owner_id, agent_id, model)`
  - `POST /api/public/task-discovery/dream-mode` — DreamMode 开关，权限校验 ownership
  - `GET /api/public/task-discovery/status` — 支持按 bot_id/owner_id 过滤
  - 所有端点通过 `Injected()` 注入依赖
- **场景**:
  - 当外部 scheduler 调用 `POST /scheduled-trigger`，则执行 `discover_all_bots()`
  - 当用户调用 `POST /dream-mode?enabled=true&bot_id=X&owner_id=Y`，则确认 bot ownership 后启用调度
  - 当用户对非自己拥有的 bot 调用 `/dream-mode`，则返回失败

### REQ-7: DI 模块改造

- **描述**：`TaskDiscoveryModule` 移除 Lifecycle 绑定，新增 Scheduler/Service/Initiator provider
- **验收标准**:
  - 移除 `TaskDiscoveryLifecycle` singleton bind
  - 新增 `TaskDiscoveryScheduler` singleton bind
  - 新增 `DiscoveryService` singleton provider（注入 reader + initiator + notify_sender）
  - 新增 `SessionInitiator` singleton provider（注入 cron_relay）
  - 新增 `TaskReader` singleton provider
  - 保留 `_bridge_bot_service_protocol` provider
  - 新增 `_bridge_cron_relay_protocol` provider
- **场景**:
  - 当 backend startup，则 DI 容器正确解析所有绑定，`TaskDiscoveryScheduler` 通过 lifecycle 自动发现并启动

### REQ-8: 文件删除

- **描述**：删除 asyncio 调度和直连 HTTP session 创建的旧代码
- **验收标准**:
  - `lifecycle.py` 删除
  - `session_creator.py` 删除
  - 无其他模块引用已删除的类
- **场景**: N/A

### REQ-9: 协议层改造

- **描述**：`protocols.py` 新增 `CronRelayServiceProtocol` 引用
- **验收标准**:
  - `BotServiceProtocol` 新增 `get_bot()` 方法签名
  - 新增 `CronRelayServiceProtocol`（含 `forward_request`、`list_all_crons`）
- **场景**: N/A

### REQ-10: E2E 测试

- **描述**：更新 e2e 测试覆盖新流程：scheduler 触发 → session 创建 → WS 消息注入 → 通知组装
- **验收标准**:
  - e2e 仍 gated（`SINGLEBOX_TASK_E2E=1`）
  - mock 数据使用新格式（含 bot_id/owner_id/dt）
  - 验证 session 创建经 relay 通道
  - 验证 WebSocket chat.send 发现提示消息（`wait_for_reply=True`）
  - 验证通知 `extra` 包含卡片参数格式
  - 验证 `GET /status` 支持新格式数据
- **场景**:
  - 当 e2e 启用且有 mock 任务，则 discover 全链路跑通：session 创建 + WS 注入 + 通知发送

---

## 约束

- core 层 transport-agnostic — `DiscoveryService` 不 import transport
- `CronRelaySessionInitiator` 位于 core 层，通过 `CronRelayServiceProtocol` 隔离 relay 实现
- `NotifySenderPlugin.send()` 从不抛异常（Protocol 约定）
- HTTP adapter thin — router 不持领域策略
- 代码中禁止出现任何具体服务商品牌名
- 通知接口为通用抽象，社区实现 log-only，企业部署自行实现发送通道
- WebSocket 协议格式参考 `test_create_session_e2e.py:128-179` 已验证实现
- engine 侧零改动 — 复用现有 `/api/{engine}/ws` + `chat.send` 处理器

## 需求就绪度

9/10 — 范围明确，代码事实清晰，WebSocket 协议已有 e2e 参考实现，DI 绑定路径已验证。

## 验收对齐检查

| 目标 | 覆盖的验收标准 | 是否充分 |
|---|---|---|
| 定时调度非 asyncio | REQ-1 | 是 |
| backend → engine 方向不反转 | REQ-4, REQ-5 | 是 |
| session 中 bot 主动告知用户 | REQ-4（WS chat.send） | 是 |
| session 创建后发通知 | REQ-5 | 是 |
| engine 侧零改动 | 全部 | 是 |
| DreamMode 启停 | REQ-1, REQ-6 | 是 |

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-19 | 初始创建 — 基于 `frolicking-exploring-ritchie.md` 方向修订 |
| 2026-08-19 | 方案从 A（纯 extInfo）升级为 A+WS（WebSocket chat.send 注入） |
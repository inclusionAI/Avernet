# task_discovery × autoInitiate 合并（方案 B）

## 概述

将 `task_discovery` 模块的编排流程从"读 SQLite → 建被动 session → 等用户确认"改为"读 SQLite → 有 `work_item_url` 则走 autoInitiate 执行 / 无则走 SessionCreator 仅展示 → 执行成功后发送通知"。通过新增 `TaskInitiator` Protocol 与 `AutoInitiateExecutor` 实现，与 `CronRelayServiceProtocol` 解耦集成；通过 `NotifySenderPlugin` Protocol 发送通知。

## 需求列表

### REQ-1: TaskInitiator Protocol + AutoInitiateExecutor
- **描述**: 新增 `TaskInitiator` Protocol 定义任务执行接口；`AutoInitiateExecutor` 实现通过注入 `CronRelayServiceProtocol` 调用 `run_single_auto_initiate()`
- **验收标准**:
  - `TaskInitiator` Protocol 定义 `async initiate(task, user_id, agent_id, model?) -> InitiateResult`
  - `AutoInitiateExecutor` 通过 `@inject` 注入 `CronRelayServiceProtocol`
  - `DiscoveredTask.work_item_url` 映射为 `run_single_auto_initiate` 的 `dima_url` 参数
  - `DiscoveredTask` 的 `discovery_basis` + `business_scenario` 映射为 `append_message`
  - 返回 `InitiateResult` 含 session_id、session_url（从 engine 响应中提取）
- **场景**:
  - 当任务有 `work_item_url` 且 bot 设备在线, 则 `AutoInitiateExecutor.initiate()` 成功返回 `InitiateResult(success=True)`
  - 当任务有 `work_item_url` 但 bot 无设备绑定, 则 `InitiateResult(success=False, error="...")`

### REQ-2: InitiateResult 模型
- **描述**: 新增 `InitiateResult` dataclass 记录 autoInitiate 执行结果
- **验收标准**:
  - 字段：`task_id`, `session_id`, `session_url`, `success`, `error`, `raw_response`
  - frozen dataclass
- **场景**:
  - 当 autoInitiate 执行成功, 则 `InitiateResult.success=True` 且 `session_id` 非空
  - 当 autoInitiate 执行失败, 则 `InitiateResult.success=False` 且 `error` 含错误信息

### REQ-3: DiscoveryService 编排扩展
- **描述**: `DiscoveryService` 新增 `task_initiator` + `notify_sender` 依赖，编排逻辑分为两条路径
- **验收标准**:
  - `DiscoveryService.__init__()` 接受 `reader`, `session_creator`, `task_initiator`, `notify_sender`
  - 有 `work_item_url` 的任务 → `task_initiator.initiate()` → 成功后 `notify_sender.send()`
  - 无 `work_item_url` 的任务 → `session_creator.create_session()` → 成功后 `notify_sender.send()`
  - 任何执行失败不阻塞其他任务（per-task try/except）
  - `create_default_service()` 工厂方法支持构建含全部依赖的服务
- **场景**:
  - 当 3 个待确认任务（2 个有 URL，1 个无 URL）, 则 2 个走 autoInitiate 执行+通知，1 个走 session 创建+通知
  - 当某任务 autoInitiate 执行失败, 则该任务标记 `error`，其余任务不受影响

### REQ-4: NotifySenderPlugin 集成
- **描述**: autoInitiate 执行成功后通过 `NotifySenderPlugin.send()` 发通知
- **验收标准**:
  - 通知在执行成功后发送（非执行前）
  - `NotifyMessage.title` 为"发现并启动了新任务"或"发现待确认任务"（执行 vs 展示）
  - `NotifyMessage.body` 含任务详情（项目名称、简介、业务场景、挖掘依据）
  - `NotifyMessage.recipient` 为 `user_id`
  - `NotifyMessage.deep_link` 为 `session_url`（若可用）
  - community 版 `CommunityNotifySender` log-only，不抛异常
  - 通知发送失败不阻塞发现流程（catch + log）
- **场景**:
  - 当 autoInitiate 执行成功且通知发送成功, 则 `DiscoveryResult.notification_sent=True`
  - 当 autoInitiate 执行成功但通知发送失败, 则日志记录但不报错，任务仍标记为成功

### REQ-5: TaskDiscoveryLifecycle 适配
- **描述**: `_discover_once()` 创建含新依赖的 `DiscoveryService`
- **验收标准**:
  - lifecycle 通过 `@inject` 注入所需依赖（`BotService` 已有，新增按需）
  - `_discover_once()` 对每个 bot 构建含 `TaskInitiator` + `NotifySenderPlugin` 的 `DiscoveryService`
  - 定时调度启动/取消行为不变
- **场景**:
  - 当 backend startup 且 `TASK_DISCOVERY_AUTO_START=true`, 则 lifecycle 正常调度
  - 当定时触发发现, 则对所有 bot 执行"读任务→autoInitiate→通知"流程

### REQ-6: HTTP Router 适配
- **描述**: `POST /discover` 端点适配新流程
- **验收标准**:
  - `POST /api/public/task-discovery/discover` 返回含 autoInitiate 执行结果的结构化响应
  - 响应中每个 task 包含 `initiated`（是否走了 autoInitiate）和 `session_id`/`session_url`
  - `GET /status` 行为不变
- **场景**:
  - 当手动触发 `POST /discover`, 则返回结构化结果含每个任务的执行路径和结果

### REQ-7: DI 接线
- **描述**: `TaskDiscoveryModule` 新增 `TaskInitiator` 绑定
- **验收标准**:
  - `TaskDiscoveryModule.configure()` 绑定 `TaskInitiator` → `AutoInitiateExecutor`（singleton）
  - `AutoInitiateExecutor` 的 `CronRelayServiceProtocol` 依赖由 `CronModule` 已有绑定解析
  - `NotifySenderPlugin` 依赖由 `CommunityNotifyModule` 已有绑定解析
  - `TaskDiscoveryModule` 在 `container.py` 中已注册（保持不变）
- **场景**:
  - 当 DI 容器构建, 则 `TaskInitiator` 可注入且 `AutoInitiateExecutor` 的依赖链完整

### REQ-8: E2E 测试更新
- **描述**: 更新 e2e 测试断言验证 autoInitiate 触发
- **验收标准**:
  - e2e 仍 gated（`SINGLEBOX_TASK_E2E=1`）
  - 测试数据中包含有/无 `work_item_url` 的任务
  - 断言有 URL 的任务走了 autoInitiate 执行路径
  - 断言无 URL 的任务走了 session 创建路径
  - 断言通知日志可见
- **场景**:
  - 当 e2e 启用且有 mock 任务, 则 discover 端点返回含 `initiated` 字段的结果

## 约束

- core 层 transport-agnostic — `TaskInitiator` Protocol 不 import transport
- `AutoInitiateExecutor` 通过 Protocol seam 注入 `CronRelayServiceProtocol`，不直接 import transport
- HTTP adapter thin — router 不持领域策略
- `NotifySenderPlugin.send()` 从不抛异常（Protocol 约定）
- 不破坏现有 `SessionCreator` / `EngineSessionCreator` 接口

## 需求就绪度

8/10 — 所有必答问题已确认，代码事实清晰，Protocol seam 和 DI 绑定充分。

## 验收对齐检查

| 目标 | 覆盖的验收标准 | 是否充分 |
|---|---|---|
| 合并 task_discovery 与 autoInitiate | REQ-1, REQ-3, REQ-5 | 是 |
| 有 work_item_url → 自动执行 | REQ-1, REQ-3 | 是 |
| 无 work_item_url → 仅展示 | REQ-3 | 是 |
| 执行成功后发通知 | REQ-4 | 是 |
| 保留双 Protocol | REQ-1, REQ-3 | 是 |
| 修改所有 task_discovery 文件 | REQ-1~REQ-8 | 是 |
| 解耦设计 | REQ-1, REQ-7 | 是 |

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-18 | 初始创建 |

# task_discovery 通知集成

## 概述

在 `DiscoveryService` 创建 engine session 成功后，通过注入的 `NotifySenderPlugin` 投递通知。所有任务统一走现有 `SessionCreator` 路径，不引入 autoInitiate 执行、不按 `work_item_url` 分支。

## 需求列表

### REQ-1: DiscoveryService 通知集成
- **描述**: `DiscoveryService` 新增 `notify_sender` 依赖，session 创建成功后调用 `NotifySenderPlugin.send()`
- **验收标准**:
  - `DiscoveryService.__init__()` 接受 `reader`, `session_creator`, `notify_sender`
  - `_discover_single()` 中 session 创建成功后调 `_send_notification()`
  - `_send_notification()` 构造 `NotifyMessage(title, body, recipient, deep_link)` 调 `send()`
  - `NotifyMessage.title` 为"发现待确认任务"
  - `NotifyMessage.body` 由 `task.to_notification_message(session_url)` 生成
  - `NotifyMessage.recipient` 为 `user_id`
  - `NotifyMessage.deep_link` 为 `session_url`
  - 通知发送不阻塞发现流程（Protocol 约定从不抛异常）
  - `create_default_service()` 工厂方法支持构建含 `notify_sender` 的服务
- **场景**:
  - 当 session 创建成功且通知发送成功, 则 `DiscoveryResult.notification_sent=True`
  - 当 session 创建成功但通知发送返回 None, 则 `DiscoveryResult.notification_sent=False`，任务仍标记为成功
  - 当 session 创建失败, 则不发通知，`DiscoveryResult.notification_sent=False`

### REQ-2: DiscoveryResult 字段扩展
- **描述**: `DiscoveryResult` 新增 `notification_sent` 字段
- **验收标准**:
  - 字段：`notification_sent: bool = False`
  - `success` property 逻辑不变（`session is not None and error is None`）
- **场景**:
  - 当 session 创建成功, 则 `notification_sent` 反映通知发送结果
  - 当 session 创建失败, 则 `notification_sent=False`

### REQ-3: TaskDiscoveryLifecycle 适配
- **描述**: lifecycle 通过 `@inject` 注入 `NotifySenderPlugin`，传给 `DiscoveryService`
- **验收标准**:
  - `TaskDiscoveryLifecycle.__init__()` 新增 `@inject` 参数 `notify_sender: NotifySenderPlugin`
  - `_discover_once()` 构建服务时传入 `notify_sender`
  - 定时调度启动/取消行为不变
- **场景**:
  - 当 backend startup 且 `TASK_DISCOVERY_AUTO_START=true`, 则 lifecycle 正常调度
  - 当定时触发发现, 则 session 创建成功后发通知

### REQ-4: HTTP Router 适配
- **描述**: `POST /discover` 端点注入 `NotifySenderPlugin` 并在响应中包含通知状态
- **验收标准**:
  - `discover_tasks()` 通过 `Injected(NotifySenderPlugin)` 获取依赖
  - `_build_service()` 接受并传递 `notify_sender`
  - 响应中每个 task 包含 `notification_sent` 字段
  - `GET /status` 行为不变
- **场景**:
  - 当手动触发 `POST /discover`, 则返回结果含每个任务的 `notification_sent`

### REQ-5: __init__.py docstring 更新
- **描述**: 更新模块 docstring 反映通知投递
- **验收标准**:
  - docstring 包含"创建 session + 投递通知"描述
- **场景**: N/A

### REQ-6: E2E 测试更新
- **描述**: e2e 测试加通知日志断言
- **验收标准**:
  - e2e 仍 gated（`SINGLEBOX_TASK_E2E=1`）
  - 断言通知日志可见（`CommunityNotifySender` log 输出）
  - 断言响应含 `notification_sent` 字段
- **场景**:
  - 当 e2e 启用且有 mock 任务, 则 discover 响应含 `notification_sent` 且日志中可见通知发送记录

## 约束

- core 层 transport-agnostic — `DiscoveryService` 不 import transport
- `NotifySenderPlugin.send()` 从不抛异常（Protocol 约定）
- HTTP adapter thin — router 不持领域策略
- 不破坏现有 `SessionCreator` / `EngineSessionCreator` 接口
- `NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定，`TaskDiscoveryModule` 无需修改

## 需求就绪度

9/10 — 范围明确，代码事实清晰，DI 绑定已验证。

## 验收对齐检查

| 目标 | 覆盖的验收标准 | 是否充分 |
|---|---|---|
| session 创建后发通知 | REQ-1, REQ-3 | 是 |
| 通知失败不阻塞流程 | REQ-1 | 是 |
| 响应含通知状态 | REQ-4 | 是 |
| 复用现有 NotifySenderPlugin 绑定 | REQ-1, REQ-3 | 是 |
| 不引入 autoInitiate 分支 | 全部 | 是 |

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-18 | 初始创建 |
| 2026-08-18 | 方向修订：从 autoInitiate 合并收窄为通知集成 |

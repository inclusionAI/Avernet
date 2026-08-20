# task_discovery 通知集成

## 背景

`task_discovery` 模块（`core/task/task_discovery/`）当前流程：读 SQLite mock → 创建 engine session → 生成通知文本（仅存在 `DiscoveryResult.notification_message` 字段中，不投递到任何通知通道）。

用户不知道有任务待处理，除非主动查询 `GET /api/public/task-discovery/status` 或查看 engine session。

`NotifySenderPlugin` Protocol + `CommunityNotifySender`（log-only）已由 `CommunityNotifyModule` 绑定为 singleton，可直接注入使用。

## 问题定义

**通知投递缺失**：发现任务并创建 session 后，通知消息仅作为字符串返回，未通过任何通道投递给用户。用户无从感知"有新任务被发现了"。

## 目标

在 `DiscoveryService` 创建 session 成功后，通过 `NotifySenderPlugin.send()` 投递通知。

- 所有任务统一走"创建 session + 展示 + 通知"单一路径
- 通知在 session 创建成功后发送
- 通知失败不阻塞发现流程（`NotifySenderPlugin` Protocol 约定从不抛异常）

## 用户/角色

| 角色 | 关注点 |
|---|---|
| 框架维护者 | Protocol seam 可插拔；transport-agnostic |
| corp 接入者 | `NotifySenderPlugin` 替换为钉钉通道即可 |
| singlebox/CI | e2e gated 测试验证通知日志可见 |

## 范围

| 文件 | 变更类型 |
|---|---|
| `discovery_service.py` | `DiscoveryService` 新增 `notify_sender` 依赖 + `_send_notification()`；`DiscoveryResult` 加 `notification_sent` 字段 |
| `lifecycle.py` | `_discover_once()` 注入并传 `NotifySenderPlugin` |
| `router.py` | `_build_service()` 适配；响应格式加 `notification_sent` |
| `__init__.py` | 更新 docstring |
| `test_task_discovery_e2e.py` | 加通知日志断言 |

## 非范围

- **不**引入 `TaskInitiator` / `AutoInitiateExecutor` — autoInitiate 执行不在 discovery 阶段
- **不**按 `work_item_url` 分支 — 所有任务统一走 SessionCreator 路径
- **不**实现真实钉钉机器人通知（corp 侧 `DingTalkNotifySender` 负责）
- **不**修改 `models.py`（复用现有 `to_notification_message()`）
- **不**修改 `task_reader.py` / `session_creator.py`
- **不**修改 `task_discovery_module.py`（`NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定）
- **不**修改 engine 侧代码
- **不**做前端页面变更

## AI Assumptions

| 假设 | 风险 | 可逆性 |
|---|---|---|
| `NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定为 singleton | 低 — 代码已确认 `notify.py:22` | 高 |
| `NotifySenderPlugin.send()` 从不抛异常 | 低 — Protocol 约定 | 高 |
| `Injected()` 可在 task_discovery router 中使用 | 低 — `cron_noauth_router` 已用同一模式 | 高 |

## Decision Log

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-08-18 | 推翻原方案 B（autoInitiate 合并），收窄为通知集成 | 用户明确：discover 阶段所有任务仅展示等确认，autoInitiate 执行是后续独立步骤 |
| 2026-08-18 | 不按 `work_item_url` 分支 | 数据字段不应决定代码走向；所有任务统一走 SessionCreator |

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-18 | 初始创建 |
| 2026-08-18 | 方向修订：从"autoInitiate 合并"收窄为"通知集成" |

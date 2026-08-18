# task_discovery × autoInitiate 合并（方案 B）

## 背景

`task_discovery` 模块（`core/task/task_discovery/`）当前实现独立流程：读 SQLite mock → 创建 engine session → 等用户确认。与 `core/cron/services/cron_relay.py` 的 `autoInitiate` 体系（查询 DIMA → 过滤 → 直接执行）是两条平行路径。

用户洞察：如果"发现"最终都走向"自动执行"，中间的人工确认是可选的，那 task_discovery 读 mock SQLite → 建 session 等确认 → 人工触发执行的路径就是冗余的。`autoInitiate` 已经做完了 DIMA 查询和过滤，`TaskDiscoveryLifecycle` 读 mock SQLite 只是过渡方案。

## 问题定义

1. **路径冗余**：task_discovery 创建被动 session 等用户确认，autoInitiate 直接执行——两套独立流程做类似的事
2. **通知缺失**：发现任务后无通知机制，用户不知道有任务待处理
3. **执行衔接断裂**：task_discovery 建了 session 但无法自动触发执行，需用户手动确认后才能走执行框架

## 目标

将 task_discovery 与 autoInitiate 合并：
- 读 SQLite 发现的任务 → 有 `work_item_url` 的任务直接通过 autoInitiate 执行 → 执行成功后通过 `NotifySenderPlugin` 发通知
- 无 `work_item_url` 的任务仅创建 session 展示 + 发通知（不执行）
- 保留 `SessionCreator` Protocol 作为降级路径

## 用户/角色

| 角色 | 关注点 |
|---|---|
| 框架维护者 | Protocol seam 可插拔；transport-agnostic；与 cron 模块解耦 |
| corp 接入者 | `NotifySenderPlugin` 替换为钉钉通道即可 |
| singlebox/CI | e2e gated 测试验证 autoInitiate 触发 |

## 范围

修改现有所有 `task_discovery` 文件：

| 文件 | 变更类型 |
|---|---|
| `models.py` | 新增 `InitiateResult`；`DiscoveredTask` 增加方法；`DiscoveryResult` 扩展 |
| `discovery_service.py` | 编排扩展：新增 `TaskInitiator` + `NotifySenderPlugin` 依赖；分流逻辑 |
| `session_creator.py` | 新增 `TaskInitiator` Protocol + `AutoInitiateExecutor` 实现 |
| `lifecycle.py` | `_discover_once()` 注入新依赖；服务创建变更 |
| `router.py` | `_build_service()` 适配；响应格式更新 |
| `task_discovery_module.py` | 新增 `TaskInitiator` 绑定 |
| `__init__.py` | 更新 docstring |
| `task_reader.py` | 不变（读 SQLite 逻辑保留） |
| `test_task_discovery_e2e.py` | 更新断言（验证 autoInitiate 执行） |

## 非范围

- **不**实现真实钉钉机器人通知（corp 侧 `DingTalkNotifySender` 负责）
- **不**替换 SQLite mock 为真实数据源（仍为过渡方案）
- **不**修改 engine 侧 autoInitiate 插件代码
- **不**做前端页面变更
- **不**修改 cron 模块核心逻辑（仅通过 Protocol 注入调用）

## Open Questions

所有必答问题已在 Clarification 阶段确认：
- 执行路径：通过 Protocol seam 注入 `CronRelayServiceProtocol`（解耦）
- 无 `work_item_url`：仅展示 + 通知，不报错
- 通知时机：执行成功后
- Protocol 保留：双 Protocol 并存

## AI Assumptions

| 假设 | 风险 | 可逆性 |
|---|---|---|
| `CronRelayServiceProtocol` 已由 `CronModule` 绑定为 singleton | 低 — 代码已确认 `cron_module.py:44` | 高 |
| `NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定为 singleton | 低 — 代码已确认 `notify.py:22` | 高 |
| `AutoInitiateExecutor` 通过 `@inject` 注入 `CronRelayServiceProtocol` | 低 — 与 `cron_noauth_router.py` 使用 `Injected()` 一致 | 高 |
| 无 `work_item_url` 的任务走 `SessionCreator` 降级路径 | 低 — 保留现有 Protocol | 高 |

## Decision Log

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-08-18 | 选用方案 B（合并 task_discovery 与 autoInitiate） | 用户明确选择：发现→自动执行，人工确认可选 |
| 2026-08-18 | 执行路径通过 Protocol seam 注入 | 用户要求解耦；`CronRelayServiceProtocol` 已绑定 |
| 2026-08-18 | 保留双 Protocol（SessionCreator + TaskInitiator） | 用户确认保留降级路径 |
| 2026-08-18 | 无 work_item_url 的任务仅展示+通知 | 用户确认：work_item_url 是原始记录，可不报错 |

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-18 | 初始创建 |

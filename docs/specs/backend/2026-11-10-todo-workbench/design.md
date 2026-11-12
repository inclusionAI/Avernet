# 待办工作台自动执行设计

## 架构与领域归属

- Todo Core 拥有产品记录、四状态生命周期、空间/Bot eligibility、验收历史和调度用例。
- ContextCollab 拥有 Context、事件顺序和真实运行状态，状态仅为 `created/in_progress/done`。
- Bot runtime 通过 `ContextCollabRuntimePlugin` 启动并按 `run_id` 查询；Todo Core 只通过 `TodoCollaborationPlugin` 消费 ContextCollab，不直接依赖 runtime port。
- HTTP 仅负责身份和 DTO；Scheduler、插件选择和配置在 DI composition root。

## 持久状态

```text
Todo:    pending -> in_progress -> pending_acceptance -> completed
Context: created -> in_progress -> done
```

Todo 状态是 A 的持久产品事实，不再在 list/get 时从 Context 动态投影。Context 的 `done` 表示真实运行完成，不表示人工验收完成。

## 创建流程

1. 校验 actor 是 Space member。
2. 校验 Bot 在 Space scope 内并取得真实 `owner_id`。
3. 仅创建 Todo，状态 `pending`、`collab_context_id = null`。
4. 返回成功；不创建 Context，不调用 runtime。

## 自动派发

`TodoScheduler` 每 tick oldest-first 扫描有限数量的 `pending` Todo，并逐条调用 `TodoExecutionService.dispatch_pending`：

1. 重读 Todo；非 `pending` 幂等返回。
2. 以 `created_by` 重验 Space member 和 Bot scope/owner。
3. 无 Context 时创建 Context，并通过 `bind_context_if_pending` 条件绑定。
4. Context 为 `created` 时调用 `run_context`；运行启动并写入 `task.started` 后，Todo 条件迁移 `pending -> in_progress`。
5. Context 已 `in_progress` 时补偿 Todo 到 `in_progress`；已 `done` 时先补到 `in_progress`，由刷新阶段再进入待验收。

Context 创建成功但 Todo 绑定失败可能产生孤儿 Context；本轮明确不做跨库事务。Run 成功而 Todo 状态写失败时，下次调度通过已绑定 Context 补偿。

`POST /todos/{id}/run` 调用同一执行服务，仅作补偿/运维入口。

## Runtime 刷新

Scheduler 再 oldest-first 扫描 `in_progress` Todo，逐条调用 `refresh_in_progress`：

1. Context 已 `done` 时直接条件迁移 Todo 到 `pending_acceptance`。
2. 否则调用 `TodoCollaborationPlugin.refresh_context_run`。
3. ContextCollab 从最后一个 `task.started` payload 读取 `run_id`，调用 `ContextCollabRuntimePlugin.get_run`。
4. `PENDING/RUNNING` 不追加事件。
5. `COMPLETED` 以 executor Bot 追加 `task.completed`，状态机把 Context 推进为 `done`。
6. `FAILED/TIME_OUT/ABORTED` 不伪造成功事件，Context/Todo 保持 `in_progress` 并记录日志。
7. 刷新后 Context 为 `done` 时，Todo 条件迁移 `in_progress -> pending_acceptance`。

并发 append 发生版本冲突时重读；若已 `done` 则视为幂等成功，否则传播错误。

## Repository 并发语义

- `list_todos` 在数据库内完成状态过滤和分页。
- `list_todos_by_status(status, limit)` 按创建时间 oldest-first 扫描当前 tenant/env。
- `bind_context_if_pending` 和 `transition_status(expected, new)` 均持行锁并严格校验 expected status。
- `record_acceptance` 严格要求 `pending_acceptance`；approved -> completed，rejected -> pending_acceptance。

## Scheduler 可靠性

Scheduler 复用论坛定时任务的 `LifecycleBase + CachePlugin` 模式：启动/停止可控；获取 `todo:scheduler` 锁后用 heartbeat 续租；lease 丢失取消/停止剩余工作；单 Todo 异常只记录并继续；顶层 tick 异常不终止循环。配置包括 enabled、interval、batch_size、jitter、lock_ttl、heartbeat，且 `heartbeat < lock_ttl`。

## 前端

状态固定为 `pending/in_progress/pending_acceptance/completed`，Tab 顺序为全部、待处理、进行中、待验收、已完成。保留负责人 Bot 选择和搜索。创建成功提示等待自动调度，移除草稿编辑和普通手动 Run 操作；后台 `/run` controller 可以保留。

## 兼容与恢复

不修改旧 Tasks。数据库 status 字段是字符串，不需要 schema migration；若环境存在旧 `draft` 数据，本轮不自动迁移，发布前需确认目标环境是否已有正式数据。回滚可停止 Todo Scheduler 并恢复旧应用版本；已创建的 Context/事件由 ContextCollab 保留。

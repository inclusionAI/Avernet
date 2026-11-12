# 待办工作台自动执行规格

Status: **confirmed**

## 功能需求

1. 用户按当前空间、关键字、Bot 和持久状态分页查询 Todo，并可查看详情。
2. 用户在有权访问的空间创建绑定单个合法 Bot 的 Todo；创建结果为 `pending`，不创建 Context、不调用 Run。
3. Scheduler 周期扫描 `pending` Todo，重验空间成员和 Bot scope/owner，创建并绑定 Context，启动运行后把 Todo 转为 `in_progress`。
4. Scheduler 周期扫描 `in_progress` Todo，通过 Context 中的 `run_id` 调 runtime `get_run`。`PENDING/RUNNING` 不变；`COMPLETED` 追加 `task.completed`、使 Context 进入 `done`、使 Todo 进入 `pending_acceptance`；失败终态第一版保持 `in_progress`。
5. `/run` 保留为补偿/运维入口，执行与 Scheduler 相同的幂等派发逻辑；普通前端不调用。
6. A 对外暴露完整 B 事件时序。尚未调度、没有 Context 的 Todo 返回空事件列表。
7. 仅持久状态为 `pending_acceptance` 的 Todo 可提交 Human 互动和验收。互动由 B 持久化；验收通过转 `completed`，拒绝保持 `pending_acceptance`。
8. 创建、自动派发及所有用户操作重新校验相应空间/Bot 权限。
9. 所有 A 持久化数据按 `avernet_tenant/env` 隔离；状态迁移使用 expected-status 条件与行锁；写入失败向上传播。
10. Scheduler 使用进程级分布式锁、续租和批量上限；单条 Todo 失败不阻断同批其他条目，丢失 lease 后停止本轮处理。

## A 对前端 API

- `GET /openapi/v1/todos`
- `POST /openapi/v1/todos`
- `GET /openapi/v1/todos/{todo_id}`
- `POST /openapi/v1/todos/{todo_id}/run`（补偿/运维）
- `GET /openapi/v1/todos/{todo_id}/events`
- `GET /openapi/v1/todos/{todo_id}/interactions`
- `POST /openapi/v1/todos/{todo_id}/interactions`
- `POST /openapi/v1/todos/{todo_id}/acceptance`

Todo 编辑不属于本轮范围；现存 PATCH 兼容面不在普通前端暴露。

## B 对 A 的协作契约

- 创建 Context，并绑定 Human principal、Bot executor、Bot owner 和 Todo resource ref。
- 启动 Context，在 `task.started` payload 中记录 `run_id/session_id`。
- 根据 `run_id` 查询真实 Bot runtime 状态。
- runtime 完成后追加 `task.completed`，Context 状态推进为 `done`。
- 保存和查询 Human 互动事件及完整执行事件流。

## 验收标准

### AC-001 Todo 持久状态与查询

创建返回 `pending` 且不创建 Context、不 Run；列表和详情只返回 A 的持久状态，不通过读取 B 动态改写；不提供普通 Todo 编辑。

### AC-002 空间与 Bot 权限

创建和自动派发都校验 Space member、Bot scope 与 Bot owner；个人空间只允许当前用户自己的 Bot，团队空间只允许该空间的 Bot；跨空间或歧义 Bot 拒绝。

### AC-003 自动派发与补偿 Run

Scheduler 扫描 `pending` Todo，幂等创建/绑定 Context、启动运行并推进到 `in_progress`；已有 `in_progress/done` Context 时可补偿 A 状态；手动 `/run` 复用同一派发用例。

### AC-004 Runtime 完成投影

Scheduler 扫描 `in_progress` Todo，并通过 `get_run(run_id)` 刷新真实运行态。仅 `COMPLETED` 使 Context 进入 `done`、Todo 进入 `pending_acceptance`；失败终态不误入验收。

### AC-005 Human 互动与验收

只有 `pending_acceptance` 可写互动和验收；互动写 B 事件流；验收通过转 `completed`，拒绝保持 `pending_acceptance`，Context 始终保持 `done`。

### AC-006 隔离、并发与调度可靠性

Todo/验收按 `avernet_tenant/env` 隔离；绑定和状态迁移严格校验 expected status；Scheduler 有锁、续租、批量上限、丢 lease 停止与单条失败隔离；数据库写失败不得吞掉。

### AC-007 前端工作台

页面使用当前空间，状态 Tab 为全部/待处理/进行中/待验收/已完成，支持负责人 Bot 选择和搜索；创建后提示等待自动调度；普通界面没有编辑和“启动执行”入口；保留事件/产物、互动和验收展示。

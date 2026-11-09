# 第二棒会话链接 → 自动拉起整图 TaskPanel — 设计文档

日期: 2026-11-03
归属: `apps/frontend-nextgen` + `apps/backend` (`agentclaw.community`)
状态: IMPLEMENTED(含同 session 多任务扩展)

## Intent & why

接力(relay)任务的第二棒可能在一个单 Bot 会话或协作群的执行 session 中运行。旧的
TaskPanel 只能靠“对话消息里的 AixUI 声明式面板消息”触发，而第二棒会话没有这条消息，
用户进入第二棒会话后看不到任务整图。同时，一个 chat session 可能先后承载多个任务；
打开会话时必须自动恢复默认任务，也让 TaskPanel 下拉条能看到同 session 的全部任务。

后端契约因此从“session → 单个任务 ref”改成“session → 有序轻量 task ref 列表”：

```text
GET /openapi/v1/collaboration/tasks/by_session?session_id=...
GET /api/v1/collaboration/tasks/by_session?session_id=...
```

返回 `data.items`：

- `task_id`
- `owner_user_id`
- `node_id`
- `updated_at`(旧数据可为 null)

列表按最新持久化 run row 排序，并按 `task_id` 去重。回调收敛继续使用既有的
`get_by_session_id`，保留 retry 优先语义，不复用这条排序链路。

## Goals

- 打开已知 session 时自动拉起 TaskPanel；多个 task 共享 session 时默认展示最新 task。
- TaskPanel 任务切换条展示同 session 的全部 task，不把用户的全部任务混入。
- 从任务列表点“进入对话”进入 session 时，默认展示列表中被点击的那个 task。
- AixUI 卡片已经触发展示某个 task 时，默认恢复逻辑不抢占该 task。
- 保持 env / owner read scope：run row 反查到的 task 必须再经过 env-scoped
  `TaskInfoRepository.get`，越界或不可见 task 不返回。

## Non-goals

- 不改聊天深链 URL 契约，不新增 `openTaskPanel` 一类显式参数。
- 不在 by_session 返回标题、状态等富化任务摘要；轻量 ref 只负责 session 关联，
  TaskPanel 展示字段继续复用现有 `/tasks/list`。
- 不改变任务面板内容、消息渲染方式或第一棒 AixUI 触发路径。

## Backend contract

### Repository

`TaskNodeRunInfoRepositoryProtocol` 保留：

```text
get_by_session_id(session_id) -> TaskNodeRunInfoRecord | None
```

给 BCN / state_machine callback convergence 使用；它仍按 retry 倒序，关注的是“这条回调
应该收敛到哪个节点”。

新增：

```text
list_by_session_id(session_id) -> list[TaskNodeRunInfoRecord]
```

一条 SQL 查询返回该 session 的所有 run rows，按 `gmt_modified desc, id desc` 排序。
同一个 `(task_id, node_id, retry)` 的多次 retry 会在结果中包含，随后由 service 层按
`task_id` 去重。

### Service

Service API 为：

```python
def get_task_nodes_by_session(session_id: str) -> list[TaskNodeSessionRef]
```

规则：

1. run rows 不按 env / tenant 过滤，因为这个表本身不持有 tenant 字段。
2. 每个 run row 的 `task_id` 先按顺序去重，未越界的重复 task 不会返回多次。
3. 对每个 task 调 env-scoped `TaskInfoRepository.get`，不存在或越界的 task 丢弃。
4. `owner_user_id` 只能来自可见 task_info 行，不能使用 run row 中的字段。
5. `updated_at` 使用 run row 的 `gmt_modified`，缺失时回退 `gmt_create`。

### HTTP DTO

`BySessionDTO` 是列表信封：

```json
{
  "session_id": "session-id",
  "items": [
    {
      "task_id": "task-latest",
      "owner_user_id": "owner-id",
      "node_id": "node-id",
      "updated_at": "2026-11-05T10:00:00"
    }
  ],
  "total": 1
}
```

`items[0]` 是默认恢复任务。`total` 是本次返回的 ref 数量。公开面与内部镜像路由使用
同一 DTO。没有可见 task 时返回 `items=[]`、`total=0` 的 200 信封。

## Frontend behavior

`getTaskBySession(sessionId)` 把 HTTP 信封解析成 `TaskBySessionRef[] | null`。空列表保持
`[]`；旧后端 404、信封失败或网络异常统一收敛为 null，不打扰会话。

`useSessionTaskPanelBootstrap` 顺序：

1. 切换到当前会话时先把 session 关联 store 的旧 task refs 清空。
2. 调 by_session；命中时把 all task ids 写入 session association store，供 TaskPanel
   切换条补充 `run_info.session_id` 维度的同 session 任务。
3. 有显式 `taskPanelOpenIntentTaskId` 时优先打开该 task；列表数据只用于标题富化，失败时
   仍按 task id 拉起 TaskPanel。
4. 没有显式 intent 时读取 `items[0]` 作为最新 task；再调用 `/tasks/list` 富化标题与
   状态。列表失败时仍按 ref 拉起。
5. `activeTaskId` 已经存在时说明 AixUI / 用户当前展示了任务，by_session 只补充关联
   数据，不抢焦点。
6. by_session 未命中时保持既有 `/tasks/list` 兜底逻辑：按
   `execution_config.main_session_id` 找当前会话最新任务。

TaskPanel 切换条使用两个维度的同会话过滤：

- 现有 `main_session_id`，覆盖任务发起主会话。
- by_session 提供的当前 session 关联 task id 集，覆盖第二棒执行 session。

跨用户深链时 task list 查询使用 session id 中的 owner 工号，TaskPanel 操作身份仍使用
当前登录人。

## Tests

Backend:

- Repository：同 session 多任务、多 retry、其它 session 分离；排序为最新持久化优先。
- Service：同 session 多任务按 task id 去重、未越界环境、越界/不可见 task 被隐藏，
  callback 的 retry-first 方法不被调用。
- Internal / public endpoint：列表信封、空结果、401。

Frontend:

- by_session 多 task 时默认打开 `items[0]`，ref 列表保存到 association store。
- task list 失败时仍能按 ref 拉起 task。
- 显式 task intent 优先于最新 task。
- AixUI 已有 active task 时不抢占焦点。
- TaskPanel 切换条可合并 main session 与 by_session 关联任务，并按 task id 去重。
- by_session 未命中时保留旧 list 兜底。

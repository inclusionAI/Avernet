# 待办工作台 A 侧规格

Status: **confirmed**

## 功能需求

1. 用户按当前空间、关键字和状态分页查询待办，并可查看详情。
2. 用户在有权访问的空间创建绑定单个合法 Bot 的待办；创建结果为 `draft`。
3. 仅 `draft` 待办可修改标题、描述和 Bot，并可显式启动执行。
4. A 启动待办时调用 B 创建协作 Context，只在 `ac_todo` 保存 `collab_context_id`，并将待办置为 `in_progress`。
5. A 对外暴露完整 B 事件时序，事件包含事件序号、事件类型、参与者、Context 版本、状态和产物名称/地址。
6. 仅 `pending_acceptance` 待办可提交 Human 互动；A 调 B 追加 `human_interaction` 事件并回到 `in_progress`，不在 A 落互动表。
7. 仅 `pending_acceptance` 待办可验收；通过转 `completed`，拒绝转 `in_progress`；验收事件也写入 B，验收结论保留在 A 的不可覆盖验收历史中。
8. 所有操作重新校验当前用户仍是待办空间成员。
9. 所有 A 持久化数据按 `avernet_tenant/env` 隔离，写入失败向上传播。

## A 对前端 API

- `GET /openapi/v1/todos`
- `POST /openapi/v1/todos`
- `GET /openapi/v1/todos/{todo_id}`
- `PATCH /openapi/v1/todos/{todo_id}`
- `POST /openapi/v1/todos/{todo_id}/run`
- `GET /openapi/v1/todos/{todo_id}/events`
- `GET /openapi/v1/todos/{todo_id}/interactions`
- `POST /openapi/v1/todos/{todo_id}/interactions`
- `POST /openapi/v1/todos/{todo_id}/acceptance`

## B 对 A 的协作契约

- `POST /api/v1/collaboration/task/context_collab`：根据任务目标、协作人员、负责人和 Bot 创建 Context。
- `GET /api/v1/collaboration/task/context_collab/{collab_context_id}`：返回完整事件列表。
- `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events`：由 B 记录一类互动事件。

## 验收标准

### AC-001 待办 CRUD 与显式 Run

用户只能在当前有成员权限的空间创建、分页检索、查看和修改草稿待办；只有 `draft` 可以显式启动，Run 后 A 只保存 B 返回的 `collab_context_id` 并转 `in_progress`。

### AC-002 空间与 Bot 权限

A 使用当前空间成员资格和 Bot 归属过滤创建时可选择的 Bot；跨空间或歧义 Bot 必须拒绝。

### AC-003 Human 互动事件

只有 `pending_acceptance` 可提交互动；A 只提交 message 和 B 侧当前事件边界，由 B 持久化 `human_interaction`，A 随后转 `in_progress`。

### AC-004 验收状态流转

只有 `pending_acceptance` 可验收；A 原子保存验收结论并前置 B 写入 `human_acceptance` 事件。通过转 `completed`，拒绝转 `in_progress`，并发重复验收最多一次成功。

### AC-005 租户隔离与失败语义

待办和验收记录按 `avernet_tenant/env` 隔离，数据库或 B 写入失败不得被吞掉；HTTP 返回稳定的 403/404/409/503 语义。

### AC-006 前端工作台

页面使用全局当前空间；Bot 清单来自当前空间；支持列表/筛选/详情/创建/草稿编辑/Run/事件时序/互动/验收；执行中详情轮询事件，产物展示名称和地址。

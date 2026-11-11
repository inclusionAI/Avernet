# 待办工作台实施任务

## Slice 1：Todo CRUD 与空间权限

- Covers AC-001, AC-002, AC-005
- Todo 核心模型、Repository、Service、OpenAPI CRUD、租户隔离。
- 状态约束：查询校验 Space member；创建校验 Bot scope；仅 `draft` 可修改。

## Slice 2：B 协作契约与状态流转

- Covers AC-001, AC-003, AC-004, AC-005
- `TodoCollaborationPlugin`、HTTP/local 实现、`collab_context_id` 持久化。
- Run、事件投影、互动事件、验收事件与 A 状态原子更新。
- 测试：Service、Repository、HTTP、Plugin contract、本地/生产 response 容忍。

## Slice 3：前端工作台

- Covers AC-006
- 当前空间 Todo 列表、当前空间 Bot 工坊清单、创建/编辑/Run。
- 事件时间线和产物、待验收互动、验收、执行中轮询。
- 测试：hooks/page tests、typecheck、frontend CI。

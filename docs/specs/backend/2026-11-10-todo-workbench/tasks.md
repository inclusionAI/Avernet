# 待办工作台实施任务

## Slice 1：Todo 创建、查询与空间权限

- Covers AC-001, AC-002, AC-005
- Todo 核心模型、Repository、Service、OpenAPI 创建/查询、租户隔离。
- 状态约束：查询校验 Space member；创建校验 Bot scope；本阶段不提供 Todo 编辑。

## Slice 2：B 协作契约与状态流转

- Covers AC-001, AC-003, AC-004, AC-005
- `TodoCollaborationPlugin`、in-process/local 实现、`collab_context_id` 持久化。
- Run 和事件投影走 B；Human 互动写 B 事件流；验收只更新 A 的 Todo 状态。
- 测试：Service、Repository、HTTP endpoint、Plugin contract、同进程适配与 Bot owner 运行时寻址。

## Slice 3：前端工作台

- Covers AC-006
- 当前空间 Todo 列表、当前空间 Bot 工坊清单、创建/Run；本阶段不提供编辑。
- 事件时间线和产物、待验收互动、验收、执行中轮询。
- 测试：hooks/page tests、typecheck、frontend CI。

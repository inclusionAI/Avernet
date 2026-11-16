# 待办工作台自动执行实施任务

## Slice 1：Todo 持久状态与 Repository CAS

- Covers AC-001, AC-002, AC-005, AC-006
- 测试先覆盖：创建 `pending` 且不创建 Context、list/get 不读 B、数据库状态过滤分页、oldest-first 扫描、条件绑定/迁移、严格验收。
- 实现 TodoStatus、Repository contract/implementation、共享 Bot scope 校验和 TodoService。

## Slice 2：Context runtime 状态刷新契约

- Covers AC-004, AC-006
- 先扩展两个 Plugin Protocol contract tests。
- 为 Context runtime 增加 `get_run`，为 ContextCollab Service 增加 `refresh_run`，仅 COMPLETED 追加 `task.completed`。
- 为 TodoCollaborationPlugin 增加 `refresh_context_run`，同步实现 production/local adapter。

## Slice 3：执行服务与 Scheduler

- Covers AC-002, AC-003, AC-004, AC-006
- 新增 TodoExecutionService，覆盖派发、状态补偿、完成刷新和失败终态保持。
- 新增 Scheduler 锁/续租/lease lost/批量/单条失败隔离测试。
- 新增 Todo config/module，将 Todo bindings 从接近文件上限的 TaskModule 移出并接入 container lifecycle。

## Slice 4：HTTP、前端与知识同步

- Covers AC-001, AC-005, AC-007
- HTTP schema/docstring 改为四状态和自动调度语义，`/run` 标记补偿入口。
- 前端四状态 Tab，移除编辑和手动 Run，更新创建提示；不修改旧 Tasks 和 `.umi-production`。
- 更新 Todo Context Boundary README、规格和验收台账。

## 验证

- Backend Todo/Repository/HTTP/endpoint focused tests。
- ContextCollab core、两个 Plugin contract 与 production/local adapter tests。
- Scheduler/DI/architecture tests。
- Frontend Todo page/hook tests、typecheck、lint。
- `git diff --check` 与所有修改/新增源码文件行数检查。

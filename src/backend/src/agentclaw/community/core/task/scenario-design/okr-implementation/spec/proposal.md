# 固定 OKR 模板运行

## Problem
Skill 目前只能直接提交通用 Task，无法以稳定、非动态决策的方式驱动“风险评估群 + 策略专家并行 → 审核 → 实施”的演示流程。

## Solution
新增固定模板运行入口。Skill 只提交 `template_id` 与业务输入；模板由 YAML 预置，运行时复用现有 TaskService、TaskGraphService、ExecutionEngine.on_report、TaskRunner 与回调持久化。固定 Plan 的推进由模板依赖和回报事件决定，不调用动态 Planner。

## Scope
- 一个固定模板 `okr-implementation`。
- `POST /api/v1/collaboration/tasks/run-template`。
- 首批风险评估群、营销策略 Bot 并行。
- 审核等待两个结果；审核通过后执行实施 Bot。
- 群运行时创建；YAML 只配置 bot_ids，允许暂时为 null，但运行前拒绝空绑定。

## Non-scope
- 模板管理、发布、版本、可视化编排。
- 动态 Planner 改造。
- 冲突解决群、业务共识群、BBS/自动研发分支。
- 新建独立执行引擎或独立回报协议。
- Dima 校验；本实现以用户确认快照为准。

## Decisions
- `static_plan` 是 TaskType 新模式；旧 yaml/workflow/dynamic 行为不变。
- `depends_on` 只属于 Fixed Plan 定义，用于 Join/调度；不改变现有分解树 Relation 的单父语义。
- 节点运行态仍落现有 graph/task_node/task_node_run_info；运行时 group_id/session_id 只进入节点运行信息。
- Bot ID 由 YAML 绑定，null 只表示未配置，不是可执行值。

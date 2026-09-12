# Task 模块职责边界、事件闭环与统一入口

## Summary

将任务模块重构为图谱状态驱动的事件反应链。`TaskGraphService` 是图谱事实、状态机、统一上报入口和事件源；TaskPlanner、TaskDispatcher、TaskRunner 分别订阅规划、派发、执行事件，处理后通过同一 report 接口回报图谱，不直接互调、不直接读写图谱实现。

`TaskCli` 是人和 Agent 的统一、极简入口：一条自然语言目标在 CLI 内部完成识别、四要素澄清和确认，确认后才创建正式执行任务并返回 `task_id`。主动任务发现仍是独立能力；主动输入后的识别与澄清属于统一入口。

## Motivation

任务执行需要清晰、可审计的状态来源，以及可独立扩展的规划、派发和执行策略。“一句话目标—四要素澄清—确认”需要成为统一任务入口能力，并与任务执行闭环保持职责分离。

## User Stories

- As a 人或 Agent, I want to enter one task goal and finish clarification in one CLI conversation, so that I only receive a real `task_id` after confirmation.
- As a 规划策略开发者, I want to receive a stable task context rather than graph internals, so that I can add centralized or relay planning without coupling to graph persistence.
- As a 派发策略开发者, I want to dispatch one atomic task node at a time, so that decisions, retries and audit are independent per node.
- As an 执行适配开发者, I want to reuse the TaskRunner single-bot, group and BBS executor structure, so that new executors do not alter graph state rules.
- As a task platform maintainer, I want every graph mutation to enter one report path and every next step to be triggered from a persisted graph event, so that lifecycle progression is traceable and recoverable.

## Acceptance Criteria

- [ ] `TaskGraphService` is the sole owner of graph persistence, graph version, state-transition validation, callback idempotency and graph-event publication.
- [ ] TaskPlanner, TaskDispatcher, TaskRunner, Harness, external Bot and transport adapters update graph facts only through the same report interface; they do not inject or call graph write methods.
- [ ] TaskPlanner consumes a graph-produced `TaskContext`, not `TaskExecutionGraph`, and returns the existing `PlanResult`.
- [ ] TaskDispatcher consumes one existing `TaskNode`, not `list[TaskNode]`, and returns an existing `TaskNodePatch` restricted to dispatch fields.
- [ ] TaskRunner consumes one already-dispatched `TaskNode` and reports execution facts through the unified report interface.
- [ ] A graph report atomically persists its accepted fact and emits the next semantic event through a reliable outbox or equivalent mechanism.
- [ ] TaskPlanner, TaskDispatcher and TaskRunner do not directly call each other. They subscribe respectively to `PLAN_REQUESTED`、`DISPATCH_REQUESTED`、`EXECUTION_REQUESTED` events.
- [ ] Stale planning or dispatch reports are rejected using the source `graph_version`, change no graph state, and are retried only from a later graph event.
- [ ] Centralized planning, master-slave execution and relay planning work through the same report/event chain; they differ only in registered planning or dispatch strategies.
- [ ] TaskCli exposes an interactive `task [goal]` creation conversation plus `task get` and `task list`; it exposes no `workflow_id`、YAML、task type、run mode、draft/intake ID、graph control or internal execution command.
- [ ] TaskCli does not use AixUI cards. Its internal clarification draft is not a formal task and is not exposed as a normal user-facing object.
- [ ] `TaskNode`、`TaskSpec`、`PlanResult`、`TaskNodePatch`、`TaskCallbackData`、`TaskOpResult`、`NodeOpResult` 和 TaskRunner executor 分层被复用；仅 `TaskContext` 作为图谱到 TaskPlanner 的上下文契约。
- [ ] `TaskIntake` 是 TaskService 内部能力而非公开服务。TaskCli 只调用 TaskService；确认后由 `execute(TaskInfoRequest) -> TaskOpResult` 创建正式任务，Intake 不创建节点或调度执行。

## In Scope

- TaskCli unified entry and the internal recognition, four-element clarification and confirmation flow.
- Graph-state-driven TaskPlanner, TaskDispatcher, TaskRunner and report/event interfaces.
- Reusable task recognition, four-element clarification and confirmation policy.
- Centralized, master-slave and relay strategy support.
- Reuse Task graph、TaskDispatcher 和 TaskRunner 的领域模型与 executor 分层。
- Architecture and contract tests that enforce the intended dependency and event directions.

## Out of Scope

- Proactive task discovery schedules, notification and pending-discovery storage.
- Replacing the existing task, node, graph, callback or TaskRunner domain model with a parallel model.
- Exposing Workflow, YAML, BBS or run-mode selection to TaskCli callers.
- Rebuilding Bot, BCS, BCN or external execution infrastructure.
- Defining the business prompts, model selection or ranking algorithms of specific strategies.

## Open Questions

- Which exact task facts belong in `TaskContext`, especially when relay Bots have restricted visibility?
- Which existing event/outbox infrastructure is the repository-standard reliable publisher for graph events?
- What are the minimal input/output fields and session-state ownership for `TaskService.intake(...)`?

# Task 模块职责边界、事件闭环与统一入口

## Summary

将任务模块重构为图谱状态驱动的事件反应链。`TaskGraphService` 是图谱事实、状态机、统一上报入口和事件源；TaskPlanner、TaskDispatcher、TaskRunner 分别订阅规划、派发、执行事件，处理后通过同一 report 接口回报图谱，不直接互调、不直接读写图谱实现。所有模式都遵循同一逻辑链“规划 → 派发 → 执行”；模式只改变每个逻辑角色的实际执行者及 Graph 对事件的定向投递目标。

分布式接力是该逻辑链的 Skill/Runtime 托管变体：执行 Bot 通过 `EXECUTION_RESULT → PLAN_RESULT → DISPATCH_RESULT → DISPATCH/BBS` 事件闭环推进，而不是由后端重新执行中心化规划。`/search` 只负责候选检索，Relay Skill 负责 GAP、候选决策和模态选择；后端负责凭证、版本、身份、幂等和当前节点状态落图。

`TaskCli` 是人和 Agent 的统一、极简入口：一条自然语言目标经后端任务识别澄清服务完成识别、四要素澄清和确认，确认成功后才创建正式执行任务并返回 `task_id`。主动任务发现仍是独立能力；主动输入后的识别与澄清属于统一入口。

## Motivation

任务执行需要清晰、可审计的状态来源，以及可独立扩展的规划、派发和执行策略。“一句话目标—四要素澄清—确认”需要成为统一任务入口能力，并与任务执行闭环保持职责分离。

## User Stories

- As a 人或 Agent, I want to enter one task goal and finish clarification in one CLI conversation, so that I only receive a real `task_id` after confirmation.
- As a 规划策略开发者, I want to receive a stable task context rather than graph internals, so that I can add centralized or relay planning without coupling to graph persistence.
- As a 派发策略开发者, I want to dispatch one atomic task node at a time, so that decisions, retries and audit are independent per node.
- As an 执行适配开发者, I want to reuse the TaskRunner single-bot, group and BBS executor structure, so that new executors do not alter graph state rules.
- As a 接力执行 Bot, I want to receive the latest complete task context after my node result is accepted, so that I plan the next step from the global acceptance GAP rather than from my own output alone.
- As a B 端平台管理员, I want to register tenant-approved TaskPlanner、TaskDispatcher 和 TaskRunner executor extensions and bind them declaratively, so that a task uses a governed strategy combination without exposing implementation choices to TaskCli callers.
- As a task platform maintainer, I want every graph mutation to enter one report path and every next step to be triggered from a persisted graph event, so that lifecycle progression is traceable and recoverable.

## Acceptance Criteria

- [ ] `TaskGraphService` is the sole owner of graph persistence, graph version, state-transition validation, callback idempotency and graph-event publication.
- [ ] TaskPlanner, TaskDispatcher, TaskRunner, Harness, external Bot and transport adapters update graph facts only through the same report interface; they do not inject or call graph write methods.
- [ ] TaskPlanner consumes a graph-produced `TaskContext`, not `TaskExecutionGraph`, and returns the existing `PlanResult`.
- [ ] TaskDispatcher consumes one existing `TaskNode`, not `list[TaskNode]`, and returns an existing `TaskNodePatch` restricted to dispatch fields.
- [ ] TaskRunner consumes one already-dispatched `TaskNode` and reports execution facts through the unified report interface.
- [ ] A graph report atomically persists its accepted fact and emits the next semantic event through a reliable outbox or equivalent mechanism.
- [ ] TaskPlanner、TaskDispatcher 和 TaskRunner 不直接调用彼此。中心化模式订阅 `PLAN_REQUESTED`、`DISPATCH_REQUESTED`、`EXECUTION_REQUESTED`；Relay 模式允许外部 Bot/Skill 通过受控 Relay report API 驱动同一语义事件链，事件适配器不得绕过 Graph 校验。
- [ ] Stale planning or dispatch reports are rejected using the `graph_version` carried by their `TaskContext` or `TaskNode`, change no graph state, and are retried only from a later graph event.
- [ ] Centralized planning, master-slave execution and relay planning work through the same logical TaskPlanner → TaskDispatcher → TaskRunner report/event chain; they differ in strategy, actual handler and Graph-internal event routing target, rather than adding direct module calls.
- [ ] In relay mode, a node result accepted by the graph creates a versioned `PLAN_REQUESTED` targeted to the completing Bot runtime. Its `TaskContext` contains the task goal and acceptance criteria, graph/node/dependency state, relevant completed outputs (including but not limited to that node's output), current GAP, and applicable constraints/resource/authorization scope.
- [ ] In relay mode, Graph 定向投递规划、必要时派发事件给上游完成者 runtime；Graph 根据报告身份、`graph_version`、父节点关系、深度和既有回调幂等机制校验续接。过期、重复或越权报告不改变图谱，也不启动 executor。
- [ ] Relay 的最新闭环必须显式建模为 `EXECUTION_RESULT → signed relay_turn → PLAN_RESULT → pure /search(query) → Skill-owned DISPATCH_RESULT → /dispatch 或 BBS`；`EXECUTION_RESULT` 成功不是结束点，Relay Bot 必须继续到下一棒成功交接、BBS 发布或任务完成。
- [ ] Relay `relay_turn` 是带 TTL 的单持有者 lease，后端保存 token digest，不保存明文 token；PLAN/SEARCH/DISPATCH 必须使用后端签发的当前 token。重复 execution report 可幂等重发并获得可用 token，过期 lease 由 Relay 专属恢复流程续租并发送 `[RESUME_RELAY]`，不得落入中心化 `exec_stuck/child_hung` 收口。
- [ ] Relay 是严格串行 baton：一次 PLAN_RESULT 最多产生一个下一节点；当前棒交接后置为 DONE，下一棒只更新自身节点和图级 Relay 控制元数据，不基于父子聚合回写任何前序节点。
- [ ] Relay 的 `/search` 只接受 `query`，只返回实际存在的候选元数据；不携带 task/node/holder/turn/catalog 上下文，不判断 HIT/MISS，不选择 single/group/BBS，不更新图谱。Relay Skill 基于候选事实产生 `HIT_SINGLE`、`HIT_MULTI_BOTS` 或 `MISS`，再通过 report 落图。
- [ ] Relay BBS 复用统一 BBS 动态选人和 Runner 能力，但 BBS 认领、执行结果和后续规划仍属于当前 Relay baton；不能调用中心化根节点收口或父子状态聚合。
- [ ] Dispatch resolves an execution carrier, not only a Bot: it may select a single Bot, a cooperation group, or escalate to BBS when no eligible single Bot/group matches. The selected `run_mode` and diagnosis are reported in the final dispatch patch.
- [ ] BBS creation, recruitment, claim and internal collaboration belong to the BBS TaskRunner strategy. Its designated coordinator reports execution facts through the unified report interface; a centralized BBS failure is handled by graph state policy, while a Relay BBS failure/result only advances the current baton and never mutates predecessor/root node state.
- [ ] A third-party plugin may contribute `TaskPlanningStrategy`、`TaskDispatchStrategy` or `TaskRunnerStrategy` (executor extension), each reusing the TaskPlanner/TaskDispatcher/TaskRunner module input and output contracts.
- [ ] A tenant-scoped, declarative `TaskRuntimeProfile` selects approved strategy and executor versions; TaskService resolves and freezes the selected profile and plugin digests when creating a task.
- [ ] A TaskDispatcher strategy chain evaluates alternatives before reporting: only the final `TaskNodePatch` is reported; a TaskRunner strategy is selected exactly once by run mode and is never automatically retried by another strategy after an external start attempt.
- [ ] Plugin adapters cannot directly invoke graph write/query operations, TaskService execution, or another task module; all task facts still enter through `TaskGraphService.report`.
- [ ] TaskCli exposes an interactive `task-cli [goal]` creation conversation plus `task-cli get` and `task-cli list`; it exposes no `workflow_id`、YAML、task type、run mode、draft/intake ID、graph control or internal execution command.
- [ ] TaskCli only calls `TaskService.intake(TaskIntakeRequest) -> TaskIntakeResult` for every clarification turn. It never loads a Skill, stores the clarification draft, maps `task_info` into an execution request, or directly calls `execute`.
- [ ] `TaskService.intake` creates and owns the clarification session, routes each message to the selected Bot/group's mounted `task-loop`, and returns a unified `TaskIntakeResult`. On a confirmed turn it internally maps the confirmed `task_info` to `TaskInfoRequest` and calls the existing `execute` exactly once.
- [ ] `TaskCli` does not use AixUI cards. `task-loop` returns a channel-neutral structured result which product chat may render as an AixUI card and TaskCli renders as terminal text. Its clarification draft is not a formal task and is not exposed as a normal user-facing object.
- [ ] `task-loop` uses one stable `task_info` shape in every clarification state: `title`、`goal`、`background`、`deliverables`、`acceptance_criteria`、`constraints`、`resources`. `goal`、`deliverables`、`acceptance_criteria`、`constraints` are required before confirmation; the other fields are parsed opportunistically.
- [ ] `TaskSpec` contains no `task_id`. Confirmed facts map to existing TaskSpec fields: title to `metadata.title`, goal to `goal.objective`, acceptance criteria to `goal.acceptances`, background to `context.background`, and deliverables/constraints/resources to `context.extend_props`; `metadata.instruction` only carries system/runtime instructions.
- [ ] `TaskNode`、`TaskSpec`、`PlanResult`、`TaskNodePatch`、`TaskCallbackData`、`TaskOpResult`、`NodeOpResult` 和 TaskRunner executor 分层被复用；仅 `TaskContext` 作为图谱到 TaskPlanner 的上下文契约。
- [ ] `TaskIntake` 是 TaskService 内部能力而非公开服务。TaskCli 只调用 TaskService；确认后由 `execute(TaskInfoRequest) -> TaskOpResult` 创建正式任务，Intake 不创建节点或调度执行。

## In Scope

- TaskCli unified entry, target resolution, and the backend-owned recognition, four-element clarification and confirmation flow.
- Graph-state-driven TaskPlanner, TaskDispatcher, TaskRunner and report/event interfaces.
- Reusable task recognition, four-element clarification and confirmation policy.
- Centralized, master-slave and relay strategy support, including Graph-controlled targeted delivery and continuation validation for relay execution.
- B-side plugin registration, tenant activation and declarative TaskRuntimeProfile strategy composition.
- Reuse Task graph、TaskDispatcher 和 TaskRunner 的领域模型与 executor 分层。
- Architecture and contract tests that enforce the intended dependency and event directions.

## Out of Scope

- Proactive task discovery schedules, notification and pending-discovery storage.
- Replacing the existing task, node, graph, callback or TaskRunner domain model with a parallel model.
- Exposing Workflow, YAML, BBS or run-mode selection to TaskCli callers.
- Rebuilding Bot, BCS, BCN or external execution infrastructure.
- Defining the business prompts, model selection or ranking algorithms of specific strategies.
- Allowing a plugin to reorder the planning → dispatch → execution lifecycle, merge multiple planner results, or introduce TaskCli task commands.

## Open Questions

- Which exact task facts belong in `TaskContext`, especially when relay Bots have restricted visibility?
- Which existing event/outbox infrastructure is the repository-standard reliable publisher for graph events, and which Relay HTTP/event adapter maps external Skill callbacks into the same report contract?

# 目标设计：统一任务事实边界与中心化 / Relay 双模式

> 本计划描述目标架构、领域契约和实现顺序。它不把当前代码行为误写为目标态已实现行为。

## 1. Architecture overview

### 1.1 Shared kernel, different orchestration actors

两种模式共享任务事实和执行载体：

```text
TaskSpec / TaskNode / RuntimeInfo / TaskContext
                ↓
        TaskGraphService.report
                ↓
      persistence + state machine + outbox
                ↓
  /search → DISPATCH_RESULT → /dispatch → TaskRunner
```

差异只在“谁规划、谁决策、何种状态收敛”：

| 维度 | 中心化规划执行 | 分布式 Relay 接力执行 |
| --- | --- | --- |
| 规划主体 | Backend `TaskPlanner` | 当前 baton Bot / 群 Manager 的 task-loop Skill |
| 派发决策主体 | Backend `TaskDispatcher` 策略 | 当前 baton Skill |
| 实际投递 | `TaskRunner` | `TaskRunner` |
| 节点拓扑 | 可生成多个节点和依赖关系 | 每轮最多一个 next TaskNode，严格串行 |
| 父节点收敛 | Graph 根据子节点/依赖状态收敛 | 不做父子回写；当前棒交接即 DONE |
| BBS | Graph/Runner 的中心化 BBS 策略 | target baton 节点级发布和认领，认领者继续 Relay |

### 1.2 Module responsibilities

| 模块 | 输入 | 输出 | 禁止职责 |
| --- | --- | --- | --- |
| task-loop recognition | 用户消息、澄清历史 | `task_info` 草案和卡片投影 | 创建图、执行任务、直接调用 Graph 写入 |
| TaskService | 已确认 `TaskInfoRequest` | 正式任务、根节点、首棒投递 | 业务规划、候选决策 |
| TaskGraphService | 查询或 report 事实 | `TaskContext`、原子状态变更、语义事件 | 能力匹配、GAP 语义推理、候选排序 |

`TaskGraphService` 是唯一公开图谱领域类。中心化和 Relay 的差异只存在于其内部私有状态策略函数中；禁止定义 `CentralizedTaskGraphService`、`RelayTaskGraphService`、Graph Mixin，或通过 `TaskGraphService.method = function` 动态挂载能力。为满足文件大小约束，report 路由、状态策略、查询和 TaskContext 投影统一收口到 `task_graph_support.py` 的私有模块级函数；公开方法和领域身份仍统一归属于 `TaskGraphService`。
| TaskPlanner | `TaskContext` + 当前目标节点 | 中心化 `PlanResult` | 写图、派发、执行 |
| Relay Skill | `TaskContext` + 当前节点 + Bot 事实 | actual_goal、gaps、next TaskSpec、派发决策 | 直接写图、直接创建群、绕过 Runner 投递 |
| TaskDispatcher | 中心化待派发节点 | 中心化派发决策 | 状态迁移、执行 |
| `/search` | query | candidates[] | 感知图谱、决定模态、写入任务 |
| TaskRunner | 已决策节点 | 外部会话/群创建、投递结果 | 规划、搜索、直接写图 |

## 2. Domain model

### 2.1 Task recognition draft and confirmation mapping

`task_info` 保留为识别澄清阶段的统一任务草案。卡片协议是其 UI 投影：`task_clarify` 使用顶层字段，`task_ready` 使用 `task_ready.task`；两者的外层 UI 字段不进入正式任务领域。

```python
task_info = {
    # optional
    "title": str,
    "background": str,
    "resources": list[str],

    # required before confirmation
    "goal": str,
    "deliverables": list[str],
    "acceptance_criteria": list[str],
    "constraints": list[str],
}
```

```python
def init_task_request(
    task_info: dict[str, Any],
    source_context: SourceContext,
) -> TaskInfoRequest:
    ...
```

Mapping:

```text
task_info.title                → TaskSpec.context.title
task_info.background           → TaskSpec.context.background
task_info.deliverables         → TaskSpec.context.extend_props["deliverables"]
task_info.constraints          → TaskSpec.context.extend_props["constraints"]
task_info.resources            → TaskSpec.context.extend_props["resources"]
task_info.goal                 → TaskSpec.goal.objective
task_info.acceptance_criteria  → TaskSpec.goal.acceptances

source_context                 → source_type / owner_user_id / owner_bot_id /
                                 execution_config / task_type / workflow_id /
                                 orchestration_mode
```

`title` 和 `background` 为可选业务上下文，缺失时使用空字符串；四要素完整性只由 task-loop 澄清阶段保证，`init_task_request` 只转换，不重复业务澄清。

`orchestration_mode` 是执行配置的封闭枚举（`centralized` / `relay`）。TaskService 创建任务时先解析 `tasks/settings` 的运行时默认值；如果调用方在 `execution_config.orchestration_mode` 中显式提供合法值，则该值覆盖默认值，并记录默认值、请求值和最终选择以便审计。

### 2.2 TaskSpec and node facts

```python
@dataclass
class Context:
    title: str
    background: str
    extend_props: dict[str, Any]


@dataclass
class Goal:
    objective: str
    acceptances: list[AcceptanceCriteria]


@dataclass
class TaskSpec:
    context: Context
    goal: Goal
```

`TaskSpec` 不包含：

```text
metadata
task_id
instruction
assignee
run_mode
relay_turn
```

```python
@dataclass
class RuntimeInfo:
    run_mode: str | None
    assignee: str | None
    actual_goal: Goal | None
    output: dict[str, Any]
    acceptance_result: AcceptanceResult | None
    progress_reason: str | None
    failure_reason: str | None
    extend_props: dict[str, Any]
```

字段语义：

```text
TaskNode.task_spec
= 上一棒或中心化 Planner 对该节点的预期子任务。

RuntimeInfo.actual_goal
= 当前执行 Bot 经过能力准入后实际接受的执行范围。

RuntimeInfo.output / acceptance_result
= 当前 Bot 针对 actual_goal 的真实产出和局部验收事实。

报告、尽调、方案、材料、文档类 RuntimeInfo.output 契约:
  {"result": "<完整 Markdown 全文>"}
result 是最终交付物全文，不得退化为标题列表、摘要或状态说明；
未形成交付物时必须表达为 DECLINED 或局部 acceptance_result.gaps。
```

### 2.3 TaskContext

```python
@dataclass
class DoneOutput:
    node_id: str
    actual_goal: Goal
    output: dict[str, Any]
    acceptance_result: AcceptanceResult


@dataclass
class TaskContext:
    spec: TaskSpec
    all_done_output: list[DoneOutput]
    gaps: list[str]
```

`TaskContext` 是通用领域对象：

```text
- 不包含 task_id、node_id、graph_version、关系图、assignee、群会话、relay_turn；
- 不包含 current node；当前节点由 Runner / Planner 目标传入；
- 不包含 aggregate_acceptance_result；根任务当前仍未覆盖的范围直接表达为 gaps；
- 不使用 Gap 领域对象，gaps 直接为 list[str]；每个字符串必须是可规划、可交接的完整业务缺口描述。
```

纳入 `all_done_output` 的规则：

```text
- EXECUTION_RESULT 已被 Graph 接纳；
- execution_decision = ACCEPTED；
- actual_goal 非空；
- 具有真实可复用 output 与局部 acceptance_result。
```

`DECLINED`、未执行节点、只有搜索/派发事实的节点不纳入。

### 2.4 GAP lifecycle

```text
根节点初始化
  → Graph 持久化 gaps=[]，其语义仅为“尚无已提交的 PLAN_RESULT GAP 快照”，不是任务完成。
  → 首棒 Skill 基于根 TaskSpec、根 TaskNode 和空 DoneOutput 计算初始 GAP。

当前 Bot 执行完成
  → S4 由 EXECUTION_RESULT 持久化当前节点事实。
  → S5 不重新读取图谱；Skill 将 S1 TaskContext 的 all_done_output 与当前节点 actual_goal/output/acceptance_result 合并。
  → S5/S6 本地解析 new_gaps 和唯一 next_task_spec（无 GAP 则为 null）。
  → S7 由 PLAN_RESULT 持久化 new_gaps + next_task_spec，整体覆盖旧 gaps，并按需创建唯一下一棒节点。
  → 有 GAP 时继续一次 /search 和一次 DISPATCH_RESULT；无 GAP 时停止，不调用 search / DISPATCH_RESULT / dispatch。
  → 正常有 GAP 链路的 HTTP 上限：GET context ×1、EXECUTION_RESULT ×1、PLAN_RESULT ×1、search ×1、DISPATCH_RESULT ×1、dispatch ×1。S5 不重新 GET context。
```

`gaps` 不是节点局部验收的字符串拼接；Skill 必须基于根任务、全部 DoneOutput 与节点局部验收重新推理。只有已接纳的 `PLAN_RESULT(gaps=[])` 才表示根任务已无已知缺口；Graph 不把初始空数组自行解释为完成。

## 3. Unified APIs

### 3.1 Create confirmed task

```http
POST /api/v1/collaboration/tasks/execute
```

Input: `TaskInfoRequest` from `init_task_request(task_info, source_context)`.

Output:

```json
{
  "task_id": "...",
  "root_node_id": "...",
  "run_id": 1,
  "status": "RUNNING",
  "first_assignee": "..."
}
```

Graph initializes the root TaskNode with root TaskSpec and empty RuntimeInfo, then Runner delivers the first baton.

### 3.2 Read latest task context

```http
GET /api/v1/collaboration/tasks/{task_id}/context
```

Input: path-only `task_id`. No request body, query parameters, `include_*` switches, `node_id`, `holder_id`, or relay token.

Response envelope `data` is exactly `TaskContext`:

```json
{
  "spec": {},
  "all_done_output": [],
  "gaps": []
}
```

The endpoint is common to centralized planning, Relay, BBS and recovery. Authorization comes from transport/runtime identity, not business request fields.

### 3.3 Unified fact report

```http
POST /api/v1/collaboration/tasks/callback/report
```

Common envelope:

```json
{
  "task_id": "...",
  "node_id": "...",
  "event_type": "EXECUTION_RESULT | PLAN_RESULT | DISPATCH_RESULT",
  "event_id": "...",
  "holder_id": "...",
  "relay_turn": "...",
  "progress_reason": "...",
  "failure_reason": null,
  "payload": {}
}
```

`relay_turn` is required only for Relay post-execution planning and dispatch actions; it is not returned by `/context`.

#### `EXECUTION_RESULT`

```json
{
  "execution_decision": "ACCEPTED | DECLINED",
  "actual_goal": {},
  "output": {},
  "acceptance_result": {}
}
```

Rules:

```text
ACCEPTED → actual_goal and acceptance_result are required; output is the real deliverable, not a summary.
DECLINED → actual_goal=null, output={}, acceptance_result=null; failure_reason=capability_mismatch.
Document/report deliverables use output={"result":"<full Markdown text>"}.
```

Graph persists only the current node execution facts and grants the current Relay holder a planning authority.

#### `PLAN_RESULT` (Relay)

```json
{
  "gaps": ["..."],
  "next_task_spec": {}
}
```

Rules:

```text
gaps != [] → next_task_spec is required; Graph creates exactly one next TaskNode with empty RuntimeInfo.
gaps == [] → next_task_spec must be null; current node becomes SUCCESS and graph becomes DONE.
```

On Relay completion, Graph populates the graph-level read aggregate `output` from accepted node outputs. It does not rewrite any predecessor/root RuntimeInfo; this aggregate is for the dashboard and task panel.

With gaps, Graph atomically persists gaps, transitions the current node to DONE, creates one target node, increments Relay loop state and issues dispatch authority.

#### `DISPATCH_RESULT` (Relay)

```json
{
  "outcome": "HIT_SINGLE | HIT_MULTI_BOTS | MISS",
  "run_mode": "single_bot | coop_group | bbs",
  "driver_bot_id": "...",
  "next_relay_bots": ["..."],
  "group_name": "...",
  "miss_reason": "..."
}
```

Rules:

```text
HIT_SINGLE:
  run_mode=single_bot
  next_relay_bots has exactly one item
  driver_bot_id equals that item.

HIT_MULTI_BOTS:
  run_mode=coop_group
  driver_bot_id belongs to next_relay_bots.
  driver_bot_id is the next group Manager and unique Relay Holder.

Current-holder guard:
  neither driver_bot_id nor next_relay_bots may contain the current holder Bot.
  If filtering the current holder leaves no real candidate, report MISS and publish BBS.

MISS:
  run_mode=bbs
  next_relay_bots=[]
  driver_bot_id is absent.
```

Graph records only target-node dispatch facts. A decision does not make the target node RUNNING; that happens after Runner delivery succeeds.

### 3.4 Pure candidate search

```http
POST /api/v1/collaboration/tasks/search
```

Input:

```json
{"query": "..."}
```

Output:

```json
{
  "candidates": [
    {
      "bot_uuid": "...",
      "bot_name": "...",
      "bot_desc": "...",
      "bot_type": "bot",
      "status": "online",
      "recommend": {}
    }
  ],
  "total": 1
}
```

`/search` does not receive graph identifiers, does not read/write Graph and does not select a carrier.

### 3.5 Actual delivery

```http
POST /api/v1/collaboration/tasks/dispatch
```

Input:

```json
{
  "task_id": "...",
  "origin_node_id": "...",
  "target_node_id": "...",
  "holder_id": "...",
  "relay_turn": "...",
  "dispatch_id": "..."
}
```

The Runner validates that target node is the origin's current planned child, is PENDING and has an accepted `DISPATCH_RESULT`.

Output contains delivery identity and target node RUNNING state.

### 3.6 BBS claim

```http
POST /api/v1/collaboration/tasks/bbs/claim
```

Input:

```json
{
  "task_id": "...",
  "node_id": "...",
  "bot_id": "...",
  "claim_id": "..."
}
```

The claim is node-local CAS. On success it sets the target node assignee/driver to claimant and transitions target PENDING → RUNNING. No predecessor or root runtime fact changes.

## 4. Relay seven-step loop

### Step 1 — Read latest context

```text
GET /tasks/{task_id}/context → TaskContext
```

The Bot also receives its current `TaskNode.task_spec`, `task_id`, `node_id`, execution identity and backend address from Runner delivery.

### Step 2 — Interpret GAP and match local capability

The Skill locally evaluates:

```text
TaskContext
+ current TaskNode.task_spec
+ IDENTITY.md / role boundary
+ mounted Skills / allowed tools / actual availability
→ actual_goal | DECLINED
```

Effective coverage requires role coverage **and** Skill/tool evidence. A tool or generic model capability alone cannot authorize a professional task.

### Step 3 — Execute only actual_goal

The Bot produces only real output and a local acceptance result for actual_goal. For document/report deliverables, output is `{"result":"<full Markdown text>"}`. Required tools being unavailable cannot be replaced by model memory, and an unavailable-tool explanation is not a deliverable.

### Step 4 — Report execution fact

```text
EXECUTION_RESULT → persist current RuntimeInfo → receive plan authority
```

### Step 5 — Recalculate GAP and plan one baton

The Bot re-reads latest TaskContext. The Skill evaluates root Spec, all DoneOutput, prior gaps and current-node result to obtain `new_gaps`.

```text
new_gaps + optional next TaskSpec → PLAN_RESULT
```

Graph either completes task or creates exactly one empty-runtime next node.

### Step 6 — Search candidates

The Skill builds a query from `new_gaps` and target TaskSpec:

```text
/search(query) → candidates[]
```

### Step 7 — Persist Skill dispatch decision

The Skill selects `HIT_SINGLE`, `HIT_MULTI_BOTS` or `MISS` using real candidate facts and reports `DISPATCH_RESULT` with `run_mode`, `driver_bot_id` and `next_relay_bots`.

### Step 8 — Deliver or publish BBS

```text
HIT → /dispatch → Runner delivery → target PENDING → RUNNING
MISS → target node published to BBS → /bbs/claim → target PENDING → RUNNING
```

For `coop_group`:

```text
- current baton Bot calls /dispatch but does not enter the next group by default;
- driver_bot_id is selected from next_relay_bots;
- driver_bot_id is group Manager and next unique Relay Holder;
- other next_relay_bots are workers;
- neither driver_bot_id nor next_relay_bots selects the current holder;
- original Human is added as observer;
- target node assignee is driver_bot_id; created group_id is runtime infrastructure metadata.
```

The next single Bot, group Driver or BBS claimant starts Step 1 again.

## 5. Centralized planning execution flow

Centralized mode uses the same facts and Runner but a different orchestration actor:

```text
Graph state transition
  → PLAN_REQUESTED(target node)
  → TaskPlanner.plan(TaskContext, target node)
  → report centralized plan result
  → Graph creates one or more nodes and dependency relations
  → DISPATCH_REQUESTED(each ready node)
  → TaskDispatcher.dispatch(node)
  → report dispatch fact
  → EXECUTION_REQUESTED(node)
  → TaskRunner.run(node)
  → report execution fact
  → Graph applies dependency/parent convergence
```

Centralized planner and dispatcher never receive or mutate full graph persistence. They use the same `TaskContext` read model and report gateway, but Graph owns dependency validation and parent-state derivation.

## 6. State policy

### 6.1 Relay

```text
PENDING --delivery/claim--> RUNNING
RUNNING --EXECUTION_RESULT--> RUNNING (execution fact accepted)
RUNNING --PLAN_RESULT with gaps--> DONE + one new PENDING baton
RUNNING --PLAN_RESULT with no gaps--> SUCCESS + graph DONE
```

No Relay action may change predecessor/root execution state. Terminal completion only writes the graph-level output aggregate; predecessor/root RuntimeInfo stays immutable.

### 6.2 Centralized

Centralized node/parent state follows the existing dependency graph policy. Parent state may be PLANNING/RUNNING/DONE/SUCCESS/HUNG according to child execution and Graph convergence; this policy is isolated from Relay.

## 7. Extensibility and profiles

`TaskRuntimeProfile` freezes allowed strategies and run modes at task creation. It may select central Planner/Dispatcher/Runner strategies, search strategy chains and allowed `single_bot`/`coop_group`/`bbs` runners.

Profile governance does not alter these invariants:

```text
- Graph owns facts and state transitions.
- Relay Skill owns relay capability/GAP/candidate reasoning.
- /search remains pure.
- /dispatch remains actual delivery only.
- Plugins do not access graph write methods or reorder lifecycle stages.
```

## 8. Migration and compatibility

This target design intentionally replaces stale Relay protocol semantics:

```text
metadata.instruction                 → removed from target TaskSpec
completed_scope / uncovered_scope    → replaced by actual_goal, DoneOutput and TaskContext.gaps
aggregate_acceptance_result          → replaced by TaskContext.gaps + per-node acceptance_result
has_gap                              → derived from gaps emptiness
children[] in Relay plan             → one optional next_task_spec; Graph assigns node_id
root-level Relay BBS owner           → target BBS node-local claim
```

Migration compatibility is mode-scoped:

- Relay result callbacks are no longer allowed to bypass the target model with the centralized node-terminal `status/output/acceptance_result` payload. Such requests are rejected with a state conflict and must be retried as `EXECUTION_RESULT → PLAN_RESULT → DISPATCH_RESULT`.
- The legacy Relay `SEARCH_RESULT` event alias remains accepted and is normalized to `DISPATCH_RESULT`.
- Centralized execution continues to support its existing node callback during migration.

### 1.3 Engine 拆除边界

本架构不允许 `task_center/engine.py` 作为任务模块的统一总编排器。中心化模式保留一个仅服务中心化执行的 `CentralizedExecutionAdapter` 适配模块，用于兼容现有中心化生命周期行为；它不被 Relay 使用，不负责 Relay 的规划、搜推或交接。

- Relay 直接使用 TaskService 组合的 Runner、Search 和 Graph.report 边界；不得通过中心化生命周期对象访问 `_runner`、`_discover`。
- Callback、Harness 和中心化请求只依赖 `CentralizedExecutionAdapter` 的公开生命周期协议。
- `engine.py` 文件及 `ExecutionEngine` 符号必须删除，生产代码和测试不得再导入旧模块。
- 后续可继续将 `CentralizedExecutionAdapter` 按 Planner/Dispatcher/Runner/Harness 责任拆分，但本次不再引入第二个通用总编排器。

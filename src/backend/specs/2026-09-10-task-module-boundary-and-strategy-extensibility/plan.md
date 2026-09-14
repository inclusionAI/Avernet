# 目标设计：任务模块事件反应链与统一任务入口

## 1. 总体架构

任务执行闭环是“图谱状态驱动的事件反应链”：任何参与者向 `TaskGraphService.report` 上报事实；图谱服务校验并推进状态机，原子持久化后发布语义事件；TaskPlanner、TaskDispatcher、TaskRunner 各自消费事件、完成本职决策或执行，再向同一入口上报。不存在负责串行调用各模块的中心化业务编排器。

```text
参与者 report → TaskGraphService → Outbox / Event Bus
                                  ↓
             TaskPlanner / TaskDispatcher / TaskRunner
                                  ↓
                        TaskGraphService.report
```

## 2. 模块职责和允许的接口

| 模块 | 对外接口/订阅 | 输入 | 输出 | 不负责 |
| --- | --- | --- | --- | --- |
| TaskCli | `task [goal]`、`task get`、`task list` | 一句话目标、终端对话 | 文本追问、`task_id`、查询结果 | 任务规则、规划、派发、执行 |
| TaskService | `intake(...)`、`execute(TaskInfoRequest)`、`get_task_dashboard(...)`、`list_tasks(...)` | 对话输入、完整任务请求、查询条件 | 对话结果、`TaskOpResult`、图谱/列表 | TaskPlanner/TaskDispatcher/TaskRunner 的具体策略 |
| TaskGraphService | `report(TaskCallbackData)`、受控查询 | 报告事实、查询条件 | `TaskOpResult`、语义事件、图谱视图 | 规划、选人、实际执行 |
| TaskPlanner | 订阅 `PLAN_REQUESTED`；`plan(TaskContext)` | `TaskContext` | `PlanResult`，通过 `report` 上报 | 图谱读写、派发、运行 |
| TaskDispatcher | 订阅 `DISPATCH_REQUESTED`；`dispatch(TaskNode)` | 一个 `TaskNode` | `TaskNodePatch`，通过 `report` 上报 | 状态迁移、批处理、启动执行 |
| TaskRunner | 订阅 `EXECUTION_REQUESTED`；`run(TaskNode)` | 一个 `TaskNode` | 启动事实和执行结果，通过 `report` 上报 | 图谱读写、规划、选人 |

`TaskIntake` 是 TaskService 内部的澄清策略和会话组件，不是独立模块或公开服务。TaskCli 只依赖 TaskService。

## 3. 领域对象

复用：`TaskExecutionGraph`、`TaskNode`、`TaskNodePatch`、`TaskSpec`、`PlanResult`、`TaskCallbackData`、`TaskOpResult`、`NodeOpResult`。

唯一新增跨模块领域视图是 `TaskContext`。它是图谱服务按规划所需最小范围生成的只读投影，不是完整图谱、Snapshot 或另一套命令对象：

```python
@dataclass(frozen=True)
class TaskContext:
    task_id: str
    target_node_id: str | None
    graph_version: int
    task_spec: TaskSpec
    node_and_dependency_state: ...
    relevant_completed_outputs: ...
    acceptance_criteria_and_current_gap: ...
    constraints_resources_and_authorization_scope: ...
```

`graph_version` 属于 `TaskExecutionGraph`，随 `TaskContext` 或 `TaskNode` 携带，用于上报时的乐观并发校验，不是独立业务参数。

`TaskContext` 是面向本次规划的最新完整任务投影：它同时包含任务目标与验收标准、图谱/节点/依赖状态、与本次决策相关的已完成产出、当前未满足的 GAP，以及约束、资源和授权边界。接力模式中，刚完成节点 A 的结果是触发新规划的最新增量，A 的 Bot 仍必须以图谱生成的这个完整投影计算 GAP；不得用 A 的本地结果替代整个上下文。

## 4. 统一上报和图谱写入

统一上报接口定义并实现于 `TaskGraphService`：

```python
class TaskGraphService:
    async def report(self, data: TaskCallbackData) -> TaskOpResult: ...
```

`TaskCallbackData` 是报告数据载体；它不是接口。它的结构化信封包含 `report_type`、`task_id`、`node_id`、`graph_version` 和 `payload`。

| `report_type` | payload | 可改变的事实 |
| --- | --- | --- |
| `PLAN` | `PlanResult` | 图谱结构和规划结论 |
| `DISPATCH` | `TaskNodePatch` | `run_mode`、`assignee`、派发诊断字段 |
| `EXECUTION_STARTED` / `EXECUTION_START_FAILED` | `TaskNodePatch` | 启动事实和诊断字段 |
| `EXECUTION_RESULT` | 执行结果 payload | 输出、完成或失败事实 |
| `OBSERVATION` / `INVALID_REPORT` | 审计 payload | 仅审计，不推进节点状态 |
| `RECOVERY` | `TaskNodePatch` | 已定义的恢复字段 |

TaskGraphService 校验身份、版本、幂等、状态机、字段白名单和授权范围；然后持久化图谱事实与 Outbox 事件。图谱原始变更和持久化实现仅属于 Graph 模块内部，其他模块不可访问。

TaskPlanner、TaskDispatcher、TaskRunner 只获得绑定到 `TaskGraphService.report` 的单方法能力，不获得完整 GraphService 实例；因此不能读取图谱或调用原始写方法。

## 5. 事件和模块 API

核心语义事件：

```text
PLAN_REQUESTED(TaskContext)
DISPATCH_REQUESTED(TaskNode)
EXECUTION_REQUESTED(TaskNode)
TASK_TERMINATED(...)
```

事件契约只声明业务 payload。Graph 与 Event Bus 可以在内部使用投递目标完成定向投递：中心化模式投递给 Master runtime，接力模式投递给刚完成上游节点的 Bot runtime；该投递信息不进入 `TaskContext`、`TaskNode` 或下列模块 API。接力上报的合法性由 Graph 基于报告身份、`TaskContext` / `TaskNode` 已有的 `graph_version`、父节点关系、深度和既有回调幂等机制校验。Dispatcher 写入 `TaskNodePatch` 的 `run_mode`、`assignee` 已固化在后续 `TaskNode` 中，故不另设 `execution_carrier` 入参。

```python
class TaskPlanner(Protocol):
    async def plan(self, context: TaskContext) -> PlanResult: ...

class TaskDispatcher(Protocol):
    async def dispatch(self, node: TaskNode) -> TaskNodePatch: ...

class TaskRunner(Protocol):
    async def run(self, node: TaskNode) -> bool: ...
```

事件消费支持至少一次投递和幂等。批量并发属于事件消费层的吞吐能力，不进入 TaskDispatcher 或 TaskRunner 的领域入参。工作流是单 Bot executor 的实现，YAML 是协作组 executor/策略的实现，均不属于 TaskCli 命令。

## 6. 标准数据流

1. TaskCli 将每轮输入交给 `TaskService.intake(...)`；TaskService 内部完成识别、四要素澄清和确认。
2. 确认后，TaskService 调用 `execute(TaskInfoRequest)` 创建正式图谱和根节点，产生 `PLAN_REQUESTED(TaskContext)`。
3. TaskPlanner 上报 `PLAN`；图谱接纳后为就绪节点产生 `DISPATCH_REQUESTED(TaskNode)`。
4. TaskDispatcher 上报 `DISPATCH`；图谱接纳后产生 `EXECUTION_REQUESTED(TaskNode)`。
5. TaskRunner 启动 executor，并上报启动事实；执行实体上报最终结果。
6. 图谱根据节点状态、依赖、验收和策略产生下一轮规划、派发、执行或终态事件。每次执行结果先被图谱接纳，再由图谱生成最新 `TaskContext`；没有模块可以以本地结果直接触发下一模块。

## 7. 统一逻辑链与不同物理执行拓扑

每个任务都使用同一逻辑职责和因果顺序：`TaskGraphService → TaskPlanner → TaskDispatcher → TaskRunner → TaskGraphService.report`。中心化动态规划与接力规划不是两套执行架构；差异仅在 Graph 将事件定向投递到哪个实际运行时，以及该运行时挂载哪种策略。

| 逻辑角色 | 中心化动态规划 / Master-Slave 的实际执行者 | 接力规划的实际执行者 |
| --- | --- | --- |
| TaskPlanner | Master Bot runtime 的中心化规划策略 | 上一个节点完成者的 Bot runtime 中的 relay 规划策略 |
| TaskDispatcher | 中心化 Dispatcher runtime / 策略 | 同一上游 Bot runtime 中的 relay 派发策略 |
| TaskRunner | 被派发的单 Bot、协作组或 BBS 的 Runner strategy | 被派发的下一执行 carrier 的 Runner strategy |

物理同驻不改变逻辑边界：接力 Bot runtime 可以同时挂载 TaskRunner、TaskPlanner 和 TaskDispatcher 的策略 adapter，但它们只能分别响应 `EXECUTION_REQUESTED`、`PLAN_REQUESTED` 和 `DISPATCH_REQUESTED`。它们不可以在内存中直接从 Runner 调 Planner/Dispatcher，也不可以直接改图谱。

### 7.1 中心化动态规划与 Master-Slave

Master runtime 消费 `PLAN_REQUESTED(TaskContext)` 并以 `PLAN` 上报阶段性 `PlanResult`；图谱为就绪节点生成 `DISPATCH_REQUESTED`，中心化 Dispatcher 决定 execution carrier，TaskRunner 在被选 carrier 侧实际执行。Master-Slave 只是 PlanResult 中存在多个 worker 节点以及依赖解锁汇总节点，仍使用这一条事件链。

### 7.2 接力规划：完整上下文驱动的续接

以 A 完成、规划后续工作 B 为例：

1. 承载 A 的 TaskRunner/执行实体通过 `report(EXECUTION_RESULT)` 上报 A 的事实。
2. TaskGraphService 原子接纳 A 的结果，更新图谱，并生成包含全局最新信息的 `TaskContext(v+1)`；它不是 A 的结果快照。
3. Graph 将 `PLAN_REQUESTED(TaskContext)` 定向投递给 A runtime；A runtime 的 TaskPlanner relay strategy 计算“最新任务上下文相对任务验收标准”的 GAP，并以 `report(PLAN, PlanResult)` 上报下一步工作 B。
4. Graph 基于报告身份、`graph_version`、父节点关系、深度和既有回调幂等机制校验报告后，必要时将 `DISPATCH_REQUESTED(B)` 定向投递给 A runtime；A runtime 的 TaskDispatcher 策略只决定 B 的执行方式与执行主体，并以最终 `DISPATCH` patch 上报。
5. Graph 接纳派发后发布 `EXECUTION_REQUESTED(B)` 给 B 的实际 carrier。B 完成后，再从第 1 步开始下一轮。

接力策略只替换 TaskPlanner/TaskDispatcher/TaskRunner 的内部决策，不改变报告入口、事件类型或图谱状态机。接力 Bot 无权直接增删节点、直接启动下一执行者或直接修改图谱。

### 7.3 Execution carrier 与 BBS 升级

Dispatcher 的输出不是“必须找到 Bot B”，而是为单节点确定 execution carrier：

| 搜推结果 | `TaskNodePatch` 中的派发结果 | 后续执行 |
| --- | --- | --- |
| 匹配单 Bot | `run_mode=single_bot`、Bot assignee | single-bot Runner strategy |
| 匹配协作组 | `run_mode=coop_group`、group assignee | coop-group Runner strategy |
| 单 Bot/协作组均无可用匹配 | `run_mode=bbs`、BBS carrier 与候选失败诊断 | BBS Runner strategy |

三种情况均只上报一次最终 `DISPATCH` patch。BBS 的建群、招募、成员认领和内部协作是 BBS Runner strategy 的执行职责；Graph 只接收其指定协调者的启动/结果事实。BBS 执行失败后由 Graph 状态策略决定重试、重新规划、挂起或终态，Dispatcher 不直接写终态。

## 8. B 端策略插件与组合编排

TaskCli 不加载、注册或选择插件。插件由 B 端控制面治理；任务运行时只使用已经被租户批准并由 Profile 冻结的 adapter。

### 8.1 模块稳定，策略可插拔

`TaskPlanner`、`TaskDispatcher`、`TaskRunner` 是稳定宿主模块。第三方扩展的是其内部策略 seam，不替换模块本身：

```python
class TaskPlanningStrategy(Protocol):
    async def plan(self, context: TaskContext) -> PlanResult: ...

class TaskDispatchStrategy(Protocol):
    async def dispatch(self, node: TaskNode) -> TaskNodePatch: ...

class TaskRunnerStrategy(Protocol):
    async def run(self, node: TaskNode) -> bool: ...
```

这三类接口分别复用 `TaskPlanner.plan`、`TaskDispatcher.dispatch`、`TaskRunner.run` 的既有入出参。`TaskRunnerStrategy` 是 executor 扩展 seam：它执行实际启动，TaskRunner 负责启动事实与结果事实的统一上报。

内置策略与第三方策略处于同一注册目录。例如：

| 宿主模块 | 内置策略 | 可新增的 B 端策略 |
| --- | --- | --- |
| TaskPlanner | master-planning、relay-planning | 领域规划策略 |
| TaskDispatcher | keyword-match | semantic-match |
| TaskRunner | single-bot、coop-group、bbs | distributed-execution |

所有策略 adapter 都不得读写图谱、调用 TaskService、调用其他任务模块或绕过 `TaskGraphService.report`。

### 8.2 插件注册与租户激活

插件包声明插件 ID、不可变版本、契约版本、贡献的策略、entrypoint、配置 Schema、支持的 run mode 和权限。B 端控制面按以下顺序治理：

```text
发布插件包 → 安装并校验契约 → 对租户激活 → 应用 TaskRuntimeProfile
```

控制面验证插件来源、契约版本、配置 Schema、运行模式和租户授权。插件管理命令或管理后台属于 B 端控制面，不属于 TaskCli；TaskCli 保持一句话任务入口。

### 8.3 TaskRuntimeProfile：声明式组合

`TaskRuntimeProfile` 是租户范围的声明式配置，而不是可直接执行和互调模块的插件。它将已经激活的策略版本组合为一个受治理的运行方案：

```yaml
apiVersion: task-runtime-profile/v1
id: acme-distributed-release-v1
version: 1

match:
  tenant: acme
  taskLabels: [release]

bindings:
  planner:
    ref: builtin.master-planning

  dispatcher:
    chain:
      - ref: acme.semantic-match@1.0.0
      - ref: builtin.keyword-match

  runners:
    single_bot:
      ref: builtin.single-bot
    coop_group:
      ref: builtin.coop-group
    bbs:
      ref: builtin.bbs
    distributed:
      ref: acme.distributed-execution@1.0.0
```

TaskService 在创建任务时按租户、Bot、来源和任务标签解析 Profile，校验其所有策略均已激活，并将 `profile_id`、Profile 版本、插件版本及 digest 冻结到任务受控运行配置。运行中的任务不会因 Profile 或插件升级发生策略漂移。

### 8.4 组合规则

- 每个规划事件只由一个 TaskPlanningStrategy 产生可上报的 `PlanResult`；不合并多个规划结果。
- Dispatcher 可以按 Profile 链式尝试策略。策略在产生外部副作用前返回受约定的 `NO_MATCH`；TaskDispatcher 继续尝试下一项，且仅将最终 `TaskNodePatch` 上报一次。
- Dispatcher 只能从冻结 Profile 允许的 run mode/TaskRunnerStrategy 中选择执行方式；TaskGraphService 校验该选择。
- TaskRunner 按节点 run mode 选择一个 TaskRunnerStrategy。启动外部执行后不进行通用 fallback，避免重复副作用；需要重试或故障切换时，由该 Runner strategy 在自身幂等语义内实现。
- Profile 只能选择、路由和配置策略，不能重排“规划 → 派发 → 执行”生命周期。

### 8.5 定制扩展场景

下列 case 说明 Profile 支持“只替换一个策略”到“三类策略联合定制”的渐进扩展；未声明的绑定均使用系统内置策略。

| Case | B 端目标 | Profile 绑定 | 运行时链路 | 关键约束 |
| --- | --- | --- | --- | --- |
| A：语义搜推 | 在保留现有规划和执行方式的前提下，提高 Bot 匹配质量 | `planner=builtin.master-planning`；`dispatcher=[acme.semantic-match, builtin.keyword-match]`；Runner 保持内置 | semantic-match 无候选时返回 `NO_MATCH`，TaskDispatcher 尝试 keyword-match；最终 Patch 决定 single-bot、coop-group 或 bbs | 两个 Dispatcher 策略都尚未产生外部副作用；只上报最终 Patch |
| B：分布式执行 | 将计算密集型节点投递到企业分布式调度平台 | Planner、Dispatcher 保持内置；`runners.distributed=acme.distributed-execution` | Dispatcher 为 `compute` 节点选择 `run_mode=distributed`；TaskRunner 加载 distributed strategy，向调度平台提交并由回调报告结果 | TaskGraphService 只接受冻结 Profile 允许的 `distributed`；启动后不切换到其他 Runner strategy |
| C：领域规划 | 用合规/发布领域规则规划工作节点，继续复用平台搜推和协作执行 | `planner=acme.compliance-planning`；`dispatcher=builtin.keyword-match`；Runner 保持内置 | 自定义 Planner 基于 TaskContext 返回带依赖的 PlanResult；后续节点仍按标准派发、执行、回报 | Planner 只产生 PlanResult，不直接指定执行者、派发或建图 |
| D：端到端定制发布 | 发布任务采用领域规划、语义匹配和分布式执行组合 | `planner=acme.release-planning`；`dispatcher=[acme.semantic-match, builtin.keyword-match]`；`runners.distributed=acme.distributed-execution` | release Planner 产出发布节点；semantic dispatcher 选择带发布权限的 worker pool 并设为 distributed；Runner 提交发布平台 | 每个事件轮次仍只有一个主 Planner、一个最终 Dispatcher 决策、一个按 run mode 选中的 Runner strategy |
| E：只组合内置能力 | 不开发插件，仅为不同租户选择不同执行模式 | `planner=builtin.relay-planning`；`dispatcher=builtin.keyword-match`；`runners.bbs=builtin.bbs` | 节点完成后触发接力规划；派发 BBS 节点；BBS strategy 执行并报告结果 | Profile 是策略选择，不改变接力规划、图谱校验或报告闭环 |

Case A 的 Profile 片段：

```yaml
bindings:
  planner:
    ref: builtin.master-planning
  dispatcher:
    chain:
      - ref: acme.semantic-match@1.0.0
      - ref: builtin.keyword-match
```

Case B 的 Profile 片段：

```yaml
bindings:
  planner:
    ref: builtin.master-planning
  dispatcher:
    ref: builtin.keyword-match
  runners:
    distributed:
      ref: acme.distributed-execution@1.0.0
```

TaskCli 对以上 case 一律保持相同：用户或 Agent 只输入任务目标；Profile 匹配、策略选择和插件版本冻结由 TaskService 在任务创建时完成。

## 9. TaskCli 与 TaskService

TaskCli 是人和 Agent 的统一终端入口，公开命令保持极简：

```text
task [<一句话任务目标>]
task get <task_id>
task list
```

`task` 在同一会话中完成任务识别、补齐 `goal`、`deliverables`、`acceptance_criteria`、`constraints`、展示汇总和最终确认。确认前不创建正式任务；确认后返回 `task_id`。不暴露 `intake_id`、`answer`、`review`、`confirm`、workflow 或 YAML 二级命令。

## 10. 目标态质量约束

- 仅 Graph 模块拥有图谱状态写入和持久化。
- TaskPlanner、TaskDispatcher、TaskRunner 不依赖完整 `TaskExecutionGraph`、GraphService 或彼此实现。
- `report` 对所有报告类型执行版本、幂等、状态机和字段白名单校验。
- 三种策略模式通过同一事件链闭环；接力越权或过期报告被拒绝。
- 插件只能通过声明的策略 seam 被宿主模块调用；Profile 引用、租户授权、版本和 digest 在建任务时校验并冻结。
- TaskCli 对话在确认前不生成正式 `task_id`。

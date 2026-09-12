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
    relevant_dependency_state: ...
    completed_outputs: ...
    acceptance_and_gap: ...
    constraints: ...
```

`graph_version` 属于 `TaskExecutionGraph`，随 `TaskContext` 或 `TaskNode` 携带，用于上报时的乐观并发校验，不是独立业务参数。

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
6. 图谱根据节点状态、依赖、验收和策略产生下一轮规划、派发、执行或终态事件。

## 7. 策略扩展

| 模式 | 图谱/策略事实 | 公共闭环 |
| --- | --- | --- |
| 中心化动态规划 | TaskPlanner 根据 `TaskContext` 产生阶段性 `PlanResult` | 规划、派发、执行、回报 |
| Master-Slave | Master 产生 worker 节点；依赖满足使汇总节点就绪 | 规划、派发、执行、回报 |
| 接力规划 | 节点完成触发新的规划请求；接力 Bot 以 `PLAN` 报告下一步 | 图谱校验版本、授权、深度后继续闭环 |

策略只替换 TaskPlanner/TaskDispatcher/TaskRunner 内部决策，不改变报告入口、事件类型或图谱状态机。接力 Bot 无权直接增删节点或修改图谱。

## 8. TaskCli 与 TaskService

TaskCli 是人和 Agent 的统一终端入口，公开命令保持极简：

```text
task [<一句话任务目标>]
task get <task_id>
task list
```

`task` 在同一会话中完成任务识别、补齐 `goal`、`deliverables`、`acceptance_criteria`、`constraints`、展示汇总和最终确认。确认前不创建正式任务；确认后返回 `task_id`。不暴露 `intake_id`、`answer`、`review`、`confirm`、workflow 或 YAML 二级命令。

## 9. 目标态质量约束

- 仅 Graph 模块拥有图谱状态写入和持久化。
- TaskPlanner、TaskDispatcher、TaskRunner 不依赖完整 `TaskExecutionGraph`、GraphService 或彼此实现。
- `report` 对所有报告类型执行版本、幂等、状态机和字段白名单校验。
- 三种策略模式通过同一事件链闭环；接力越权或过期报告被拒绝。
- TaskCli 对话在确认前不生成正式 `task_id`。

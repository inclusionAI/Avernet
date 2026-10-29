# Bot S1-S6 函数式执行协议

task-cli 的命令、intake session 和 HTTP adapter 设计见 [`task-cli/design.md`](../2026-09-23-task-cli/design.md)。

## 1. 文档定位

本文定义分布式 Relay 模式下 S1-S6 的目标协议：Bot 执行 S1–S5.5 并通过 HTTP 触发 S6；Backend 的 `TaskDispatcher` 只实际派发后继棒。正常已确认任务的根节点由前端把 Backend 生成的权威执行指令转发到用户确认任务的当前 Bot 会话；`benchmark_execute` 评测任务由 Backend 在同一个评测会话中转发该指令。两种入口都不创建第二个 Runner 会话。每个步骤均建模为：

```text
输入参数 -> 处理函数 -> 输出值或领域结果
```

已有领域对象直接作为函数参数传递。只有对象本身具有独立领域语义，或需要跨 API、消息、持久化边界时才定义新类型；禁止为单个函数机械包装参数。

本文定义已落地的 Relay 合同：任务级 `task_gaps` 只存在于 Bot 当前工作流；S1 一律通过节点级 context API 获得图谱投影；规划使用未落图 `TaskNode`，一次 `PLAN_RESULT` 原子登记规划和执行者决策；Bot 的执行指令固定为“更新缺口 → 规划节点 → 搜索 → 决策 → PLAN_RESULT → S6 /dispatch”。Backend 待投递意图只属于后继节点；后台消费者负责后继棒未投递意图的恢复。

本文只讨论 Relay 模式，不改变 centralized、static plan、workflow、YAML 等模式。

## 2. 设计原则

### 2.1 单一上下文来源

Bot 只消费一种 `TaskContext`，且只能由节点级 context API 返回：

- 第一棒：`TaskService.execute()` 创建根节点并返回 `TaskOpResult(task_id, node_id)`，同时在 `extend_props.root_execution_instruction` 返回 Backend 生成的权威执行指令。正常任务由前端将该指令原样发送到用户确认任务的当前 Bot 会话；`benchmark_execute` 评测任务由 Backend 发送到同一个评测会话。
- 后续棒：S5.5 创建下一节点时同事务登记待投递意图；当前棒 Skill 调用 S6 HTTP 派发，后台消费者在未投递时恢复。派发消息只携带下一节点的 `task_id` 与 `node_id`。
- S1：Bot Runtime 以这两个标识调用节点级 context API，取得 `TaskContext`。
- harness 恢复：使用已持有的同一对标识调用同一接口。

`TaskService.execute()` 不返回或注入 `TaskContext`；它只返回根节点执行指令。根 Bot 收到指令后仍必须调用节点级 context API 获取唯一权威 `TaskContext`。后续棒才通过待投递意图、Dispatcher 和 Runner 交接，因此根节点与后继节点的启动通道不同，但业务上下文来源始终只有节点级 context API。

### 2.2 事实与推理分离

Backend 持久化：

- 根任务与当前节点的 `TaskSpec`；
- 节点 `actual_goal`；
- 节点执行 `output`；
- 节点 `AcceptanceResult.done_items`；
- 节点 `AcceptanceResult.gap_items`；
- 下一棒 `TaskSpec`；
- Relay 节点、图状态和幂等事实。

Bot 本地推理：

- 任务级 `task_gaps: list[str]`；
- 当前 Bot 可承接的 `actual_goal`；
- 下一棒 `next_task_spec`；
- 候选执行者和执行模态。

任务级 `task_gaps` 不写入 `TaskExecutionGraph.extend_props`，也不通过 `TaskContext` 传给下一棒。每一棒都根据根任务规格和已接纳执行事实重新计算。

### 2.3 步骤间显式传值

任一步骤需要的数据必须满足下列条件之一：

1. 位于该步骤的显式函数参数中；
2. 是显式注入处理函数的 Port，例如 context reader、candidate searcher 或 reporter；
3. 是已有领域对象中的字段。

不得通过 Prompt 中未定义的自由文本、进程全局变量或重复读取 TaskGraph 隐式传值。

## 3. 用户会话到第一棒

```text
用户会话
  -> P0 TaskService.intake(START)
  -> 目标解析 / 信息澄清（按返回状态循环）
  -> P1 TaskService.intake(CONFIRM)
  -> TaskIntakeResult(status=CONFIRMED)
  -> CLI POST /start {session_id}
  -> P2 Backend 读取 CONFIRMED Result 并构造内部 TaskInfoRequest
  -> TaskService.execute(TaskInfoRequest)
  -> 返回 TaskOpResult(task_id, node_id, root_execution_instruction)
  -> 正常任务 Frontend / benchmark 评测任务 Backend 将 root_execution_instruction 发送到当前 Bot 会话
  -> 首棒 S1 读取节点级 TaskContext
  -> S2 calculate_task_gaps
  -> S3 match_bot_capability
  -> S4 execute_current_task -> report_execution_result
  -> S5.1 refresh_task_gaps
  -> S5.2 plan_next_node
  -> S5.3 TaskService.search_bots（无 gaps 时跳过）
  -> S5.4 select_executor（无 gaps 时跳过）
  -> S5.5 TaskService.report(PLAN_RESULT)
  -> Bot S6 调用 /dispatch；Backend 消费后继节点投递意图完成派发
  -> 下一棒从 S1 重新开始，或任务完成，或进入 BBS 等待认领
```

### 3.1 任务识别澄清阶段的公共领域对象

确认前不得使用 `TaskInfo`。`TaskInfo` 含正式 `task_id`，任务识别澄清阶段尚未创建任务实体，直接复用既有 `TaskSpec` 作为唯一任务业务规格，不新增草稿规格模型。

```python
class IntakeTargetType(StrEnum):
    BOT = "BOT"
    GROUP = "GROUP"


@dataclass(frozen=True)
class IntakeTarget:
    target_type: IntakeTargetType
    target_id: str


class TaskIntakeStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


```

约束：

```text
调用者身份和会话控制均由 Adapter 构造 `TaskIntakeContext`，不由 CLI 请求体声明
IntakeTarget 使用稳定 ID，不使用展示名称作为身份
Intake session 创建后 target 不可变
TaskSpec 完整条件：
  goal.objective 非空
  context.extend_props.deliverables 非空
  goal.acceptances 非空
  constraints 已明确，允许 []，但必须经用户确认
```

`deliverables`、`constraints`、`resources` 分别位于 `TaskSpec.context.extend_props`；验收项使用 `TaskSpec.goal.acceptances`。Task Intake Module 在首次写入验收项时分配稳定 ID，后续澄清更新同一验收项必须保留该 ID。

`TaskIntakeCommand` 不接收认证、会话控制或调用来源字段。HTTP Adapter 将已认证 Principal、既有调用上下文和本次请求控制信息收敛为 Task Intake 应用边界的最小请求上下文：

```python
@dataclass(frozen=True)
class TaskIntakeContext:
    session_id: str | None
    owner_user_id: str
    source_type: TaskSourceType
    caller_bot_id: str | None = None
```

`TaskIntakeContext` 不是新的认证领域模型，也不替代既有 `VerifiedCaller`；它将 `session_id`、`owner_user_id`、`caller_bot_id`、`source_type` 收敛为一个调用上下文，避免这些系统字段散落在 Task Intake 函数签名中。它们均不来自请求体，也不允许 Skill/LLM 生成或覆盖。`owner_user_id` 写入 `TaskIntakeResult.owner_user_id` 并校验 session owner；`caller_bot_id` 仅用于 Bot 调用未指定目标时的默认 target；`source_type` 在 START 时写入 `TaskIntakeResult.source_type`。确认后的任务创建链路从 Result 读取 `owner_user_id` 与 `source_type`，写入 `TaskInfoRequest`。

### 3.2 任务识别澄清阶段的输入对象

所有用户动作均为有判别字段的 command。确认和取消不得依赖模型从自由文本猜测。

```python
class TaskIntakeAction(StrEnum):
    START = "START"
    RESUME = "RESUME"
    CONTINUE = "CONTINUE"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class TaskIntakeCommand:
    action: TaskIntakeAction
    message: str | None = None
    requested_target: IntakeTarget | None = None
```

`TaskIntakeCommand` 是 CLI / HTTP Adapter 调用 `TaskService.intake` 的内部应用层命令，不是用户直接填写的对象。它只表示调用者本次希望 Intake 做什么：`message` 来自用户；用户显式指定 `@bot` / `@group` 时，Adapter 解析为稳定 ID 后写入 `requested_target`；`action` 由结构化交互动作决定。它不承载幂等键、会话控制、调用者身份、调用来源或最终解析的 `TaskIntakeResult.target`。`session_id` 由 `TaskIntakeContext` 承载；用户仅在 `task-cli resume <session-id>` 时把 ID 交给 CLI，CLI 再注入上下文。

HTTP 请求体只承载 `TaskIntakeCommand`；HTTP Adapter 按相同规则校验字段、解析 target，并构造 `TaskIntakeContext`；领域服务不接收展示名称或未校验的用户控制字段。

澄清动作不传请求级幂等键；服务端按会话状态与内部版本处理并发。字段有效性：

```text
START -> message 必填；requested_target 可选
RESUME -> context.session_id 必填
CONTINUE -> context.session_id、message 必填
CONFIRM / CANCEL -> context.session_id 必填
```

### 3.3 任务识别澄清阶段的输出对象

```python
@dataclass
class TaskIntakeResult:
    session_id: str
    owner_user_id: str
    source_type: TaskSourceType
    status: TaskIntakeStatus
    target: IntakeTarget | None = None
    task_spec: TaskSpec | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    create_time_ms: int = 0
    update_time_ms: int = 0
```

约束：

```text
CLARIFYING -> 尚未由用户确认；TaskSpec 可完整或不完整
CLARIFYING + TaskSpec 不完整 -> missing_fields、questions 非空
CLARIFYING + TaskSpec 完整 -> target、task_spec 非空；展示确认卡
CONFIRMED -> target、task_spec 非空；表示用户确认 TaskSpec，不代表正式任务已经创建
CANCELLED -> 不创建 TaskInfo 和 TaskExecutionGraph
```

`TaskIntakeResult` 是 Task Intake 唯一的持久化状态对象，也是所有 intake 动作（包括 `CONFIRM`）的直接返回值。所有字段均可返回给用户。其完备性只取决于 `task_spec` 是否满足完整性约束，与是否已创建正式任务无关；因此不保存 `task_id/node_id`。`TaskInfoRequest` 是确认后的独立任务创建链路交给 `TaskService.execute()` 的创建命令，由 Result 和服务端策略组装；`TaskOpResult` 是正式任务创建的唯一标识返回值。字段由 `status` 约束，不新增状态专属派生类。

### 3.4 P0：识别、选择与澄清

#### 输入参数

```python
TaskIntakeCommand
TaskIntakeContext
```

#### 处理函数

```python
class TaskService:
    async def intake(
        self,
        command: TaskIntakeCommand,
        context: TaskIntakeContext,
    ) -> TaskIntakeResult:
        ...
```

五种 action 的完整处理与 `TaskIntakeCommand + TaskIntakeContext → TaskIntakeResult` 字段转换伪代码见 [task-cli 设计第 8 节](../2026-09-23-task-cli/design.md#8-taskserviceintake-处理伪代码)。

处理规则：

1. `START` 先解析并校验 target，再识别一个 `TaskSpec`。
2. Human 未显式指定 target 时，依次尝试同名可运行 Bot、`default_bot_id`、按目标最匹配的可运行 Bot、随机可运行 Bot；没有可运行 Bot 时返回 `NO_AVAILABLE_TARGET` 并提示创建或启用。
3. Bot 调用方未显式指定 target 时，target 为调用 Bot 自身。
4. `CONTINUE` 只根据新回复更新草稿，保留未修改字段并重新校验完整性。
5. 一段输入只形成一个 `TaskSpec`；多意图由澄清 Skill 合并为一个任务目标或追问用户拆分后分别发起。
6. 无论信息是否完整都返回 `CLARIFYING`；TaskSpec 不完整时返回问题，完整时展示确认卡。
7. intake session 必须持久化；CLI 退出后可通过 `task-cli resume <session-id>` 继续。

#### 输出对象

```python
TaskIntakeResult
```

该阶段只返回以下状态，不创建正式任务：

```text
CLARIFYING
```

### 3.5 P1：确认 TaskSpec

#### 输入参数

```python
TaskIntakeCommand(action=CONFIRM)
context: TaskIntakeContext
```

前置条件：intake session 状态为 `CLARIFYING`、`TaskSpec` 完整，且认证主体是该 session 的所有者。

#### 处理函数

```python
await task_service.intake(
    command,
    context,
)
```

确认处理只把 READY 的 Result 持久化为 `CONFIRMED` 并返回该 Result；不创建任务、不构造 `TaskInfoRequest`。

#### 输出对象

```python
TaskIntakeResult(status=TaskIntakeStatus.CONFIRMED)
```

### 3.6 P2：构造 TaskInfoRequest、注册任务并创建根节点

#### 输入参数

```python
TaskIntakeResult(status=TaskIntakeStatus.CONFIRMED)
```

#### TaskInfoRequest 转换

独立任务创建链路读取 CONFIRMED 的 `TaskSpec`，结合重新校验的调用者、已锁定 target 和服务端执行策略组装 `TaskInfoRequest`。`TaskInfoRequest` 是 `TaskService.execute()` 的既有输入。

```python
def build_task_info_request(
    result: TaskIntakeResult,
) -> TaskInfoRequest:
    ...
```

该转换位于确认后的独立任务创建链路，不是确认处理的内部步骤。`CONFIRM` 的返回值是 `TaskIntakeResult(status=CONFIRMED)`，不向 Result 添加任务标识。

```python
require(result.status == TaskIntakeStatus.CONFIRMED)
require(result.task_spec is not None)
require(result.target is not None)

owner_bot_id = (
    result.target.target_id
    if result.target.target_type == IntakeTargetType.BOT
    else target_resolver.resolve_group_manager_bot_id(
        result.target.target_id,
        result.owner_user_id,
    )
)

return TaskInfoRequest(
    task_spec=result.task_spec,
    source_type=result.source_type,
    owner_user_id=result.owner_user_id,
    owner_bot_id=owner_bot_id,
    execution_config={
        **execution_policy.for_new_task(),
        "session_id": result.session_id,
        "initial_target": {
            "target_type": result.target.target_type.value,
            "target_id": result.target.target_id,
        },
    },
)
```

`target_resolver` 与 `execution_policy` 是确认后创建链路的内部依赖，不是调用方传入的新领域对象。转换不修改 `TaskIntakeResult`；start 成功后也不向 Result 写入任务标识。

字段映射：

| 权威来源 | TaskInfoRequest |
| --- | --- |
| `result.task_spec` | `task_spec`；直接传递已确认的 `TaskSpec` |
| `result.session_id` | `execution_config.session_id`，作为创建幂等键 |
| `result.owner_user_id` | `owner_user_id` |
| `result.source_type` | `source_type`；Human task-cli 为 `API`，Bot 为 `BOT`，协作群为 `COOP_GROUP` |
| Bot `result.target` | `owner_bot_id` |
| Group `result.target` | Group manager/master Bot ID -> `owner_bot_id` |
| `result.target` 的类型与 ID | `execution_config.initial_target`，冻结首棒执行目标；Group ID 不得丢失 |
| 服务端任务运行策略 | `execution_config` |

目标设计将 `TaskInfoRequest.task_spec` 收敛为 `TaskSpec`，删除与其语义重复的 `RequestContext`、`RequestGoal`、`RequestAcceptance` 和 `RequestTaskSpec`。现有实现仍使用这些请求规格类型，实施时须同步完成该合同迁移。

#### 中间对象

```python
TaskInfoRequest
```

#### 处理函数

正式任务创建继续复用现有 `TaskService.execute()`，不新增同构的 Service `start()`；HTTP `/start` 的应用层负责按 `session_id` 校验并构造请求：

```python
async def execute(
    request: TaskInfoRequest,
) -> TaskOpResult:
    ...
```

处理顺序：

```text
TaskInfoRequest
  -> 生成 task_id
  -> TaskInfoRequest.to_task_info(task_id)
  -> 持久化 TaskInfo
  -> initialize_graph(TaskInfo)
  -> 创建唯一根 TaskNode
  -> Backend 生成包含 task_id/node_id/relay_token/backend_url 的根节点执行指令
  -> 返回 TaskOpResult；根节点不登记 delivery intent
```

#### 输出对象

复用并扩展现有 `TaskOpResult`：

```python
@dataclass
class TaskOpResult:
    task_id: str
    success: bool
    node_id: str | None = None
    error: str | None = None
    run_id: int | None = None
    extend_props: dict | None = None
```

约束：

```text
success == True:
  task_id 非空
  node_id 非空
  run_id 非空
  node_id == task_id
  error == None

success == False:
  error 非空
  创建事务已回滚时 node_id == None
  已提交但 HTTP 响应丢失时，不返回 success=False；按 session_id 重试取得原成功结果
```

HTTP API：

```http
POST /api/v1/collaboration/tasks/start
```

HTTP 请求体仅为 `{"session_id": "..."}`；Backend 应用层按认证用户加载并校验已确认 `TaskIntakeResult`，构造内部 `TaskInfoRequest` 后调用 `TaskService.execute()`。正常确认后 CLI 自动调用；进程若在 CONFIRM 和 HTTP `/start` 之间中断，`RESUME` 自身只读，恢复界面提供「继续提交/查看任务」操作调用同一幂等 `/start`。CLI 无须判断任务是否已创建，由 Backend 返回已有 `TaskOpResult` 或创建一次；HTTP Adapter 不承担会话业务校验或事务编排。

成功响应：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "task_id": "task-001",
    "node_id": "task-001",
    "success": true,
    "run_id": 1,
    "error": null,
    "extend_props": {
      "orchestration_mode": "relay",
      "root_node_id": "task-001",
      "root_execution_instruction": "[task-execute]\\n..."
    }
  }
}
```

成功的 `TaskOpResult` 直接返回 task-cli；`TaskIntakeResult` 不保存或返回 `task_id/node_id`，仍只代表同一份已完备的澄清结果。Task Module 以 `execution_config.session_id` 保存内部创建幂等关联。根节点执行指令由创建响应返回并在当前 Bot 会话触发，不属于后台派发分支。

内部创建幂等关联一旦存在，Task Intake 拒绝该 intake session 的后续 `CONTINUE`、`CANCEL`；不把“已提交”、`task_id` 或 `node_id` 反写到 `TaskIntakeResult`。任务是否已经创建只由 Task Module 的内部关联和 `TaskOpResult` 表示。

### 3.7 P3：在当前会话启动第一棒

#### 输入参数

```python
root_node: TaskNode  # 已持久化，task_id == node_id
```

前置条件：

```text
root_node.node_id == root_node.task_id
TaskInfo.execution_config.initial_target == 已确认的 result.target
Bot 目标 -> root_node.run_info.run_mode == "single_bot"
         -> root_node.run_info.assignee == initial_target.target_id
Group 目标 -> root_node.run_info.run_mode == "coop_group"
           -> root_node.run_info.assignee == initial_target.target_id
```

`TaskInfo.owner_bot_id` 是任务归属 Bot；Group 目标时它是 manager/master Bot，不是根节点执行者。`source_type` 是发起来源，不用于反推 Bot/Group 目标。

#### 处理函数

`TaskService.execute()` 从已持久化根节点读取服务端生成的 `relay_token`，结合部署配置中的 `backend_url` 生成完整 `root_execution_instruction`，随 `TaskOpResult` 返回。转发方不得解释或重建协议，只把该指令隐藏附加到任务面板消息并发送给当前会话；正常任务转发方是前端，`benchmark_execute` 评测任务转发方是 Backend。根节点不调用 `TaskDispatcher.dispatch(root_node)` 或 `TaskRunner.start_run(root_node)`，避免创建新会话或触发权限兜底建群。`TaskInfoRequest` 仅用于创建任务，不能替代包含节点和执行事实的 `TaskContext`。

#### 输出对象

```python
TaskOpResult；当前 Bot 收到 Backend 生成的 root_execution_instruction
```

首棒不变量：

```text
task_id == node_id
root_task_spec == current_task_spec
all_done_output == []
```

第一棒实际进入 S1 时，由 Bot Runtime 调用节点级 context API；该接口使用持久化的 `TaskInfo` 和根 `TaskNode` 构造 `TaskContext`。不得在 `start` 路径根据 `TaskInfoRequest` 手工拼装另一份上下文。

P3 的根节点指令由 Backend 生成、Frontend 在当前会话转发；只有后续棒由当前棒 Skill 调用 S6 HTTP 接口并进入 `TaskDispatcher.dispatch(node)`。不新增 `FirstTaskDispatchInput` 或 `FirstTaskDispatchResult`。Bot 收到节点标识后自行进入 S1。

`TaskOpResult` 返回 `task_id`、`node_id` 和根节点执行指令，但不承担 `TaskContext` 传输；首棒从创建响应中的指令取得标识，后续棒从 Runner 指令取得标识，随后都调用同一个节点级 context API。

### 3.8 S1-S6 数据流与对象转换

```text
首棒 / 后续派发路径
  当前会话根指令 / 后续 Runner 派发消息中的 task_id + node_id
    -> load_task_context
    -> TaskContext
    -> S2

S2
  TaskContext
    -> calculate_task_gaps
    -> task_gaps: list[str]

S3
  TaskSpec + task_gaps + BotCapability
    -> match_bot_capability
    -> TaskContext.actual_goal（原地补全运行期目标；不新增结果对象）

S4
  TaskContext.actual_goal
    -> TaskNodePatch
    -> TaskReportCommand(EXECUTION_RESULT, event=TaskNodePatch)
    -> TaskReportResult（execution_report）

S5.1 更新缺口
  TaskContext + TaskNodePatch
    -> refresh_task_gaps
    -> task_gaps: list[str] + task_acceptance_result: AcceptanceResult | None

S5.2 规划下一棒（无 gaps 时跳过）
  TaskContext + task_gaps + TaskNodePatch
    -> plan_next_node
    -> next_node: TaskNode（未落图，仅填任务规格）

S5.3 搜索候选
  TaskContext + next_node.task_spec.goal
    -> project_candidate_query（本地检索投影）
    -> TaskService.search_bots（POST /search）
    -> candidates: list[BotCandidate]

S5.4 决定执行者
  next_node + candidates
    -> select_executor
    -> next_node: TaskNode（填入 run_mode、assignee 或 BBS/MISS 决策）

S5.5 统一上报
  S5.1 根任务验收 / S5.4 已填执行者的 next_node / S5.2 规划失败原因（三选一）
    -> TaskReportCommand(PLAN_RESULT)
    -> TaskService.report
    -> TaskReportResult（planning_report）

S6
  Skill 持有 task_id、origin_node_id、当前 relay_token 与 planning_report.target_node_id
    -> POST /dispatch(task_id, origin_node_id, target_node_id, relay_token)
    -> HTTP Adapter 校验当前执行代次， Backend 根据 task_id + target_node_id
       读取已落图的权威 next_node: TaskNode
    -> TaskDispatcher.dispatch(next_node)（内部返回 dispatched_node: TaskNode）
       -> 接力派发策略内部调用 TaskRunner.start_run(next_node)
    -> HTTP Adapter 返回 {ok: true, node_id}；下一棒收到 task_id + node_id
```

`TaskReportResult` 是统一上报接口的目标返回领域对象：S4b 产生 `execution_report`，S5.5 产生 `planning_report`。Bot 在 S5.5 接纳且命中执行者后调用 S6；首棒没有前一棒 Skill，由后台消费者触发同一派发逻辑。旧版 S6/S7 的规划与选人职责已并入 S5，S6 专指实际交接。

## 4. 公共领域对象

以下定义复用现有 `TaskSpec`、`Goal`、`AcceptanceCriteria`、`AcceptanceResult`、`DoneOutput` 和 `SearchOutcome`；不新增 Relay 专用包装对象。

### 4.1 TaskContext

```python
from __future__ import annotations

@dataclass(frozen=True)
class TaskContext:
    task_id: str
    node_id: str

    # 由 Backend 从当前节点 harness_retries 权威快照投影。
    attempt: int = 0

    # 整个任务的原始规格，供任务级 GAP 推理使用。
    root_task_spec: TaskSpec

    # 当前这一棒的执行规格。
    current_task_spec: TaskSpec

    # S3 在本棒运行上下文中补全；S1 对新尝试投影为空，恢复已接纳执行时投影节点值。
    actual_goal: Goal | None = None

    # S3 判定当前 Bot 无法承接时的能力缺口说明。
    capability_failure_reason: str | None = None

    # 严格早于本棒的、已被 Backend 接纳的前序节点事实；排除本棒及后继。
    all_done_output: list[DoneOutput]

    # 仅供本棒恢复判定；均由图谱事实和上报接纳记录即时投影。
    accepted_execution_patch: TaskNodePatch | None = None
    accepted_plan_report: TaskReportResult | None = None
```

`TaskContext` 是只读投影，不另行持久化，也不包含任务级 `gaps`。`attempt` 由 Backend 从当前节点 `run_info.extend_props["harness_retries"]` 投影；Bot 不得自行计数或硬编码，artifact 上报项中的 `attempt` 必须逐项取该值。`actual_goal` 是唯一的运行期补全字段：服务端 S1 对新的执行代次投影为 `None`，S3 在 Bot 本地填写；当本棒执行结果已被服务端接纳，恢复读取时从当前节点 `run_info.actual_goal` 投影，避免重新执行 LLM 能力匹配。`all_done_output` 的范围以当前 `node_id` 为界，沿唯一 Relay 前驱链选择已接纳的前序执行事实；本棒的执行结果即使已经上报，也只出现在 `accepted_execution_patch`，绝不回填进 `all_done_output`。空 `output_patch={}` 仍可表示已接纳执行，不能靠输出是否非空判断。`accepted_plan_report` 仅在本棒 `PLAN_RESULT` 已接纳时非空，用于恢复原 `target_node_id` 或完成/HUNG 结果；两字段均由已有领域对象承载，不新增包装对象。同一节点发生 harness 重试时，Backend 只投影当前有效执行尝试的接纳阶段；先前失败尝试留作节点审计事实，不得作为当前尝试的 `accepted_execution_patch` 阻断重试。

### 4.2 BotCapability

```python
@dataclass(frozen=True)
class BotCapability:
    # IDENTITY.md 的有效内容。
    identity: str

    # skill name -> 当前已激活 Skill 的能力说明。
    activated_skills: dict[str, str] = field(default_factory=dict)

    # tool name -> 当前实际可用工具的能力说明。
    available_tools: dict[str, str] = field(default_factory=dict)
```

`BotCapability` 是本次 Bot 执行使用的值对象，不写入 TaskGraph。若目标为协作群，`run_info.assignee` 仍是 Group ID；该 Group 的唯一 manager/master Bot 是 Relay 协调执行者，负责 S1–S5 与上报。S3 读取该协调 Bot 在群运行时实际可调用的 Skill、工具及群协作能力，不把 Group ID 当成 Bot ID，也不把未经授权的成员能力算入。群成员协作属于 S4 内部执行过程，最终由协调 Bot 汇总为当前节点的一份执行事实。

### 4.3 ExecutionDecision

```python
class ExecutionDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
```

### 4.5 TaskReportCommand 与 TaskReportResult

统一 `POST /callback/report` 的目标领域契约。它跨 S4b 和 S5.5 使用，不是任何单一步骤的包装对象。

```python
class TaskReportEventType(StrEnum):
    EXECUTION_RESULT = "EXECUTION_RESULT"
    PLAN_RESULT = "PLAN_RESULT"


@dataclass(frozen=True)
class TaskReportTraceStep:
    # action_type 使用 Bot 侧步骤码，如 S1_CONTEXT、S5C_SEARCH_CANDIDATES。
    action_type: str
    # 该步骤的推理输出/产出摘要；必须是 JSON object 字符串，包含 status 与实际输出字段。
    action_result: str
    # 该步骤的输入；结构化输入必须序列化为 JSON 字符串；无输入时可为 None。
    action_input: str | None = None
    # 本步骤前节点状态。
    status_from: str | None = None
    # 本步骤后节点状态。
    status_to: str | None = None
    # 过程证据、为什么推进、为什么选择等执行过程原因。
    boost_reason: str | None = None
    # 失败原因；成功时为 None。
    error_msg: str | None = None


@dataclass(frozen=True)
class TaskReportTrace:
    steps: list[TaskReportTraceStep] = field(default_factory=list)


@dataclass(frozen=True)
class TaskReportCommand:
    task_id: str
    node_id: str
    event_id: str            # Bot 调用层生成；同一事件重试复用
    relay_token: str         # Backend 派发前签发；Bot 原样携带本次代次值
    event_type: TaskReportEventType

    # event_type 的判别联合：
    # EXECUTION_RESULT -> TaskNodePatch
    # PLAN_RESULT -> 未落图 TaskNode；无下一棒时为 None
    event: TaskNodePatch | TaskNode | None

    progress_reason: str = ""
    failure_reason: str | None = None
    # 仅 PLAN_RESULT 收口时填写；针对根任务，不是当前节点的局部验收。
    task_acceptance_result: AcceptanceResult | None = None
    # Bot 侧执行轨迹；同一 event_id 重试必须原样复用。
    trace: TaskReportTrace | None = None


class TaskReportStatus(StrEnum):
    APPLIED = "APPLIED"


@dataclass(frozen=True)
class TaskReportResult:
    status: TaskReportStatus

    # PLAN_RESULT 创建了下一棒节点时必填。
    target_node_id: str | None = None

    # PLAN_RESULT 的迁移结果。
    task_completed: bool = False
    task_hung: bool = False
    bbs_publish_pending: bool = False
```

约束：

```text
TaskReportCommand.event 必须与 event_type 对应
TaskReportCommand.trace 参与 event_id 内容指纹
EXECUTION_RESULT + APPLIED + 非终态 -> 可以进入规划步骤
PLAN_RESULT(event=None, task_acceptance_result 非空, failure_reason=None) + APPLIED -> task_completed == True
PLAN_RESULT(event=None, task_acceptance_result=None, failure_reason 非空) + APPLIED -> task_hung == True
PLAN_RESULT(event=None, task_acceptance_result=None, failure_reason=None) -> 无效请求，不能推断为完成
PLAN_RESULT(event=TaskNode, run_mode=bbs) + APPLIED -> target_node_id 非空且 bbs_publish_pending == True；仅表示发布意图已持久化，不进入 S6
PLAN_RESULT(event=TaskNode, run_mode=single_bot/coop_group) + APPLIED -> target_node_id 非空，可进入 S6
```

`report` 是节点状态迁移，不是可任意追加的事件流：每次有效执行尝试的 `EXECUTION_RESULT` 和每个节点最终的 `PLAN_RESULT` 各只能接纳一次。Backend 在首派前生成 `relay_token`；只有开启新的执行代次并重新 prepare/dispatch 时才轮换该值。Relay 运行时无进展的 same-session harness 恢复不轮换 token，只在既有会话发送自然语言续跑消息。Bot 使用派发指令中的代次值调用节点级 context/report/dispatch，旧执行代次持旧值的迟到请求被拒绝。Relay Skill 只调用生产内网白名单保护的 `/api/v1/collaboration/tasks/**`，不携带用户或 Bot 认证凭证；`relay_token` 仅是节点执行代次栅栏，不是通用身份凭证。`event_id` 由 Bot 调用层为每个上报事件生成，同一 HTTP 请求重试必须复用；Backend 将事件 ID、规范化内容指纹和原 `TaskReportResult` 与图谱迁移同事务保存。相同事件重试返回原结果（包括 `target_node_id`），相同 ID 不同内容或同一阶段不同内容返回冲突；并发由图谱版本和唯一后继关系兜底。Relay 是 Skill 驱动执行，携带当前 `relay_token` 的新上报代表 Bot 侧最新执行事实，不以节点后台快照状态（例如 `HUNG`）拒绝；上报落地后的终态迁移以该事实为准。Bot 不生成 `relay_token`，也不传 graph_version、attempt 或自报身份；不修改 BaaS/OpenClaw 插件。

`trace` 是使 Bot 本地推理过程在后端可观测的唯一补充载荷，不参与状态机分支。`EXECUTION_RESULT.trace` 必须按顺序覆盖 S1、S2、S3、S4a、S4b 的执行检查点；`PLAN_RESULT.trace` 必须按顺序覆盖 S5.1～S5.5 的检查点。`trace` 是 HTTP 可选字段；一旦提供，`trace.steps` 必须存在，每个 step 必须是扁平七字段 `action_type/action_input/action_result/status_from/status_to/boost_reason/error_msg`，不允许 `step/status/detail` 旧结构或额外一级字段。`action_type` 直接使用该步骤的 S 码，不能使用 `execute/plan` 等旧事件名；`action_input` 只能是字符串或 `None`，记录该步骤输入，结构化内容必须序列化为 JSON 文本；`action_result` 必须是非空字符串，记录该步骤推理输出/产出摘要，且必须是可解析的 JSON object 字符串，包含非空 `status` 字段与至少一个实际输出字段；`completed/failed/ready/not_applicable` 等状态值只能作为 JSON 内的 `status` 字段；`boost_reason` 记录执行过程证据：为什么推进、为什么选择等等维度信息；`error_msg` 记录失败原因；`status_from/status_to` 记录本步骤前后节点状态。每个 `TaskReportTraceStep` 只包含该步骤的事实和必要摘要，不包含 `relay_token`、原始凭证或可被请求方伪造的图谱版本。Backend 在接纳报告时把每个 trace step 独立写入 `task_trajectory_events`；后续同事件的幂等重试不重复追加。trace 变化视作同一 `event_id` 的内容变化，服务端返回冲突。S6 由 dispatch API 的服务端动作日志和 dispatch 轨迹事件记录，不要求 Bot 事后补一条第三个 report 事件；S6 服务端记录同样沿用七字段语义，`action_result` 记录实际派发输出 JSON（包含 `status` 与派发结果字段），不是 delivered/failed 裸状态词，失败原因保存在 `error_msg`。

响应丢失后 Runtime 先经 S1 查询接纳阶段：已接纳则使用投影的原 patch/结果续跑，不重新调用 LLM 生成第二份可能不同的请求；未接纳才重试当前事件。

`TaskNode` 在 S5.2～S5.4 是**未落图节点**：`task_id`、`task_spec`、`run_info` 已知，`node_id`、`status`、`node_run_graph` 暂为 `None`。目标模型须正式支持这种合法临时状态；持久化的 `TaskNode` 则必须三者非空。Bot 只能填写规划规格、执行模式、执行者及必要的协作群信息；Backend 忽略或拒绝请求携带的节点 ID、状态、图谱引用与时间戳，并负责补齐它们。这是同一领域对象的两个生命周期状态，不新增 `PlanResult` 包装类。

相对于当前 `TaskNode` 定义，目标类型约束需要调整为：

```python
# 仅列生命周期变化的字段；其余字段复用现有 TaskNode。
node_id: str | None
status: Status | None
node_run_graph: TaskExecutionGraph | None
```

这三个 `None` 只对未落图对象有效；进入 `TaskExecutionGraph.tasks` 前必须由 Backend 一次性补齐。持久化和 Runner 消费端必须验证完整性，不能将临时空态当作运行时节点。

`BotCandidate` 是搜索命中的 Bot 候选值对象，不是 S5.4 的执行者决策。只暴露 Skill 选人所需的字段；现有返回中的检索分词、原始命中量和失败关键词仍归搜索服务的轨迹记录，不成为 Bot 的业务输入：

```python
@dataclass(frozen=True)
class BotCandidate:
    bot_id: str
    name: str
    description: str = ""
    score: float | None = None
```

`TaskService.search_bots(*, query: str, task_id: str, node_id: str) -> list[BotCandidate]` 是 Service 合同。HTTP Adapter 在 `POST /api/v1/collaboration/tasks/search` 先校验 `query/task_id/node_id/relay_token` 均必填且当前执行代次有效，再将领域候选投影为类型化 `TaskSearchResultDTO {candidates: list[BotCandidateDTO]}`，不再返回 `dict[str, Any]`；检索分词、失败关键词等诊断信息仅进入服务端轨迹，不进入 Bot 业务输入。`task_id/node_id` 只用于搜索轨迹归属当前棒，不影响候选检索；下一棒此时尚无节点 ID。

目标 Service 方法为 `TaskService.report(TaskReportCommand) -> TaskReportResult`。当前实现链路仍为 `RelayTaskEventDTO -> report_task_event(散列参数及 payload dict) -> dict`，需要在 Service、HTTP adapter 和测试中一起迁移，不能只修改 Bot 文档。

## 5. S1：获得 TaskContext

S1 只有一个业务函数：按 `task_id + node_id` 调用节点级 context API。第一棒和后续棒的标识都来自 Runner 派发指令；`TaskOpResult` 返回给任务创建调用方，不直接传给首棒 Bot；harness 恢复时标识来自 Runtime 的恢复记录。这些入口不是不同的上下文构造链路。

### 5.1 输入参数

```python
task_id: str
node_id: str
```

### 5.2 处理函数

```python
async def load_task_context(
    task_id: str,
    node_id: str,
) -> TaskContext:
    task_context = await get_node_context(task_id, node_id)
    if task_context.task_id != task_id:
        raise ValueError("task_id mismatch")
    if task_context.node_id != node_id:
        raise ValueError("node_id mismatch")
    return task_context
```

`get_node_context()` 表示 Bot Runtime 对节点级 context API 的既有调用，不是新增领域对象或领域服务。HTTP client、鉴权和重试属于 Runtime/adapter 的内部依赖，不列入 S1 的领域输入。

### 5.3 输出对象

```python
TaskContext
```

### 5.4 调用接口

```http
GET /api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context
```

该接口是 S1 的唯一业务上下文来源。第一棒、后续棒和 harness 恢复在相同任务图状态下得到的 `TaskContext` 字段和值必须一致。Runtime 按图谱已接纳阶段选择续跑位置：`accepted_plan_report` 非空时不重做 S2–S5，必要时直接恢复 S6；否则 `accepted_execution_patch` 非空时不重做 S3–S4，以该 patch 进入 S5；两者均为空才从 S2 开始。若该节点有尚未决议的上报事务，context API 不得返回“两字段均空”的可执行快照；应等待事务完成，或返回可重试的处理中响应。Backend 在事务/认领超时后完成恢复或回滚，再允许 S1 判定续跑位置。

### 5.5 输入到输出转换

```text
正常派发：
  dispatch.task_id + dispatch.node_id
    -> GET /tasks/{task_id}/nodes/{node_id}/context
    -> TaskContext

第一棒：
  Runner 派发根节点指令中的 task_id + node_id
    -> GET /tasks/{task_id}/nodes/{node_id}/context
    -> TaskContext

harness 恢复：
  saved_task_id + saved_node_id
    -> GET /tasks/{task_id}/nodes/{node_id}/context
    -> TaskContext

Task Module：
  task_id + node_id ─> 查询当前节点及已接纳事实 ─> TaskContext
  TaskContext.task_id            = task_id
  TaskContext.node_id            = node_id
  TaskContext.attempt             = 当前 TaskNode.run_info.extend_props.harness_retries
  TaskContext.root_task_spec     = 任务原始 TaskSpec
  TaskContext.current_task_spec  = 当前 TaskNode.task_spec
  TaskContext.all_done_output    = 仅本棒之前已接纳前序节点的 DoneOutput[]
  TaskContext.accepted_execution_patch = 本棒已接纳的 TaskNodePatch，未接纳则 None
  TaskContext.accepted_plan_report     = 本棒已接纳的 TaskReportResult，未接纳则 None
```

## 6. S2：计算当前任务级 gaps

### 6.1 输入参数

```python
task_context: TaskContext
```

### 6.2 处理函数

```python
def calculate_task_gaps(
    task_context: TaskContext,
) -> list[str]:
    root_spec = task_context.root_task_spec
    done_outputs = task_context.all_done_output

    # infer_gaps 是 Skill 对任务总验收项与既有执行事实的推理；
    # 不读取、不修改 TaskContext.gaps，也不写任务图谱。
    inferred = infer_gaps(
        root_spec=root_spec,
        done_outputs=done_outputs,
    )

    # list[str] 的稳定化只消除重复和空项，不能改变 Skill 给出的缺口语义。
    return unique_non_empty_strings(inferred)
```

计算依据：

```text
root_task_spec.goal.acceptances
+ all_done_output[].actual_goal
+ all_done_output[].output
+ all_done_output[].acceptance_result
```

S2 不读取历史任务级 gaps。

### 6.3 输入到输出转换

```text
TaskContext.root_task_spec.goal.acceptances
+ TaskContext.all_done_output[].actual_goal/output/acceptance_result
    -> infer_gaps(...)
    -> 去空、去重且保持推理顺序
    -> list[str] task_gaps

TaskContext 自身不变；task_gaps 不回写 TaskContext 或 TaskExecutionGraph。
```

### 6.4 输出值

```python
task_gaps: list[str]
```

示例：

```python
[
    "尚未完成接口异常响应定义",
    "尚未覆盖 harness 恢复流程",
]
```

## 7. S3：当前 Bot 能力匹配

### 7.1 输入参数

```python
task_context: TaskContext
task_gaps: list[str]
bot_capability: BotCapability
```

### 7.2 处理函数

```python
def match_bot_capability(
    task_context: TaskContext,
    task_gaps: list[str],
    bot_capability: BotCapability,
) -> TaskContext:
    if not task_gaps:
        raise ValueError("pending node has no task gaps")

    current_task_spec = task_context.current_task_spec
    # Skill 只能从当前节点的目标和验收项中裁出可执行范围；
    # 不能用根任务目标或 Bot 自己的偏好扩张范围。
    executable_acceptances = [
        item
        for item in current_task_spec.goal.acceptances
        if is_required_by_gaps(item, task_gaps)
        and bot_capability.supports(item, current_task_spec.context)
    ]
    if not executable_acceptances:
        task_context.actual_goal = None
        task_context.capability_failure_reason = explain_capability_gap(
            current_task_spec=current_task_spec,
            task_gaps=task_gaps,
            bot_capability=bot_capability,
        )
        return task_context

    task_context.actual_goal = Goal(
        objective=derive_actual_objective(
            current_task_spec.goal,
            executable_acceptances,
        ),
        acceptances=executable_acceptances,
    )
    task_context.capability_failure_reason = None
    return task_context
```

### 7.3 输出对象

```python
task_context: TaskContext
```

S3 不新增 `CapabilityMatchResult`。能力决策直接由 `TaskContext` 的运行期字段表达：

```python
actual_goal: Goal | None
capability_failure_reason: str | None
```

约束：

```text
actual_goal != None:
  capability_failure_reason == None

actual_goal == None:
  capability_failure_reason != None
```

`actual_goal` 不得扩大 `current_task_spec.goal`，其中的验收项必须来自当前节点验收范围。

### 7.4 输入到输出转换

```text
TaskContext.current_task_spec.goal.acceptances
  + task_gaps
  + bot_capability.activated_skills / available_tools
    -> 过滤出“仍需要且当前 Bot 能完成”的 AcceptanceCriteria[]

过滤结果非空
  -> TaskContext.actual_goal = Goal(...)
  -> TaskContext.capability_failure_reason = None

过滤结果为空
  -> TaskContext.actual_goal = None
  -> TaskContext.capability_failure_reason = 能力缺口说明
```

S1 新执行读取时 `actual_goal=None`。若本棒执行结果已被接纳并进入恢复路径，S1 从当前节点 `run_info.actual_goal` 投影该值，同时以 `accepted_execution_patch` 表明 S4 已完成，不重新运行 S3。

S3 的前置条件是 `task_gaps` 非空。一个处于待执行状态的当前节点必须对应至少一个尚未完成的任务范围；如果 S2 在执行前得到空列表，视为上下文或状态不一致，进入恢复处理，不得将其解释为 `capability_mismatch`。正常的 COMPLETE 决策只使用 S5 在合并本棒执行事实后得到的空 gaps。

## 8. S4：执行当前节点并上报

### 8.1 输入参数

```python
task_context: TaskContext
```

`assignee` 不属于 S4 的业务输入：

- 当前节点唯一的目标执行者是 `TaskNode.run_info.assignee`。单 Bot 模式为 Bot ID，Group 模式为 Group ID；执行者由规划与派发链路写入权威节点，Relay HTTP Adapter 不接受请求体覆盖。Skill 请求通过生产内网白名单进入，并以当前节点的 `relay_token` 约束执行代次；不能用 `owner_bot_id` 覆盖已冻结的执行目标。

执行者身份属于认证与上报控制面，不写入 `TaskContext`，也不要求 Skill 临时生成。现有实现中的 `relay_holder_id` 与 `RelayTurn.holder_id` 是旧接力租约技术字段；新协议不把它们作为独立业务身份传递。旧单 Bot 节点可与 `run_info.assignee` 相等；Group 节点必须按授权关系校验协调 Bot，而非要求 Bot principal 等于 Group ID。

### 8.2 节点执行结果

复用现有 `TaskNodePatch`，不新增 `TaskExecutionResult` 或 `CapabilityMatchResult`。S4 从同一个 `TaskContext` 读取 S3 结果：`actual_goal != None` 表示 ACCEPT，`actual_goal == None` 且 `capability_failure_reason != None` 表示 DECLINE。S4 的 Relay 执行字段为：`actual_goal`、`output_patch`、`local_acceptance_result`、`execution_decision`、`exec_error`、`failure_reason`。

约束：

```text
ACCEPTED:
  actual_goal 必填
  output_patch 必填，允许为空字典
  local_acceptance_result 必填
  failure_reason 为空

DECLINED:
  actual_goal 为空
  output_patch 为空
  local_acceptance_result 为空
  failure_reason == capability_failure_reason 且必填

EXECUTION_ERROR:
  execution_decision == ACCEPTED
  actual_goal == TaskContext.actual_goal
  exec_error 必填
  local_acceptance_result 为空
  failure_reason 必填
```

不得使用 `if not output_patch` 判断 ACCEPTED 结果是否存在；空字典可以是合法业务输出。

写入任务图谱的字段对应关系：

| `TaskNodePatch` 字段 | `TaskNode.run_info` 持久化位置 |
| --- | --- |
| `execution_decision` | `extend_props["execution_decision"]` |
| `actual_goal` | `actual_goal` |
| `output_patch` | `output` |
| `local_acceptance_result` | `acceptance_result` |
| `failure_reason` | `failure_reason` |

### 8.3 执行函数

```python
async def execute_current_task(
    task_context: TaskContext,
) -> TaskNodePatch:
    actual_goal = task_context.actual_goal
    if actual_goal is None:
        return TaskNodePatch(
            task_id=task_context.task_id,
            node_id=task_context.node_id,
            execution_decision=ExecutionDecision.DECLINED,
            failure_reason=require(task_context.capability_failure_reason),
        )

    try:
        output_patch, local_acceptance_result = await execute_actual_goal(
            task_context=task_context,
            actual_goal=actual_goal,
        )
    except Exception as error:
        return TaskNodePatch(
            task_id=task_context.task_id,
            node_id=task_context.node_id,
            actual_goal=actual_goal,
            execution_decision=ExecutionDecision.ACCEPTED,
            exec_error=normalize_execution_error(error),
            failure_reason="current bot execution failed",
        )

    return TaskNodePatch(
        task_id=task_context.task_id,
        node_id=task_context.node_id,
        actual_goal=actual_goal,
        output_patch=output_patch,
        local_acceptance_result=local_acceptance_result,
        execution_decision=ExecutionDecision.ACCEPTED,
    )
```

`execute_actual_goal()` 表示 Bot 使用已经获准的 Skill 与工具完成业务任务；它不是新的 Task 领域对象或 Task Module 接口。`execute_current_task()` 只返回业务执行事实，尚未写入任务图谱。

### 8.4 上报函数

```python
async def report_execution_result(
    node_patch: TaskNodePatch,
    relay_token: str,
    event_id: str,
) -> TaskReportResult:
    # 请求由生产内网白名单准入；relay_token 区分本节点执行代次。
    # event_id 为本事件新 UUID；同一 HTTP 请求重试时原样复用。
    return await task_service.report(
        TaskReportCommand(
            task_id=node_patch.task_id,
            node_id=node_patch.node_id,
            relay_token=relay_token,
            event_id=event_id,
            event_type=TaskReportEventType.EXECUTION_RESULT,
            event=node_patch,
        )
    )
```

处理顺序：

```text
TaskContext.actual_goal
    -> execute_current_task(task_context)
    -> TaskNodePatch
    -> report_execution_result(TaskNodePatch)
    -> TaskReportCommand(event_type=EXECUTION_RESULT)
    -> TaskReportResult
```

S4 不重新计算 `actual_goal`：`ACCEPTED` 时它必须原样采用 S3 的 `actual_goal`；`DECLINED` 时它只把不可执行事实提交给 Backend，交由 S5 决定后续规划。

### 8.5 输出值

```python
node_patch: TaskNodePatch
execution_report: TaskReportResult
```

`execution_report` 仅表示服务端是否接纳该次状态迁移；不向 Bot 暴露租约令牌。`task_id`、`node_id` 已在 `TaskContext` 中，Relay HTTP 由生产内网白名单准入，`relay_token` 只校验当前执行代次；节点执行者以 `run_info.assignee` 为权威事实。并发与重试由 Backend 的持久化状态及条件更新处理。

### 8.6 输入到输出转换

```text
TaskContext.task_id / node_id
  -> TaskNodePatch.task_id / node_id

TaskContext.actual_goal
  + Bot 对 actual_goal 的工具执行结果
  -> TaskNodePatch(
       actual_goal, output_patch, local_acceptance_result,
       execution_decision=ACCEPTED
     )

TaskContext.actual_goal == None
  + TaskContext.capability_failure_reason
  -> TaskNodePatch(
       execution_decision=DECLINED, failure_reason
     )

Bot 工具执行异常
  -> TaskNodePatch(
       actual_goal, execution_decision=ACCEPTED, exec_error, failure_reason
     )

TaskNodePatch
  -> TaskReportCommand(event_type=EXECUTION_RESULT, event=TaskNodePatch)
  -> TaskReportResult
```

### 8.7 API 映射

```http
POST /api/v1/collaboration/tasks/callback/report
```

事件类型：

```text
EXECUTION_RESULT
```

同一次逻辑事件重试复用相同 `event_id`、`relay_token` 和请求内容；Backend 按 `(task_id, node_id, 当前执行代次, event_type)` 接纳唯一迁移，并保存 `event_id` 对应的原结果。

新上报合同只包含 `event_id` 和 `relay_token`，不包含 `holder_id` 或 `relay_turn`。Relay HTTP 由生产内网白名单准入，不再解析 BCS 凭证；`relay_token` 是 Backend 签发并轮换的节点执行代次，不能由 Bot 自行生成，也不能用于其它接口的身份认证。

## 9. S5：本地规划并一次上报

S5 合并原 S5～S7，分为五个子步骤：更新缺口 → 规划下一棒 → 搜索候选 → 决定执行者 → 统一上报。前四步只在 Bot 本地处理或调用只读检索；仅 S5.5 写图谱。S4b 的 `EXECUTION_RESULT` 仍独立上报。“一次上报”专指规划节点与执行者决策只上报一次。任务级 gaps 和搜索候选不放进 `TaskNode`。

### 9.1 S5.1：更新缺口

输入参数：

```python
task_context: TaskContext
node_patch: TaskNodePatch
```

输出参数：

```python
task_gaps: list[str]
task_acceptance_result: AcceptanceResult | None
```

处理函数：

```python
def refresh_task_gaps(
    task_context: TaskContext,
    node_patch: TaskNodePatch,
) -> tuple[list[str], AcceptanceResult | None]:
    facts = list(task_context.all_done_output)
    if node_patch.execution_decision == ExecutionDecision.ACCEPTED and node_patch.exec_error is None:
        facts.append(DoneOutput(
            node_id=task_context.node_id,
            actual_goal=node_patch.actual_goal,
            output=node_patch.output_patch,
            acceptance_result=node_patch.local_acceptance_result,
        ))
    gaps = unique_non_empty_strings(
        infer_gaps(root_spec=task_context.root_task_spec, done_outputs=facts)
    )
    task_acceptance_result = (
        accept_task(root_spec=task_context.root_task_spec, done_outputs=facts)
        if not gaps else None
    )
    return gaps, task_acceptance_result
```

`task_acceptance_result` 验收根任务目标，区别于当前节点的 `local_acceptance_result`。S4 执行异常时，本棒在 S4b 停止，不调用 S5。

### 9.2 S5.2：规划下一棒

输入参数：

```python
task_context: TaskContext
task_gaps: list[str]
node_patch: TaskNodePatch
```

输出参数：

```python
next_node: TaskNode | None
```

处理函数：

```python
def plan_next_node(
    task_context: TaskContext,
    task_gaps: list[str],
    node_patch: TaskNodePatch,
) -> TaskNode | None:
    if not task_gaps:
        return None
    next_spec = derive_next_task_spec(
        root_spec=task_context.root_task_spec,
        current_spec=task_context.current_task_spec,
        task_gaps=task_gaps,
        current_patch=node_patch,
    )
    if next_spec is None:
        raise PlanningUnavailable("有缺口但无法规划下一棒")
    return TaskNode(
        node_id=None,
        task_id=task_context.task_id,
        status=None,
        task_spec=next_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,
    )
```

`None` 只表示 `task_gaps=[]`；有缺口但无法规划必须走错误通道，不能误判为完成。此处 `TaskNode` 尚未落图，`node_id/status/node_run_graph` 为合法临时空态。S5.2 仅允许初始化一个下一棒节点；该节点是唯一剩余节点，必须通过 `next_spec.goal.acceptances` 完整承载每一个 `task_gap`，不得把缺口留给未规划的后续节点。

### 9.3 S5.3：搜索候选

输入参数：

```python
task_context: TaskContext
next_node: TaskNode
task_service: TaskService
```

输出参数：

```python
candidates: list[BotCandidate]
```

处理函数：

```python
candidate_query = project_candidate_query(next_node.task_spec.goal)
require(candidate_query)
candidates: list[BotCandidate] = await task_service.search_bots(
    query=candidate_query,
    task_id=task_context.task_id,
    node_id=task_context.node_id,  # 轨迹归属当前棒；下一棒尚无 ID
)
```

`project_candidate_query` 是 Bot 本地检索投影，不是 TaskSpec 字段，也不进入 TaskGraph。它只读取 `next_node.task_spec.goal.objective` 与 `goal.acceptances[].description`，合并出简洁的自然语言能力请求；trim 后不超过 80 个 Unicode code points。压缩时删除修饰词、阶段说明和重复语义，不得尾部截断或模糊到丢失剩余能力范围；不得读取 `context.background/title/extend_props`，也不包含历史输出、报告路径、协议、控制 token、会话/群或执行控制话术。query 只用于能力检索，不改变 TaskSpec 语义。

S5.3 直接使用 `TaskService.search_bots`，不新增 Bot 本地搜索包装函数。`task_gaps` 已在 S5.2 转为下一棒规格且由后继 Acceptances 覆盖；candidate query 只按能力域提取，不再拼接原始缺口列表。空候选是正常结果，进入 S5.4；搜索服务不可用是可重试错误，不进入 S5.5。搜索不创建节点、不选人。

### 9.4 S5.4：决定执行者

输入参数：

```python
next_node: TaskNode
candidates: list[BotCandidate]
allowed_run_modes: list[str]
```

输出参数：

```python
next_node: TaskNode | None
```

处理函数：

```python
def select_executor(
    next_node: TaskNode,
    candidates: list[BotCandidate],
    allowed_run_modes: list[str],
) -> TaskNode | None:
    decision = select_from_candidates(
        next_spec=next_node.task_spec,
        candidates=candidates,
    )
    if decision.outcome == SearchOutcome.MISS:
        if "bbs" not in allowed_run_modes:
            return None
        next_node.run_info.run_mode = "bbs"
        next_node.run_info.assignee = None
        next_node.run_info.failure_reason = require(decision.miss_reason)
    elif decision.outcome == SearchOutcome.HIT_SINGLE:
        next_node.run_info.run_mode = "single_bot"
        next_node.run_info.assignee = require(decision.bot_id)
    elif decision.outcome == SearchOutcome.HIT_MULTI_BOTS:
        formation = require(decision.group_formation)
        require(formation.collab_mode == "manager_worker")
        require(has_exactly_one_manager(formation.members_info))
        next_node.run_info.run_mode = "coop_group"
        next_node.run_info.extend_props["pending_group_formation"] = require(
            decision.group_formation
        ).to_dict()
    else:
        raise TaskStateError("Relay 不支持该执行者决策")
    return next_node
```

服务端下发的 S5d 允许执行模态白名单来自任务创建时冻结的 `runtime_profile.allowed_run_modes`。Bot 只能从白名单内选择 `run_mode`；`MISS` 仅在 `bbs` 被允许时转 BBS。若候选不足且 `bbs` 被禁用，S5.4 不伪造 HIT，必须转为规划失败：`event=None`、`task_acceptance_result=None`、`failure_reason=bbs_disabled_and_no_candidate`。

S5.4 不新增或复用 `SearchResult` 作为协议对象；Skill 的本地决策直接填充同一个未落图 `TaskNode.run_info`，随后随 `PLAN_RESULT` 一次上报。对于 `run_mode=single_bot`，该值必须是 S5D 实际选中或根据任务目标冻结的真实 Bot ID；Backend 拒绝空白值和决策占位符（如 `NOT_DECIDED`）。S5.2 已将 `task_gaps` 编入下一棒规格，S5.4 不重复接收缺口列表。Relay 首版的候选接口只返回 Bot，故 S5.4 **不支持 `HIT_GROUP`**；若本地决策意外产生该枚举，显式拒绝并重新决策，不把未经检索的 Group ID 写入图谱。`HIT_MULTI_BOTS` 仅允许可确定唯一 manager/master Bot 的 `manager_worker` 组群，Backend 创建成功后填写最终 Group ID；不满足条件的 chat/state_machine 组群不进入首版 Relay。白名单允许时，`MISS` 必须显式为 `run_mode=bbs` 并带原因，不能仅凭 `assignee=None` 推断；白名单禁用 BBS 时按上述规划失败分支处理。用户在发起时显式选择已有 Group 属于首棒目标解析，不受 S5.4 限制。

### 9.5 S5.5：统一上报规划节点

步骤输入为 `command: TaskReportCommand`，输出为 `planning_report: TaskReportResult`；不新增 `report_planned_node` 包装函数。Bot Runtime 预先确认 S4b 的 `execution_report.status == APPLIED`，再根据以下互斥路径构造 command。`task_id/node_id` 始终取自当前棒的 `TaskContext`：

| 路径 | 前序来源 | `TaskReportCommand(PLAN_RESULT)` 关键字段 |
| --- | --- | --- |
| 根任务完成 | S5.1 返回 `task_gaps=[]` 和非空 `task_acceptance_result`；跳过 S5.2～S5.4 | `event=None`、`task_acceptance_result=上述根任务验收`、`failure_reason=None` |
| 继续接力或 BBS | S5.2 初始化 `next_node`，S5.4 在同一节点填好 `run_info` | `event=next_node`、`task_acceptance_result=None`、`failure_reason=None` |
| 规划失败 | S5.2 有缺口但无法生成节点，或 S5.4 候选不足且 BBS 被白名单禁用 | `event=None`、`task_acceptance_result=None`、`failure_reason=str(exc).strip()`（必须非空） |

处理函数即统一上报接口：

```python
class TaskService:
    async def report(self, command: TaskReportCommand) -> TaskReportResult: ...

planning_report: TaskReportResult = await task_service.report(command)
```

`PlanningUnavailable` 仅由 S5.2 产生；S5.4 在白名单禁用 BBS 且无候选时直接按规划失败分支上报 `bbs_disabled_and_no_candidate`；`planning_failure_reason` 是 Runtime 捕获该异常后填入 command 的错误分支值，不是 `TaskContext`、`TaskNode` 或 S5.1 的字段。S5.3 搜索不可用应重试搜索，不转换为规划失败上报。`execution_report` 是进入 S5 的前置校验结果，`task_service` 是 Runtime 调用依赖，二者都不是 S5.5 的业务入参。

调用一次 `POST /api/v1/collaboration/tasks/callback/report`。Backend 原子应用：

- `event=None`、`task_acceptance_result` 非空、无失败原因：验收并完成整体任务，不创建节点。
- 有缺口但规划失败，`event=None`、有失败原因：进入 HUNG/待干预状态，不误判为完成。
- `event=TaskNode` 且 `run_mode=bbs`：创建唯一目标节点、记录 MISS 原因并持久化 BBS 发布意图；消费者以目标节点 ID 派生稳定发布键，重试复用该键或对账原发布结果，异步发布成功后才记录实际发布事实，不进入 S6。
- `event=TaskNode` 且 `run_mode=single_bot/coop_group`：创建唯一目标节点、写入执行决策和前后依赖，返回 `target_node_id`；登记待投递意图供 Backend S6 消费。

命中执行者时，上述落图事务还须登记以 `(task_id, target_node_id)` 唯一标识的待投递意图。Bot 在 S5.5 后崩溃也不能丢失交接；服务端可扫描该意图恢复 S6 投递。

S5.5 不接收 `task_gaps`：根任务验收、已填执行者的 `next_node`、S5.2 规划失败原因分别构成三种上报分支。Backend 根据上报内容及既有图谱事实校验，不接受三者全空或同时非空。Backend 生成 `node_id`、`PENDING` 状态、图谱引用及服务端时间戳。图谱迁移与 BBS 发布意图应可原子恢复；重试同一业务决策返回原 `target_node_id`。当前实现先创建节点、再上报执行者的两段协议需要重构，不能只改 Bot 步骤。

Backend 在同一图谱迁移中读取任务创建时冻结的 Relay `max_rounds`，校验当前轮次与唯一后继预算；达到上限时进入明确的 HUNG/待干预结果，不创建后继、不登记投递意图。Bot 不传 `max_rounds`，也不能靠本地规划绕过该限制。

HTTP Adapter 对未落图 `TaskNode` 只序列化 `task_id`、`task_spec` 及允许 Bot 填写的 `run_info` 字段；不传输 `node_run_graph`，也不接受客户端指定的 `node_id/status`。Backend 将请求内容复制进新建节点，不能把收到的临时对象直接挂入图谱。

## 12. S6：Backend 实际交接

S6 有供当前棒 Skill 调用的 HTTP 入口；HTTP Adapter 将请求映射为 Backend 内部的 `TaskDispatcher.dispatch(next_node)`。只有 S5.5 的 `PLAN_RESULT(HIT_*)` 后继节点在创建节点的同一事务登记以 `(task_id,target_node_id)` 唯一标识的待投递意图。后继节点优先由 Skill 调用触发，后台消费者负责未投递意图的恢复。根节点不进入 S6；任务完成、规划失败或 `MISS/BBS` 也不调用 S6。

### 12.1 输入参数与直接调用接口

```python
# 后继棒 Skill 从 planning_report.target_node_id 取得目标标识；
# HTTP 传 task_id、origin_node_id、target_node_id 与当前棒 relay_token，
# 不传 Bot 本地临时 TaskNode。
next_node: TaskNode = await task_graph_service.get_node(task_id, target_node_id)

class TaskDispatcher:
    async def dispatch(self, node: TaskNode) -> TaskNode: ...

# Runner 是接力派发策略的内部调用依赖，不是 S6 的第二个直接入口。
class TaskRunner:
    async def start_run(self, node: TaskNode) -> bool: ...
```

### 12.2 输入到输出转换

```text
Skill: planning_report(APPLIED, target_node_id)
  -> POST /dispatch(task_id, origin_node_id, target_node_id, relay_token)
或 Backend 后继棒恢复消费者: 待投递意图(task_id, target_node_id)
  -> HTTP Adapter 校验当前 relay_token 执行代次；Backend 读取权威 next_node: TaskNode（已落图）
  -> TaskDispatcher.dispatch(next_node) -> dispatched_node: TaskNode
     -> Dispatcher 校验节点状态与前后继关系，原子认领唯一投递意图
     -> Relay 派发策略校验预选执行者
     -> 内部 TaskRunner.start_run(next_node)，发送含 task_id + node_id 的指令
  -> dispatch 成功时记录投递接纳；失败时保留可恢复状态
  -> 下一棒 S1 调节点级 context API -> TaskContext
```

`TaskDispatcher.dispatch(next_node)` 成功返回内部 `dispatched_node: TaskNode` 仅表示投递通道已接纳指令，不表示下一棒执行完成；Runner 返回 `False` 或抛错时，接力派发策略必须抛出投递失败，不能返回成功节点。HTTP Adapter 只返回摘要响应：

```json
{"ok": true, "node_id": "<target_node_id>"}
```

它不返回下一棒 `TaskContext`，也不序列化完整 `TaskNode`。Relay `relay_handoff` 派发策略校验图谱中已持久化的执行者和模态，不重新搜索、改派或落图；内部 Runner 按执行模态生成接力任务指令并真实投递。该流程仅适用于后继棒。

`HIT_MULTI_BOTS` 建群是外部副作用：Backend 为目标节点派生稳定建群键，每次重试向 BCS 复用同一键，并在建群响应丢失时先查询/对账原群，再把 Group ID 持久化到目标节点后投递。不得每次重试生成随机建群键；BCS 若不支持稳定去重且无法对账，Relay 首版不能开放动态建群。执行指令也须携带目标节点的稳定投递标识，由接收端在启动本棒前按该标识去重；这不等于承诺 Bot 工具外部副作用恰好一次。

`dispatch(node) -> TaskNode` 仅在通道已接纳指令或意图已是 `DELIVERED` 时返回；意图仍在 `DELIVERING` 时抛可重试的 `DispatchInProgress`，HTTP Adapter 映射为 `409 Conflict` 与 `Retry-After`，不能返回成功节点。

### 12.3 恢复与模式隔离

Bot 在 S5.5 获得 `TaskReportResult(APPLIED, target_node_id)` 且命中执行者后调用 S6；若调用前崩溃，Backend 消费者扫描 `PENDING` 意图补发。HTTP 与后台消费者都调用 `TaskDispatcher.dispatch(node)`，由 Dispatcher 内部基于 `(task_id,target_node_id)` 原子认领和识别重试，不能各自重复投递；已投递返回同一权威节点，投递中的并发调用返回可重试的处理中状态。投递状态与节点状态由 Backend 维护，Bot 不生成或携带 `dispatch_id`。统一入口按任务创建时冻结的 `orchestration_mode` 和 `runtime_profile` 解析策略；Relay 不调用中心化 `TaskPlanner`。Relay profile 省略 `planner_strategy`，不得以 `null` 或 `default` 代替；缺失所需 Dispatcher/Runner 策略时显式失败，不能回退中心化策略。

### 12.4 API 映射

Skill 通过生产内网白名单保护的 `POST /api/v1/collaboration/tasks/dispatch` 触发 S6，请求携带 `task_id`、`origin_node_id`、`target_node_id` 与当前棒 `relay_token`。HTTP Adapter 校验当前执行代次并读取已落图 `next_node`，再调用 `TaskDispatcher.dispatch(next_node)`；不能信任请求体提供的完整节点或执行者。节点状态、前后继关系及待投递意图认领由 Dispatcher 所在的核心链路校验，不放进 HTTP Adapter 的业务判断。HTTP 成功响应保持 `{ok: true, node_id: target_node_id}` 最小摘要，不序列化完整 `TaskNode` 或图谱反向引用；投递中返回 `409 Conflict` 与 `Retry-After`。`TaskRunner.start_run(next_node)` 是接力派发策略内部实现，不作为第二个 HTTP 接口。Backend 后继棒恢复消费者复用同一 Dispatcher，不额外定义 `TaskService.dispatch_task` 包装方法。

## 13. 步骤总表

| 步骤 | 功能描述 | 入参 | 执行方式 | 出参 | API |
| --- | --- | --- | --- | --- | --- |
| S1 | S1：获取上下文 | `task_id: str`、`node_id: str`。首棒取自 Backend 创建响应的当前会话指令；后续棒取自 Runner 派发消息 | Bot Runtime 调节点级 context API；不由 Skill 推理补全上下文 | `TaskContext` | `GET /tasks/{task_id}/nodes/{node_id}/context` |
| S2 | S2：计算缺口 | `task_context: TaskContext` | Bot Skill 本地推理根任务验收项与 `all_done_output` 的覆盖情况 | `task_gaps: list[str]` | 无 |
| S3 | S3：能力匹配 | `task_context: TaskContext`、`task_gaps: list[str]`、`bot_capability: BotCapability` | Bot Skill 本地能力推理；读取当前 Bot 已激活的 Skill/工具事实，并将结果补全到 `TaskContext.actual_goal/capability_failure_reason` | `task_context: TaskContext` | 无 |
| S4a：执行 | S4a：执行任务 | `task_context: TaskContext` | `actual_goal` 非空时 Bot 仅执行该目标，形成业务产出和节点级验收事实；`capability_failure_reason` 非空时不执行业务任务。本步不写任务图谱 | `node_patch: TaskNodePatch`，其中关键字段：`output_patch: dict[str, Any]`、`local_acceptance_result: AcceptanceResult` | 无 |
| S4b：上报 | S4b：上报结果 | `node_patch: TaskNodePatch` | Runtime 将 `TaskNodePatch` 原样放入 `TaskReportCommand(EXECUTION_RESULT)`；服务端接纳后写入当前节点运行事实 | `execution_report: TaskReportResult`，成功状态为 `APPLIED` | `POST /callback/report`，事件为 `EXECUTION_RESULT` |
| S5.1 | 更新缺口 | `task_context: TaskContext`、`node_patch: TaskNodePatch` | 根任务规格 + 历史产出 + 本棒事实重算缺口，缺口为空时生成根任务验收 | `task_gaps: list[str]`、`task_acceptance_result: AcceptanceResult \| None` | 无 |
| S5.2 | 规划下一棒 | `task_context: TaskContext`、`task_gaps: list[str]`、`node_patch: TaskNodePatch` | 有缺口时生成 `TaskSpec`，初始化未落图 `TaskNode`；无缺口返回 `None` | `next_node: TaskNode \| None` | 无 |
| S5.3 | 搜索候选 | `task_context: TaskContext`、`next_node: TaskNode`；调用依赖 `task_service: TaskService` | 以 `project_candidate_query(next_node.task_spec.goal)` 生成本地候选查询并调用 `search_bots`；`task_context.node_id` 只用于当前棒轨迹归属 | `candidates: list[BotCandidate]` | `POST /search` |
| S5.4 | 决定执行者 | `next_node: TaskNode`、`candidates: list[BotCandidate]` | Skill 本地决策，向同一个未落图节点填入执行模式、执行者；MISS 显式标记 BBS | 已填执行者的 `next_node: TaskNode` | 无 |
| S5.5 | 上报规划节点 | `command: TaskReportCommand`；由 S5.1 根任务验收、S5.4 `next_node` 或 S5.2 规划异常三选一构造；前置条件为执行上报 `APPLIED` | Runtime 调 `TaskService.report(command)`，只上报一次 `PLAN_RESULT`；Backend 原子收口或创建唯一后继并写入选人决策 | `planning_report: TaskReportResult` | `POST /callback/report`，`PLAN_RESULT` |
| S6 | 实际交接 | 后继棒：`task_id`、`origin_node_id`、`planning_report.target_node_id`、`relay_token`；Backend 据此读取权威 `next_node: TaskNode` | Skill 调 HTTP；Adapter 校验 relay_token 执行代次并读取节点，调用 `TaskDispatcher.dispatch(next_node)`。接力策略内部调用 Runner 投递；后台消费者恢复未投递后继意图 | HTTP 返回 `{ok: true, node_id: target_node_id}`；成功表示通道接纳，下一棒进入 S1 | `POST /api/v1/collaboration/tasks/dispatch` → `TaskDispatcher.dispatch(next_node)` |

S4a 的两个关键业务字段定义：

```python
# Bot 对 actual_goal 的完整业务交付；result 是稳定的主内容承载字段。
output_patch: dict[str, Any] = {
    "result": "完整业务产出"
}

# Bot 对 actual_goal.acceptances 的逐项验收结论。
local_acceptance_result: AcceptanceResult = AcceptanceResult(
    verdict=AcceptanceVerdict.DONE,  # 或 AcceptanceVerdict.FAILED
    done_items=[
        {"id": "acceptance-id", "passed": True, "summary": "已满足的证据"}
    ],
    gap_items=[
        {"id": "acceptance-id", "passed": False, "summary": "未满足的原因"}
    ],
)
```

`done_items` 与 `gap_items` 的 `id` 并集必须逐项且仅一次覆盖 `actual_goal.acceptances`；`DONE` 时 `gap_items=[]`，`FAILED` 时 `gap_items` 非空。

## 14. 幂等与恢复约束

### 14.1 EXECUTION_RESULT

- Backend 以内部 `(task_id, node_id, attempt, EXECUTION_RESULT)` 识别当前尝试唯一的执行结果；同一尝试重复请求不重复写节点事实，同键不同内容返回冲突。
- 接纳 `exec_error` 时记录该 attempt 失败并停止 S5，不能把错误 patch 当成已完成业务事实。只有新的执行代次重投才轮换 `relay_token`；`active + 无进展` 先等待引擎自身 timeout/终态，不直接进入 harness 续跑。进入恢复态后的 same-session 续跑不推进 attempt、不轮换 token；预算耗尽则进入 HUNG/待干预，不无限重试。
- S1 只投影当前 attempt 已接纳的执行 patch；旧失败 attempt 保留为审计轨迹，旧绑定的迟到 context/report/dispatch 不得推进当前节点。成功尝试接纳后才允许该节点唯一的 `PLAN_RESULT`。
- `relay_token` 由 Backend 首派前签发，仅新的执行代次重投时轮换；same-session harness 续跑不轮换。Bot 只原样携带；`event_id` 由 Bot 调用层生成并在同一事件重试时复用。两者不是身份凭证。Relay 上报核心定位字段不携带 attempt 或图谱版本；`TaskContext.attempt` 是 S1 暴露的当前节点权威尝试号，仅用于对齐 `artifacts[].attempt`。

### 14.2 PLAN_RESULT

- Backend 以 `(task_id, origin_node_id, PLAN_RESULT)` 识别一次包含规划与选人决策的逻辑事件。
- HTTP 重试必须使用相同业务内容；返回原 `target_node_id`，不得创建第二个目标节点或重复发布 BBS。同键不同内容返回冲突。
- `target_node_id` 只能来自本次上报响应，不能由 Bot 推导。
- 下一棒可以由同一 Bot 执行，但必须是新的 `TaskNode` 和独立运行绑定；轮次上限与缺口/规格校验阻止无进展循环，不以 Bot 身份相同作为禁止接棒的理由。

### 14.3 实际派发

- Backend 在 S5.5 落图事务中为命中执行者的唯一目标节点持久化投递意图与状态（如 `PENDING/DELIVERING/DELIVERED`），以 `(task_id, target_node_id)` 约束仅有一条逻辑交接；调用方不传 `dispatch_id`。
- Skill 的 HTTP 请求与 Backend 消费者进入 Dispatcher 后只能认领同一条投递记录；已投递时返回权威节点而不启动第二条逻辑交接，投递中的记录须受租约或状态条件更新约束。
- `start` 和 S5.5 提交后，即使 CLI 或 Bot 立即崩溃，服务端也须扫描 `PENDING` 意图并补发；未得到可靠投递确认不能标记 `DELIVERED`。投递通道若仅支持至少一次投递，Runner/消费端仍需以目标节点 ID 去重。建群、BBS 发布也各自复用从目标节点 ID 派生的稳定外部操作键或对账原结果；不能承诺任意 Bot 工具外部副作用恰好一次。投递接纳不等于任务完成。

### 14.4 harness 恢复

Runtime liveness 采用引擎状态作为事实源：`active + 有进展` 不做恢复；`active + 无进展` 按观察轮次递增介入。同一节点最多 2 次送达成功的 same-session resume：

- 第 1 轮观察期为 4 个巡检周期，连续无进展后发送 resume；
- 第 2 轮观察期为 6 个巡检周期，视为上一轮 resume 后仍未推进；
- 第 3 轮观察期为 8 个巡检周期，若仍无恢复信号则显式置 `HUNG`。

`terminal_success_unreported` 的三个观察窗分别是 360 秒、600 秒、900 秒；`probe_unavailable` 不消耗 resume 预算，也不把部署或接口故障误判为 Bot 失活。

每次成功送达的 resume 都会开启新的“恢复观察窗”：

- **要重置**：本轮无进展计数、未知状态计数、Terminal Success 计数、巡检心跳 cursor 基线、观察时间起点；
- **不重置**：`relay_harness_resume_attempts`、`relay_token`、`task_id/node_id`、`run_mode`、`assignee`、会话/群身份。

首个 post-resume probe 使用新的 cursor 基线，确保 resume 消息本身不会被误判为新的业务进展。

Same-session resume 消息按节点权威恢复阶段对齐：

- `accepted_plan_report` 非空：resume 到 `S6`；
- 仅 `accepted_execution_patch` 非空：resume 到 `S5`；
- 两者均空：resume 到 `S1-S4a`；
- 图读取失败时回退到原有 generic resume prompt。

节点保持 `RUNNING`；不重新 prepare、不重新 dispatch、不轮换 `relay_token`、不改变 `run_mode`、`assignee`、群/会话或任务标识。

### 14.5 本棒已接纳事实与恢复读取

- 恢复读取与正常派发使用同一份 Task Module 上下文构造逻辑。
- 无论本棒 `EXECUTION_RESULT` 是否已持久化，恢复后的 `all_done_output` 都不得包含当前节点事实；它始终只含本棒之前的已接纳前序事实。
- 本棒已接纳的 `TaskNodePatch` 和 `TaskReportResult` 分别从图谱投影到 `accepted_execution_patch`、`accepted_plan_report`，使 Runtime 能跳过已完成阶段并取回原 `target_node_id`。图谱的上报接纳标记必须能区分未上报与已上报但 `output_patch={}` 的情况。
- 本棒工具已产生副作用、但 `EXECUTION_RESULT` 尚未被 Backend 接纳时，图谱不能恢复未上报的业务结果；该窗口由 Bot 工具自身幂等或本地检查点保证，不能把 S1 投影误称为外部副作用恰好一次保障。
- Bot 不感知 Graph 版本；Graph 乐观锁属于 Backend 内部实现。

## 15. 状态收口约束

任务完成不能再依赖：

```python
graph.extend_props["gaps"] == []
```

Backend 应根据成功提交的显式规划决策收口：

```text
PLAN_RESULT(event=None, task_acceptance_result 非空, failure_reason=None)
  -> 当前节点 SUCCESS
  -> Graph DONE
```

继续接力：

```text
PLAN_RESULT(event=<已确定执行者的未落图 TaskNode>)
  -> 当前节点 DONE
  -> 创建一个 PENDING 下一棒节点并写入执行决策
  -> Graph RUNNING
```

上述节点状态、Graph 状态和下一节点创建必须在同一 Backend 原子操作中完成。

## 16. 实现边界

- 上述输入输出对象属于 Task Relay 执行协议；不应使用 HTTP DTO 作为 Core 领域模型。
- HTTP adapter 负责领域对象与 JSON 请求响应之间的转换。
- Skill 是 S2、S3、S5.1、S5.2、S5.4 推理函数的实现者，但不拥有 TaskGraph 写权限。
- Backend 负责 relay_token 执行代次校验、状态校验、幂等、持久化、节点创建和真实派发；Relay HTTP 依赖生产内网白名单准入。
- `BotCapability` 是能力匹配输入，不是全局 Bot 能力注册表。
- 本设计不新增持久化的任务级 gaps，也不新增第二套任务图。

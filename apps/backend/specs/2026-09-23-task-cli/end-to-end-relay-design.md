# avernet-goal-cli 到分布式接力执行：端到端设计稿

> Relay 单个 Target Execution 的实现合同，2026-10-12。CLI 命令、Batch Schema、一个 Goal 多目标、Intake 和 `/start` 聚合响应以 [avernet-goal-cli 发起与澄清设计](design.md) 为唯一权威；本文只描述其中一个独立 Target Execution 进入 [Bot S1–S6 函数式执行协议](../2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md) 后的执行链路。本文残留的单目标命令和单结果示例不构成 CLI 合同。

> 实现核对（2026-10-13）：Backend 实现位于 `apps/backend/`。Relay、intake/start、节点级 context、统一 report 和 S6 dispatch 已完成重构；Rust `avernet-goal-cli` 已迁移至 CLI 组织下的独立同名仓库并发布，Avernet 仓库只保留 Backend 实现与跨仓协议设计。

## 1. 一页结论

用户只需输入一句话任务目标，必要时在同一个 intake 会话中补充信息，并点击确认。确认只冻结任务规格；独立的 `TaskService.execute(TaskInfoRequest)` 才创建任务、根节点并返回 `TaskOpResult(task_id, node_id)`。Bot 每一棒均按 `task_id + node_id` 从 Task Module 拉取同一形态的 `TaskContext`；执行事实写入任务图谱，任务级 `task_gaps: list[str]` 由各棒本地重算，不在图谱持久化。

```text
task-cli 输入
  → 目标解析与任务澄清 → 用户确认 TaskSpec
  → TaskService.execute → 根节点 task_id == node_id + 权威 root_execution_instruction
  → 正常任务 Frontend / benchmark 评测任务 Backend 在当前 Bot 会话转发指令 → 首棒 S1 获取 TaskContext
  → S2 缺口 → S3 能力 → S4 执行/上报
  → S5.1 更新缺口 → S5.2 规划下一棒 → S5.3 搜索候选
  → S5.4 决定执行者 → S5.5 一次上报并登记意图
  → Skill S6 调用 /dispatch → Backend 投递下一棒执行指令
  → 下一棒 S1，直到收口或进入 BBS/异常处理
```

对外讲解的四个业务问题仍是：**缺什么 → 下一棒做什么 → 谁来做 → 真正交接**。前三个问题在 S5.1～S5.4 本地完成，S5.5 一次上报规划节点与选人结果，S6 才真正交接。S2 是本棒执行前的缺口快照；S5.1 在加入本棒事实后重算。

### 1.1 来源文档对齐与未决项

| 主题 | 本文采用的口径 | 来源文档/实现差异 |
| --- | --- | --- |
| `TaskContext` 传递 | S1 每棒调用节点级 context API；派发指令只定位节点，不携带权威 `TaskContext` | 旧版 task-cli 描述过在派发消息中携带完整上下文；本次同步改为统一拉取 |
| 首棒执行目标 | 已确认的 `IntakeTarget` 冻结为 `execution_config.initial_target`；根节点据其类型设置 `run_mode/assignee`，Group ID 不因解析 manager Bot 丢失 | `owner_bot_id` 是任务归属 Bot，不等同于 Group 执行者 |
| Group 执行主体 | Group ID 保留为节点 `assignee`；唯一 manager/master Bot 是 Relay 协调执行者，负责 S1–S5 与上报；S3 读取其在群运行时真实可用的能力 | 群成员协作是 S4 内部执行，不把 Group ID 误当 Bot 身份 |
| 同节点 harness 恢复 | 运行时无进展不开启新 attempt、不轮换 `relay_token`；只在既有会话发送自然语言续跑消息，节点保持 `RUNNING`；新执行代次重投才另行签发绑定 | Skill 不手填 attempt；报告响应丢失且已接纳成功时不递增 |
| 本棒恢复 | `TaskContext.all_done_output` 只含本棒之前的事实；本棒已接纳执行 patch 与规划结果由图谱分别投影，Runtime 据此续跑 | 不持久化 `TaskContext`，也不重新执行已接纳的本棒业务操作 |
| 首棒触发 | `start/execute` 创建根节点并返回 Backend 生成的 `root_execution_instruction`；正常任务由 Frontend 原样发送到用户确认任务的当前 Bot 会话，`benchmark_execute` 评测任务由 Backend 发送到同一评测会话 | 根节点不调用 Dispatcher/Runner，避免创建新会话或权限兜底协作群；后继节点才使用可靠投递意图 |
| 确认与创建 | `TaskService.intake(CONFIRM)` 只返回 `TaskIntakeResult(CONFIRMED)`；CLI 随后以 `session_id` 请求 `/start`，由 Backend 构造内部 `TaskInfoRequest` | `RESUME` 本身只读；确认后中断可在恢复界面继续创建，不重放 CONFIRM |
| S5 顺序 | Bot 先更新缺口、规划未落图节点、搜索并决定执行者；随后只上报一次 `PLAN_RESULT`，Backend 原子落图并写入选人决策 | 不存在 `DISPATCH_RESULT` 第二次决策上报 |
| 目标对象 | `TaskInfoRequest.task_spec: TaskSpec`；`TaskOpResult.node_id`；节点级 `TaskContext`；统一 `TaskReportResult` | 已作为 Relay 对外合同 |

**闭环约束：** `start` 将 `TaskInfo`、图谱根节点和 `session_id` 唯一创建关联放在同一持久化事务，并从权威根节点生成执行指令返回；Frontend 只负责把该指令送入当前会话。后继节点与其投递意图同事务登记，由当前棒 Skill 调用 S6 HTTP 入口触发，后台消费者在 Skill 崩溃或请求失败时恢复。

## 2. 用户动线与目标解析

### 2.1 用户看到的命令

```bash
task-cli "复盘昨晚支付失败率峰值，给出根因、影响范围和处置建议"
task-cli "复盘昨晚支付失败率峰值" @payment-bot bot
task-cli "复盘昨晚支付失败率峰值" @payment-group group
task-cli resume <session-id>
task-cli get <task-id>
```

`bot` 与 `group` 显式目标互斥；引用由 CLI Adapter 在调用者可见范围内解析为稳定 ID。交互会话内补充、确认、取消均通过卡片/输入完成，不引入 `reply`、`select-target`、`select-task`、`confirm`、`cancel` 子命令，也不要求用户反复输入 session ID。`resume` 必须显式指定 `session_id`。

Human 未指定目标时按“同名可运行 Bot → 默认 Bot → 目标能力匹配 → 可访问 Bot 随机选择 → 无可用 Bot 则提示创建/启用”解析；首次选定后写入 `TaskIntakeResult.target`，同一 intake 会话不再重选。Bot 调用方默认指向自身。显式 Group 需校验可见性、运行状态与唯一 manager/master；正式任务的 `owner_bot_id` 映射为该 manager/master，但首棒执行目标仍是已确认的 Group ID。

### 2.2 一次用户操作如何进入领域层

| 来源 | 用户/系统填写 | 领域对象 |
| --- | --- | --- |
| 用户操作 | 一句话目标或澄清内容、`@bot/@group`、确认/取消卡片动作 | `TaskIntakeCommand(action, message, requested_target)`；由 CLI/HTTP Adapter 构造，不由用户填写 Python 对象 |
| CLI/HTTP 控制面 | 当前 `session_id`；`START` 时为空 | `TaskIntakeContext`；由 Task Intake 创建并返回会话标识 |
| 认证与调用链 | `owner_user_id`、`source_type`、可选 `caller_bot_id` | 同一个 `TaskIntakeContext`；不由 LLM 推断，不从请求体接受身份 |
| Task Intake | 已解析 target、逐轮 `TaskSpec`、问题、状态 | 持久化并返回 `TaskIntakeResult` |

`session_id` 是多轮会话标识，不是任务 ID；用户只在跨进程 `resume` 时需要显式提供。Intake 不要求每次动作的 `request_id`。`START/CONTINUE` 响应丢失后不能仅凭相同文本严格识别重试，CLI 不盲目自动重放；`CONFIRM/CANCEL` 按会话终态处理，正式任务 `start` 按 `session_id` 唯一创建。

## 3. 领域模型：只保留各自独立的语义

### 3.1 澄清与正式任务

```python
@dataclass(frozen=True)
class TaskIntakeCommand:
    action: TaskIntakeAction  # START | RESUME | CONTINUE | CONFIRM | CANCEL
    message: str | None = None
    requested_target: IntakeTarget | None = None

@dataclass(frozen=True)
class TaskIntakeContext:
    session_id: str | None
    owner_user_id: str
    source_type: TaskSourceType
    caller_bot_id: str | None = None

@dataclass
class TaskIntakeResult:
    session_id: str
    owner_user_id: str
    source_type: TaskSourceType
    status: TaskIntakeStatus  # CLARIFYING | CONFIRMED | CANCELLED
    target: IntakeTarget | None = None
    task_spec: TaskSpec | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    create_time_ms: int = 0
    update_time_ms: int = 0
```

`TaskSpec` 是确认前后共用的**业务规格**，包含 `context` 与 `goal`，不含任务 ID、owner 或执行配置。完整性要求：`goal.objective`、`context.extend_props.deliverables`、`goal.acceptances` 非空；约束已明确（允许空列表）。验收项首次生成时分配稳定 ID。`TaskIntakeResult` 是唯一 intake 持久化状态；`CLARIFYING + TaskSpec 完整` 即可展示确认卡，不另增 READY 状态。`CONFIRMED` 仅表示用户认可规格，绝不表示已创建正式任务；其中没有 `task_id/node_id`。

```python
def build_task_info_request(result: TaskIntakeResult) -> TaskInfoRequest:
    require(result.status == TaskIntakeStatus.CONFIRMED)
    require(result.task_spec is not None and result.target is not None)
    owner_bot_id = resolve_owner_bot_id(result.target, result.owner_user_id)
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

此转换只读取已确认 Result 与服务端策略；`owner_user_id/source_type` 不要求用户重复输入，`TaskSpec` 不让 LLM 在确认后重写。`source_type` 记录发起来源，不决定首棒的 Bot/Group 模态。`TaskService.execute(TaskInfoRequest) -> TaskOpResult` 从冻结的 `initial_target` 创建根节点：Bot 目标对应 `single_bot/target_id`，Group 目标对应 `coop_group/target_id`；`owner_bot_id` 只记录归属 Bot。成功时 `task_id == node_id`。`TaskOpResult` 是任务标识的唯一直接返回对象。

### 3.2 接力执行

| 对象/值 | 核心字段 | 产生者 → 消费者 | 是否持久化 |
| --- | --- | --- | --- |
| `TaskContext` | `task_id`、`node_id`、当前 `attempt`、根/当前规格、仅前序 `all_done_output`、S3 运行期 `actual_goal/capability_failure_reason`、可选本棒 `accepted_execution_patch/accepted_plan_report` | Task Module 节点级投影 → 当前棒 Bot S1–S5.5 与恢复 | 对象不另存；从图谱事实与上报接纳记录投影 |
| `BotCapability` | `identity`、`activated_skills`、`available_tools` | Bot Runtime 的真实能力快照 → S3 | 不写 TaskGraph |
| `task_gaps: list[str]` | 根任务尚未满足的验收缺口 | S2/S5.1 Skill 推理 → S3/S5.2 | 仅本棒本地；不写 `TaskContext` 或图谱 |
| `TaskNodePatch` | `actual_goal`、`output_patch`、`local_acceptance_result`、`execution_decision`、异常原因 | S4a → S4b；Backend 接纳后写当前节点 `run_info` | 接纳后持久化节点事实 |
| 未落图 `TaskNode` | `task_id`、`task_spec`、逐步填充的 `run_info.run_mode/assignee`；`node_id/status/node_run_graph` 暂空 | S5.2 → S5.4 → S5.5 | Backend 接纳后补齐服务端字段并落图；不新增 Relay `PlanResult` |
| `BotCandidate` | `bot_id`、`name`、`description`、可选 `score` | S5.3 → S5.4 | 候选只在 Bot 本地；不写图谱 |
| `TaskReportCommand` | `task_id/node_id/event_type/event`；收口时带 `task_acceptance_result`，S5.2 规划失败时带 `failure_reason`；可选 `artifacts[].attempt = TaskContext.attempt`；可选 `trace.steps`，每个 step 为扁平七字段 | Bot Runtime → 统一上报 Service API | 三种 `PLAN_RESULT` 分支互斥；服务端按节点状态幂等接纳，`trace` 每条 step 独立进入 `task_trajectory_events` |
| `TaskReportResult` | `status`、可选 `target_node_id`、终态标志 | Backend → S4b/S5.5 | 返回已接纳迁移结果；不是多个新结果类 |

目标 `TaskContext` 不含 `gaps`。`all_done_output` 严格只包含**本棒之前**沿 Relay 前驱链已接纳的 `DoneOutput`，本棒即使已上报也绝不纳入；S5.1 才将本棒 `TaskNodePatch` 转为事实追加一次。`accepted_execution_patch` 与 `accepted_plan_report` 只表示本棒已经被接纳的阶段，来自图谱投影，不是 `TaskContext` 的独立持久化。节点的 `AcceptanceResult.gap_items` 仍持久化，和“不保存任务级 gaps”不矛盾。

`TaskReportCommand.event` 按 `event_type` 判别：`EXECUTION_RESULT → TaskNodePatch`，`PLAN_RESULT → 未落图 TaskNode | None`。`None` 加 S5.1 的根任务验收表示完成；`None` 加 S5.2 抛出 `PlanningUnavailable` 后捕获的非空原因表示 HUNG；有下一棒时由其 `run_mode/assignee` 表达 HIT 或 BBS/MISS。三者全空或并存均为无效命令；S5.3 搜索失败只重试，不转为规划失败。`trace` 是可选观测载荷：`EXECUTION_RESULT` 记录 S1～S4b 检查点，`PLAN_RESULT` 记录 S5.1～S5.5 检查点。`trace.steps[i]` 是扁平七字段 `action_type/action_input/action_result/status_from/status_to/boost_reason/error_msg`；`action_type` 使用步骤码本身，`action_input` 是步骤输入，可为字符串或 null，结构化输入先序列化为 JSON 文本；`action_result` 是该步骤推理输出/产出摘要，必须为可解析的 JSON object 字符串，包含非空 `status` 字段与至少一个实际输出字段；状态值只能作为 JSON 内的 `status` 字段；`boost_reason` 是执行过程证据：为什么推进、为什么选择等等维度信息；`error_msg` 是失败原因；`status_from/status_to` 是本步骤前后节点状态。同一事件重试原样复用，不得改变。Backend 在接纳迁移的同一图谱事务中保存执行事件 `(task_id,node_id,attempt,event_type)`、规划事件 `(task_id,node_id,event_type)` 的接纳标记、规范化内容指纹和原 `TaskReportResult`，并在接纳后把 trace 的每个 step 独立写入 `task_trajectory_events`；相同事件 ID 与内容重试返回原结果，不同内容返回冲突。Harness 的 Relay same-session 续跑不轮换 `relay_token`，只在既有会话发送自然语言续跑消息，且仅在引擎可恢复终态或连续无响应时触发；只有新的执行代次重投才轮换 token，旧代次上报被当前代次栅栏拒绝。Bot 只原样携带派发指令中的 token。响应丢失后 Runtime 先经 S1 读取当前代次已接纳阶段：执行已接纳则用 `accepted_execution_patch` 继续 S5，规划已接纳则用 `accepted_plan_report` 恢复 S6，不重新运行 LLM 以碰运气生成同一文本。上报是同步图谱迁移，成功返回 `APPLIED`；网络/领域失败时原样重试同一逻辑事件，不因失败进入下一步。S6 由 `/dispatch` 的服务端动作日志记录，不新增第三个 report 事件；S6 服务端记录同样沿用七字段语义，`action_result` 记录实际派发输出 JSON（包含 `status` 与派发结果字段），不是 delivered/failed 裸状态词，失败原因保存在 `error_msg`。

## 4. 接口清单：调用方、输入与返回

以下路径和对象为当前合同。现有 HTTP Envelope 若附带请求追踪 ID，它只用于日志关联，不作为 Intake 的幂等键；不另造同构领域 DTO。

| 阶段 | 调用方 → 提供方 | Service API / HTTP | 输入 | 输出与语义 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 发起/澄清/确认 | CLI Adapter → Task Module | `TaskService.intake(command, context)`；`POST /api/v1/collaboration/tasks/intake` | `TaskIntakeCommand` 请求体；`session_id` 与认证事实形成 `TaskIntakeContext` | `TaskIntakeResult`；`CONFIRM` 也只返回它 | 目标设计；需实现 |
| 创建正式任务 | CLI → Backend 创建链路 → Task Module | `POST /api/v1/collaboration/tasks/start` → 内部既有 `TaskService.execute(TaskInfoRequest)` | HTTP 只传 `session_id`；Backend 读取已确认 `TaskIntakeResult` 并构造 `TaskInfoRequest` | `TaskOpResult(task_id,node_id,success,run_id,root_execution_instruction)`；根节点 `node_id == task_id` | 已实现；不新增 Service `start` |
| 获取本棒上下文 | Bot Runtime → Task Module | `GET /api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context` | 路径中的 `task_id,node_id` 与 Header 中的当前 `relay_token`；生产内网白名单准入 | `TaskContext`；根规格、当前节点规格、当前 `attempt`、仅前序已接纳执行事实、运行期 `actual_goal/capability_failure_reason` 恢复投影 | 已实现 |
| 候选检索 | Bot Runtime → Task Module 搜索能力 | `TaskService.search_bots`；`POST /api/v1/collaboration/tasks/search` | `query=project_candidate_query(next_node.task_spec.goal)`；`task_id/node_id/relay_token` 为当前棒标识与执行代次 | HTTP `Envelope[TaskSearchResultDTO]`；内部领域仍为 `list[BotCandidate]`，Skill 在下一子步骤形成执行者决策 | 已实现 |
| 统一事件上报 | Bot Runtime → Task Module | `POST /api/v1/collaboration/tasks/callback/report` | `TaskReportCommand`，先执行结果，再上报一次包含规划与选人决策的 `PLAN_RESULT` | `TaskReportResult`；返回接纳状态与可能的目标节点 ID | 已实现 |
| 实际交接 | 当前棒 Skill → HTTP Adapter → `TaskDispatcher`；Backend 消费者只恢复后继节点 | `POST /api/v1/collaboration/tasks/dispatch` → `TaskDispatcher.dispatch(next_node)` | HTTP 传 `task_id,origin_node_id,target_node_id,relay_token`；Backend 读取已落图的权威 `next_node: TaskNode` | `{ok: true, node_id}`；成功表示内部 Runner 已将指令交给投递通道，不代表任务完成 | 已实现 |
| 查询任务 | task-cli → Task Module | `task-cli get <task-id>` 对应既有 dashboard Service API | `task_id` | 图与节点状态、产出 | 既有查询能力，非 Intake |

`/search` 只检索候选，不写图谱；S5.4 在 Bot 本地作执行者决策，S5.5 才一次上报。上报负责原子登记待投递意图，不表示已投递；Skill 在接纳后调用 `/dispatch`，Backend S6 实际投递的指令至少包含 `task_id/node_id`，任务内容以目标 Bot 在 S1 拉取的 `TaskContext` 为准。

### 4.1 上报事件的请求与结果约束

```text
EXECUTION_RESULT(TaskNodePatch)
  → 更新当前 TaskNode.run_info 的业务输出与节点验收事实
  → TaskReportResult(APPLIED)

PLAN_RESULT(event=None, task_acceptance_result 非空, failure_reason=None)
  → Graph DONE
  → TaskReportResult(APPLIED, task_completed=True)

PLAN_RESULT(event=未落图 TaskNode, run_mode=single_bot/coop_group)
  → 原子创建唯一 PENDING 目标节点并写执行者决策
  → TaskReportResult(APPLIED, target_node_id)

PLAN_RESULT(event=未落图 TaskNode, run_mode=bbs)
  → 原子创建目标节点并持久化 BBS 发布意图
  → TaskReportResult(APPLIED, target_node_id, bbs_publish_pending=True)

PLAN_RESULT(event=None, failure_reason 非空)
  → HUNG/待干预；不得误判为完成
  → TaskReportResult(APPLIED, task_hung=True)
```

上报成功只返回 `APPLIED`；网络或领域错误原样重试同一逻辑事件，不执行下一步。`bbs_publish_pending=True` 是原始规划报告中不可变的“发布意图已落盘”结果，不宣称 BBS 已实际发布；真实发布状态由后续图谱/看板事实表示，也始终不进入 S6。

### 4.2 HTTP 请求形态与领域转换

```text
POST /api/v1/collaboration/tasks/intake
  body: TaskIntakeCommand {action, message?, requested_target?}
  control/auth → TaskIntakeContext {session_id?, owner_user_id,
                                    source_type, caller_bot_id?}
  data: TaskIntakeResult

POST /api/v1/collaboration/tasks/start       # 目标；当前等价入口为 /execute
  body: {session_id: str}
  Backend: 读取已确认 TaskIntakeResult → build_task_info_request → TaskService.execute
  data: TaskOpResult {task_id, node_id, success, run_id, ...}

GET /api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context
  data: TaskContext {task_id, node_id, attempt, root_task_spec,
                     current_task_spec, all_done_output,
                     accepted_execution_patch?, accepted_plan_report?}

POST /api/v1/collaboration/tasks/search
  body: {query: str, task_id: str, node_id: str, relay_token: str}
        # query 是由 objective + acceptances[].description 投影出的本地候选查询；标识与执行代次必须属于当前棒
  data: list[BotCandidate] → Bot Skill 本地决策 → 填充未落图 TaskNode

POST /api/v1/collaboration/tasks/callback/report
  body: TaskReportCommand {task_id, node_id, event_id, relay_token,
                          event_type, event, trace?}
  data: TaskReportResult {status, target_node_id?,
                          task_completed, task_hung, bbs_publish_pending}

POST /api/v1/collaboration/tasks/dispatch
  body: {task_id: str, origin_node_id: str,
         target_node_id: str, relay_token: str}
  data: {ok: true, node_id: str}  # 投递通道已接纳；不表示执行完成
```

`TaskReportCommand.event` 是领域判别联合；Relay HTTP 由生产内网白名单准入，节点执行代次由服务端签发的 `relay_token` 校验，租约、并发与重试状态仅由服务端内部处理。`/dispatch` 不要求 Skill 传 `dispatch_id` 或完整 `TaskNode`；Adapter 按标识读取权威节点并调用 Dispatcher。

## 5. 从用户操作到根节点：逐步输入 → 处理 → 输出

| 步骤 | 输入领域对象 | 处理函数与职责 | 输出领域对象/值 |
| --- | --- | --- | --- |
| P0 发起 | `TaskIntakeCommand(START,message,requested_target?)` + 系统 `TaskIntakeContext` | `TaskService.intake`：解析权限和默认目标，识别初始 `TaskSpec`，创建 intake 会话 | `TaskIntakeResult(CLARIFYING,target,task_spec,questions)` |
| P0 继续/恢复 | `TaskIntakeCommand(CONTINUE/RESUME)` + 同一 session 的 `TaskIntakeContext` | `handle`：恢复只读；补充时保留已确认字段并重验完整性 | 更新或原样返回 `TaskIntakeResult`；`CLARIFYING` 且完整时展示确认卡，`CONFIRMED` 时展示「继续提交/查看任务」 |
| P1 确认/取消 | `TaskIntakeCommand(CONFIRM/CANCEL)` + 系统 `TaskIntakeContext` | `handle`：确认只冻结完整 `TaskSpec`，取消不建任务 | `TaskIntakeResult(CONFIRMED/CANCELLED)`；无任务 ID |
| P2 创建命令 | HTTP `{session_id}`；Backend 读取 `TaskIntakeResult(CONFIRMED)` | 校验会话 owner 与状态后调用 `build_task_info_request`：解析 owner Bot，复制已确认 `TaskSpec`，附服务端执行策略与 intake 幂等关联 | 内部 `TaskInfoRequest` |
| P3 注册根节点 | `TaskInfoRequest` | `TaskService.execute`：原子创建 `TaskInfo`、图和根节点，并根据权威节点与部署配置生成首棒指令 | `TaskOpResult(task_id,node_id,root_execution_instruction)`，成功时 `task_id == node_id` |
| P4 当前会话唤起首棒 | `TaskOpResult.root_execution_instruction` | 正常任务由 Frontend、`benchmark_execute` 评测任务由 Backend；转发方不解释协议，把指令隐藏附加到任务面板消息并发送给完成确认的当前 Bot 会话 | 当前 Bot 取得 `task_id,node_id,relay_token,backend_url`，进入 S1 |

确认动作和创建动作分开：正常路径中 CLI 收到 `CONFIRMED` 后立即以 `session_id` 调 `/start`；若两次请求之间崩溃，`task-cli resume <session-id>` 只读地恢复已确认会话，再展示「继续提交/查看任务」操作。CLI 无须从 `TaskIntakeResult` 判断任务是否已创建；该操作调用同一个幂等 `/start`，由 Backend 在已创建时返回原 `TaskOpResult`，否则创建一次。`TaskIntakeResult` 始终不反写任务 ID。用户看见任务 ID 后可用 `task-cli get` 查询进度。

## 6. 每一棒 S1–S6：输入 → 处理 → 输出

旧版 S6/S7 的规划、搜索和选人职责已并入 S5.1～S5.5；S6 专指 Backend 实际交接。五个子步骤的完整函数签名、输入输出参数及错误分支见 [Bot 函数式执行协议第 9 节](../2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md#9-s5本地规划并一次上报)。

| 步骤 | 输入领域对象/值 | 处理方式及对象转换 | 输出领域对象/值 | 外部接口 |
| --- | --- | --- | --- | --- |
| S1 获取上下文 | 首棒与后续棒执行指令中的 `task_id/node_id`；恢复记录中的同名标识 | Runtime 调节点级 context API；Backend 投影根/当前规格、严格前序事实与本棒已接纳阶段，不从 Prompt 猜测 | `task_context: TaskContext`；Runtime 据本棒接纳字段选择续跑位置 | `GET /tasks/{task_id}/nodes/{node_id}/context` |
| S2 计算缺口 | `task_context: TaskContext` | Skill 比对 `root_task_spec.goal.acceptances` 与 `all_done_output` 的证据 | `task_gaps: list[str]` | 无 |
| S3 能力匹配 | `task_context: TaskContext`、`task_gaps: list[str]`、`bot_capability: BotCapability` | Skill 将当前节点验收范围与缺口、真实已激活技能/工具取交集；不能扩张节点目标，并将结果补全到同一个 `TaskContext` | `task_context.actual_goal: Goal` 或 `capability_failure_reason: str` | 无 |
| S4a 执行节点 | `task_context: TaskContext` | `actual_goal` 非空时仅执行该目标，形成 `output_patch` 与 `local_acceptance_result`；`capability_failure_reason` 非空时不执行业务工具 | `node_patch: TaskNodePatch` | Bot 获准工具；无 Task 上报 |
| S4b 上报执行 | `node_patch: TaskNodePatch` | 将 patch 放入 `TaskReportCommand(EXECUTION_RESULT)`；Backend 接纳后写当前节点 `run_info` | `execution_report: TaskReportResult` | `POST /callback/report` |
| S5.1 更新缺口 | `task_context: TaskContext`、`node_patch: TaskNodePatch` | 根规格 + 历史事实 + 本棒事实重算；缺口为空时做根任务验收 | `task_gaps: list[str]`、`task_acceptance_result: AcceptanceResult \| None` | 无 |
| S5.2 规划下一棒 | `task_context: TaskContext`、`task_gaps: list[str]`、`node_patch: TaskNodePatch` | 有缺口时初始化未落图 `TaskNode`，填 `task_spec`；无缺口返回 `None` | `next_node: TaskNode \| None` | 无 |
| S5.3 搜索候选 | `task_context: TaskContext`、`next_node: TaskNode`；调用依赖 `task_service: TaskService` | 以 `project_candidate_query(next_node.task_spec.goal)` 本地投影出的候选查询搜索；`task_context.node_id` 只标记当前棒搜索轨迹，不选人、不创建节点 | `candidates: list[BotCandidate]` | `POST /search` |
| S5.4 决定执行者 | `next_node: TaskNode`、`candidates: list[BotCandidate]` | Skill 本地选择单 Bot/协作群或 MISS，并填充同一未落图节点的 `run_info` | 已填执行者的 `next_node: TaskNode` | 无 |
| S5.5 统一上报 | `command: TaskReportCommand`；由 S5.1 根任务验收、S5.4 `next_node` 或 S5.2 规划异常三选一构造；前置条件为执行上报 `APPLIED` | 直接调用 `TaskService.report(command)`，一次 `PLAN_RESULT`；Backend 原子收口或创建唯一后继并写执行者决策 | `planning_report: TaskReportResult` | `POST /callback/report` |
| S6 真正交接 | 当前棒 `task_id/origin_node_id/relay_token` 与后继棒 `planning_report.target_node_id`，Backend 读取权威 `next_node: TaskNode` | Skill 调 HTTP；Adapter 校验 relay_token 执行代次并调用 `TaskDispatcher.dispatch(next_node)`；接力策略内部调用 Runner。后台消费者恢复未投递后继意图 | `{ok: true, node_id}`；成功表示通道接纳，下一棒进入 S1 | `POST /api/v1/collaboration/tasks/dispatch` → `TaskDispatcher.dispatch(next_node)` |

S6 的返回值**不是**下一棒 `TaskContext`。指令通知下一棒“现在执行该节点”，下一棒的 S1 再拉取权威上下文。Bot 不直接调用另一个 Bot，也不靠复制上一棒的 `TaskContext` 完成交接。

### 6.1 关键转换伪代码

```python
# 本棒执行前：两个规格及已接纳阶段都来自同一个 S1 TaskContext。
task_context = await load_task_context(task_id, node_id)
if task_context.accepted_plan_report is not None:
    planning_report = task_context.accepted_plan_report
    require(planning_report.status == TaskReportStatus.APPLIED)
    # 完成/HUNG/BBS 不派发；有已确定执行者的后继时恢复 S6。
    if planning_report.target_node_id is not None and not planning_report.bbs_publish_pending:
        await dispatch_http(
            task_id=task_id,
            origin_node_id=node_id,
            target_node_id=planning_report.target_node_id,
            relay_token=token,
        )
    return

if task_context.accepted_execution_patch is not None:
    # S4b 已接纳：不得重新执行业务操作；以图谱投影的原 patch 进入 S5。
    node_patch = task_context.accepted_execution_patch
else:
    initial_gaps = calculate_task_gaps(task_context)
    task_context = match_bot_capability(
        task_context, initial_gaps, bot_capability,
    )
    node_patch = await execute_current_task(task_context)
    execution_report = await report_execution_result(node_patch)
    require(execution_report.status == TaskReportStatus.APPLIED)
if node_patch.exec_error is not None:
    stop_for_harness_recovery()
    return

# S5.1 重算；仅无缺口时产出根任务验收。S5.2～S5.4 只操作本地未落图节点。
task_gaps, task_acceptance_result = refresh_task_gaps(task_context, node_patch)
if not task_gaps:
    require(task_acceptance_result is not None)
    command = TaskReportCommand(
        task_id=task_context.task_id,
        node_id=task_context.node_id,
        event_id=new_event_id(),
        relay_token=token,
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=task_acceptance_result,
    )
else:
    require(task_acceptance_result is None)
    try:
        next_node = plan_next_node(task_context, task_gaps, node_patch)
        require(next_node is not None)
    except PlanningUnavailable as exc:  # 只捕获 S5.2 规划失败
        reason = str(exc).strip()
        require(reason)
        command = TaskReportCommand(
            task_id=task_context.task_id,
            node_id=task_context.node_id,
            event_id=new_event_id(),
            relay_token=token,
            event_type=TaskReportEventType.PLAN_RESULT,
            event=None,
            failure_reason=reason,
        )
    else:
        candidates = await task_service.search_bots(
            query=project_candidate_query(next_node.task_spec.goal),
            task_id=task_context.task_id,
            node_id=task_context.node_id,  # 当前棒；下一棒尚无 node_id
        )
        next_node = select_executor(next_node, candidates)
        command = TaskReportCommand(
            task_id=task_context.task_id,
            node_id=task_context.node_id,
            event_id=new_event_id(),
            relay_token=token,
            event_type=TaskReportEventType.PLAN_RESULT,
            event=next_node,
        )
# S5.3 搜索失败由 Runtime 重试，不转成 S5.2 的规划失败原因。

# S5.5 一次上报规划节点和选人决策；同一事务登记待投递意图。
planning_report: TaskReportResult = await task_service.report(command)
require(planning_report.status == TaskReportStatus.APPLIED)
# 命中执行者时，Bot 进入 S6：HTTP 只提交标识，Backend 读取权威节点。
if planning_report.target_node_id is not None and not planning_report.bbs_publish_pending:
    dispatch_summary = await dispatch_http(
        task_id=task_context.task_id,
        origin_node_id=task_context.node_id,
        target_node_id=planning_report.target_node_id,
        relay_token=token,
    )
# Bot 在 S6 后结束本棒；若此处崩溃，Backend 扫描待投递意图恢复。
```

## 7. 两棒具体推演：支付失败率峰值复盘

1. `user-alice` 执行 `task-cli "复盘昨晚支付失败率峰值，给出根因、影响范围和处置建议"`。默认目标解析为 `bot-payment-diagnosis`；澄清后确认的根 `TaskSpec` 有三个验收项：`ac1` 影响范围、`ac2` 根因证据、`ac3` 处置建议。
2. `CONFIRM` 返回 `TaskIntakeResult(CONFIRMED)`，**不含任务 ID**。创建链路生成 `TaskInfoRequest` 并调用 `TaskService.execute`，返回 `TaskOpResult(task_id="task-pay-001", node_id="task-pay-001")`。
3. `start` 创建根节点并返回 Backend 生成的 `root_execution_instruction`；Frontend 将它发送给当前 `bot-payment-diagnosis` 会话，不创建新 Runner 会话。该 Bot 从 S1 拉取根节点 `TaskContext`。S2 得到 `ac1/ac2/ac3` 缺口；S3 匹配其日志与指标能力，只承接 `ac1/ac2`。S4 上报证据与节点验收结果；S5.1 重算后只剩 `ac3`。
4. S5.2 初始化未落图 `TaskNode`，规格为“基于已验证证据编制处置建议”；S5.3 搜索候选，S5.4 选择 `bot-incident-response` 并填入执行者决策。S5.5 一次上报后 Backend 创建 `node-pay-002` 并登记意图；首棒 Skill 在 S6 调用 `/dispatch`，Backend 将 `task-pay-001/node-pay-002` 的执行指令投递给下一棒。
5. 第二棒重新从 S1 拉取 `TaskContext`，其中根规格仍有 `ac1/ac2/ac3`，当前节点规格仅覆盖 `ac3`，`all_done_output` 含首棒已接纳的 `ac1/ac2` 事实。第二棒执行并上报 `ac3`，S5.1 得到空 gaps，跳过 S5.2～S5.4，S5.5 上报收口；不执行 S6。用户通过 `task-cli get task-pay-001` 看到任务完成。

这个案例中，第一棒的 `task_gaps=[ac3]` 与第二棒的 `task_gaps=[]` 只存在于各自本轮 Bot 执行上下文；图谱保留的是验收事实与两棒节点规格。

## 8. 一致性、幂等与安全条件

- **单一上下文来源**：首棒、后续棒、harness 恢复都调用同一节点级 context API。Bot 不消费派发消息中的完整业务快照；消息只负责执行唤起和节点定位。
- **先写事实后做决定**：`EXECUTION_RESULT` 接纳后才更新 gaps；Bot 完成本地规划、搜索和选人后才一次上报 `PLAN_RESULT`；上报同事务登记意图，Skill 随后调 S6 HTTP 派发；Backend 扫描意图负责失败恢复。
- **地址、准入与重试**：Backend 自身地址由当前部署的 `configs/*.yaml` 加载为 `TaskDispatchConfig.backend_url`，Task Runtime 不读取环境变量、不识别部署类型；Runner 将该非敏感地址写入受控执行指令，Skill 调用生产内网白名单保护的 Backend `/api/v1` HTTP 时显式使用，不调用 Gateway OpenAPI，也不读取或发送 BCS session token。Backend 首派前生成 `relay_token`，同一 `node_id` 重试时在图谱事务中轮换；Bot 只原样携带本次代次值，旧执行迟到请求被拒绝。Bot 调用层生成 `event_id`，同一上报事件的 HTTP 重试复用该值。Bot 不传 `dispatch_id`、attempt、图谱版本或自报身份；持久化投递意图和图谱迁移负责服务端并发与重试。
- **身份和权限**：Intake owner、目标 Bot/Group 可见性在用户/API 发起链路中校验。Relay 执行面依赖生产网络白名单准入；节点 `assignee` 由规划和派发写入权威图谱，Skill 不得通过请求体覆盖。
- **完成条件**：任务完成以已接纳的显式 `PLAN_RESULT` 决策为准，不以持久化的 `graph.extend_props["gaps"] == []` 为准。`PLAN_RESULT` 的节点完成、目标节点创建与 Graph 状态迁移须原子完成。
- **恢复**：same-session harness 采用 probe-first：节点不重置、不重新 prepare/dispatch、不轮换 token；`active + 无进展` 只计数并等待引擎自身 timeout/终态，不发送恢复消息；只有引擎 `terminal_failed` / `timeout` / `aborted`、`terminal_success` 但图谱未收到报告，或连续 `unknown/unresponsive` 时，才向原始 Bot/BCS session 发送“继续完成 task-loop S1~S6”的自然语言消息。节点使用 `relay_harness_resume_attempts` 计数，默认 2 次后显式 `HUNG`。正常恢复者仍使用原 `task_id/node_id` 重新读取图谱投影；`all_done_output` 仍只含前序事实，另以 `accepted_execution_patch/accepted_plan_report` 判断本棒已接纳阶段，避免重执行业务或重做规划。上报前已经发生、但尚未持久化的外部工具副作用仍须由 Bot 工具自身的幂等/检查点机制处理；图谱无法凭空恢复未上报的工具结果。Bot 不需要消费 Graph 内部乐观锁版本。

## 9. 落地顺序与汇报风险

| 优先级 | 必须打通的变更 | 否则出现的问题 |
| --- | --- | --- |
| P0 | `start/execute` 返回 Backend 生成的 `root_execution_instruction`；正常任务由 Frontend 在当前 Bot 会话原样转发，`benchmark_execute` 评测任务由 Backend 在同一评测会话原样转发；根节点不得进入 Dispatcher/Runner | 根节点会创建错误的新会话或缺少 `backend_url/relay_token` |
| P0 | 节点级 `TaskContext` 查询和统一投影；修改 Runner 指令只定位节点 | 首棒、后续棒与恢复继续依赖不同上下文来源 |
| P0 | `TaskOpResult.node_id`、`TaskInfoRequest.task_spec: TaskSpec`、intake session 创建幂等关联 | CLI 不可靠地取得根节点，确认后崩溃可能重复创建任务 |
| P0 | 冻结 `execution_config.initial_target` 并据此初始化根节点 `run_mode/assignee`；Group ID 不因解析 `owner_bot_id` 丢失 | 指定 Group 却错误投递给 manager Bot |
| P0 | S1 严格投影前序 `all_done_output` 与本棒 `accepted_execution_patch/accepted_plan_report`；上报迁移同事务保存接纳标记、内容指纹及原结果 | 响应丢失后无法判断续跑位置，可能重复执行业务或重新规划产生冲突 |
| P1 | `TaskReportCommand/TaskReportResult` 统一领域合同与 HTTP 转换；保留现有统一上报 URL | 各事件返回裸字典，Bot 步骤无法稳定依赖同一结果类型 |
| P1 | 调整 Relay Prompt 与 Backend 状态迁移为 `更新缺口 → 规划未落图节点 → 搜索 → 决策 → PLAN_RESULT → dispatch`；移除任务级 gaps 图谱依赖 | 仍会先创建节点再选人，且完成条件依赖旧持久字段 |
| P1 | 对 `TaskNodePatch.local_acceptance_result`、空 `output_patch`、幂等重试与 BBS/HUNG 分支补合同测试 | 节点事实可能丢失或异常分支被误判为成功 |

本稿不修改现有 Runtime 或 HTTP 实现。会前可用第 1、2、7 节说明用户体验与接力价值，用第 3–6 节回答领域模型和接口问题，用第 9 节说明落地差距与决策点。

## 10. 分布式接力接口与职责边界

本节定义**任务澄清、正式创建和分布式接力共用的 `TaskService` 对外接口**，以及内部 `TaskGraphService`、`TaskDispatcher`、`TaskRunner` 接口；不另定义同构的 `TaskServiceProtocol`。Relay 的规划由 Bot Skill 完成，不调用中心化 `TaskPlanner`；S6 仅直接调用 Dispatcher 的统一派发入口，Runner 是派发策略内部的投递依赖。以下签名复用前文领域对象，`...` 表示实现体，不引入同构的输入/输出包装类。

| 类 | Relay 中的职责 | 本流程是否调用 |
| --- | --- | --- |
| `TaskService` | 对外门面：澄清会话、任务创建、节点上下文、事件接纳、候选检索；验证身份、状态并在服务端处理并发与重试 | 是：P0–P3、S1、S4b、S5.3、S5.5；S6 经 HTTP Adapter 调用 Dispatcher，不另加 TaskService 包装方法 |
| `TaskGraphService` | 唯一图谱事实所有者：创建根节点、投影上下文、写当前节点事实、原子应用规划与状态迁移 | 是：由 `TaskService` 内部调用 |
| `TaskPlanner` | 中心化模式的框架规划器；根据图谱选规划策略 | **否**。Relay 的未落图 `TaskNode` 由当前 Bot 的 S5.2 Skill 初始化 |
| `TaskDispatcher` | 统一单节点派发入口；策略完成模式对应的执行者处理并在内部调用 Runner，投递被接纳后才返回节点 | 是：S6 由 Skill 经 HTTP Adapter 调用；首棒和恢复由 Backend 消费者调用同一入口 |
| `TaskRunner` | 单节点投递原语；Relay 策略生成接力 Prompt 并按已确定的执行者与模态投递，中心化策略保留原执行链 | 是：由对应派发策略内部调用；不是 S6 的第二个直接入口 |

### 10.1 `TaskService`：对外 Service API

```python
class TaskService:
    async def intake(
        self, command: TaskIntakeCommand, context: TaskIntakeContext,
    ) -> TaskIntakeResult: ...

    async def execute(self, request: TaskInfoRequest) -> TaskOpResult: ...

    async def get_node_context(
        self, task_id: str, node_id: str,
    ) -> TaskContext: ...

    async def report(
        self, command: TaskReportCommand,
    ) -> TaskReportResult: ...

    async def search_bots(
        self, *, query: str, task_id: str, node_id: str,
    ) -> list[BotCandidate]: ...

    async def get_task_dashboard(
        self, task_id: str, node_id: str | None = None,
    ) -> TaskExecutionGraph: ...
```

对外领域方法不定义 `user_id/caller_id` 参数，也不允许请求体覆盖权威节点执行者。Relay HTTP Adapter 依赖生产内网白名单准入，并校验服务端签发的当前 `relay_token`、节点关系与状态；用户侧 Intake/OpenAPI 鉴权保持不变。

旧 `TaskGraphService.report(TaskCallbackData) -> TaskOpResult` 不进入目标合同：`TaskCallbackData` 的 `report_type/graph_version/payload` 是旧回调形态，不能代替按 `event_type` 判别的 `TaskReportCommand`。图谱版本属于 Backend 并发控制，不由 Bot 传入；`TaskOpResult` 只表示任务创建，事件接纳一律返回 `TaskReportResult`。`TaskGraphService` 负责具体图谱事实操作，不再对外提供第二套 `report` 入口。

- `intake`：处理 `START/RESUME/CONTINUE/CONFIRM/CANCEL`，校验调用方与会话，持久化 `TaskIntakeResult`；`CONFIRM` 只冻结已完整的 `TaskSpec` 并返回 `TaskIntakeResult(CONFIRMED)`，不创建任务。`TaskIntakeContext` 由认证与会话控制信息构造，不由用户填写。

  完整分支与字段转换伪代码见 [task-cli 设计第 8 节](design.md#8-taskserviceintake-处理伪代码)：`START` 解析 target 并从用户消息生成 `TaskSpec`；`CONTINUE` 合并新回复、保留既有验收项 ID 并重算缺项；`RESUME` 只读取；`CONFIRM/CANCEL` 只变更 intake 状态。Backend 通过会话版本条件更新防止并发覆盖；不承诺 `START/CONTINUE` 的动作级严格去重。
- HTTP `/start`：只收 `session_id`；Backend 应用层校验会话 owner 与 `CONFIRMED` 状态，读取已保存 Result 并构造内部 `TaskInfoRequest`，再调用唯一创建方法 `TaskService.execute(request)`。TaskInfo、根图、唯一 session 关联和根投递意图必须同提交；返回成功的 `TaskOpResult(task_id=node_id)`，重试同一 session 返回原结果。它不返回 `TaskContext`，也不等候 Runner 投递。现有 `execute` 将 TaskInfo 与图谱分步写入，实施前需增强其可靠提交能力，或提供经过故障注入验证的可恢复提交协议；HTTP Adapter 只映射协议，不承担会话业务校验或事务编排。
- `get_node_context`：在生产内网白名单准入并通过当前节点 `relay_token` 代次校验后，委托 `TaskGraphService` 从已持久化图谱投影严格前序 `all_done_output` 与本棒已接纳的执行 patch/规划结果；首棒、后续棒、harness 恢复均调用此同一接口。现有 `get_task_context(task_id)` 只能做兼容入口，不得猜测多节点任务的当前节点。
- `report`：统一接纳 `EXECUTION_RESULT` 与合并后的 `PLAN_RESULT`。后者一次接纳未落图节点及执行者决策，原子完成当前棒、创建下一棒并写入执行者，或收口/HUNG/BBS。图谱同事务保存当前代次、`event_id`、规范化内容指纹与原结果；同一事件重试返回原迁移结果，不重复建节点或发布 BBS。方法只收 `TaskReportCommand`，其中 `relay_token` 由 Backend 签发、`event_id` 由 Bot 调用层生成；不显式传认证调用方。
- `search_bots`：只读检索，以 S5.2 生成的下一棒 `TaskSpec.goal` 本地投影出的 `candidate_query` 为 `query`；投影只读取 `goal.objective` 与 `goal.acceptances[].description`，trim 后不超过 80 个 Unicode code points。返回 `list[BotCandidate]` 供 S5.4 Skill 决策；不另设 Bot 本地搜索包装函数，也不重复拼接 `task_gaps`。`task_id/node_id` 必填，只将轨迹归属当前棒，不要求下一棒先落图；HTTP Adapter 还校验当前棒 `relay_token`。当前实现名为 `search_task_candidates` 且返回字典；HTTP Adapter 需把候选归一化，已有 HTTP 调用方依赖的响应字段需兼容迁移。
- S6 不增加 `TaskService.dispatch_task` 包装方法：Skill 调生产内网白名单保护的 `/dispatch` HTTP 路径，Adapter 校验起点节点的当前 `relay_token`，以 `task_id,target_node_id` 读取权威 `next_node` 并调用 `TaskDispatcher.dispatch(next_node)`；Dispatcher 核心链路校验前后节点关系、节点状态和投递意图。HTTP 请求还携带当前棒 `origin_node_id`，成功响应保持 `{ok,node_id}` 最小摘要。接力派发策略内部调用 `TaskRunner.start_run(next_node)`。根节点不走该派发分支；失败恢复消费者复用同一 Dispatcher；Skill 不传 `dispatch_id`。
- `get_task_dashboard`：服务 `task-cli get`，只读图与节点状态；不作为 Bot S1 的业务上下文来源。

### 10.2 `TaskGraphService`：图谱事实与投影

```python
class TaskGraphService:
    async def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph: ...

    async def get_node_context(self, task_id: str, node_id: str) -> TaskContext: ...

    async def get_node(self, task_id: str, node_id: str) -> TaskNode: ...

    async def update_task_node_info(self, patch: TaskNodePatch) -> NodeOpResult: ...

    async def apply_relay_plan_result(
        self,
        *,
        task_id: str,
        origin_node_id: str,
        next_node: TaskNode | None,
        task_acceptance_result: AcceptanceResult | None,
        failure_reason: str | None,
    ) -> TaskNode | None: ...

    async def query_task_dashboard(
        self, task_id: str, node_id: str | None = None,
    ) -> TaskExecutionGraph: ...
```

- `initialize_graph`：创建唯一根节点；按已冻结 `execution_config.initial_target` 设置根节点 `run_mode/assignee` 和 `relay_token`。Relay 根节点不写 delivery intent，创建响应根据权威节点生成当前会话首棒指令。Task Module 对根节点状态/执行者的写入仍由图谱写口完成。
- `get_node_context`：`root_task_spec = 根 TaskNode.task_spec`，`current_task_spec = 指定 TaskNode.task_spec`，`all_done_output = 沿 Relay 前驱链严格早于指定节点的已接纳 DoneOutput[]`；新执行代次投影 `actual_goal/capability_failure_reason` 为空，恢复已接纳执行时从当前节点运行事实投影二者；本棒已接纳的执行 patch 和规划结果分别投影为 `accepted_execution_patch/accepted_plan_report`。不读取或返回任务级 `gaps`，不把前一棒的内存 `TaskContext` 复制给下一棒，也不持久化 `TaskContext` 对象。
- `get_node`：供 Backend S6 根据投递意图读取已落图的权威节点；不接受 Bot 传来的完整 `TaskNode` 作为投递事实。
- `update_task_node_info`：复用现有 patch 写口，S4b 将 `TaskNodePatch` 的 `output_patch`、`local_acceptance_result` 等写入当前节点 `run_info`；Bot 不直接改图。
- `apply_relay_plan_result`：`next_node=None` 且有任务验收时收口；`next_node=None` 且有非空规划失败原因时 HUNG；非空时校验仅 Bot 可写字段，Backend 生成 ID、状态、图谱引用，在同一图谱迁移中结束当前棒、创建**唯一**下一棒并写入执行者决策。`run_mode=bbs` 时持久化可恢复的发布意图；命中执行者时原子登记 `(task_id,target_node_id)` 唯一的 `PENDING` 投递意图。图谱类不签发令牌、不返回 HTTP DTO。

当前 `TaskGraphService` 已有 `initialize_graph`、`update_task_node_info`、`apply_relay_plan_result`、`get_task_context(task_id)` 和 `query_task_dashboard`，但节点级投影和 `apply_relay_plan_result` 的领域签名均需迁移。图谱类不负责候选搜索、Bot 选择或网络投递。

### 10.3 `TaskPlanner` 与 `TaskDispatcher`：规划和派发边界

`TaskPlanner` 仅供中心化模式调用。Relay S5.2 由 Bot Skill 初始化未落图 `TaskNode`，S5.4 在 Bot 本地选人，S5.5 才一次落图；统一 `TaskDispatcher` 在 Relay S6 不重复这些决策。Relay 首版 S5.4 只接受 `HIT_SINGLE`、`HIT_MULTI_BOTS`、`MISS`，且只能从服务端下发指令携带的 `runtime_profile.allowed_run_modes` 白名单中选择；`search_bots` 只返回 Bot 候选，故不支持 `HIT_GROUP`，意外得到该决策时显式拒绝，不伪造已检索的 Group 候选。BBS 被白名单禁用且无候选时，S5.4 按规划失败上报 `bbs_disabled_and_no_candidate`，不得转 BBS。`HIT_MULTI_BOTS` 仅允许可确定唯一协调 Bot 的 `manager_worker` 组群。用户发起任务时显式指定已有 Group 是根节点目标解析，仍受支持。S5.5 的图谱迁移从冻结运行配置读取 Relay `max_rounds` 并原子校验，超限则 HUNG，不创建后继；Bot 不传这个控制值。

```python
class TaskPlanner:
    async def plan(self, context: TaskContext) -> PlanResult: ...  # 仅中心化

class TaskDispatcher:
    async def dispatch(self, node: TaskNode) -> TaskNode: ...  # 成功表示指令被投递通道接纳
```

`dispatch(node)` 根据节点所属任务创建时冻结的 `orchestration_mode + runtime_profile.dispatcher_strategy` 从启动时注册的策略表选择实现，不在入口中写中心化/Relay 的 `if/else`。该入口只由后继棒 Skill HTTP 请求和后继棒恢复消费者共用；Dispatcher 核心链路校验权威节点状态与前后继关系，并以 `(task_id,node_id)` 原子认领唯一投递意图。已投递重试返回权威节点，投递中返回可重试的处理中状态，不重复调用 Runner。统一成功语义是：对应策略完成该模式所需的执行者处理，内部调用 `TaskRunner.start_run(node)`；只有指令被投递通道接纳才返回 `TaskNode`，Runner 返回 `False` 或抛错则派发失败。中心化 `default` 策略保留现有搜索、选人和更新节点逻辑，但要将原先外置的 Runner 调用移入策略以满足相同返回语义；Relay `relay_handoff` 策略只校验已持久化的 `assignee`、`run_mode`、节点状态，不搜索、不改派、不重复落图，然后调用 Runner。批量操作若仍需要，只能在外层逐个调用该原子接口。`run_info.run_mode` 是单 Bot/协作群/BBS 的投递模态，不能代替任务级 `orchestration_mode`。

接力策略的关键处理逻辑如下；`next_node` 已由 Backend 从图谱读取，不能传 S5.2 的临时节点：

```python
class RelayHandoffDispatchStrategy:
    async def dispatch(self, next_node: TaskNode) -> TaskNode:
        require(next_node.node_id is not None)
        require(next_node.run_info.run_mode in {"single_bot", "coop_group"})
        # 校验已选人决策；协作群组建尚待完成时，在策略内部完成并持久化。
        # 不在此重新搜索或改派。
        next_node = await ensure_selected_executor_ready(next_node)
        accepted = await self.task_runner.start_run(next_node)
        require(accepted, "delivery_not_accepted")  # 失败不返回成功节点
        return next_node
```

`ensure_selected_executor_ready` 表示策略内部的协作群组建和执行者就绪校验，不是新增的对外接口或领域对象；已确定的单 Bot/协作群不再选人。Skill 的 HTTP 请求和 Backend 消费者共用 Dispatcher 内部的同一待投递意图认领逻辑；`dispatch` 成功返回后记录投递接纳，调用失败保留可重试状态。投递通道若只保证至少一次，消费端仍须按目标节点去重。HTTP 复用既有 `/dispatch` 路径；`TaskDispatcher.dispatch` 内部仍返回权威 `TaskNode`，但 HTTP 成功响应只从该对象投影 `{ok: true, node_id: target_node_id}`，不序列化完整 `TaskNode`、图谱反向引用或其他领域字段，也不增加返回领域模型。

动态建群只允许 `manager_worker` 且群成员角色中可确定唯一 manager/master Bot。Backend 以目标节点 ID 派生稳定 BCS 建群幂等键；调用超时或进程崩溃后，重试必须复用同一键或查询原结果，取得同一个 Group ID，再保存到目标节点并投递。现有 BCS Adapter 每次创建群使用随机键，不符合目标协议；若 BCS 不能提供稳定去重或结果对账，就不能开放 Relay 的 `HIT_MULTI_BOTS`。同理，BBS 发布意图的消费者须以目标节点 ID 派生稳定发布键或对账原发布结果；下一棒接收端按目标节点 ID 去重启动指令。

`dispatch(node) -> TaskNode` 仅在投递通道确认接纳或已处于 `DELIVERED` 时返回。若唯一意图仍在 `DELIVERING`，Dispatcher 抛可重试的 `DispatchInProgress`，HTTP Adapter 映射为 `409 Conflict` 并附 `Retry-After`；不得返回貌似成功的节点。

### 10.4 `TaskRunner`：执行指令投递

```python
class TaskRunner:
    async def start_run(self, node: TaskNode) -> bool: ...  # 单节点原子入口

    async def form_coop_group(self, formation: GroupFormation) -> str: ...
```

`start_run(node)` 是对应派发策略内部调用的单节点投递原语，按冻结的 `orchestration_mode + runtime_profile.runner_strategy` 解析实现。中心化 `default` 策略保持现有执行流程；Relay `relay_prompt` 策略构造包含 `task_id/node_id` 和“从 S1 拉取上下文、执行本棒至 S5.5”的任务指令，再通过 Bot/协作群投递适配器发送。`TaskExecutor` 内部也抽取单节点投递原语，不在 `start_run` 内包装为 `[node]`。`HIT_MULTI_BOTS` 时先按已接纳决策组建协作群，再投递。Runner 返回 `True` 仅说明指令被接纳，不表示任务完成；返回 `False` 或抛错时 Dispatcher 不得返回成功节点。无真实投递实现时必须失败，不能以日志记录冒充成功。harness 恢复通过同一节点投递入口重新驱动，业务指令不携带权威 `TaskContext`；下一棒仍在 S1 拉取。

#### 10.4.1 `runtime_profile` 配置、冻结与策略装配

```yaml
# 创建中心化任务时冻结到执行图的配置快照
orchestration_mode: centralized
runtime_profile:
  planner_strategy: default
  dispatcher_strategy: default
  runner_strategy: default
  allowed_run_modes: [single_bot, coop_group, bbs]

# 创建分布式接力任务时冻结到执行图的配置快照
orchestration_mode: relay
runtime_profile:
  dispatcher_strategy: relay_handoff
  runner_strategy: relay_prompt
  allowed_run_modes: [single_bot, coop_group, bbs]
```

`runtime_profile` 存的是策略标识符，不是已实例化对象。组合根在进程启动时从白名单配置实例化并注册中心化/Relay 策略；每次调用统一入口按任务图中冻结的配置解析。Relay **省略** `planner_strategy`，既不配置 `null`，也不填 `default`：配置解析器必须按 `orchestration_mode` 校验，不能对 Relay 的缺省 planner 自动补 `default`；中心化任务则必须显式有 planner。Relay 若出现 planner 配置或任一模式的策略无法解析，应显式报错，不得静默回退另一模式。现有 `TaskRuntimeProfile.from_execution_config` 会给缺失 planner 补 `default`，实施时必须同步修改解析、序列化和配置合同测试。中心化模式保留原选人与投递行为，但要把外置 Runner 调用移入中心化派发策略并验证没有重复投递；这属于目标接口重构，不能把旧代码称为已符合新语义。

### 10.5 Relay 的实际调用序列

```text
P0–P1 TaskService.intake(START/RESUME/CONTINUE/CONFIRM/CANCEL)
       → TaskIntakeResult；CONFIRM 不创建任务
P2   CLI POST /start {session_id}；Backend 读取已确认 Result 并 build_task_info_request
P3   Backend TaskService.execute(TaskInfoRequest)
       → TaskGraphService.initialize_graph + 生成 root_execution_instruction
P4   正常任务 Frontend / benchmark 评测任务 Backend 将 root_execution_instruction 发送到当前 Bot 会话 → 首棒 S1
S1   TaskService.get_node_context
       → TaskGraphService.get_node_context
S4b  TaskService.report(EXECUTION_RESULT)
       → TaskGraphService.update_task_node_info(TaskNodePatch)
S5.1 Bot Skill.refresh_task_gaps
S5.2 Bot Skill.plan_next_node                         # 不调用 TaskPlanner
S5.3 TaskService.search_bots
S5.4 Bot Skill.select_executor                        # 本地选人，不调用 TaskDispatcher
S5.5 TaskService.report(PLAN_RESULT)
       → TaskGraphService.apply_relay_plan_result      # 一次落图、写选人决策和待投递意图
S6   Skill POST /dispatch(task_id, origin_node_id, target_node_id, relay_token)
       → HTTP Adapter 校验 relay_token 执行代次、读取权威 next_node
       → TaskDispatcher.dispatch(next_node)           # S6 唯一直接派发接口
          → 校验节点状态、认领投递意图；入口代次由 relay_token 保证
          → relay_handoff 内部 TaskRunner.start_run(next_node)
       → 下一棒 S1
```

S6 是 Bot 可通过 HTTP 触发、由 Backend 执行的交接阶段。Skill 在 S5.5 接纳后调用；若未调用成功，服务端扫描 `PENDING` 意图补发。HTTP 请求与后台消费者对同一 `(task_id,target_node_id)` 只能认领同一逻辑投递。至少一次投递场景下 Runner 和消费端还须按目标节点去重。真正需要变更的是 `TaskService`、`TaskGraphService` 的 Relay 合同，以及 Dispatcher/Runner 单节点原语、策略装配、HTTP adapter 和对应的 Service API/配置合同测试。

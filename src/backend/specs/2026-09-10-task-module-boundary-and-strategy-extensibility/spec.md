# Task 模块边界、统一事实接口与双编排模式

> 本文定义目标架构，不描述当前实现已经具备的行为。它同时支持中心化规划执行与分布式接力执行；Relay 以本文的 S1-S8 严格闭环和 API 契约为准。

## Summary

任务模块以 `TaskGraphService` 为任务事实的唯一来源（SSOT）：所有任务过程事实均通过统一 report 入口校验、持久化和驱动状态机。`TaskContext` 是从图谱投影出的最小、通用业务上下文；它不属于 Relay 专用，也不包含节点、拓扑、会话或授权令牌。

两种编排模式共享 TaskSpec、TaskNode、TaskContext、统一 report、候选搜索、Runner 和 BBS 能力，但由不同主体完成规划与交接：

- **中心化规划执行模式**：Graph 的语义事件驱动 Backend `TaskPlanner → TaskDispatcher → TaskRunner`。中心化模式可生成多个节点，并由 Graph 依据依赖关系和子节点状态进行父节点收敛。
- **分布式接力执行模式（Relay）**：当前 baton Bot / 协作群 Manager 通过 Skill 运行固定 S1-S8：“读取上下文 → 计算 GAP → 能力匹配 → 执行并统一上报 → 更新 GAP → 解析下一棒 → 搜推并指定执行者 → 实际交接”的严格串行闭环。S2/S3/S5/S6 是本地推理，不提前调用上报接口。每次规划最多生成一个下一棒节点；后续节点不得回写前序节点或根节点的运行事实。

任务识别澄清使用 task-loop 的 `task_info` 卡片领域模型。确认后，平台以稳定接口 `init_task_request(task_info, source_context) -> TaskInfoRequest` 创建正式任务；卡片外层的 UI 状态不进入 TaskSpec。

## Motivation

当前任务模块同时承载图谱、中心化规划、TaskRunner、Skill 回调与 Relay 接力，若缺少统一的事实边界，会导致：

- Bot、Planner、Dispatcher 或 Runner 直接写图谱，生命周期和幂等无法收口；
- Relay 把本棒局部输出误认为整体任务完成，或以模型通用能力越过 Bot 职责；
- `/search`、派发决策、实际投递的职责混杂；
- 协作群的创建 driver、执行成员、唯一接力者和 Human 观察者身份不清；
- 中心化父子收敛规则错误渗入 Relay 串行 baton。

本设计通过最小领域对象、统一 API 和模式隔离，保留现有中心化能力，同时将最新 Relay 方案规范化。

## Goals

- `TaskGraphService` 是任务过程事实、图谱状态机、版本、幂等和持久化的唯一所有者。
- `TaskContext` 是中心化 Planner、Relay Bot、BBS Bot 和恢复流程共用的最新业务上下文。
- task-loop 的 `task_info` 保持为识别澄清阶段的统一任务草案；确认后的转换接口稳定。
- Relay 每一棒采用相同的 S1-S8 执行闭环，严格串行、一次只产生一个下一棒。
- `/search` 只做候选检索；`DISPATCH_RESULT` 只记录 Skill 派发决策；`/dispatch` 只做 Runner 实际投递。
- 单 Bot、协作群和 BBS 都能承接下一棒；协作群默认将任务提交 Human 作为 observer 加入。
- 中心化与 Relay 复用统一事实边界和 Runner 能力，但不强迫二者共享父子状态收敛策略。

## Non-goals

- 不在本设计中重新实现 Bot 能力画像、Skills 可见性、搜索排序或模型推理算法。
- 不将任务卡片 UI 状态（`type`、`actions`、`needs_confirmation`、问题文案）放入 TaskSpec。
- 不让 `/search` 读取 TaskGraph、生成节点、记录候选快照或决定执行模态。
- 不让 Relay Bot 直接写 Graph、直接创建会话或绕过 `/dispatch` 给群外 Bot 发消息。
- 不要求中心化模式采用 Relay 的串行 baton 状态规则，也不允许 Relay 使用中心化父子回写规则。

## User Stories

- 作为任务提交者，我希望以自然语言提交任务、补齐四要素并确认，使平台只在确认后创建正式任务。
- 作为中心化 Planner 开发者，我希望只消费 `TaskContext` 和当前目标节点，而非完整图谱实现。
- 作为 Relay Bot，我希望读取最新整体上下文，界定自身可执行范围，只完成职责覆盖的工作，并把其余 GAP 交接给下一棒。
- 作为协作群执行者，我希望由明确的下一棒 Driver/Manager 汇总群产出并继续 Relay，而不是由已交接的上一棒继续留在群内。
- 作为任务提交 Human，我希望默认成为协作群 observer，能够查看下钻会话但不参与执行。
- 作为平台维护者，我希望所有运行事实通过同一 report 网关进入 Graph，使版本、幂等、状态和审计可验证。

## Acceptance Criteria

### Shared facts and boundaries

- [ ] `TaskGraphService.report(...)` 是任务过程事实的唯一写入口；任何 Bot、Planner、Dispatcher、Runner、Harness 或 HTTP adapter 均不得绕过它写图谱。
- [ ] `GET /api/v1/collaboration/tasks/{task_id}/context` 返回最新 `TaskContext`，其 `data` 直接对应领域对象，不携带 Relay 专用节点、holder、turn 或 `include_*` 参数。
- [ ] `TaskContext` 只包含 `spec`、`all_done_output` 和 `gaps`；其中 gaps 的语义由 Bot/Skill 的 PLAN_RESULT 定义，Graph 不自行做 GAP 推理。
- [ ] `DoneOutput` 包含 `node_id`、`actual_goal`、`output` 和节点局部 `acceptance_result`。
- [ ] `TaskSpec` 不包含 `metadata`、`task_id` 或 `instruction`；用户业务事实只映射到 `context` 和 `goal`。

### Task intake

- [ ] task-loop 以稳定 `task_info` 字段集合维护任务草案；`goal`、`deliverables`、`acceptance_criteria`、`constraints` 是确认前的必填四要素。
- [ ] 平台保留当前 `task_clarify` 与 `task_ready.task` 的卡片 UI 协议；确认时以 `init_task_request(task_info, source_context)` 转换为 `TaskInfoRequest`。
- [ ] `title`、`background`、`resources` 是 task_info 可选字段；缺失不阻塞确认。

### Relay

- [ ] Relay 以固定 S1-S8 闭环推进：S1 读取上下文、S2 计算 GAP、S3 能力匹配、S4 执行并上报 `EXECUTION_RESULT`、S5 更新 GAP、S6 解析下一棒、S7 `PLAN_RESULT → /search → DISPATCH_RESULT`、S8 `/dispatch | BBS`；`EXECUTION_RESULT` 成功不是本棒结束。
- [ ] Relay 会话中的执行阶段明细使用 `【S1/8 ...】` 到 `【S8/8 ...】` 连续编号；重试、恢复或接口章节号不得造成步骤编号跳变。
- [ ] S2/S3/S5/S6 是 Skill 端本地推理，不调用任务 HTTP 上报接口；S4 后可直接基于 S1 上下文与当前节点事实进入 S5，无需再次读取 TaskContext。
- [ ] Relay 正常有 GAP 链路最多 6 次 HTTP 调用：1 次 context、3 类必要事实上报、1 次 search、1 次 dispatch；无 GAP 链路不得调用 search、DISPATCH_RESULT 或 dispatch。确定性错误只能使用同幂等 ID 原样重试，不得增加进度型或探测型调用。
- [ ] Relay Bot 的有效覆盖等于“职责/系统角色覆盖 AND Skill/工具事实覆盖”；工具、搜索或通用模型能力不得扩大职责边界。
- [ ] `actual_goal` 由当前 Bot 本地计算，在 `EXECUTION_RESULT` 中首次持久化；不新增执行范围开始事件。
- [ ] Relay Skill 依据最新 `TaskContext` 和当前完成节点事实重新计算 `gaps: list[str]`，并通过 `PLAN_RESULT` 让 Graph 持久化最新 GAP 快照。
- [ ] Relay `PLAN_RESULT` 的 `gaps` 非空时必须携带唯一 `next_task_spec`；GAP 为空时不得携带下一棒，并完成任务。
- [ ] Relay 图级 status 不镜像根节点或父节点状态：前序/root 的 `DONE` 只表示已交接；存在待处理、规划中或执行中的接力节点，或仍有未闭合 GAP 时，图级有效态保持 `RUNNING`。
- [ ] Relay 图级 `DONE` 只能由接力收敛推导：所有节点已达自身终态、当前叶子为 `SUCCESS`，且最新 `PLAN_RESULT` 持久化的 `gaps` 为显式空列表；缺失 GAP 快照不得被推断为完成。
- [ ] Relay 图级 `FAILED/HUNG/CANCELLED` 由显式控制事实决定，不通过父子状态传播回写前序节点；中心化模式继续使用自己的根节点与父子收敛策略。
- [ ] Relay 的下一棒节点由 Graph 生成 `node_id`，初始 `runtime_info` 为空；Skill 不预填下一棒 `actual_goal`。
- [ ] Relay 当前棒仅修改当前节点与图级 Relay 控制事实；不得修改任何前序节点或根节点的 `status`、`actual_goal`、`output`、`acceptance_result`、`assignee` 或 `run_mode`。
- [ ] Relay 文档/报告类 `RuntimeInfo.output` 使用 `{"result":"<完整 Markdown 全文>"}` 承载最终交付物，不得退化为标题、摘要或状态说明；任务完成时图级 `output` 仅做只读汇总，不回写任何前序/root RuntimeInfo。
- [ ] Relay `DISPATCH_RESULT` 的 `driver_bot_id` 与 `next_relay_bots` 均不得选择当前 holder；过滤当前 holder 后无其它候选时必须 MISS 并转 BBS。
- [ ] Relay 协作群决策包含 `driver_bot_id` 和 `next_relay_bots`；`driver_bot_id` 必须属于 `next_relay_bots`，并作为下一棒群 Manager、唯一 Relay Holder 和节点 assignee。
- [ ] 当前棒 Bot 默认不加入下一棒协作群；Human 默认以 observer 身份加入下一棒协作群。
- [ ] Relay BBS claim 是目标 BBS 节点级 claim；BBS Bot 认领后执行相同 Relay 闭环，不能触发中心化根节点或父子收敛。
- [ ] Relay 结果入口只接受 `EXECUTION_RESULT`、`PLAN_RESULT` 和 `DISPATCH_RESULT`；中心化节点终态 callback 不能把 Relay 节点或根任务推到终态。
- [ ] Runner 给 Relay 节点只注入同一套事件协议，不得同时携带中心化一次性回投协议。

### Centralized planning execution

- [ ] 中心化模式由 Graph 语义事件驱动 `TaskPlanner → TaskDispatcher → TaskRunner`；各模块通过 report 回写事实，不直接互调。
- [ ] 中心化 Planner 消费 `TaskContext` 与当前目标 TaskNode；可产生多个子节点及依赖关系。
- [ ] 中心化模式保留由 Graph 基于关系和子节点状态进行父节点收敛的规则。
- [ ] 中心化模式可复用 `/search`、`DISPATCH_RESULT`、`/dispatch`、Runner 和 BBS executor，但其父子状态策略不传播到 Relay。

### Reliability and extensibility

- [ ] report 校验调用身份、节点归属、状态迁移、字段白名单、事件幂等与 Relay authority；合法写入和 Outbox/可靠事件发布原子完成。
- [ ] Relay authority 是带 TTL 的单持有者 lease；按阶段限制 `PLAN_RESULT` 和派发动作，过期后通过 Relay 专属恢复续接，不能进入中心化 stuck/child-hung 收敛。
- [ ] `TaskRuntimeProfile` 可冻结中心化 Planner/Dispatcher/Runner 策略与允许的 run_mode；Relay 不改变该治理边界。

## Open Decisions

以下事项刻意不在本次目标态中展开实现，但必须在实施前确定：

- 首棒在尚无上一轮 GAP 快照时如何展示 `gaps=[]`：目标态由首棒 Skill 基于根 TaskSpec 和根 TaskNode 先计算初始 GAP；Graph 不将空 gaps 推断为任务完成。
- `TaskContext` 大产出的资源引用与按需读取机制；核心 `/context` 不引入 `include_*` 裁剪参数。
- Relay authority 在 HTTP 鉴权上下文成熟前的临时调用身份承载方式；该问题不得改变领域 API 的输入模型。

### Engine 拆除约束

目标代码中不得存在 `task_center/engine.py` 或 `ExecutionEngine`。中心化执行使用 `CentralizedExecutionAdapter` 作为中心化模式兼容层；该模块不参与 Relay 编排，Relay 只通过通用 Graph、Runner、Search、Dispatch 和 BBS 接口推进。

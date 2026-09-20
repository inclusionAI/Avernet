# 目标态验收清单：任务模块事件反应链

> 本清单定义目标态能力与验收。

## 1. 图谱事实与事件

- [ ] `TaskGraphService.report(TaskCallbackData)` 是唯一任务过程事实入口。
- [ ] 图谱服务校验报告身份、版本、幂等、状态机、字段白名单和授权范围。
- [ ] 图谱事实持久化与 Outbox 事件原子完成。
- [ ] 图谱服务从状态变化产生 `PLAN_REQUESTED`、`DISPATCH_REQUESTED`、`EXECUTION_REQUESTED` 和终态事件。

验收：重复、过期、越权或非法字段报告不会改变图谱；合法报告只产生一次有效状态推进。

## 2. 规划、派发与执行

- [ ] TaskPlanner 仅消费 `TaskContext`，输出 `PlanResult` 并以 `PLAN` 报告。
- [ ] TaskDispatcher 仅消费单个 `TaskNode`，输出受限 `TaskNodePatch` 并以 `DISPATCH` 报告。
- [ ] TaskRunner 仅消费单个 `TaskNode`，启动 executor 并报告启动及最终执行事实。
- [ ] 事件消费具备至少一次投递下的幂等语义。

验收：TaskPlanner、TaskDispatcher、TaskRunner 不读取/写入完整图谱、不互相调用；批量吞吐不改变其单节点领域接口。

## 3A. 最新 Relay 接力闭环

- [ ] Relay 执行结果接纳后签发带 TTL 的单持有者 `relay_turn`；Bot 必须继续 `PLAN_RESULT`，不能以 `EXECUTION_RESULT` 或 acceptance 结果提前结束。
- [ ] Relay `PLAN_RESULT` 严格串行，一次最多创建一个下一棒节点；当前棒交接后置 `DONE`，不使用中心化父子状态聚合。
- [ ] Relay `/search` 只接收 `query` 并返回真实候选；HIT/MISS、single/group/BBS 模态决策由 Relay Skill 产生并通过 `DISPATCH_RESULT` 上报。
- [ ] Relay BBS 复用统一动态选人/Runner，但认领与结果只推进当前 BBS baton，后续通过新 `relay_turn` 回到规划闭环。
- [ ] Relay lease 过期由专属恢复流程续租并发送 `[RESUME_RELAY]`；恢复不重做已上报业务，不触发中心化 `exec_stuck`、`child_hung` 或根节点 BBS 收口。
- [ ] 当前 Relay baton 的执行、失败、BBS 认领、恢复只修改当前节点和图级 Relay 控制元数据，不修改任何前序节点。

验收：覆盖普通单 Bot、协作群、BBS、执行失败、能力不匹配、搜索 MISS、重复上报、过期 turn 和恢复上限；后续节点不能改变前序节点状态/输出/assignee。

## 3. 策略扩展

- [ ] 中心化动态规划策略通过 `TaskContext -> PlanResult` 驱动闭环。
- [ ] Master-Slave 通过依赖解锁汇总节点，并使用同一事件链。
- [ ] 接力规划通过 `PLAN` 报告产生下一步节点。
- [ ] 图谱拒绝接力规划中的过期版本、越权归属、非法关系和超深度请求。
- [ ] 接力节点 A 的执行结果被图谱接纳后，Graph 生成包含任务目标、验收标准、全局节点/依赖状态、相关已完成产出、当前 GAP 与授权边界的最新 `TaskContext`；A 的结果只是其中一项输入。
- [ ] 接力模式由 Graph 将 `PLAN_REQUESTED`、必要时 `DISPATCH_REQUESTED` 定向投递给上游完成者 runtime；Graph 基于报告身份、已有 `graph_version`、父节点、深度和既有回调幂等机制防止重复或越权续接。
- [ ] 接力 runtime 即使物理同驻 Planner、Dispatcher、Runner，也仅按各自事件执行；不存在 Runner 直接调用规划/派发或直接写图谱的旁路。
- [ ] Dispatcher 选择 execution carrier：单 Bot、协作组，或在两者没有可用匹配时上报 BBS 升级 patch；BBS 的内部协作和回报属于 BBS Runner strategy。

验收：三种模式保持同一逻辑 TaskPlanner → TaskDispatcher → TaskRunner 链，只改变策略、实际 handler 和 Graph 的事件定向投递；不增加跨模块直连或图谱写入旁路。

## 4. B 端插件和 Profile 组合

- [ ] 支持注册 `TaskPlanningStrategy`、`TaskDispatchStrategy`、`TaskRunnerStrategy` 三类插件贡献，并校验契约版本、配置 Schema、权限和 run mode。
- [ ] 支持插件包安装与租户激活；未激活的插件不得被 Profile 引用。
- [ ] 支持租户范围 `TaskRuntimeProfile` 声明式绑定 TaskPlanner、TaskDispatcher 和 TaskRunner 策略版本及配置。
- [ ] TaskService 在建任务时解析、校验并冻结 Profile 与插件 digest。
- [ ] 支持 Dispatcher 的无副作用 `NO_MATCH` 链式选择；仅最终 Patch 可上报。
- [ ] 支持 TaskRunner 按 run mode 精确选择一个 strategy；不在外层自动 fallback。
- [ ] 覆盖仅自定义 semantic-match、仅自定义 distributed-execution、仅自定义领域规划、三类联合定制及纯内置策略组合五类 Profile case。

验收：第三方 semantic-match 与 distributed-execution 能与内置 master-planning、keyword-match、single-bot、coop-group、bbs 在同一个 Profile 内组合；插件不能访问图谱写入或改变任务生命周期。

## 5. 统一任务入口

- [ ] TaskService 仅暴露统一的 `intake(TaskIntakeRequest) -> TaskIntakeResult` 澄清接口；首次、补答、确认、取消均使用该接口和自然语言 `message`，不增加 answer/review/confirm 子接口。
- [ ] TaskService 在首次调用生成并持久化 `intake_session_id`，绑定目标 Bot/group；该 ID 仅由 TaskCli 回传，不是 `task_id`、不进入 TaskSpec 或图谱、也不向终端用户展示。
- [ ] TaskCli 通过 TaskService 调用目标 Bot/group 已挂载的 task-loop，不直接加载 Skill；task-loop 只做识别、澄清和确认，不创建任务或执行任务。
- [ ] 所有澄清状态使用同一个 `task_info`：`title`、`goal`、`background`、`deliverables`、`acceptance_criteria`、`constraints`、`resources`；四要素为 goal/deliverables/acceptance_criteria/constraints。
- [ ] TaskService 将确认后的 `task_info` 映射为既有 `TaskInfoRequest`，在同一次 intake 调用内执行 `execute(...)`；仅 execute 成功后返回 `CONFIRMED` 和 `task_id`。
- [ ] TaskSpec 移除 task_id；用户信息不拼接到 `metadata.instruction`，deliverables/constraints/resources 使用 `context.extend_props`。
- [ ] TaskCli 仅公开 `task-cli [goal]`、`task-cli get <task_id>`、`task-cli list`。
- [ ] 人身份支持未指定目标、显式 `@bot`、显式 `@group`；未指定时按同名 Bot、需求 Bot、随机合格 Bot 选择。Bot 身份固定使用当前 Bot。显式目标不合格时必须拒绝而非回退。
- [ ] Workflow 和 YAML 仅属于 executor 实现，不成为 TaskCli 的任务类型或二级命令。

验收：人或 Agent 从一句话开始即可完成澄清、确认和执行；TaskCli 不暴露 `intake_id`、`answer`、`review` 或 `confirm`；确认成功返回已经创建的 `task_id`。

## 6. 架构约束与验证

- [ ] 依赖检查限制 TaskPlanner、TaskDispatcher、TaskRunner 访问 Graph 模块或彼此实现。
- [ ] 契约测试覆盖报告类型、事件投递、版本与幂等。
- [ ] 策略闭环测试覆盖中心化、Master-Slave 与接力模式。
- [ ] 接力闭环测试覆盖“A 执行结果 report → Graph 生成完整新 TaskContext → A runtime 定向规划 → 定向派发 → 下一 execution carrier 执行”，以及过期、重复、越权续接不产生外部执行副作用。
- [ ] Dispatcher 契约测试覆盖单 Bot、协作组和无匹配时 BBS 升级三种最终 Patch；BBS 失败由 Graph 状态策略驱动后续事件。
- [ ] 插件契约、租户激活、Profile 校验、冻结版本、Dispatcher 链和 Runner 路由均有自动化测试。
- [ ] CLI 会话测试覆盖澄清四要素、确认、执行和查询。

验收：架构规则和核心协议均由自动化测试验证。

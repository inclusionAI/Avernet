# 目标态验收清单：统一任务边界与双编排模式

> 本清单以目标架构为准；实施时按依赖顺序拆分提交，不将未完成项标记为已实现。

## 1. TaskSpec、task_info 与 TaskContext

- [x] 将正式 `TaskSpec` 收敛为 `context + goal`，移除 `metadata`、`task_id` 和 `instruction`。
- [x] 保留 task-loop 的 `task_info` 作为任务识别澄清草案，并稳定字段：`title`、`background`、`resources`、`goal`、`deliverables`、`acceptance_criteria`、`constraints`。
- [x] 保持 `task_clarify` 与 `task_ready.task` 的现有卡片 UI 协议；实现到 `task_info` 的归一化和确认时转换。
- [x] 实现并覆盖 `init_task_request(task_info, source_context) -> TaskInfoRequest` 的映射；四要素业务校验仍由 task-loop 澄清阶段负责。
- [x] 定义 `DoneOutput(node_id, actual_goal, output, acceptance_result)`。
- [x] 定义最小 `TaskContext(spec, all_done_output, gaps)`；不增加 `node_context`、`aggregate_acceptance_result`、图关系或 Relay token。
- [x] 明确 `all_done_output` 仅收录已接纳、`ACCEPTED` 且有真实实际目标/输出/局部验收的节点事实。

验收：任务卡片字段、确认转换、TaskSpec、TaskContext 和 Graph 序列化之间没有重复或语义冲突的模型。

## 2. 通用图谱上下文与统一 report

- [x] 提供 `GET /api/v1/collaboration/tasks/{task_id}/context`，路径输入仅为 `task_id`，响应 `data` 直接是完整 `TaskContext`。
- [x] Context API 不定义 `node_id`、`holder_id`、`relay_turn`、`graph_version` 或任何 `include_*` 查询参数。
- [x] 根任务初始化时持久化 `gaps=[]` 作为“尚无 PLAN_RESULT GAP 快照”；首棒 Skill 基于根 Spec 和根节点计算初始 GAP，后续 `PLAN_RESULT` 整体覆盖最新 gaps。
- [x] `TaskGraphService.report(...)` 成为唯一任务过程写入入口，校验身份、幂等、状态机、字段白名单和模式授权。
- [x] Graph 写入与 Outbox/可靠语义事件发布原子完成。
- [x] Graph report 支持并规范化 `EXECUTION_RESULT`、`PLAN_RESULT`、`DISPATCH_RESULT`。
- [x] 删除 `TaskGraphRelayFactsMixin` 和中心化 Graph monkey patch；中心化与 Relay 统一由一个 `TaskGraphService` 暴露事实、状态机、查询和 TaskContext 投影。
- [x] 模式差异收敛为私有模块级状态策略函数，不再定义模式专用 Graph 领域类。

验收：任一非法、重复、过期或越权 report 不改变图谱；Context 查询只返回已持久化的任务业务事实。

## 3. Relay 节点执行与 GAP 闭环

- [x] Relay Runner 投递当前节点 TaskSpec、任务/节点身份、执行身份、backend 地址和协议；当前节点不塞入 TaskContext。
- [x] Relay Skill 以 `TaskContext + 当前 TaskNode + Bot 职责/IDENTITY/Skills/工具可用性` 计算 `actual_goal | DECLINED`。
- [x] `actual_goal` 仅在 `EXECUTION_RESULT` 中首次持久化；不引入执行范围开始事件。
- [x] `EXECUTION_RESULT(ACCEPTED)` 写入当前节点 `actual_goal`、`output`、局部 `acceptance_result`，并签发 plan authority。
- [x] `EXECUTION_RESULT(DECLINED)` 写入能力不匹配事实，但不创建 DoneOutput、不允许伪造业务输出。
- [x] Relay Skill 在执行结果接纳后读取最新 Context，基于根 Spec、DoneOutput、当前节点事实和上一轮 gaps 重新计算 `new_gaps: list[str]`。
- [x] `PLAN_RESULT` 以 `gaps + optional next_task_spec` 表达 Relay 规划：GAP 非空创建唯一下一棒；GAP 为空完成任务。
- [x] Relay `PLAN_RESULT` 不再使用 `aggregate_acceptance_result`、`has_gap`、多个 `children` 或 Bot 自造 node_id。
- [x] Relay 当前棒只更新当前节点和图级 relay 控制事实；测试覆盖其不能修改前序/root 的状态、输出、实际目标、验收、assignee 或 run_mode。
- [x] Relay authority 支持 TTL、幂等重放、专属恢复和 `[RESUME_RELAY]`；恢复不重做业务执行、不进入中心化父子收敛。

验收：单 Bot、部分覆盖、零覆盖、工具不可用、重复事件、过期 authority、完成和轮次上限均能形成可审计且不越权的 Relay 闭环。

## 4. Search、派发决策与实际交接

- [x] `POST /api/v1/collaboration/tasks/search` 只接受 `{query}`，只返回真实候选字段，且不读取/写入图谱。
- [x] Relay Skill 使用 `new_gaps + target TaskSpec + candidates[]` 决定 `HIT_SINGLE`、`HIT_MULTI_BOTS` 或 `MISS`。
- [x] `DISPATCH_RESULT` 持久化 `run_mode`、`driver_bot_id`、`next_relay_bots`、可选 group_name 或 miss_reason。
- [x] `HIT_SINGLE` 约束 `next_relay_bots` 恰一个且等于 `driver_bot_id`。
- [x] `HIT_MULTI_BOTS` 约束 `driver_bot_id ∈ next_relay_bots`，并将 driver 定义为下一棒 Manager、唯一 Relay Holder 和 target assignee。
- [x] `POST /api/v1/collaboration/tasks/dispatch` 使用 `task_id`、`origin_node_id`、`target_node_id`、`holder_id`、`relay_turn`、`dispatch_id` 做实际投递。
- [x] Runner 仅在实际 delivery 成功后使 target PENDING → RUNNING；决策落图本身不得置 RUNNING。
- [x] 协作群中当前棒 Bot 默认不加入下一棒群；Human 默认以 observer 加入；driver 和 workers 从 `next_relay_bots` 建群。
- [x] BBS MISS 发布 target node；`/bbs/claim` 以目标节点级 CAS 认领，认领 Bot 从 Relay Step 1 重启闭环。

验收：search、决策、delivery、BBS claim 四类动作可独立重试和审计；协作群只有 driver 可继续 Relay，Human 可查看会话但不执行。

## 5. 中心化规划执行兼容

- [x] 中心化模式已接入 `PLAN_REQUESTED → DISPATCH_REQUESTED` Graph 语义事件和持久化语义 Outbox；规划、派发、投递、结果收敛、恢复、轨迹与 Static Plan 已归位到现有模块类。
- [~] 中心化 `TaskPlanner` 仍消费 `TaskContext + target TaskNode` 并可生成多个节点；派发和执行分别由 `TaskDispatcher` 与 `TaskRunner` 通过现有接口完成。
- [x] 中心化 Graph 保留基于关系和子节点状态的父节点收敛、重试与 BBS 升级策略。
- [x] Relay 严格串行 DONE/SUCCESS 策略不进入中心化父子收敛；中心化父子状态规则不回写 Relay 前序节点。
- [x] 两种模式复用 Context、report、search、dispatch、Runner 和 BBS 基础设施，但由模式策略决定执行主体和状态策略。

Engine 拆除与职责归位：

- [x] 将中心化 plan、dispatch、execution、result、recovery、trajectory 与 Static Plan 职责归位到现有模块类，删除临时 `Centralized*Handler`。
- [x] 将 `DISPATCH_REQUESTED` 的派发准备与实际投递分别归位到 `TaskDispatcher` 和 `TaskRunner`。
- [x] 将 `static_plan.py` 与 `static_plan_runtime.py` 合并为唯一 Static Plan 领域实现。
- [x] 删除 `static_plan_execution.py` 和 `static_plan_bbs.py`；规划、派发、投递、超时/BBS 恢复分别归位到 `TaskPlanner`、`TaskDispatcher`、`TaskRunner` 和 `TaskHarness`。
- [x] 删除 TaskService 对旧生命周期类的生产依赖，由 Adapter/DI 组合现有模块能力。

验收：相同 TaskSpec 可选择中心化或 Relay；二者共享 API/事实边界而不混淆状态收敛语义。

## 6. Plugin、Profile、测试和迁移

- [x] `TaskRuntimeProfile` 冻结受治理的 Planner、Dispatcher、Runner 策略及允许 run_mode；运行中的任务不受后续 Profile 变更影响。
- [x] 插件策略只通过宿主模块契约运行，不直接读写 Graph、不调用 TaskService 或其他任务模块实现。
- [x] 迁移兼容层把旧 Relay `completed_scope/uncovered_scope`、`has_gap/children`、根级 BBS owner 和 `metadata.instruction` 归一到目标契约后再持久化。
- [x] 为 Context API、task_info 转换、三类 report、search、dispatch、BBS claim 建立 HTTP/Service API 契约测试。
- [x] 为中心化和 Relay 各自建立端到端状态机测试，并加入“Relay 不改前序节点”“群 Driver 唯一接力”“Human observer”覆盖。
- [x] 更新任务模块 README、协议文档和 task-loop Relay 协议，使其不再引用旧 `aggregate_acceptance_result`、`completed_scope/uncovered_scope`、`has_gap/children` 和 `metadata.instruction` 语义。

验收：架构依赖检查、契约测试和模式级 E2E 能证明统一事实边界存在，且中心化与 Relay 的差异被显式、可测试地隔离。

## Engine 拆除验收

- [x] 删除 `task_center/engine.py` 和 `ExecutionEngine` 符号。
- [x] 中心化生命周期职责已归位到现有模块类，`CentralizedTaskLifecycle` 与临时 `Centralized*Handler` 文件和符号已删除。
- [x] Relay 通过 TaskService 组合的 Runner/Search Adapter 端口工作，不再在 Relay 流程中直接访问中心化生命周期的 `_runner`/`_discover`。
- [x] 任务模块测试不再依赖旧 `engine.py` 模块，且全量 task/adapters 测试通过。
- [x] 增加架构守卫：禁止重新引入 `engine.py`、`ExecutionEngine`/生命周期 Handler、重复 owner 类和 `TaskGraphService` monkey patch。

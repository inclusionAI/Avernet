# Spec — 任务目标驱动的任务动态规划执行框架 (Goal-Driven Dynamic-Planning Task Execution Framework)

> **权威源(冲突时以此为准)**:
> - 最新领域模型(用户给定 classDiagram,2026-08-11):`TaskInfo`/`TaskSpec`(metadata/context/goal,**无 SLA**)/`TaskExecutionGraph`(**含 `run_id`/`relations: list[Relation]`**)/`TaskNode`(**含 `task_id`/`node_run_graph`;结构归属由 `relations` 分解树表达,无 `decomposed_by`/`depends_on` 字段**)/`Relation`({src_id=结构父,dst_id=结构子,type,extend_props};DEPENDENCY=分解树单入)/`RuntimeInfo`(**`run_mode` 为 str,无 `collab_mode`**)/`AcceptanceCriteria`(id/description)/`AcceptanceResult`({verdict,acceptances_metric,gaps},**无 verifier**)/`Status` **6 态**(PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG)/`AcceptanceVerdict`/`RelationType`(DEPENDENCY)。
> - 流程架构图 `apoi9lcedw9u8ivq`:管理态任务中心 + 运行态任务图谱/规划/派发/执行 + 旁路任务Harness + 执行主体 Bot/协作群/BBS/Human/工具。
> - 5 个模块设计文档(权威 API 契约):任务中心 `yugg6dorsxo8sgmp`、任务图谱 `lunk1txfuv6gtwk2`、任务规划 `uuq2tlue91q4lkal`、任务派发 `ue1ie0g3supwo2uf`、任务执行 `lxg2mwgmtfqg6d95`。
> - 行为基线 case 剧本 `gwqie46v7hzr1w6h`(存储行业尽调,三阶段覆盖三模态)。
>
>
> **代码库实现声明**:现有 Avernet/ocb 代码实现**已失效**,不作为参考;代码库需**按本 spec(最新设计)从零重新实现**。
> 技术设计(类定义/API 契约/交互逻辑/状态流转表/时序图)见 `plan.md`(状态机见 §5.4);实现计划见 `tasks.md`。
> 日期:2026-08-11。Triage:`ready-for-agent`。

## Problem Statement

需要一个**以目标(Goal + Acceptance)为唯一收敛判据、能跨 单 Bot / 协作群 / BBS 三种执行模态自驱跑完「理解 → 规划 → 派发 → 执行 → 验收 → 重规划」闭环**的任务动态规划执行框架,且必须**严格按最新设计的领域模型与六模块架构实现**。

领域模型收敛为:**`Relation` 一等公民(`TaskNode` 不持 `depends_on`)**、**6 态 `Status`(含 `PLANNING`)**、`TaskNode` 含 `task_id`/`node_run_graph`、`TaskExecutionGraph` 含 `run_id`/`relations`(分解树,承载结构归属;数据流由批规划+结构父聚合上下文承载)、`RuntimeInfo.run_mode` 为 str(无 `collab_mode`)、无 `SLA`/`Scope`/`CollabMode` 枚举、`AcceptanceResult` 无 `verifier`;5 个模块文档进一步明确:`TaskService` 2 facade(`execute`/`get_task_dashboard`)、`TaskGraphService` 独立图谱模块(8 API:5 核心写/读+3 派生只读)、`TaskPlanner.plan` 与 `TaskGraphService.add_task_nodes` 有显式状态触发条件(a/b/c)、`TaskDispatcher.dispatch` 决定"谁来做"写 `assignee`、`TaskRunner.start_run` 一个入口三模态自适应、`TaskLoopCallback` PUSH 回投。代码库按此从零重新实现。

## Solution

按最新设计**从零实现**一个目标驱动的任务动态规划执行框架:

1. **领域模型 = 最新 classDiagram**:
   - 规格面:`TaskInfo`(入口,带 `source_channel_type/id` + `execution_config`)→ `TaskSpec`(metadata/context/goal,**无 SLA**)→ `Metadata`(**含 `task_id`**/title/instruction)、`Context`(background/**`extend_props`**)、`Goal`(objective/`acceptances: list[AcceptanceCriteria]`)、`AcceptanceCriteria`(id/description)。
   - 运行态:`TaskExecutionGraph`(**`run_id`**/loop_round/status/output/**`tasks`**/**`relations: list[Relation]`**/extend_props;loop_round=图级升 BBS 轮次达 MAX_LOOP→HUNG;节点级重规划次数由 extend_props.plan_round 记达 MAX_PLAN_ROUND→HUNG)、`TaskNode`(node_id/**`task_id`**/status/task_spec/run_info/**`node_run_graph`**)、`Relation`({src_id,dst_id,**`type: RelationType`**,extend_props})、`RuntimeInfo`(**`run_mode: str`**/assignee/start_time/end_time/output/acceptance_result/extend_props,**无 collab_mode**;start_time/end_time 为**毫秒整数 int**)、`AcceptanceResult`(verdict/`acceptances_metric`/gaps,**无 verifier**)。
   - 枚举:`Status` **6 态**(PENDING/**PLANNING**/RUNNING/DONE/FAILED/HUNG)、`AcceptanceVerdict`(PASS/FAIL)、`RelationType`(DEPENDENCY)。
   - **分解树统一承载结构归属**:`Relation{type=DEPENDENCY}` 在 `TaskExecutionGraph.relations` 表分解树(单入:每非根节点恰好 1 条入边=结构父,`src_id`=父,`dst_id`=子)。`TaskNode` 不持 `decomposed_by`/`depends_on` 字段——结构归属、验收、传播一律从 `relations` 派生。无跨兄弟直接数据边;数据流由步进式批规划顺序 + 执行时结构父聚合上下文承载。就绪=被 `add_task_nodes` 加入即就绪,无 `dependencies_satisfied` 闸门。
   - **`PLANNING` 新态**:承担"待规划/委托中"语义(节点已被/将被分解委托子执行)。
2. **六模块 = 流程架构图 + 5 模块文档**:
   - **任务中心 `TaskService`**(对外 facade,2 API):`execute(task_info)→TaskOpResult` / `get_task_dashboard(task_id,node_id?)→TaskExecutionGraph`。内部由编排核协调其余模块。
   - **任务图谱 `TaskGraphService`**(内部 SSOT,8 API:5 核心写/读+3 派生只读):`initialize_graph`/`add_task_nodes`/`update_task_node_info`/`update_task_graph_info`/`query_task_dashboard`。图谱原子变更唯一网关。
   - **任务规划 `TaskPlanner`**(零参,内置策略池):`plan(TaskExecutionGraph)→list[TaskNode]`,按状态触发条件 first-match-wins 选内置 `PlanningStrategy` 产逻辑子节点(不含物理执行信息)。引擎自带,不开放自定义。
   - **任务派发 `TaskDispatcher`**(零参,内置策略池):`dispatch(toDoTaskList)→list[TaskNode]`,first-match-wins 选内置 `DispatchStrategy`(config 有 `bot`→direct 跳搜推;否则 search 搜推)决定"谁来做",填 `run_info.run_mode`/`assignee`;HIT_MULTI_BOTS 标 `pending_group_formation`(拉群归编排核+runner)。引擎自带,不开放自定义。
   - **任务执行 `TaskRunner`**:`start_run(list[TaskNode])→list[Boolean]` 三模态自适应 + `query_status`/`query_detail`/`query_result`/`query_bot_tasks`;`TaskLoopCallback` PUSH 回投。
   - **任务Harness**(旁路常驻):周期巡检 SLA 超时/崩溃,写同网关,不抢正向驱动。
3. **事件驱动 + 状态条件触发**:
   - `TaskPlanner.plan` 显式触发条件(规划文档):图谱有更新(新增失败节点/PLANNING 节点)AND 没有派发(RUNNING)或执行中节点 AND 状态图谱有处于 PLANNING 状态的节点。
   - `TaskGraphService.add_task_nodes` 显式触发条件(图谱文档 a/b/c):a. 只有一个根节点且 `status=PENDING`(初始规划);b. 叶子节点验收未通过(FAILED + `acceptance_result.gaps` 非空);c. 父节点验收未通过(PLANNING 节点 且 前序依赖节点全 DONE)。
   - 规划原则(规划文档):处于派发、执行状态的节点不能修改(含其前序依赖节点);只针对失败节点 与 子节点都已经完成并且自身处于 PLANNING 状态的父节点进行规划。
   - `TaskDispatcher.dispatch` 触发:每次规划出新 toDo 之后。
   - `TaskRunner.start_run` 触发:图谱上有 TaskNode 完成派发后立即执行。
   - 编排逻辑收口为 `TaskService` 内部编排核(事件驱动 on_* + 状态条件触发,实现细节见 `plan.md`),不对外暴露 fixpoint 泵。
4. **目标驱动 + 局部 reroute**:`goal.acceptances[]` 为完成 oracle;验收 FAIL 的 `gaps` 驱动 `plan` 产补救拓扑,补救子挂该 FAIL/PLANNING 节点本体下(`add_task_nodes`);递归 MISS 到深度上限 `MAX_DEPTH` → **自动升 BBS**(删子树+挂广场,BBS bot 认领自规划执行);BBS 链路再迭代到 `BBS_MAX_DEPTH` 仍执行不下去 → STUCK → HUNG(人介入);`loop_round` 仅升 BBS 时递增。
5. **重新实现而非改造**:代码库按本 spec 从零重新实现,不沿用失效实现。

## Design Constraints (WHAT-level)

> 行为级约束(非实现细节;具体类/API/状态机见 `plan.md`,状态流转表见 §5.4)。

- **完成判据唯一**:`Goal + Acceptance` 为唯一收敛 oracle;验收结论(`verdict`/`gaps`/`acceptances_metric`)是模型字段、在图内;`gaps` 驱动补救规划,不游离、不入 `plan` 参数。
- **分解树统一承载结构归属**:`Relation{type=DEPENDENCY}` 在 `TaskExecutionGraph.relations` 表分解树(单入:每非根节点恰好 1 条入边=结构父)。`TaskNode` 不持 `decomposed_by`/`depends_on` 字段。结构子(`get_child_tasks`)/结构父(`get_parent_task`)/验收/传播/`depth` 均从 `relations` 派生;无跨兄弟直接数据边,数据流由步进式批规划 + 执行时结构父聚合上下文承载。就绪=被 add 即就绪,无 `dependencies_satisfied` 闸门。
- **状态 6 态,`PLANNING` 显式承载"待规划/委托中"**:`PENDING`/`PLANNING`/`RUNNING`/`DONE`/`FAILED`/`HUNG`;节点被分解委托子执行时进 `PLANNING`(而非靠"有分解子"结构派生隐式判);`phase`/`NodeType`/`Edge` 退出模型。
- **事件驱动 + 状态条件触发**:模块调用由图谱状态变化事件驱动,且 `plan`/`add_task_nodes` 有显式状态触发条件(a/b/c + plan 三条件);不在单条反应式 fixpoint 泵里跑完所有步。
- **规划纯逻辑、不含物理执行信息**:`plan` 产 `list[TaskNode]` 只含逻辑行动(目标/验收/依赖),不含谁来做;派发才决定执行者。
- **规划原则硬约束**:派发/执行中节点不可改(含前序依赖);只针对失败节点 与 子全完成且自身 PLANNING 的父节点规划。
- **reroute 局部化**:补救挂该 FAIL/PLANNING 节点本体下,子全 PASS 传播顶回该节点 DONE;未触下游在依赖满足前不入图,无复位;自动 reroute 不调级联回滚。
- **深度闸门是引擎决策**:MISS 拆解前查核内派生深度(从 `relations` 递归),达内层 `MAX_DEPTH` → **自动升 BBS**(非 HUNG);BBS 链路 `loop_round` 达 `BBS_MAX_DEPTH` → STUCK → HUNG;规划器保持纯读图。
- **派发只决定"谁来做"**:`dispatch` 经搜推匹配单 bot / 已有协作群 / 多 bot 动态拉协作群 / MISS;写 `run_info.run_mode`(str)/`assignee`;协作群协作模式(chat/manager_worker/state_machine)作 `form_coop_group` 内部参数(对齐 BCS `GroupStrategy`),**不进 `RuntimeInfo` 持久字段**(模型无 `collab_mode`)。
- **执行三模态一个入口**:`TaskRunner.start_run(批量)` 按 `run_mode` 自适应分发单 bot/协作群/BBS;BBS = bot 认领任务后自算 gap+自规划子任务(落图 `run_mode="bbs"`,`assignee=bot_id`)→ 自执行 → 上报结果+验收;完成结果经 PUSH `TaskLoopCallback.report_result` 或 PULL `query_status`/`query_detail`/`query_result` 回收。
- **执行主体只发 `task_loop_id`**:回调数据协议 `TaskCallbackData` 承载 `loop_task_id`/`workflow_type`/`workflow_id`/`instance_id`/`result`;合法终态严格为 `success:bool + data + gaps:list[str]`(FAIL 的 gaps 必须非空),执行/回收异常为 `exec_error`;框架适配层做 `loop_task_id↔(task_id,node_id)`、`success/gaps→verdict`、`data→output` 映射,再走图谱写口。空/非法终态不得默认 PASS,统一进入 Harness。
- **BCS 身份边界**:任务领域、搜推结果和 `GroupFormation` 只保存产品 Bot ID;动态拉群时框架通过 BotService 权威 owner_id 在 BCS integration 边界转换为 `{product_bot_id}:{owner_id}`。BCS 请求中的 driver/originator/participant/manager/worker/binding bot_ids 使用 BCS UUID;state-machine binding key 是 workflow 逻辑名,不得使用 Bot ID,且 workflow binding 与 BCS ParticipantRole 分离。
- **执行 SLA 单一所有者**:worker fire-and-poll 执行由结果 Poller 统一判定业务 SLA;Singlebox Adapter 只判传输错误,不得以更短 adapter timeout 提前截断仍在生成的 Bot。SLA/poll_exhausted 属执行异常(`exec_error→Harness`),不属于验收 FAIL。
- **聚合收敛**:`terminal PASS` = `plan(root)==[]` ∧ 全非根节点 DONE ∧ 无 RUNNING → 根保持 PLANNING 等 owner bot 经 `TaskLoopCallback.report_result` 回投 verdict=PASS → 根节点 DONE ∧ 图 status=DONE;验收 100% 走回投(engine 不主动触发终验 skill,无 `OwnerBotVerifyPort`)。`terminal FAIL` 仅人工放弃(若提供),自动路径不产生终态 FAIL。
- **并发安全**:同任务图推进串行化(可重入锁),跨任务并行;防止回投并发撕裂图。
- **transport-agnostic**:core 逻辑不绑定框架/传输;搜推匹配与动态拉群不外泄为对外 API;图谱原子变更收口单一写网关(Harness 旁路同写口)。
- **框架通用、零 case 知识**:框架不含任何具体任务的节点结构知识(维度叶/汇总/终验等一律不写死);一切分解(含是否产汇总/终验节点)由引擎内置规划策略(`PlanningStrategy`,默认 `GapBasedPlanningStrategy`/`WorkflowPlanningStrategy`)产出。引擎自带能力,不开放自定义(corp 经 ocb 仓继承覆写 `_build_*` 注入真实策略版本);框架只为规划提供**机制**(读图算 gap、步进式、去重、依赖/委托语义、深度闸门),不提供**内容**。
- **单一实现**:全仓库只保留一套任务执行实现(规范位置 `core/task`),不并存旧模型并行包;演进到最新模型时替换旧 domain,保留可复用的 seam 架构、DI 接线与开源边界,删除并行/失效实现。
- **开源执行边界**:Avernet(开源仓)只发**契约 seam + Noop/singlebox double**(本地关键词 cover 的 bot catalog、BCS local/mock 拉群、stub decomposer);真实搜推、真实 Bot/协作群/BBS 执行、LLM 规划/验收 SKILL 均**不在 Avernet**,由 corp `ocb` 仓的 adapter 落地。

## User Stories

1. 作为运营方,我想代码领域模型与最新 classDiagram 一致(`Relation` 一等公民、6 态含 `PLANNING`、`run_mode` str、无 `collab_mode`/`SLA`/`Scope`/`RunMode` 枚举、`AcceptanceResult` 无 verifier),这样设计与实现不漂移。
2. 作为运营方,我想结构归属用 `Relation{type=DEPENDENCY}` 分解树(单入)存于 `TaskExecutionGraph.relations`,`TaskNode` 不持 `decomposed_by`/`depends_on`,这样关系可带 `extend_props`、父子查询从 relations 派生、模型更规整。
3. 作为运营方,我想 `TaskNode` 含 `task_id`/`node_run_graph`(结构父由 `relations` 分解树派生,无 `decomposed_by` 字段),这样节点知道归属哪张图、结构父可从 relations 查得、facade 写操作可据此定位。
4. 作为运营方,我想状态有 `PLANNING` 显式承载"待规划/委托中",这样状态机不与结构派生混淆、`add_task_nodes` 条件 c 可直接判 `PLANNING`。
5. 作为运营方,我想 `TaskExecutionGraph` 含 `run_id`(运行实例唯一 ID),这样多次执行同一 task_spec 可区分实例。
6. 作为运营方,我想 `AcceptanceCriteria` 只保留 `id`/`description`(不再用 `tag`/`type` 兼承 scope 枚举),这样验收项定义最精简。
7. 作为业务方,我想提交一句话需求并由 Bot 澄清成带验收标准的目标并锁定后才执行,这样目标可被机器验收。
8. 作为业务方,我想随时看任务状态/进度/距目标差距(`get_task_dashboard` 返回 `TaskExecutionGraph`),这样不必问人。
9. 作为业务方,我想目标验收 FAIL 后系统自动识别 gap 并补做(补救子挂该节点下,子全 PASS 传播治愈),这样不必手动重启。
10. 作为业务方,我想资源/SLA 耗尽时收到明确 FAIL/HUNG 与原因,这样我能决定放弃还是放宽约束。
11. 作为 owner-bot skill,我想调 `TaskService.execute(TaskInfo)` 启动任务,这样入口契约统一。
12. 作为前端/API,我想调 `TaskService.get_task_dashboard(task_id,node_id?)` 拿 `TaskExecutionGraph` 看板,这样 UI 不接触底层复杂关系边。
13. 作为需求识别 skill,我想调 `TaskGraphService.initialize_graph(TaskInfo)` 建图(根节点 PENDING),这样建图有原子操作。
14. 作为任务规划 skill,我想调 `TaskGraphService.add_task_nodes(list[TaskNode], parent_node_id)`(显式传父)把规划出的子图并网,触发条件 a/b/c 明确,这样规划产物落图谱有原子操作且时机清晰。
15. 作为任务派发 skill,我想调 `TaskGraphService.update_task_node_info(TaskNodePatch)` 写派发结果(run_mode/assignee),这样派发落库有单一网关。
16. 作为任务执行 skill,我想调 `TaskGraphService.update_task_node_info(TaskNodePatch)` 上报执行产出与验收结果,这样回投与派发走同一个节点级写口。
17. 作为 TaskPlanner,我想 `plan(TaskExecutionGraph)→list[TaskNode]` 在"有失败/PLANNING 节点 ∧ 无 RUNNING 节点 ∧ 有 PLANNING 节点"时被调,产逻辑子节点(不含执行信息),这样规划纯逻辑可复跑。
18. 作为 TaskPlanner,我想遵循"派发/执行中节点不可改(含前序依赖);只对失败节点 + 子全完成且自身 PLANNING 的父节点规划"原则,这样规划不破坏在跑节点。
19. 作为 TaskDispatcher,我想 `dispatch(toDoTaskList)`(无 graph 入参)做搜推:单 bot / 已有协作群 / 多 bot 动态拉协作群 / MISS;写 `run_mode`/`assignee`,这样派发职责纯净。
20. 作为 TaskDispatcher,我想多 bot 合 cover 时经 `TaskRunner.form_coop_group` 动态拉协作群(3 模式 chat/manager_worker/state_machine 经 BCS),协作模式不进 RuntimeInfo 持久字段,这样拉群不外泄为对外 API。
21. 作为 TaskRunner,我想 `start_run(list[TaskNode])→list[Boolean]` 一个入口按 `run_mode` 自适应分发 SINGLE_BOT/COOP_GROUP/BBS(BBS bot 认领任务→自算 gap+规划子任务→自执行→上报),这样三模态收敛。
22. 作为 TaskRunner,我想 `query_status(task_id)`/`query_detail(TaskNode)`/`query_result(TaskNode)`/`query_bot_tasks(bot_id)` 查状态/详情/产出/bot 任务列表,这样产品与系统可探活。
23. 作为执行实体(bot workflow/bcn 协作群),我想调 `TaskLoopCallback.start_run(TaskCallbackData)` 上报开始、`report_result(TaskCallbackData)` 上报完成/失败,这样 PUSH 回投有统一协议。
24. 作为框架适配层,我想把 `TaskCallbackData`(loop_task_id/workflow_type/workflow_id/instance_id/result)映射成 `(task_id,node_id)`+`verdict`+`output` 走 `update_task_node_info`,这样回投驱动图谱状态。
25. 作为 TaskHarness,我想旁路常驻周期检测 SLA 超时/崩溃并自愈(经 `update_task_node_info` 复位 `RUNNING→PENDING` 重投,不直接写 HUNG),这样主链不卡死且旁路与主链同写口。
26. 作为系统,我想任务 FAIL 时 Planner 读 graph 内 `acceptance_result.gaps` 自算 gap 重规划、补救拓扑经 `add_task_nodes` 并网(挂该节点下),子全 PASS → 传播该节点 DONE,这样 reroute 闭环(plan 不接收外部 gaps)。
27. 作为系统,我想递归 MISS 到内层深度上限 `MAX_DEPTH` 自动升 BBS(删子树+挂广场),BBS 链路再迭代到 `BBS_MAX_DEPTH` 仍执行不下去变 `HUNG`(STUCK,人介入),这样图不无限膨胀。
28. 作为系统,我想升 BBS 自动(无人工确认挡板,BBS bot 自主认领执行),BBS 仍执行不下去时 STUCK→HUNG 留人工入口,这样长尾能力可被利用且最终有人把关。
29. 作为系统,我想 `loop_round` 仅升 BBS 时递增(外层 BBS 上升轮次)并可在 `extend_props` 带元信息,这样 BBS 迭代可审计。
30. 作为系统,我想 `RuntimeInfo.start_time/end_time` 由图谱流转 RUNNING 时自动维护、存储为**毫秒整数(int)**,这样时间戳与业务解耦。
30b. 作为系统,我想每个**父节点**的重规划产子次数计入其 extend_props.plan_round,达 MAX_PLAN_ROUND(默认 10)→该父 HUNG(不再产子),这样单节点 gap 反复读不闭时不会无限产子撑爆图;loop_round 收敛到只数升 BBS 的总次数(MAX_LOOP)。
31. 作为系统,我想崩溃堆栈/超时标记/miss 事件进 `extend_props`,这样非业务异常态增量合并不污染主字段。
32. 作为开发者,我想对外服务集中在"任务中心"`TaskService` facade(2 API),图谱/规划/派发/执行/Harness 各管各的内部 API,这样模块边界清晰、调用方只认一处入口。
33. 作为开发者,我想图谱原子变更收口在"任务图谱"`TaskGraphService`(8 API:5 核心写/读+3 派生只读),这样状态流转有单一写网关、不散在各执行器。
34. 作为开发者,我想规划(`plan` 产逻辑 Node)与派发(`dispatch` 决定谁做)与执行(`start_run` 真投递)三层分开,这样职责不混。
35. 作为测试,我想以六模块对外契约为断言面,这样行为可回归。
36. 作为迁移,我想 2026-08-04 的 case 推演(存储行业尽调全链路)在新模型上等价跑通,这样行为不丢。
37. 作为人类审批者,我想 BBS 仍执行不下去时 STUCK→HUNG 收到人工入口(升 BBS 已自动无挡板),这样最终人工介入有明确位点。
38. 作为平台,我想任务与组织 OKR 关联可下钻(预留事件位点),这样贡献度对齐目标层次。

## Testing Decisions

### 什么是好测试

只测外部可观测行为。断言面:**任务中心 facade 契约**(`get_task_dashboard` 返回的 `TaskExecutionGraph`:`status`/`loop_round`/`tasks[].status`/`tasks[].run_info.acceptance_result`/`relations`)、**事件日志重放**、**执行主体侧 `query_result`/`query_detail`**。不断言内部 `update_task_node_info` 实现或内存结构。

### 测试 seams(最高 seam 为先)

- **主 seam(单一)**:singlebox 端到端(按新设计搭建 singlebox 编排)。一条 case 跑完 `execute→initialize_graph→plan(委托 decompose 按三阶段 AC 拆)→add_task_nodes(条件 a)→dispatch(决定谁来做:single_bot/coop_group 动态拉群;MISS+depth≥MAX→升 BBS)→start_run→TaskLoopCallback.report_result 回投→任一专题 FAIL+gaps→plan(条件 b)→add_task_nodes(补救子挂该节点下)→二次 PASS→传播治愈→图 status=DONE`。断言 `get_task_dashboard` 终态 + 事件日志可重放 + 经历三模态至少各一(含一次动态拉协作群)。**理想 seam 数=1**。
- **补充 seam(仅 E2E 覆盖不到处)**:
  - 图谱原子变更 seam:`TaskGraphService` 内部契约(状态流转合法性、`add_task_nodes` 条件 a/b/c 触发校验、`relations` 分解树派生 `depth`/`get_child_tasks`/`get_parent_task`、就绪=被add即就绪、`PLANNING` 语义),做契约断言。
  - Harness 自愈 seam:SLA 超时/崩溃→`update_task_node_info(复位 PENDING)` 旁路重投(不写 HUNG/FAILED;STUCK 走编排核升 BBS 链路上限判),独立常驻 seam。
  - 策略/投递 seam(`PlanningStrategy`/`DispatchStrategy`/`DeliveryPort`):引擎自建默认 stub 策略+投递;测试经 `CaseEngine` 子类覆写 `_build_*` 注入 case 策略 adapter(corp 同构;无 verify/bbs market seam)。
  - Planner/Dispatcher 纯逻辑 seam:`plan` 给定图谱产固定 `list[TaskNode]`、`dispatch` 给定 toDo 产派发决策,纯函数式断言(无执行主体)。

> seam 与断言面按新模型(`Relation`/6 态/`run_mode` str)定义;存储行业尽调全链路 case 作为 E2E 行为基线,断言对象按新模型字段。

## Out of Scope

- case 业务行为变更(存储行业尽调全链路、reroute 补做、BBS 接力行为不变,只换模型与模块边界)。
- BCN/BCS 协作层(`manager_worker`/`state_machine`/`chat`、群级 `GroupStrategy`、任务台账)自身改造——本框架经 `TaskRunner` 调用执行主体,BCS 拉群为 `form_coop_group` 内部机制(复用现有 `crates/contracts/bcs-domain`),契约不改。
- 生产部署 / 云上真实搜推(singlebox 用关键词 cover + 本地 bot catalog)。
- 前端画布渲染(只校验 `get_task_dashboard` 投影)。
- 外部 issue tracker 接入(未配置;按 `specs/` 出版)。
- 人工 `abandon_task`/`rollback_to_node` facade(5 模块文档未提供;本 spec 预留事件位点,具体 API 待后续扩展确认后补)。
- 本 spec 给出重构后的契约定义;如需回写个人空间文档另行确认。
- AI-Credit 审计 / OKR 关联产品化(仅在框架预留事件位点)。
- 持久化/ORM 适配(singlebox 用 in-memory `TaskGraphService`;ORM 适配按需后续)。

## Further Notes

- **代码库声明**:代码库按本 spec 从零重新实现。技术设计见 `plan.md`,实现计划见 `tasks.md`。
- **分解树统一承载结构归属**:`Relation{type=DEPENDENCY}` 在 `TaskExecutionGraph.relations` 表分解树(单入:`src_id`=结构父,`dst_id`=结构子)。`TaskNode` 不持 `decomposed_by`/`depends_on`。结构子(`get_child_tasks`)/结构父(`get_parent_task`)/验收/传播读 `relations`;`depth` 从 `relations` 分解树递归。5 文档 `depends_on` 字样语义统一收口到 `relations` 分解树(无单独结构归属字段)。
- **`PLANNING` 语义**:节点被分解、委托子节点执行时进 `PLANNING`(显式状态);`add_task_nodes` 条件 c 直接判 `PLANNING`。子全 PASS → 传播该节点 DONE(直驱 `PLANNING→DONE`);根节点终验时 `PLANNING` 为委托态,owner bot skill 回投 PASS/FAIL → `PLANNING→DONE`(终态图 DONE)/ `PLANNING→FAILED`(acceptance 驱动,根不特殊化,按 gaps 补救子)。
- **`collab_mode`**:`RuntimeInfo` 无 `collab_mode` 字段;协作群协作方式(chat/manager_worker/state_machine)作 `TaskRunner.form_coop_group(GroupFormation)` 内部参数(对齐 BCS `GroupStrategy`),不进模型持久字段。`run_mode` 为 str("single_bot"/"coop_group"/"bbs")。
- **枚举精简**:`TaskSpec` 无 `SLA`(SLA 超时由 Harness 周期巡检 + `execution_config` 承载);`AcceptanceCriteria` 只 `id`/`description`(无 `tag`/`type`);`RuntimeInfo.run_mode` 为 str。
- **`AcceptanceResult` 字段**:`{verdict, acceptances_metric, gaps}`(无 `verifier`);`acceptances_metric`=已满足验收指标明细(原 `acceptances_met` 语义)。
- **`Metadata.task_id` 与 `TaskNode.task_id`**:`Metadata` 持 `task_id`(任务 ID);`TaskNode` 亦持 `task_id`(节点所发整体任务 ID),两者同源,便于节点定位归属图。
- **`TaskExecutionGraph.run_id`**:运行实例唯一 ID,区分同一 task_spec 的多次执行实例。
- **`TaskNode.node_run_graph`**:节点所属的执行图实例引用(模型 `*--` 关系),便于节点反向定位图。
- **驱动模型**:**事件驱动 + 状态条件触发**:`plan`/`add_task_nodes` 有显式状态触发条件(a/b/c + plan 三条件);编排逻辑收口为 `TaskService` 内部编排核(实现细节,不对外暴露,见 `plan.md`)。
- **TaskService facade**:2 API(`execute`/`get_task_dashboard`);`add_task_nodes`/`update_task_node_info`/`query_task_dashboard` 下沉 `TaskGraphService`,`dispatch`/`plan`/`start_run` 各归各模块。
- **MISS 信号设计**:MISS 的节点仍 PENDING(没执行过);用 `RuntimeInfo.extend_props.miss_events: list[str]` 记运行期事件(像崩溃栈),plan 读它自发现补救目标,引擎即写即消费,不跨持久化为状态。比加状态干净。
- **深度闸门**:MISS 拆解前引擎查核内派生深度(从 `relations` 分解树递归),达内层 `execution_config.MAX_DEPTH` → **自动升 BBS**(非 HUNG);BBS 链路 `loop_round` 达 `BBS_MAX_DEPTH` → STUCK → HUNG。
- **reroute 局部化**:补救挂该 FAIL/PLANNING 节点本体下,未触下游在依赖满足前从未入图,无下游可复位;自动 reroute 不调级联回滚。
- **输入/验收上下文**:store 不提供投影 API;执行/验收上下文由 `TaskRunner` 内部用 `get_child_tasks`/`get_parent_task` 组合自算(验收只按 `(task_id,node_id)` 上报节点:有结构子→验收模式聚合结构子(子树)output;无结构子→执行模式聚合结构父 P 的聚合上下文={P.task_spec/goal + P 已 DONE 结构子(本节点兄弟)output};无 NODE/SUBTREE/TASK scope 区分,见 `plan.md` §3.5)。
- **回投坑点**:`output` MERGE 只浅合并一层(patch 覆盖);`extend_props_patch` 扁平传不可再包一层。
- **终态结果契约**:`success` 必须是 JSON bool;PASS=`success=true,gaps=[]`;FAIL=`success=false,gaps` 非空;旧 `fail_detail` 仅兼容归一为单 gap。空内容、非法 JSON、缺 success、类型错误、FAIL 无 gaps 均转 `terminal_result_invalid→Harness`,绝不默认 PASS。
- **结果回收 SLA**:`TaskExecutorResultPoller` 是 worker single_bot/coop_group 业务 SLA 的唯一所有者;超时/连续轮询失败回投 `exec_error` 并 best-effort `cancel_run`;Singlebox worker collector 无独立业务超时。planning/search 的同步 `send_and_wait_async` 仍持各自调用 SLA。
- **Harness 与主链解耦**:Harness 是旁路常驻、只读图谱+反向 `update_task_node_info`,不参与正向规划/派发;主链故障由 Harness 复位后,下一轮事件自然续驱。
- **`loop_round` 审计 + 图级写归属**(外层 BBS 上升轮次):仅升 BBS 时 `TaskExecutionGraph.loop_round++`(正常补救不再 ++);达 `BBS_MAX_DEPTH`(默认 3)→ STUCK → HUNG。
  图级终态(图 `status`=DONE/HUNG、图 `output`、`loop_round`、图 `extend_props` 的 `bbs_mode`/`hung_reason`)由编排核经 `TaskGraphService.update_task_graph_info(TaskGraphPatch)` 图级写口收口(原子、加锁、SSOT 唯一图级写口);`TaskGraphPatch`(增量 patch:`loop_round_increment`/`status`/`output_patch`/`extend_props_patch`)是中间类型。编排核不直写返回的 graph 引用。
  Harness 旁路只**复位** `RUNNING→PENDING` 重投(不写 HUNG/FAILED;STUCK→HUNG 走编排核 on_miss/on_fail 升 BBS 链路上限判)。
- **BBS 上升与执行**:正常链路 MISS 到 `MAX_DEPTH`→自动升 BBS(删 MISS 子树+挂任务广场);BBS bot 认领任务→加载完整上下文(已完成 output+验收 vs task_spec/goal)→自算 gap+自规划子任务(落图 `run_mode="bbs"`)→自执行→上报结果+验收→正常驱动根/子 plan;BBS 链路再迭代到 `BBS_MAX_DEPTH` 仍执行不下去→STUCK→HUNG(人介入)。广场 lease/认领机制属 corp 实现侧。
- **开源执行边界(Avernet vs corp ocb)**:Avernet(开源仓)只发**契约 seam + Noop/singlebox double**(本地关键词 cover 的 bot catalog、BCS local/mock 拉群);真实搜推、真实 Bot/协作群/BBS 执行、LLM 规划/验收 SKILL 均**不在 Avernet**,由 corp `ocb` 仓的 adapter 落地。故 Tasks 中"实现 3 模式执行模块"在 Avernet 范畴 = 落 seam + singlebox double + BCS 复用接线;prod 接线属 corp,非 singlebox 阻塞。
- **验收/BBS 机制(V1/V2)**:验收 100% 走 `on_report` 回投(engine **不主动验**,**无 `OwnerBotVerifyPort`**,owner bot 感知 gap 闭经 `TaskLoopCallback.report_result` 回投 verdict 落态)。BBS 投递归 `TaskRunner` BBS 模态(**无独立 `BbsMarketPort`**;升 BBS 只翻图态 `bbs_mode=True`,实际投递在下次 `dispatch→start_run` 经 runner BBS `DeliveryPort`)。corp 真实投递后端经 ocb 仓 `CorpEngine` 覆写 `_build_runner`+`runner.set_delivery` 注入。
- **协程化(任务执行是耗时任务)**:`on_execute`/`on_report`/`on_miss`/`on_harness`/`start_run`/`form_coop_group`/`report_result`/`DeliveryPort.deliver`/**`plan`/`dispatch`/策略 `matches`/`apply`** 全链路 `async def`(对齐 backend lifecycle async 模式)。`plan`/`dispatch`(corp 为 LLM 规划 / bot catalog 搜推,耗时 IO)在 per-task `threading.RLock` **锁内 `await`**(同 task 串行推进的 IO,设计意图;不同 task 锁隔离互不阻塞);锁内不 `await` 的是高并发外部投递 IO(`start_run`/BCS 拉群 `form_coop_group`/`deliver`),这些 `await` 在锁外。`on_*` 锁内 `async collect`(`await plan/dispatch` + 同步 add/patch)→ 锁外 `_drain` await run/group/miss/finish(投递 IO)。多节点投递并发 `asyncio.gather`+`Semaphore`(`_DELIVER_CONCURRENCY=8`)下沉 `TaskRunner.start_run` 内部(engine 批量调 `start_run`)。`TaskService.execute` 为 async,内部 `await engine.on_execute`。注:`threading.RLock` 在本仓一次性事件循环/跨线程回调模型下跨线程正确串行;corp 单持久 loop 并发同 task 多回投需切 `asyncio.Lock`(ocb 仓定)。单测经 `asyncio.new_event_loop().run_until_complete` 驱动(不用 `@pytest.mark.asyncio`)。
- **策划自驱动(引擎内置,不开放自定义)**:planner(`TaskPlanner`)零参构造,内置策略池 `[WorkflowPlanningStrategy, GapBasedPlanningStrategy]`(first-match-wins by priority,类 SQL optimizer rule-based,据 `execution_config` 动态匹配:config 有 `workflow`→workflow 策略;否则 gap 兜底)。引擎自带能力,不开放外部注入;corp 经 ocb 仓 `CorpEngine` 覆写 `_build_*` 工厂方法替换策略版本(同构 seam)。Avernet 默认 stub(gap 返 [];corp 真实 LLM 规划在 ocb 仓)。dispatcher 同构(`[DirectDispatchStrategy, SearchBasedDispatchStrategy]`,config 有 `bot`→direct;否则 search)。runner 三类投递后端经 `set_delivery` 注入(corp 真实 workflow engine/BCS/BBS 广场)。code 中 `N_overview`/`N_market` 等只能是 case 策略 stub 产出,不写死框架。
- **5 模块文档字段对齐**:5 文档部分字段表述(`depends_on`、`tn.goal` 简写、`asignee` 拼写、`is_plan`、`instance_id` vs `run_id`)以本 spec + `plan.md` 字段为准,文档语义(触发条件/职责/分层)保留。

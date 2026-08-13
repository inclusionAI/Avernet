# Tasks — 任务目标驱动的任务动态规划执行框架

> 案例用权威剧本 `gwqie46v7hzr1w6h` 端到端推演。
> 落点:六模块 Python 实现归 `src/backend`(core transport-agnostic + facade/adapter);动态拉群经 `TaskRunner.form_coop_group` 复用 **BCS(Rust,`crates/contracts/bcs-domain` `GroupStrategy`/`CollaborationRuntimeDefinition`)**。

## 实现原则(对齐 AGENTS.md)
- **契约先行 / transport-agnostic**;delivery adapter 翻译协议不持领域策略;composition root 选实现。
- **协程化(CR 反馈:任务执行是耗时任务)**:`on_execute`/`on_report`/`on_miss`/`on_harness`/`start_run`/`form_coop_group`/`report_result`/`DeliveryPort.deliver`/**`plan`/`dispatch`/策略 `matches`/`apply`** 全链路 `async def`;`plan`/`dispatch`(corp LLM/catalog 耗时 IO)在 per-task `threading.RLock` 锁内 `await`(同 task 串行,设计意图;不同 task 锁隔离);锁内不 `await` 的是高并发外部投递 IO(`start_run`/拉群/`deliver`),这些 `await` 在锁外;`on_*` 锁内 async collect(`await plan/dispatch`+同步 add/patch)→ 锁外 `_drain` await run/group/miss/finish;多节点投递 gather+Semaphore(`_DELIVER_CONCURRENCY=8`)下沉 `TaskRunner.start_run` 内部;`threading.RLock` 适用本仓一次性事件循环/跨线程回调模型(corp 单持久 loop 并发同 task 需切 `asyncio.Lock`);单测经 `asyncio.new_event_loop().run_until_complete` 驱动(不用 `@pytest.mark.asyncio`)。
- **类型契约**:必填端到端非可选;`T|None` 仅当 `None` 是合法域态或外部输入边界。
- **TDD / 主 seam 优先**:P8 singlebox E2E 最高 seam;P1–P7 契约单测补 E2E 覆盖不到的分支。
- **写网关收口**:图谱原子变更只走 `TaskGraphService` 8 API(5 核心写/读 + 3 派生只读);旁路同写口。
- **框架零 case 知识**:任何具体节点名(`N_overview`/`N_market` 等)只允许出现在 case 的策略 stub 产出或测试 stub,**禁止出现在框架代码**。
- **不并存两套**:合并为单一实现,删除并行包与旧模型失效代码。

## 依赖与落地序(M0 模型 → M1 图谱 → M2 编排核 → M3 planner → M4 dispatcher/runner → M5 harness/facade → M6 singlebox E2E)

```
M0  领域模型(Relation/6态/瘦身后字段) + 中间类型(patch/criteria/op_result/callback_data)
 → M1  TaskGraphService 独立(8 API:5 核心写/读+3 派生只读;relations 依赖派生)
 → M2  编排核 on_*(事件驱动 + 状态条件 a/b/c + plan 三条件;串行锁)
 → M3  TaskPlanner 编排壳 + 内置策略池(零参,去硬编码)
 → M4  TaskDispatcher(搜推4态+form_coop_group)+ TaskRunner(start_run/query_*/TaskLoopCallback;BCS复用)
 → M5  TaskHarness 旁路 + TaskService facade 2 API
 → M6  singlebox E2E(gwqie46v7hzr1w6h 三阶段三模态)
```
M2 依赖 M1/M3/M4 接口(可 seam/double);M5 集成;M6 验证。

---

## M0 — 领域模型(先落模型)
- T0.1 新 `models.py`:枚举 `Status` 6 态(PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG)、`AcceptanceVerdict`、`RelationType`(DEPENDENCY)。
- T0.2 规格面 dataclass:`Metadata`(task_id/title/instruction)、`Context`(background/extend_props)、`AcceptanceCriteria`(id/description)、`Goal`、`TaskSpec`(无 SLA)、`TaskInfo`。
- T0.3 运行态 dataclass:`AcceptanceResult`(verdict/acceptances_metric/gaps,无 verifier)、`RuntimeInfo`(run_mode:str/assignee/start_time/end_time/output/acceptance_result/extend_props,无 collab_mode)、`Relation`(src_id=结构父/dst_id=结构子/type=DEPENDENCY/extend_props;分解树单入)、`TaskNode`(node_id/task_id/status/task_spec/run_info/node_run_graph;**无 decomposed_by/depends_on**;结构归属由 relations 派生)、`TaskExecutionGraph`(run_id/loop_round/status/output/tasks/relations/extend_props)。
- T0.4 中间类型:`TaskNodePatch`、`TaskNodeQueryCriteria`、`TaskOpResult`、`NodeOpResult`、`TaskCallbackData`(loop_task_id/workflow_type/workflow_id/instance_id/result)。
- T0.5 派生规则注释(分解树统一):① 结构子(`get_child_tasks`)与结构父(`get_parent_task`)均从 `relations` 分解树(src→dst 单入)派生;② `depth` 从 relations 递归;③ 传播读结构子(`get_child_tasks`),就绪=被 add 即就绪(无 `dependencies_satisfied` 闸门);④ 数据流由步进式批规划+结构父聚合上下文承载,无跨兄弟数据边。
- ✅ 验收:模型与最新 classDiagram 1:1;`grep` 无 `depends_on`/`decomposed_by`/`collab_mode`/`SLA`/`Scope`/`RunMode`/`CollabMode`/`verifier`/`NodeRuntimePatch`/`TaskGraphInfo` 残留;TaskNode 无 `decomposed_by` 字段(结构归属由 relations 分解树派生)。

## M1 — TaskGraphService(独立图谱 SSOT,8 API:5 核心写/读+3 派生只读)
- T1.1 `TaskGraphService.initialize_graph(task_info) -> TaskExecutionGraph`:建图(run_id 分配,根 PENDING);幂等冲突。
- T1.2 `add_task_nodes(tasks, parent_node_id) -> TaskExecutionGraph`:并子图(**显式传父** `parent_node_id`,方案 C);DEPENDENCY 关系写入 `graph.relations`(src=parent_node_id,dst=新子,单入);父节点进 PLANNING(`_DELEGATABLE_PARENT={PENDING,FAILED,PLANNING}`);单层同构硬约束(本批前序依赖仅指向已存节点,本批内不互依);触发条件 a/b/c 校验(编排核调前判,store 双检)。
- T1.3 `update_task_node_info(patch) -> NodeOpResult`:节点级原子写;两张状态机——`_ACCEPTANCE_TRANSITIONS`(`RUNNING→{DONE,FAILED}`、`PLANNING→{DONE,FAILED}` 根终验)与 `_DIRECT_TRANSITIONS`(`PENDING→RUNNING` 派发、`RUNNING→PENDING` Harness 复位、`PLANNING→DONE` 传播);acceptance 驱动 PASS→DONE / FAIL+gaps→FAILED(验收 skill 强制要求给 gaps,不存在 FAIL 无 gaps);status 直驱派发写 run_mode/assignee+RUNNING;幂等。
- T1.4 `update_task_graph_info(task_id, patch: TaskGraphPatch) -> TaskExecutionGraph`:图级原子写口(SSOT 唯一图级写网关);收口图级终态:`loop_round_increment` 原子加(升 BBS)、`status` 置图终态(`DONE` 根终验 PASS/`HUNG` STUCK)、`output_patch` 浅合并、`extend_props_patch` 浅合并(`bbs_mode`/`hung_reason`);加锁;增量 patch 未给不动。编排核升 BBS/根终验完成一律经此方法,不直写 graph 引用。
- T1.5 `query_task_dashboard(task_id, node_id=None) -> TaskExecutionGraph`:只读看板(整图/子树投影)。
- T1.6 派生查询(只读):升公开 `query_task_nodes`/`get_child_tasks`/`get_parent_task`;保持内部 `_node_depth`/`_execution_config`(内层 `MAX_DEPTH`+外层 `BBS_MAX_DEPTH`);均从 `relations` 分解树派生;就绪扫描 criteria={status=PENDING}。
- T1.7 `remove_subtree(task_id,node_id)->TaskExecutionGraph`:删节点+其下整个子树(升 BBS 清理;前提:xx_node 下所有子都 MISS、没走 RUNNING)。
- T1.8 单测:状态流转合法性(两状态机)、`update_task_graph_info` 图级写(loop_round 原子加 / status DONE/HUNG / output_patch+extend_props_patch 浅合并 / 未给字段不动 / 增量多次累积 / task not found)、add a/b/c 条件触发校验、relations 分解树派生(`get_child_tasks`/`get_parent_task`/`depth`)、PLANNING 语义(含根终验 `PLANNING→FAILED`)、传播规则、单层同构护栏。

## M2 — 编排核 on_*(事件驱动 + 状态条件)
- T2.1 `async ExecutionEngine.on_execute(task_id)`:initialize_graph 后,条件 a(根 PENDING)→ plan→add→dispatch→start_run。
- T2.2 `async on_report(patch: TaskNodePatch)`:patch 内含 (task_id,node_id)+acceptance_result+output_patch;update_task_node_info 翻态;PASS→传播→条件 c(PLANNING 父∧兄弟全DONE)→plan→add→dispatch;FAIL+gaps→闸门→条件 b→plan→add→dispatch(FAIL 无 gaps 已消灭)。返回 NodeOpResult 供 ack。
- T2.3 `async on_miss(patch: TaskNodePatch)`:patch.extend_props_patch.miss_events 由 dispatcher 填;深度闸门→<MAX: plan→add(挂该节点下)→消费→dispatch;≥MAX(MISS 深度达 `MAX_DEPTH`):**自动升 BBS**——remove_subtree(删 xx_node+子树)+loop_round+++标 BBS+标 `bbs_mode=True`(V2:无 BbsMarketPort;BBS 投递归 runner BBS 模态);BBS bot 认领→自算 gap+规划子任务(run_mode=bbs)→上报→on_report 驱动;BBS 链路 loop_round≥`BBS_MAX_DEPTH`→STUCK→经 `update_task_graph_info(TaskGraphPatch{status=HUNG, extend_props_patch={hung_reason:stuck}})` 收口 graph HUNG→人介入。
- T2.4 `on_harness(patch: TaskNodePatch)`:旁路——Harness 复位 `RUNNING→PENDING`(update_task_node_info 直驱)+ 重新 `_dispatch_and_run` 重投;不直接写 HUNG(STUCK 走 on_miss/on_fail 升 BBS 链路上限判);不抢正向驱动。
- T2.5 串行化:同 task_id 可重入锁;跨 task 并行。loop_round:仅升 BBS 时 graph.loop_round++(外层 BBS 上升轮次,正常补救不再 ++)。
- T2.6 根验收(V1:验收 100% 回投,engine 不主动验):`plan(root)==[]` ∧ 全非根 DONE ∧ 无 RUNNING → 根保持 PLANNING 等 owner bot 经 `TaskLoopCallback.report_result` 回投 verdict(无 `OwnerBotVerifyPort`,engine 不主动触发终验 skill;owner bot 感知 gap 闭经回投落态)。PASS→root DONE+经 `update_task_graph_info(TaskGraphPatch{status=DONE, output_patch=…})` 收口图 status=DONE;FAIL+gaps→plan(root) 补救子(根不特殊化),继续驱动。**框架代码不识别"终验节点"**,终验=根节点验收,由 owner bot skill 回投。
- T2.7 零 case 知识红线:framework 代码 `grep -rE 'N_overview|N_market|N_aggregate|N_verify|N_report|N_practice|dim_|n_root' src/agentclaw/community/core/task/` 必须 0 命中(节点名仅存 case 策略 stub 产出/测试)。
- T2.x 单测:on_execute 首帧、on_report PASS/FAIL/根终验两分支(PASS/FAIL+gaps)、on_miss 升 BBS(自动无人工挡板)、on_harness 复位重投不抢正向、串行化、loop_round 仅升 BBS 递增、零 case grep。

## M3 — TaskPlanner 编排壳 + 内置策略池(零参)
- T3.1 `PlanningStrategy` Protocol:`matches(graph)->bool` + `apply(graph)->list[TaskNode]`(引擎内置,first-match-wins;默认 `WorkflowPlanningStrategy`/`GapBasedPlanningStrategy`;status=PENDING,run_info 空,task_id 已填,node_run_graph 指向图;返回 [] 表 gap 已闭)。Avernet stub(gap 返 [];workflow 读 config 拓扑 stub);corp ocb 仓覆写 `_build_planner` 注入真实策略。
- T3.2 `async TaskPlanner.plan(graph)->list[TaskNode]`:**触发条件**(图谱有更新 ∧ 无 RUNNING 节点 ∧ 有 PLANNING 节点);不满足返回 []。读图自发现目标(FAIL 叶子/PLANNING 父),委托 decompose(graph)(seam 自负责 target-finding);规划原则硬约束(派发/执行中节点不可改含前序依赖;只对失败+子全DONE自身PLANNING父);纯读图去重;步进式 deps 满足才产。**删除**写死节点。
- T3.2a 零 case 知识:`TaskPlanner`/`ExecutionEngine` 不得出现任何节点名字面量或"终验节点"启发式(如按入图顺序判终验);终验=根节点验收(§5.4/T2.6),由 owner bot skill 回投,框架不识别特殊节点。
- T3.3 `GapBasedPlanningStrategy`/`WorkflowPlanningStrategy`(`PlanningStrategy`)引擎内置策略池(first-match-wins,零参 TaskPlanner)。
- T3.4 默认实现:Avernet `StubDecomposer`(测试注入 case 节点);corp `PlanBotDecomposer`(plan_bot agent/LLM SKILL,Avernet 不含红线)。
- T3.x 单测:注入 StubDecomposer 断言机制(触发条件/去重/步进/硬契约);decompose 返回 [] → plan [];换 stub 产别结构 → 框架照常驱动。

## M4 — TaskDispatcher + TaskRunner(执行模块,按 `lxg2mwgmtfqg6d95`)
- **M4a TaskDispatcher + 内置策略池(零参)**
  - T4.1 `DispatchStrategy` Protocol:`matches(node,graph)->bool` + `apply(node,graph)->SearchResult`(4 态 HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS/MISS;HIT_MULTI_BOTS 填 GroupFormation 内部参数不持久)。默认 `DirectDispatchStrategy`(config 有 `bot`→HIT_SINGLE)/`SearchBasedDispatchStrategy`(兜底;Avernet stub 恒 MISS;corp ocb 仓覆写注入真实 catalog)。
  - T4.2 `TaskDispatcher`(不持 graph);`dispatch(toDoTaskList)->list[TaskNode]`(对齐派发文档签名):无 graph 入参、**不写图、不起 run**;search→把 run_mode(str)/assignee 填到 `TaskNode.run_info` 上返回;HIT_MULTI_BOTS→`runner.form_coop_group` 得 gid 填 node;MISS→不填执行者(仍 None),标 `run_info.extend_props.miss_events` 交编排核。编排核拿返回节点→有 assignee 的 `graph.update_task_node_info(run_mode/assignee,RUNNING)` 落库 + `runner.start_run`;标 miss_events 的走 `on_miss`。
  - T4.3 `SearchBasedDispatchStrategy`/`DirectDispatchStrategy`(`DispatchStrategy`)引擎内置策略池(first-match-wins,零参 TaskDispatcher)。
  - T4a.x 单测:四态填 TaskNode.run_info(HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS 填 run_mode/assignee、MISS 不填标 miss_events)、collab_mode 来自 search(内部)、dispatcher 不写图不起 run、编排核落库后 DISPATCHED 必 RUNNING、前序依赖双检、start_run 由编排核触发(批量)、MISS 节点 status 仍 PENDING。
- **M4b TaskRunner + TaskLoopCallback**
  - T4.4 `async TaskRunner.start_run(toDoTaskList)->list[bool]`:批量;协程化——真实投递(单 bot workflow/BCS/BBS 广场)是网络 IO,内部 `asyncio.gather`+`_DELIVER_CONCURRENCY`(Semaphore=8)并发投递(对齐 backend lifecycle),`await` 不阻塞编排核;按 run_mode(str)自适应投递 single_bot/coop_group/bbs(BBS bot 认领任务→自算 gap+规划子任务→自执行);返回每派发是否成功。`form_coop_group` 同 async(BCS 建群 IO)。
  - T4.5 `TaskRunner.query_status(task_id)->Status` / `query_detail(TaskNode)->TaskNode` / `query_result(TaskNode)->TaskNode` / `query_bot_tasks(bot_id)->list[TaskNode]`。
  - T4.6 `TaskRunner.form_coop_group(GroupFormation)->group_id`:(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS(`group_strategy=collab_mode`;state_machine 注入 workflow yaml);群自闭环持 `SubDagRef(bcs_run_id)`。Avernet BCS local/mock;prod wiring 属 corp。
  - T4.7 `TaskLoopCallback`:PUSH 回投;`TaskCallbackData{loop_task_id,workflow_type,workflow_id,instance_id,result}`;`async start_run(data)`(进度)/`async report_result(data)`(完成/失败;协程化——`await` 编排核 `on_report` 不阻塞回投调用方);适配层把 data 组装成 TaskNodePatch(task_id/node_id 从 loop_task_id 映射;acceptance_result 从 success/data;output_patch=fold data;fail_detail→extend_props_patch)→ 编排核 on_report(patch)。
  - T4.8 上下文组装:`start_run` 内部 `_build_context(task_id,node)` 用 `get_child_tasks`/`get_parent_task` 组合自动判定——有结构子(`get_child_tasks` 非空)→验收模式聚合结构子(子树)DONE output+node.goal;无结构子→执行模式取结构父 P=`get_parent_task`,聚合 P 的聚合上下文={P.task_spec/goal + P 已DONE结构子(本节点兄弟)output}+本节点 task_spec。无 NODE/SUBTREE/TASK scope 入参,验收只按 (task_id,node_id) 上报节点;数据流经结构父中转,无跨兄弟数据边。
  - T4b.x 单测:start_run 三 run_mode 分发(含 BBS bot 认领自规划自执行)、query_*回填、TaskLoopCallback.report_result→on_report 适配映射、form_coop_group BCS local/mock 契约、BBS bot 上报+驱动计划、_build_context 验收/执行双模式自动切换。

## M5 — TaskHarness + TaskService facade
- T5.1 `TaskHarness.run_poll_loop`:周期 `query_task_nodes(RUNNING)`→比对 start_time + sla_timeout(execution_config/extend_props)→超时/崩溃 `update_task_node_info(复位 PENDING, extend_props_patch={崩溃栈/超时})` 重投;不抢正向(STUCK 走 on_miss 升 BBS 链路上限判)。
- T5.2 `TaskService` facade 2 API:`execute(task_info)->TaskOpResult`(initialize_graph + `engine.on_execute`;若注入 harness 则 `harness.register(task_id)`;返回含 run_id)/`get_task_dashboard(task_id,node_id=None)->TaskExecutionGraph`(query_task_dashboard)。另暴露只读属性 `callback`(TaskLoopCallback)与 `engine`。内部持编排核 + TaskGraphService + Planner + Dispatcher + Runner + Harness(+ TaskRunner/TaskLoopCallback)。
- T5.3 `TaskService.__init__` 零参 facade:签名 `(graph, harness=None)`;`_build_engine()` 工厂方法自建 `ExecutionEngine(graph)`(零参自建 planner/dispatcher/runner 内置策略池+stub 投递);回填 `harness.set_on_harness(engine.on_harness)`;构造 `TaskLoopCallback(CallbackAdapter(), engine)`。strategy/delivery 经 engine `_build_*` 注入(测试/corp 经子类覆写;无 verify/bbs market port)。engine 对调用方不可见(无 engine property)。
- T5.4 transport adapter:core transport-agnostic;context-boundary;API 版本化与 conformance(`docs/arch/protocol-contract-tests.md`)。
- T5.x 单测:facade 2 API 契约、harness 旁路不抢正向、组合根装配。

## M6 — E2E singlebox(主 seam,理想 seam 数=1)
- T6.1 singlebox 编排:模型+六模块全接;in-memory `TaskGraphService`;`StubDecomposer`(注入 case 节点);`StubBotDiscover`(本地 catalog 关键词 cover);`form_coop_group` BCS local/mock。
- T6.2 用权威案例剧本 `gwqie46v7hzr1w6h` 存储行业尽调(三阶段三模态)端到端跑完:`execute→initialize_graph→on_execute(条件a)→plan(decompose(graph) 按三阶段 AC 拆 N_overview/四专题/N_practice_bbs/N_report)→add_task_nodes(根→PLANNING,relations 登记)→dispatch(决定谁来做:single_bot/coop_group 动态拉群 manager_worker;MISS+depth≥MAX→升 BBS)→start_run→TaskLoopCallback.report_result 回投→任一专题 FAIL+gaps→on_report(条件b)→plan(补救挂该节点下,该节点→PLANNING)→二次 PASS→传播治愈→图 status=DONE`。注入一次 MISS+depth 达 MAX→自动升 BBS(remove_subtree)+loop_round+++BBS bot 认领执行+上报;再注入 BBS loop_round≥BBS_MAX_DEPTH→STUCK→HUNG(人介入)。
- T6.3 断言面:`get_task_dashboard` 终态(`TaskExecutionGraph`:status/loop_round/tasks[].status/acceptance_result/relations)+ 事件日志可重放 + `query_result`/`query_detail`。
- T6.4 singlebox 覆盖脚本对齐仓库 ci(`scripts/ci/singlebox_coverage*`),与 PR CI 同一基线。

## Cross-cutting
- 架构宪法(`docs/arch/arch.rules.md`)、CI gates(`docs/arch/ci.enforce.md`)、依赖边界、forbidden transport in core、config schema、PR checklist。
- 事件日志/审计位点(`loop_round`、`miss_events`、`run_id`、BBS lease)。

## Risks / 待实现期定的项
- 旧 `core/task` ORM repo 与图模型适配:in-memory 优先,ORM 适配按需(M1)。
- `form_coop_group` prod BCS wiring(M4b,corp)。
- `PlanningStrategy`/`DispatchStrategy` 生产实现(真实 LLM 规划/搜推 catalog,corp ocb 仓覆写 `_build_*`);Avernet 只发 stub/singlebox。
- 线上真实搜推(M4a,corp)。
- 人工 `abandon_task`/`rollback_to_node` facade(5 模块文档未提供;预留 `on_harness`/人工事件位点,待确认后补)。
- `relations` 分解树(单入)下 `get_child_tasks`/`get_parent_task`/`depth`/就绪派生正确性(M1 单测锚定)。
- `run_id` 生成策略(M1:递增 or UUID;singlebox 用递增)。
- `PLANNING` 传播治愈语义(FAILED+补救子全PASS→DONE)需 M2 单测锚定(确认"非DONE含FAILED可治愈")。

## 里程碑(M0–M6 已实现并 push;分支 `feat/task-goal-driven-collab-dev`)
- ✅ **M0** 领域模型(先落模型)。
- ✅ **M1** TaskGraphService 独立(8 API:5 核心写/读+3 派生只读+relations 派生)。
- ✅ **M2** 编排核 on_*(事件驱动 + 状态条件 a/b/c + plan 三条件)。
- ✅ **M3** TaskPlanner 编排壳 + 内置策略池(零参,去硬编码)。
- ✅ **M4** Dispatcher(搜推4态+form_coop_group)+ TaskRunner(start_run/query_*/TaskLoopCallback;BCS复用),singlebox double。
- ✅ **M5** Harness + TaskService facade 2 API 集成。
- ✅ **M6** singlebox E2E,行为基线对齐 `gwqie46v7hzr1w6h`(机制不变,内容来自 stub decomposer)。

> 全 task 单测 121 passed(graph34/planner8/dispatch7/runner28/harness9/center28/e2e7);零 case 红线 0 命中;pre-push Backend SAST gate 通过。

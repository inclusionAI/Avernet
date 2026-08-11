# Tasks — 任务目标驱动的任务动态规划执行框架

> 案例用权威剧本 `gwqie46v7hzr1w6h` 端到端推演。
> 落点:六模块 Python 实现归 `src/backend`(core transport-agnostic + facade/adapter);动态拉群经 `TaskRunner.form_coop_group` 复用 **BCS(Rust,`crates/contracts/bcs-domain` `GroupStrategy`/`CollaborationRuntimeDefinition`)**。

## 实现原则(对齐 AGENTS.md)
- **契约先行 / transport-agnostic**;delivery adapter 翻译协议不持领域策略;composition root 选实现。
- **类型契约**:必填端到端非可选;`T|None` 仅当 `None` 是合法域态或外部输入边界。
- **TDD / 主 seam 优先**:P8 singlebox E2E 最高 seam;P1–P7 契约单测补 E2E 覆盖不到的分支。
- **写网关收口**:图谱原子变更只走 `TaskGraphService` 7 API(4 核心写/读 + 3 派生只读);旁路同写口。
- **框架零 case 知识**:任何具体节点名(`N_overview`/`N_market` 等)只允许出现在 case 的 DecomposerPort 产出或测试 stub,**禁止出现在框架代码**。
- **不并存两套**:合并为单一实现,删除并行包与旧模型失效代码。

## 依赖与落地序(M0 模型 → M1 图谱 → M2 编排核 → M3 planner → M4 dispatcher/runner → M5 harness/facade → M6 singlebox E2E)

```
M0  领域模型(Relation/6态/瘦身后字段) + 中间类型(patch/criteria/op_result/callback_data)
 → M1  TaskGraphService 独立(7 API:4 核心+3 派生只读;relations 依赖派生)
 → M2  编排核 on_*(事件驱动 + 状态条件 a/b/c + plan 三条件;串行锁)
 → M3  TaskPlanner 编排壳 + DecomposerPort 委托(去硬编码)
 → M4  TaskDispatcher(搜推4态+form_coop_group)+ TaskRunner(start_run/query_*/TaskLoopCallback;BCS复用)
 → M5  TaskHarness 旁路 + TaskService facade 2 API
 → M6  singlebox E2E(gwqie46v7hzr1w6h 三阶段三模态)
```
M2 依赖 M1/M3/M4 接口(可 seam/double);M5 集成;M6 验证。

---

## M0 — 领域模型(先落模型)
- T0.1 新 `models.py`:枚举 `Status` 6 态(PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG)、`AcceptanceVerdict`、`RelationType`(DEPENDENCY)。
- T0.2 规格面 dataclass:`Metadata`(task_id/title/instruction)、`Context`(background/extend_props)、`AcceptanceCriteria`(id/type/description)、`Goal`、`TaskSpec`(无 SLA)、`TaskInfo`。
- T0.3 运行态 dataclass:`AcceptanceResult`(verdict/acceptances_metric/gaps,无 verifier)、`RuntimeInfo`(run_mode:str/assignee/start_time/end_time/output/acceptance_result/extend_props,无 collab_mode)、`Relation`(src_id/dst_id/type/extend_props)、`TaskNode`(node_id/task_id/status/task_spec/run_info/node_run_graph/**`decomposed_by`(str|None,根=None)**,无 depends_on)、`TaskExecutionGraph`(run_id/loop_round/status/output/tasks/relations/extend_props)。
- T0.4 中间类型:`TaskNodePatch`、`TaskNodeQueryCriteria`、`TaskOpResult`、`NodeOpResult`、`TaskCallbackData`(loop_task_id/workflow_type/workflow_id/instance_id/result)。
- T0.5 派生规则注释(B1 解耦):① 结构子(decompose_children)从 `TaskNode.decomposed_by` 查;② dependencies_satisfied/depth(数据维度)/compute_parent_tasks 从 `relations.type=DEPENDENCY` 派生;③ 传播读结构子,就绪读数据依赖。
- ✅ 验收:模型与最新 classDiagram 1:1;`grep` 无 `depends_on`/`collab_mode`/`SLA`/`Scope`/`RunMode`/`CollabMode`/`verifier`/`NodeRuntimePatch`/`TaskGraphInfo` 残留;TaskNode 含 `decomposed_by` 字段(根=None)。

## M1 — TaskGraphService(独立图谱 SSOT,7 API:4 核心+3 派生只读)
- T1.1 `TaskGraphService.initialize_graph(task_info) -> TaskExecutionGraph`:建图(run_id 分配,根 PENDING);幂等冲突。
- T1.2 `add_task_nodes(tasks) -> TaskExecutionGraph`:并子图;DEPENDENCY 关系写入 `graph.relations`;父节点进 PLANNING;单层同构硬约束(本批前序依赖仅指向已存节点,本批内不互依);触发条件 a/b/c 校验(编排核调前判,store 双检)。
- T1.3 `update_task_node_info(patch) -> NodeOpResult`:节点级原子写;唯一翻态依据=patch.acceptance_result(PASS→DONE/FAIL+gaps→FAILED/FAIL无gaps→HUNG/STUCK→HUNG);派发写 run_mode/assignee+RUNNING;幂等。
- T1.4 `query_task_dashboard(task_id, node_id=None) -> TaskExecutionGraph`:只读看板(整图/子树投影)。
- T1.5 派生查询(只读):升公开 `query_task_nodes`/`decompose_children_tasks`/`compute_parent_tasks`;保持内部 `_node_depth`/`_execution_config`(仅编排核用)。均从 `relations` 派生。
- T1.x 单测:状态流转合法性、add a/b/c 条件触发校验、relations 依赖派生(deps_satisfied/depth)、PLANNING 语义、传播规则、单层同构护栏。

## M2 — 编排核 on_*(事件驱动 + 状态条件)
- T2.1 `ExecutionEngine.on_execute(task_id)`:initialize_graph 后,条件 a(根 PENDING)→ plan→add→dispatch→start_run。
- T2.2 `on_report(task_id,node_id,output_patch,acceptance_result)`:update_task_node_info 翻态;PASS→传播→条件 c(PLANNING 父∧前序全DONE)→plan→add→dispatch;FAIL+gaps→闸门→条件 b→plan→add→dispatch;FAIL无gaps/STUCK→HUNG。
- T2.3 `on_miss(task_id,node_id)`:写 miss_events→深度闸门→plan→add→消费→dispatch;depth≥MAX→HUNG。
- T2.4 `on_harness(task_id,node_id,patch)`:旁路 update_task_node_info(HUNG/FAILED),不抢正向。
- T2.5 串行化:同 task_id 可重入锁;跨 task 并行。loop_round:reroute 补救非根节点时 graph.loop_round++(graph 持久字段)。
- T2.6 根终验(主动验证):`plan(root)==[]` ∧ 全非根 DONE ∧ 无 RUNNING → 经 source_channel 触发 owner bot 终验 skill(验 root.goal 全 AC,验收模式聚合 root 结构子=全图 DONE 产出)→ on_report(root,verdict);PASS→root DONE+graph DONE;FAIL+gaps→plan(root) 补救子(根不特殊化);FAIL无gaps→root HUNG。**框架代码不识别"终验节点"**,终验=根节点验收,由 owner bot skill 回投。
- T2.7 零 case 知识红线:framework 代码 `grep -rE 'N_overview|N_market|N_aggregate|N_verify|N_report|N_practice|dim_|n_root' src/agentclaw/community/core/task/` 必须 0 命中(节点名仅存 singlebox stub DecomposerPort 产出/测试)。
- T2.x 单测:on_execute 首帧、on_report PASS/FAIL/根终验三分支、on_miss 闸门、on_harness 不抢正向、串行化、loop_round 递增、零 case grep。

## M3 — TaskPlanner 编排壳 + DecomposerPort 委托
- T3.1 `DecomposerPort` Protocol:`decompose(node,graph)->list[TaskNode]`(产下一步可执行子节点;status=PENDING,run_info 空,task_id 已填,node_run_graph 指向图;可返回 [] 表不可再分)。
- T3.2 `TaskPlanner.plan(graph)->list[TaskNode]`:**触发条件**(图谱有更新 ∧ 无 RUNNING 节点 ∧ 有 PLANNING 节点);不满足返回 []。读图自发现目标(FAIL 叶子/PLANNING 父),委托 decompose;规划原则硬约束(派发/执行中节点不可改含前序依赖;只对失败+子全DONE自身PLANNING父);纯读图去重;步进式 deps 满足才产。**删除**写死节点。
- T3.2a 零 case 知识:`TaskPlanner`/`ExecutionEngine` 不得出现任何节点名字面量或"终验节点"启发式(如按入图顺序判终验);终验=根节点验收(§5.4/T2.6),由 owner bot skill 回投,框架不识别特殊节点。
- T3.3 `GapBasedPlanningRule`(`OptimizerRule`)承载编排壳,委托 `DecomposerPort`。
- T3.4 默认实现:Avernet `StubDecomposer`(测试注入 case 节点);corp `PlanBotDecomposer`(plan_bot agent/LLM SKILL,Avernet 不含红线)。
- T3.x 单测:注入 StubDecomposer 断言机制(触发条件/去重/步进/硬契约);decompose 返回 [] → plan [];换 stub 产别结构 → 框架照常驱动。

## M4 — TaskDispatcher + TaskRunner(执行模块,按 `lxg2mwgmtfqg6d95`)
- **M4a TaskDispatcher + BotDiscoverPort**
  - T4.1 `BotDiscoverPort.search(node,graph)->SearchResult`:4 态 HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS/MISS;HIT_MULTI_BOTS 一并决出 collab_mode(填 GroupFormation,内部参数不持久)。Avernet `StubBotDiscover`(本地关键词 cover + bot catalog)。
  - T4.2 `TaskDispatcher`(不持 graph);`dispatch(to_do_list)->list[DispatchOutcome]`:无 graph 入参、**不写图、不起 run**;search→产 DispatchOutcome(run_mode str/assignee 推荐);HIT_MULTI_BOTS→`runner.form_coop_group` 得 gid 填 outcome;MISS→DispatchOutcome(miss)交编排核。编排核拿 outcome→`graph.update_task_node_info(run_mode/assignee,RUNNING)` 落库 + `runner.start_run`。
  - T4.3 `SearchBasedDispatchRule`(`OptimizerRule`)委托 `BotDiscoverPort`+`TaskRunner`。
  - T4a.x 单测:四 outcome 产出(DispatchOutcome 含 run_mode/assignee)、MISS 不决策、collab_mode 来自 search(内部)、dispatcher 不写图不起 run、编排核落库后 DISPATCHED 必 RUNNING、前序依赖双检、start_run 由编排核触发(批量)。
- **M4b TaskRunner + TaskLoopCallback**
  - T4.4 `TaskRunner.start_run(toDoTaskList)->list[bool]`:批量;按 run_mode(str)自适应投递 single_bot/coop_group/bbs(BBS 仅挂悬赏,认领执行 bot 自主);返回每派发是否成功。
  - T4.5 `TaskRunner.query_status(task_id)->Status` / `query_detail(TaskNode)->TaskNode` / `query_result(TaskNode)->TaskNode` / `query_bot_tasks(bot_id)->list[TaskNode]`。
  - T4.6 `TaskRunner.form_coop_group(GroupFormation)->group_id`:(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS(`group_strategy=collab_mode`;state_machine 注入 workflow yaml);群自闭环持 `SubDagRef(bcs_run_id)`。Avernet BCS local/mock;prod wiring 属 corp。
  - T4.7 `TaskLoopCallback`:PUSH 回投;`TaskCallbackData{loop_task_id,workflow_type,workflow_id,instance_id,result}`;`start_run(data)`(进度)/`report_result(data)`(完成/失败);适配层映射 loop_task_id→(task_id,node_id)、result.success→verdict、result.data→output、result.fail_detail→extend_props → 编排核 on_report。
  - T4.8 上下文组装:`start_run` 内部 `_build_context(task_id,node)` 用 `compute_parent_tasks`/`decompose_children_tasks`(升为公开)组合自动判定——有结构子→验收模式聚合结构子(子树)DONE output+goal;无结构子→执行模式聚合上游 DEPENDENCY 父 output。无 NODE/SUBTREE/TASK scope 入参,验收只按 (task_id,node_id) 上报节点。
  - T4b.x 单测:start_run 三 run_mode 分发(含 BBS 只挂悬赏)、query_*回填、TaskLoopCallback.report_result→on_report 适配映射、form_coop_group BCS local/mock 契约、BBS 自主回投用例、_build_context 验收/执行双模式自动切换。

## M5 — TaskHarness + TaskService facade
- T5.1 `TaskHarness.run_poll_loop`:周期 `query_task_nodes(RUNNING)`→比对 start_time + sla_timeout(execution_config/extend_props)→超时/崩溃 `update_task_node_info(HUNG/FAILED, extend_props_patch)`;不抢正向。
- T5.2 `TaskService` facade 2 API:`execute(task_info)->TaskOpResult`(initialize_graph + on_execute;返回含 run_id)/`get_task_dashboard(task_id,node_id=None)->TaskExecutionGraph`(query_task_dashboard)。内部持编排核 + TaskGraphService + Planner + Dispatcher + Runner + Harness。
- T5.3 `TaskService.__init__` 组合根:注入 decomposer/discover/runner_ctor/harness;Avernet 用 stub/singlebox double,corp adapter 红线。
- T5.4 transport adapter:core transport-agnostic;context-boundary;API 版本化与 conformance(`docs/arch/protocol-contract-tests.md`)。
- T5.x 单测:facade 2 API 契约、harness 旁路不抢正向、组合根装配。

## M6 — E2E singlebox(主 seam,理想 seam 数=1)
- T6.1 singlebox 编排:模型+六模块全接;in-memory `TaskGraphService`;`StubDecomposer`(注入 case 节点);`StubBotDiscover`(本地 catalog 关键词 cover);`form_coop_group` BCS local/mock。
- T6.2 用权威案例剧本 `gwqie46v7hzr1w6h` 存储行业尽调(三阶段三模态)端到端跑完:`execute→initialize_graph→on_execute(条件a)→plan(decompose 按三阶段 AC 拆 N_overview/四专题/N_practice_bbs/N_report)→add_task_nodes(根→PLANNING,relations 登记)→dispatch(决定谁来做:single_bot/coop_group 动态拉群 manager_worker/bbs)→start_run→TaskLoopCallback.report_result 回投→任一专题 FAIL+gaps→on_report(条件b)→plan(补救挂该节点下,该节点→PLANNING)→二次 PASS→传播治愈→图 status=DONE`。注入一次 STUCK→HUNG→人工确认→BBS 自主认领接力。
- T6.3 断言面:`get_task_dashboard` 终态(`TaskExecutionGraph`:status/loop_round/tasks[].status/acceptance_result/relations)+ 事件日志可重放 + `query_result`/`query_detail`。
- T6.4 singlebox 覆盖脚本对齐仓库 ci(`scripts/ci/singlebox_coverage*`),与 PR CI 同一基线。

## Cross-cutting
- 架构宪法(`docs/arch/arch.rules.md`)、CI gates(`docs/arch/ci.enforce.md`)、依赖边界、forbidden transport in core、config schema、PR checklist。
- 事件日志/审计位点(`loop_round`、`miss_events`、`run_id`、BBS lease)。

## Risks / 待实现期定的项
- 旧 `core/task` ORM repo 与图模型适配:in-memory 优先,ORM 适配按需(M1)。
- `form_coop_group` prod BCS wiring(M4b,corp)。
- `DecomposerPort` 生产实现(plan_bot agent/LLM SKILL,corp);Avernet 只发 stub/singlebox。
- 线上真实搜推(M4a,corp)。
- 人工 `abandon_task`/`rollback_to_node` facade(5 模块文档未提供;预留 `on_harness`/人工事件位点,待确认后补)。
- `relations` 在汇聚/多入边下派生正确性(M1 单测锚定)。
- `run_id` 生成策略(M1:递增 or UUID;singlebox 用递增)。
- `PLANNING` 传播治愈语义(FAILED+补救子全PASS→DONE)需 M2 单测锚定(确认"非DONE含FAILED可治愈")。

## 里程碑
- ⬜ **M0** 领域模型(先落模型)。
- ⬜ **M1** TaskGraphService 独立(7 API:4 核心+3 派生只读+relations 派生)。
- ⬜ **M2** 编排核 on_*(事件驱动 + 状态条件 a/b/c + plan 三条件)。
- ⬜ **M3** TaskPlanner 编排壳 + DecomposerPort 委托(去硬编码)。
- ⬜ **M4** Dispatcher(搜推4态+form_coop_group)+ TaskRunner(start_run/query_*/TaskLoopCallback;BCS复用),singlebox double。
- ⬜ **M5** Harness + TaskService facade 2 API 集成。
- ⬜ **M6** singlebox E2E,行为基线对齐 `gwqie46v7hzr1w6h`(机制不变,内容来自 stub decomposer)。

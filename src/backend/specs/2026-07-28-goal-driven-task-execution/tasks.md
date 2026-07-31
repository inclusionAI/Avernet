# 目标驱动任务执行 loop — 实施任务清单(tasks.md)

> 配套 `spec.md`(WHAT/WHY)/ `plan.md`(HOW)。本文给可勾任务,按 phase 分模块,每 PR TDD,顺序实现(上一功能确认后再做下一个)。
> 任务状态机 7 态 / 节点 6 态定义见 `2026-07-30-task-status-state-machine-alignment/`;Plan 是 `Task.plan` 聚合根字段(非 `spec.plan`);Port 契约落 `core/task/protocols.py`,api 仅 2 个 Service API Protocol。这些口径下方任务直接使用,不再重复说明。
> 落点:ocb backend 四层单向依赖;禁裸 SQL;rebase 工作流;不混格式化噪声。
> 日期:2026-07-28。

> **勾选状态**:以 git 历史为准。Phase 0/1/2(主体)/3(主体)/4.1/4.5.3 已落地并补勾;其余 [ ] 未落或部分落,标注见各条。

---

## 0. 顶层实现顺序(强约束)

1. **先开源后内部**:**Phase 0~5 全在 Avernet 开源仓(community)**,含 community impl 的 Port + ExecutionPort(httpx 调开源 engine/BCS)+ structured 验收 + bbs_executor + 真实开源 engine(R6)/BCS(B5/B6/B2)接入。开源版完整可跑、5 case 全绿、等确认后,**才进 Phase 6 corp**。**Phase 4.5(前端副屏画布)在 Phase 4 之后、与 Phase 5 可并行**,属 Avernet 开源仓。
2. **corp(ocb 仓,`src/backend/src/agentclaw/corp`)只在 Phase 6**:CorpTaskModule override + bcsfuse httpx adapter(P1 可选)+ corp-only 鉴权路由 + corp profile DI。corp 不写业务/Repo/状态机(community 统一 Repository 一份 body 经 `ZdasDB.orm_session()` 跑 ZDAS)。
3. **分阶段分模块 PR**:每 phase = 1 个或多个 PR;每 PR 一个 `dev_*` 分支 + rebase + Conventional commit。
4. **每 PR TDD**:先红测试再绿;phase 末单测 + 契约(Rule 25)+ 架构(Rule 22/Boundary/Protocol conformance)+ 集成(tmp_path SQLite)全绿才算完成。
5. **顺序确认**:一 phase 完成确认后,再进下一 phase;一功能确认后,再实现下一个功能。

## 1. 贯穿规矩(所有 phase)

- 源根:community = `ocb-public/src/backend/src/agentclaw/community`;corp = `src/backend/src/agentclaw/corp`。PEP 420 `agentclaw` 命名空间聚合。
- 四层:`api/`(Service API Protocol-only,typing.Protocol,无运行时 import core)/ `core/task/`(domain + services + protocols + repository)/ `plugin_api/`(基础设施 Protocol,复用既有,不新加)/ `plugins/`(impl)。
- 新模块 checklist(backend README):schemas → Service API Protocol(api)+ Port 契约(core/task/protocols.py)→ local/community impl → core services → dependencies → router 挂载 → 测试 → `core/task/README.md` Context Boundary(Rule 22)→ `tests/architecture/test_module_boundaries.py` 加 `task` → Port 契约 `tests/contracts/test_task_*.py`(Rule 25)+ 从 `test_protocol_contracts.py` EXEMPT 移除 → Noop/Mock(Rule 21)。
- 三类测试 marker:`@pytest.mark.unit`(默认)/ `integration`(真实 SQLite tmp_path)/ `e2e`(完整栈);corp 侧 `requires_mosn`/`requires_zdas` 跳过。
- 不裸 SQL(走 ORM `orm_session()`);不落 ecb。
- 双仓红线:① 蚂蚁中间件 prod adapter 不进 Avernet(Protocol 在 community `plugin_api/`,corp `plugins/prod/` 已有,task 只 `Injected`);② skill/算法代码(8 SKILL prompt + LLM understand/judge/select_collab)不进 task 模块,归 skill_center/engine。

---

## Phase 0 — 领域模型 + API 交互接口骨架(首 PR,开源)【Avernet】

> 目标:领域模型对象 + 执行流程 API 端点骨架(Noop impl),router→Noop 跑通,架构/契约测试绿。**无业务逻辑、无持久化、无 Scheduler 真实逻辑**。

- [x] **0.1** `core/task/domain/models.py`:Task(含 spec+plan+execution_graph)/TaskSpec(不含 plan)/Plan/TaskExecutionGraph/Node(含 sub_dag)/Edge/AttemptedRecord/ProgressNode/SubDagRef(dataclass)+ enums(**TaskStatus 7 态** DRAFTING/DEFINED/EXECUTING/REVIEWING/DONE/CANCELLED/FAILED / **NodeStatus 6 态** PENDING/RUNNING/DONE/FAILED/SKIPPED/HUMAN_REQUIRED / GraphStatus/EdgeKind/RunMode/CollabMode/WatchdogAction/RouteClass/AttemptTrigger/AttemptOutcome)+ AcceptanceCriteria 多态袋 + `Node.sub_dag: Optional[SubDagRef]`。**TDD**:`tests/community/core/task/domain/test_models.py`。已落。
- [x] **0.2** `core/task/domain/state_machine.py`:TaskStateMachine(VALID_TRANSITIONS **7 态**;§2.2 合法迁移 + `EXECUTING→EXECUTING` 自环 + 终态 DONE/CANCELLED/FAILED 不可转 + `REVIEWING→EXECUTING` 返工)+ NodeStatus 转移合法性。**TDD**:`test_state_machine.py`。已落。
- [x] **0.3** `core/task/domain/events.py`:TaskEvent/EventKind(含 `GOAL_VERIFIED`/`GOAL_REJECTED`;`HUNG` deprecated 无 writer,保留以读历史日志)+ `reported` 标志 + `occurred_at`(从 `gmt_create` 回填)+ `next_seq` 单调。**TDD**:`test_events.py`。已落。
- [x] **0.4** Port + DTO 契约落 **`core/task/protocols.py`**(非 api):`TaskService`/`TaskScheduler`(core 内部 Protocol)+ `DecomposerPort`/`BotDiscoverPort`/`TaskDriverPort`/`ExecutionPort`(含 `probe`)/`BbsExecutor`/`PanelEventPublisher`/`PanelDeliveryPort`/`BcsCollaborationProtocol` + DTO(BotCandidate/RouteRecommendation/DispatchResult/PanelMessage);`domain/repository.py` 持 `TaskNotFoundError`;Repo Protocol 由 `plugins/task_repository.py`/`task_event_repository.py` 实现(append 内部单 writer 分配 seq=get_latest_seq+1)。**TDD**:`test_protocol_contracts.py`(`runtime_checkable` 结构)。已落。
- [x] **0.5** `api/task/service_api.py` → `TaskServiceProtocol` / `TaskSchedulerProtocol`(**仅 2 个 Service API Protocol**,loose `*args,**kwargs->Any`,router/DI 绑定键,api 不 import core;structural conformance 由 `test_task_service_api_conformance.py` 校验)。Port 契约不进 api(四层规矩:core+plugins 不可 import api)。`BcsCollaborationProtocol`/`AcceptanceChecker`(deferred)等 Port 全在 `core/task/protocols.py`。**TDD**:`test_task_service_api_conformance.py`。已落。
- [x] **0.6** `adapters/http/task/schemas.py`:Pydantic 请求/响应(CreateTask/AmendTask/FinalizePlan/EventReport/TaskCreated/TaskDetail/TaskProgress/TaskList)+ 副屏可视化 schemas(plan §1.4b):`TaskGraphView`/`TaskNodeView`/`TaskEdgeView`/`TaskNodeDetailView`/`SubDagRefView` + **`TaskEventItem`/`TaskHistoryResponse`**(GET /history trace,字段按 §1.3b 对照表超集)。**TDD**:`test_schemas.py`。已落。
- [x] **0.7** `adapters/http/task/router.py`:APIRouter(`prefix=/api/tasks`)端点:POST `` ` `/amend` `/plan` `/approve` `/tick` `/events`;GET `/{id}` `/progress` `/graph` `/nodes/{nid}` `/nodes/{nid}/sub-dag` `/history`;WS `/graph/stream`。`Injected(TaskServiceProtocol/TaskSchedulerProtocol)`;body 只调 Protocol 方法,无业务逻辑。**TDD**:`test_router.py`(TestClient + Noop injector override;断言路径/状态码/依赖注入/不裸 SQL/副屏端点挂载/ /history)。已落。
- [x] **0.8** `plugins/local/task.py` + `plugins/community/task/__init__.py`:Noop/Mock impl(NoopTaskService/NoopBotDiscoverPort/NoopDecomposerPort/NoopTaskDriverPort/NoopExecutionPort[含 probe]/NoopBcsCollaborationPort[mock SM graph] + local_executor doubles;继承 Port 契约)。**TDD**:`test_noop_impl.py`(结构 + Rule 21 flavor + bcs_collab mock 返回结构)。已落。
- [x] **0.9** DI 注册 + 挂载:`di/modules/task_module.py`(Base TaskModule)+ `testing_task_module.py` + `infrastructure/community/task.py`(CommunityTaskModule 绑 Noop/community impl;BotDiscover/ExecutionPort 注入真实)+ `di/container.py` base list 追加 `TaskModule()` + `di/profile_modules.py` 三列 + `adapters/http/app.py` `include_router(task_router)` + `core/task/README.md` Context Boundary + `tests/architecture/test_module_boundaries.py` 加 `task` + 契约 `tests/contracts/test_task_*.py` + 从 `test_protocol_contracts.py` EXEMPT 移除 task_*。**TDD**:架构测试绿。已落。
- [x] **0.10** 桩端到端 smoke:TestClient `POST /api/tasks`→Noop;`GET /api/tasks/{id}`;`POST /events`。**TDD**:`test_task_endpoints_smoke.py`。已落。

> **Phase 0 完成**:领域模型 + Port 契约 + 2 Service API Protocol + 端点骨架 + Noop + 注册 + 架构/契约/桩测试全绿。

---

## Phase 1 — 持久化层(2 Repo + 3 表 + 统一 Repository)【Avernet】

- [x] **1.1** `core/task/repository/models.py`:ORM `ac_task`(含 `plan_json` 列)/`ac_task_event`/`ac_task_execution_graph` 挂 community `core/base.py:Base`;`AutoIncrementBigInteger.with_variant(Integer,"sqlite")`;索引;`ac_task_event` append-only(只 `gmt_create`);`ac_task_execution_graph.graph Text` + `version Int`。**TDD**:`test_orm_models.py`。已落。
- [x] **1.2** `core/task/sql/{ac_task,ac_task_event,ac_task_execution_graph}.sql`:prod DDL 参考(OceanBase/MySQL,运维手动 provision)。已落。
- [x] **1.3** `plugins/task_repository.py` + `plugins/task_event_repository.py`:统一 ORM Repo(`orm_session()`;event append-only;seq 单 writer `get_latest_seq+1`;`claim_node` CAS `PENDING→RUNNING+assignee`;`occurred_at` 从 `gmt_create` 回填)。**TDD**:`tests/community/plugins/test_task_repository.py`(integration 真实 SQLite)。已落。
- [x] **1.4** `plugins/local/database.py` `bootstrap()` 追加 side-effect `import agentclaw.community.core.task.repository.models`。**TDD**:启动后 3 表存在。已落。
- [x] **1.5** TaskModule/CommunityTaskModule/TestingTaskModule 绑 2 Repo→Protocol(单例)。**TDD**:`container.resolve(TaskRepo/TaskEventRepo)` 非空。已落。
- [x] **1.6** router `/events` handler 接 `TaskEventRepo.append`(真实持久化)+ `GET /history` 读出。**TDD**:`POST /events`→落库→`GET /tasks/{id}/history` 读出。已落。

> **Phase 1 完成**:持久化层 + 统一 Repo + 3 表 + seq/claim CAS 测试绿。

---

## Phase 2 — TaskService(定义/状态/查询/on_event)【Avernet】

- [x] **2.1** `task_service.py` 定义组:`create`(new Task(plan=None)+ `init_root_phase(DRAFTING)` + emit TASK_CREATED + ★ 发 `PanelMessage` 触发副屏弹出,FR-OBS-11,经 PanelEventPublisher+PanelDeliveryPort carrier)/`amend`(任务留 DRAFTING,R2)/`finalize_plan`(`task.plan=plan` + `advance(DEFINED)` + emit PLAN_FINALIZED)/`approve`(委派 `TaskScheduler.start`)。**TDD**:`test_task_service_def.py`。已落。
- [x] **2.2** TaskService 状态组:`init_root_phase`/`advance_phase`(state_machine guard)/`spawn_build_dag`(plan→骨架 Node/Edge)/`set_node_status`/`append_attempted`/`add_sibling_node`/`spawn_sub_dag`(写 `SubDagRef` 引用,不跟踪 child 态,§1.3a)/`mark_graph_status`/`mark_terminal`/`claim_node`(CAS)。**TDD**:`test_task_service_state.py`。已落。
- [x] **2.3** TaskService `on_event`:`_apply_event`(guard+落态 set Node 态 + append attempted)+ `GOAL_VERIFIED`→`_apply_goal_verdict`(PASS→`mark_graph(VERIFIED)+advance(DONE)`;编排 FAIL→`mark_graph(AWAITING_HUMAN_ACCEPT)+emit HUMAN_REQUIRED`;BBS FAIL→`advance(FAILED)`)+ 编排链路转 `Scheduler.on_event`+ BBS `return`。**TDD**:`test_on_event.py`。已落。
- [x] **2.4** TaskService 查询组:`get`/`list_by_user`/`progress`/`get_execution_graph`(BBS 认领读用)/`get_task_history(after_seq)`/`get_task_graph`/`get_node_detail`/`get_sub_dag`(协作群下钻:读 SubDagRef → SmGraphAdapter 实时映射;非协作群或无引用→404/skip)/`subscribe_task_graph`(WS 增量)。**TDD**:`test_query.py`。已落。
- [x] **2.5** router 接 TaskService 真实(替换 Phase 0 Noop override),含副屏端点接查询组真实。**TDD**:真实 CRUD 端到端 + 状态机 guard 生效 + graph 端点返回 TaskGraphView 真实快照。已落。
- [ ] **2.6** CorpTaskModule 桩:corp profile 下 TaskService 仍 community 统一(corp 不重写;只占位确保 resolve)。**TDD**:corp profile resolve TaskService。未落(Phase 6)。

> **Phase 2 完成**(2.1~2.5):TaskService 合一(定义/状态/查询/on_event)+ invariant guard + 录入期推进闭环 + create 发面板消息 + /history trace。

---

## Phase 3 — TaskScheduler 编排(决策 + tick/on_event 骨架)【Avernet】

- [ ] **3.1** `task_scheduler.py` 私有决策:`_route`(C1~C5 纯规则,attempted 降权 P10)/`_select_collab`(规则版,confidence<0.7 降级 `manager_worker`)/`_compute_gap`(纯规则 need_reroute/need_split,带 atomic/recompose_count 终止性)。**TDD**:`test_scheduler_decisions.py`。部分落(`watchdog` 已落;三个决策方法待确认独立签名)。
- [x] **3.2** `Scheduler.start`(approve 委派):`advance(DEFINED→EXECUTING)` + `spawn_build_dag` + `mark_graph(ON_PLAZA)` + `enqueue tick`。**TDD**:`test_start.py`。已落。
- [x] **3.3** `Scheduler.tick`:`root_phase==EXECUTING` + 拓扑序解锁(PENDING/replan 后 FAILED(acceptance_result=fail),前置 DONE/SKIPPED)+ recommend/dispatch + `set_node_status(RUNNING)` + `all_done`→`advance(REVIEWING)`+emit 触发判断完成 SKILL + 终止性。`watchdog(node)` 集成(WAIT/PROBE/REDRIVE/ESCALATE,§6.5)。**TDD**:`test_tick.py`。已落。
- [x] **3.4** `Scheduler.on_event`:`NODE_REJECTED`→`gap`→reroute(enqueue tick)/split(`add_sibling`);`NODE_FAILED`→R7 同 executor 重试 max_attempts(默认 2)→转重路由。**TDD**:`test_scheduler_on_event.py`。已落。
- [x] **3.5** router `/tick` `/approve` 接 Scheduler 真实。**TDD**:approve→start→tick 触发。已落。

> **Phase 3 完成**(3.2~3.5):Scheduler 编排骨架 + tick/on_event 流转 + 终止性 + watchdog。3.1 决策方法待补独立签名。

---

## Phase 4 — 执行 loop(deepresearch ①② + Port impl + 终验)【Avernet】

- [x] **4.1** `bot_discover_service.py`(BotDiscoverPort community impl)+ `bot_catalog.py`(`BotCatalogPort`,默认 singlebox 5-bot 舰队):规则 recommend + cover 计算 + attempted 不排除(P10 降权在 `_route`)。**TDD**:`test_bot_discover.py`。已落。
- [ ] **4.2** `execution_port_client.py`(ExecutionPort community impl):`dispatch_single_bot`(httpx 调开源 engine,R6)/`coop_group`(httpx 调开源 BCS,B5)/`redispatch_node`(B5 同群重派,P8)/`probe`(watchdog 探活,§6.5)/`bbs`(接 bbs_executor,Phase 5)。**TDD**:`test_execution_port.py`(httpx Mock)。部分落(桩 + local_executor doubles;真实 httpx 待 R6/B5)。
- [ ] **4.2a** `bcs_collaboration_service.py`(BcsCollaborationProtocol community httpx):`fetch_state_machine_run_graph`/`fetch_node_detail`(调本地开源 BCS;只读查询面)。**TDD**:`test_bcs_collab_httpx.py`。未落(local mock 已供联调)。
- [ ] **4.2b** `graph_adapter.py`(`SmGraphAdapter`):纯映射;`fetch_run_graph` → `to_sub_dag_view(snap)` 按 §1.3b 对照表把 SM graph 映射成 `TaskGraphView` 子树 + §1.3c 状态映射。被 `TaskService.get_sub_dag` 调。**TDD**:`test_sm_graph_adapter.py`。未落。
- [ ] **4.3** `decomposer_service.py`(DecomposerPort community impl):规则拆解 BFS + dedup 0.92 + confidence(<0.7 打回)。**TDD**:`test_decomposer.py`。未落。
- [ ] **4.4** `workflow_compiler.py`:compile_to_state_machine/compile_to_workflow_pack/import_workflow_to_graph/select_collab_mode(规则版)。**TDD**:`test_workflow_compiler.py`。未落。
- [ ] **4.5** deepresearch ① §5.2:cover<100%→`is_atomic`/`recompose_count>2` 判定→`add_sibling_node`+父 SKIPPED / `set FAILED(acceptance_result=fail)` 留待终验。接 `Scheduler.tick`。**TDD**:`test_decompose_loop.py`。未落。
- [ ] **4.6** deepresearch ② §5.3:验收 fail→`_apply_event` 落态→`Scheduler.on_event` gap→reroute/split + `loop_round` 终止。**TDD**:`test_reroute_loop.py`。未落。
- [ ] **4.7** `acceptance_checker.py`(AcceptanceChecker structured 双轨 impl:StructuredRunner+EffectsChecker;LLM judge 归 owner-bot SKILL,community 不持 prompt):作为 owner-bot SKILL 调用工具,backend 暴露工具 Protocol/端点。**TDD**:`test_acceptance_structured.py`。未落(deferred)。
- [ ] **4.8** 终验触发:`tick all_done`→`advance(REVIEWING)`+`emit 触发判断完成 SKILL`(owner-bot 回投 `GOAL_VERIFIED`)→`on_event._apply_goal_verdict`。**TDD**:`test_goal_verify.py`(PASS→DONE / 编排 FAIL→BBS 门 emit HUMAN_REQUIRED)。未落。
- [ ] **4.9** 端到端 Case A(单 bot happy)+ Case D(搜推 fail 拆解)绿。**TDD**:`test_e2e_case_a_d.py`(integration,Mock engine/BCS httpx)。未落。
- [ ] **4.9a** TaskService.get_sub_dag 接真实 `SmGraphAdapter`:读 SubDagRef → adapter.fetch_run_graph → to_sub_dag_view 返回 live TaskGraphView 子树。**TDD**:`test_get_sub_dag_live.py`。未落。

> **Phase 4 完成**:开源执行 loop 闭环(Case A/D)+ Port community impl + deepresearch 两 loop + 终验触发 + 终止性 + 副屏后端可视化(SmGraphAdapter + BcsCollaborationProtocol httpx + get_sub_dag live)。

---

## Phase 4.5 — 副屏动态 workflow 画布(前端,专项)【Avernet】

> 目标:新建独立动态 workflow 画布(参考现有 `bcsPanel/StateMachineRunView` 实现扩展),落地 spec FR-OBS-01~11。分工置(plan §1.4b):**任务入口页**副屏展示任务整体执行流程(顶层动态 DAG);**协作群页**副屏维持现有 `bcsPanel/StateMachineRunView`(该群子 DAG,本 phase 不改)。前置:Phase 0~4 backend API(`GET /tasks/{id}/graph` 等)就绪。前端落点依赖 plan §9 开放问题 5 拍板,本 phase 给基准实现(任务入口页内嵌新画布 + 复用 `@aix-chat/ui` ChatLayout/UmdPanel 机制)。

- [ ] **4.5.1** 任务入口页骨架:`src/frontend/src/pages/TaskLoop/`(路由 `/task-loop/:taskId`)+ 任务创建入口组件(提需求 → `POST /api/tasks`)。复用现有 ChatLayout.Panel 副屏 slot。**TDD/手测**:页面渲染 + 路由 + 副屏 slot 存在。
- [ ] **4.5.2** 新建 `TaskWorkflowView` 画布组件(参考 `StateMachineRunView` 结构:graph 轮询 + 节点/边渲染 + 节点详情弹窗),消费 `GET /tasks/{id}/graph`(TaskGraphView);按 run_mode/collab_mode 渲染模态标签;状态色映射对齐 §1.3c。**TDD/手测**:Mock TaskGraphView 渲染节点/边/状态色/模态标签。
- [x] **4.5.3** 面板消息触发副屏弹出(FR-OBS-11):backend `create` 发 `<AixUI panel component=taskPanel.TaskWorkflowView params={taskId}>` 消息 → 前端命中 → `chatBridge.openPanelTab` → ChatLayout.openTab 自动展开。已落。
  - Carrier transport:`PanelDeliveryPort` Protocol(core.task.protocols)+ `EventBusPanelPublisher.publish` 预格式化 `<AixUI panel>` content + `TaskPanelEvent`(content+session_id)+ `TaskPanelCarrier`(Lifecycle 参与者,`startup()` 订阅 EventBus → delivery port,吞异常不阻塞 create)+ `NoopPanelDelivery`(community 默认)/`RecordingPanelDelivery`(test)。DI:`CommunityTaskModule.panel_delivery_port` + `task_panel_carrier` 单例 provider;lifespan 自动发现并 install。
  - 前端收敛:`openTaskPanel(taskId, title?)` helper(`TaskPanel.tsx`),community profile 由创建流直接命中 `chatBridge.openPanelTab` 弹出副屏;`TaskLoop/index.tsx` `handleCreate` 创建成功即调用。无 chat 布局时静默降级。
  - 边界契约:`panel_publisher.py` import `core.task.protocols`(plugins 不可 import api);`plugins/local/README.md` `internal_dependencies` 追加 `core.task.repository.models`。测试:`test_panel_carrier.py` + `test_task_module_wiring.py` 全绿。
- [ ] **4.5.4** 节点详情(FR-OBS-04):点节点 → `GET /tasks/{id}/nodes/{nid}`(TaskNodeDetailView)→ 弹窗展示 artifacts/acceptance_result/attempted_executors/properties.error_msg 等。**TDD/手测**:点节点弹窗内容。
- [ ] **4.5.5** 下钻协作群节点 = 跨页导航(FR-OBS-03/10,plan §1.3a 主交互):任务页点协作群节点 → 跳转该协作群页(`/group/:groupId`)→ 该页副屏自然展示群内 sub-DAG(现有 `bcsPanel/StateMachineRunView`,不改)。可选:`get_sub_dag` 端点做任务页内只读预览悬浮卡(实时映射,P1)。**TDD/手测**。
- [ ] **4.5.6** 增量 + 轮询(FR-OBS-07):WS `/tasks/{id}/graph/stream` 增量刷新为主,画布轻量轮询 `GET /tasks/{id}/graph` 兜底。**TDD/手测**。
- [ ] **4.5.7** 信息 cover 验证(FR-OBS-05/AC-12):对照 §1.3b 表,在任务页画布 + 协作群页画布分别验证 state_machine 画布全字段可见;自定义协作群作为"workflow 固化特例"在协作群页画布无缝展示。**TDD/手测**:对照表 checklist 逐项过。

> **Phase 4.5 完成**:副屏动态 workflow 画布(任务入口页整体 DAG + 创建即弹出 + 节点详情 + 下钻跨页 + 增量/轮询 + 信息 cover)。与 Phase 5 可并行。

---

## Phase 5 — BBS(bbs_executor + 共享黑板)【Avernet】

- [ ] **5.1** `bbs_executor.py`(BbsExecutor Port impl):广场/认领/续做。**共享黑板 = `TaskExecutionGraph`**(plan §2.3):BBS bot 经 `TaskService.get_execution_graph` 读图谱认领未完成节点 → `claim_node`(CAS,状态组)→ 自主执行 → 回投 → `TaskService.on_event(run_mode=BBS)` 经状态组改 Node 态。bbs_executor 只管广场/认领/续做执行细节,不持状态、不 bypass 状态组。**TDD**:`test_bbs_executor.py`。
- [ ] **5.2** `claim_node` CAS(状态组)+ BBS 回投(run_mode=BBS,经 `POST /events` → `on_event`,on_event 内 BBS 分支 return 不转 Scheduler)+ BBS 内 FAILED(acceptance_result=fail)一次性重放认领(P9,1 轮,别的 BBS bot 可接)+ 不进 deepresearch 重路由。**TDD**:`test_bbs_claim_replay.py`。
- [ ] **5.3** `escalate_to_bbs`:人工门(emit `HUMAN_REQUIRED`)→用户确认 True→`dispatch_bbs`→BBS 终验 `GOAL_VERIFIED`(run_mode=BBS)→`_apply_goal_verdict`(DONE/FAILED);拒绝→`cancel`(P12)。**TDD**:`test_escalate_bbs.py`。
- [ ] **5.4** 端到端 Case B(coop state_machine)+ Case E(终验 fail→BBS)+ Case C(验收 fail 重路由)绿。**TDD**:`test_e2e_case_b_c_e.py`。

> **Phase 5 完成**:BBS 内部续做 + 两链路统一 + 5 case 全绿。Avernet 开源版完整可跑,等确认 → Phase 6 corp。

---

## Phase 6 — corp 适配层(teamclaw/ocb 仓)【仅此 phase 进 corp】

> 前提:Phase 0~5 开源版全绿并确认。corp 不重写业务/Repo/状态机;只把 community Noop Port override 成 corp 真实 impl,加 corp-only 鉴权/中间件。

- [ ] **6.1** `corp/di/modules/infrastructure/corp/task.py`(CorpTaskModule):把 Port 的 Noop override 成 corp impl(`@provider` explicit,B8);`corp_column()` + `test_corp_reuse_column()` 追加。**TDD**:`test_corp_task_module.py`。
- [ ] **6.2** `corp/core/task/services/bcsfuse_{decomposer,bot_discover,acceptance_judge}_service.py`:bcsfuse httpx adapter(R1/R2/R3,P1 可选;LLM prompt 在 bcsfuse 侧)。**TDD**:`test_bcsfuse_adapters.py`(P1 可后置)。
- [ ] **6.3** corp ExecutionPort(仅当派活需经 ARCA/MOSN 时):prod dispatch 经注入 prod Baas/Device Protocol;否则复用 community httpx。**TDD**:corp integration(`requires_mosn`,跳过 CI)。
- [ ] **6.4** `corp/adapters/http/task/`(可选 corp-only 路由,若需内部鉴权)+ `CorpAppServicesModule` 经 `OptionalRouters` 注入。**TDD**:corp 鉴权 endpoint。
- [ ] **6.5** engine R6 / BCS B5·B6·B2 真实接入(corp profile 下 httpx 走真实域名/MOSN)。**TDD**:integration(`requires_mosn`/`requires_services`)。
- [ ] **6.6** NotifySender 触达(钉钉卡片,corp `dingtalk_notify_sender` 已有):关键节点/交付/人工门。**TDD**:`test_notify_corp.py`(`requires_mosn`)。
- [ ] **6.7** prod 配置:`corp/configs/application-{prod,pre}.yaml` `task_loop` 块(bcsfuse base_url 等);ZDAS 3 表 DDL 手动 provision(`core/task/sql/*.sql`)。**TDD**:配置加载 + ZDAS 表存在性(`requires_zdas`)。
- [ ] **6.8** 文档收尾:README index 加 spec 目录;`plan.md §9` 开放问题逐项闭环或转 follow-up;变更记录补 tasks/implement 节。

> **Phase 6 完成**:corp 适配层 + prod 配置 + 真实跨模块接入 + 文档收尾。全 6 phase 完成 = 系统交付。

---

## 验收标准(AC,tasks 阶段)

| ID | 验收 | 验证 |
|---|---|---|
| AC-T-01 | Phase 0 单 PR 可合(领域模型 + API 接口骨架),全绿,无业务/持久化依赖 | 测试 + 审查 |
| AC-T-02 | 每 phase TDD(先红后绿),契约 + 架构测试绿 | CI |
| AC-T-03 | 顺序:一 phase 确认后才进下一 phase;Avernet(0~5)全绿后才进 corp(6) | review gate |
| AC-T-04 | 落点:community 先行全量;corp 仅 Phase 6 适配,零业务/Repo/状态机 | 架构测试 |
| AC-T-05 | 5 case 端到端绿(A/D 于 Phase 4;B/C/E 于 Phase 5) | e2e |
| AC-T-06 | 不裸 SQL;四层单向;无 ecb;三类 marker 正确 | 架构 + 审查 |
| AC-T-07 | 双仓红线守:中间件 adapter / skill 算法代码 不进 Avernet | 审查 |
| AC-T-08 | 副屏可视化(FR-OBS-01~11):Phase 0 SubDagRef+graph API+Port;Phase 2 查询组+面板消息触发;Phase 4 SmGraphAdapter+httpx+get_sub_dag live;Phase 4.5 前端画布(整体 DAG/创建即弹出/节点详情/下钻跨页/信息 cover)全落地 | 审查 + 手测 |
| AC-T-09 | AC-12 信息对照表(state_machine 画布字段 → 任务图谱字段)由 SmGraphAdapter 逐字段映射 + Phase 4.5.7 对照 checklist 验证 | 单测 + 手测 |

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-07-28 | 栖真 | 初版 tasks:6 phase 分模块 TDD;首 PR=Phase 0 领域模型+API 接口;开源(0~5)先行,corp(6)后置;顺序确认 |
| 2026-07-29 | 栖真 | 副屏可视化任务穿插:Phase 0 加 SubDagRef + BcsCollaborationProtocol + 副屏 schemas + graph/nodes/sub-dag/stream 端点;Phase 2 加 create 发面板消息 + spawn_sub_dag 写引用 + 副屏查询方法;Phase 4 加 SmGraphAdapter + httpx + get_sub_dag live;新增 Phase 4.5 前端副屏画布 |
| 2026-07-31 | 栖真 | 对齐代码现状:状态机 7 态 / Plan 在 Task.plan / Port 落 core/task/protocols.py(api 2 Protocol)/ prefix=/api/tasks + GET /history / watchdog(§6.5);Phase 0/1/2(2.1~2.5)/3(3.2~3.5)/4.1/4.5.3 补勾;3.1 决策方法、4.2~4.9/4.9a/5/6 标注未落或部分;清洗为当前态 |

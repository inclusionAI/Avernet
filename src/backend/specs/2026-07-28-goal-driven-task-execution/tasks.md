# 目标驱动任务执行 loop — 实施任务清单(tasks.md)

> 配套 `spec.md`(WHAT/WHY)/ `plan.md`(HOW)。本文给可勾任务,按 phase 分模块,每 PR TDD,顺序实现(上一功能确认后再做下一个)。
> **2026-07-29 修订**:对接 spec FR-OBS-10/11 + plan §1.3a/1.3b/1.4b —— 副屏动态 workflow 可视化任务穿插进 Phase 0/2/4,前端画布单列 Phase 4.5(backend API 先行,前端依赖 §10 开放问题 6 拍板)。
> 落点:ocb backend 四层单向依赖 + aicoding 模板;禁裸 SQL;rebase 工作流;不混格式化噪声。
> 日期:2026-07-28。

---

## 0. 顶层实现顺序(强约束)

1. **先开源后内部**:**Phase 0~5 全在 Avernet 开源仓(community,`ocb-public/src/backend/src/agentclaw/community`)**,含 community impl 的 4 Port + ExecutionPort(httpx 调开源 engine/BCS)+ structured 验收 + bbs_executor + 真实开源 engine(R6)/BCS(B5/B6/B2)接入。开源版完整可跑、5 case 全绿、等确认后,**才进 Phase 6 corp**。**Phase 4.5(前端副屏画布)在 Phase 4 之后、与 Phase 5 可并行**,属 Avernet 开源仓。
2. **corp(ocb 仓,`src/backend/src/agentclaw/corp`)只在 Phase 6**:CorpTaskModule override + bcsfuse httpx adapter(P1 可选)+ corp-only 鉴权路由 + corp profile DI。corp 不写业务/Repo/状态机(community 统一 Repository 一份 body 经 `ZdasDB.orm_session()` 跑 ZDAS)。
3. **分阶段分模块 PR**:每 phase = 1 个或多个 PR;每 PR 一个 `dev_*` 分支 + rebase + Conventional commit。
4. **每 PR TDD**:先红测试再绿;phase 末单测 + 契约(Rule 25)+ 架构(Rule 22/Boundary/Protocol conformance)+ 集成(tmp_path SQLite)全绿才算完成。
5. **顺序确认**:一 phase 完成确认后,再进下一 phase;一功能确认后,再实现下一个功能。

## 1. 贯穿规矩(所有 phase)

- 源根:community = `ocb-public/src/backend/src/agentclaw/community`;corp = `src/backend/src/agentclaw/corp`。PEP 420 `agentclaw` 命名空间聚合。
- 四层:`api/`(Protocol-only,typing.Protocol,无 import)/ `core/task/`(domain + services + dependencies + repository)/ `plugin_api/`(基础设施 Protocol,复用既有 27 个,不新加)/ `plugins/`(impl)。
- 新模块 checklist(backend README):schemas → Protocol → local/community impl → core services → dependencies → router 挂载 → 测试 → `core/task/README.md` Context Boundary(Rule 22)→ `tests/architecture/test_module_boundaries.py` 加 `task` → 新 Protocol 契约 `tests/contracts/test_task_*.py`(Rule 25)+ 从 `test_protocol_contracts.py` EXEMPT 移除 → Noop/Mock(Rule 21)。
- 三类测试 marker:`@pytest.mark.unit`(默认)/ `integration`(真实 SQLite tmp_path)/ `e2e`(完整栈);corp 侧 `requires_mosn`/`requires_zdas` 跳过。
- 不裸 SQL(走 ORM `orm_session()`);不 import 旧架构(`services/openclawserver/` 等);不落 ecb。
- 双仓红线:① 蚂蚁中间件 prod adapter 不进 Avernet(Protocol 在 community `plugin_api/`,corp `plugins/prod/` 已有,task 只 `Injected`);② skill/算法代码(8 SKILL prompt + LLM understand/judge/select_collab)不进 task 模块,归 skill_center/engine。

---

## Phase 0 — 领域模型 + API 交互接口骨架(首 PR,开源)【Avernet】

> 目标:领域模型对象 + 执行流程 API 端点骨架(Noop impl),router→Noop 跑通,架构/契约测试绿。**无业务逻辑、无持久化、无 Scheduler 真实逻辑**。

- [ ] **0.1** `core/task/domain/models.py`:Task/TaskSpec/Plan/TaskExecutionGraph/Node/Edge/AttemptedRecord/ProgressNode(dataclass 或 Pydantic)+ enums(TaskStatus 8 态/NodeStatus 7 态/GraphStatus/EdgeKind/RunMode/CollabMode)+ AcceptanceCriteria 多态袋 + **`SubDagRef`(plan §1.3a:`ref_kind="bcs_state_machine"`/`bcs_run_id`/`group_id`/`workflow_yaml_snapshot`,不含 child 态)** + Node.sub_dag 类型改为 `SubDagRef | None`。**TDD**:`tests/community/core/task/domain/test_models.py`(构造/必填校验/JSON 序列化/默认值/AttemptedRecord route_class·from·to·trigger/SubDagRef 字段)。
- [ ] **0.2** `core/task/domain/state_machine.py`:TaskStateMachine(VALID_TRANSITIONS 8 态)+ NodeStatus 转移合法性。**TDD**:`test_state_machine.py`(合法转移通过/非法抛 InvalidTransition/`EXECUTING→EXECUTING` 自环/终态 DELIVERED·CANCELLED·HUNG 不可转/VALIDATING→EXECUTING 回退)。
- [ ] **0.3** `core/task/domain/events.py`:TaskEvent/TaskEventKind(含 `GOAL_VERIFIED`/`HUMAN_REQUIRED`)。**TDD**:`test_events.py`(kind 枚举完整/payload 来源字段)。
- [ ] **0.4** `core/task/domain/repositories.py`:`TaskRepo`/`TaskEventRepo` Protocol(业务 Port,非 plugin_api;定义 create/get/update/list + append/list_by_task/get_latest_seq/claim_node 签名)。**TDD**:`test_repositories_protocol.py`(Protocol 结构 `runtime_checkable`)。
- [ ] **0.5** `api/{task_service,task_scheduler_service,task_decomposer_service,task_acceptance_service,task_execution_port,task_bot_discover_service,bcs_collaboration_service}.py`:7 Protocol(flat,一文件一 Protocol,typing.Protocol only,无运行时 import)。**新增 `BcsCollaborationProtocol`**(plan §2.4/§1.4b:查询面 Port,`fetch_state_machine_run_graph(run_id)`/`fetch_node_detail(run_id,node_id)`,只读)。**TDD**:`test_service_api_conformance.py` 加 `_PAIRS` 行(含 bcs_collaboration);Protocol 结构断言 PLAN.md §2 方法面覆盖。
- [ ] **0.6** `adapters/http/task/schemas.py`:Pydantic 请求/响应(CreateTask/SubmitPlan/Approve/Tick/PostEvent/EscalateBbs/ConfirmDelivery/TaskView/ProgressView/ExecutionGraphView/EventView)+ **副屏可视化 schemas(plan §1.4b):`TaskGraphView`/`TaskNodeView`/`TaskEdgeView`/`TaskNodeDetailView`/`SubDagRefView`**(字段按 §1.3b 对照表超集:run_mode/collab_mode/status/sub_status/attempt/artifacts/acceptance_result/attempted_executors/properties/sub_dag_ref;Edge outcome/guard)。**TDD**:`test_schemas.py`(校验/缺字段报错/`description` for OpenAPI/不 import core/对照表字段齐全)。
- [ ] **0.7** `adapters/http/task/router.py`:APIRouter(`prefix=/api/v1/task-loop`)端点:POST `/tasks` `/plan` `/approve` `/tick` `/events` `/escalate-bbs` `/deliveries`;GET `/tasks/{id}` `/progress` `/progress/stream`(WS);**新增副屏 4 端点(plan §7.2):`GET /tasks/{id}/graph`、`GET /tasks/{id}/nodes/{node_id}`、`GET /tasks/{id}/nodes/{node_id}/sub-dag`、`GET /tasks/{id}/graph/stream`(WS)**。`Injected(各 Protocol)`;body 只调 Protocol 方法,无业务逻辑。**TDD**:`test_router.py`(TestClient + Noop dependency override;断言路径/状态码/依赖注入/不直访 DB/不裸 SQL/副屏 4 端点挂载)。
- [ ] **0.8** `plugins/local/task.py` + `plugins/community/task.py`:Noop/Mock impl(4 Port + TaskScheduler + TaskService 状态组桩 + **`BcsCollaborationProtocol` mock 返回伪造 SM graph 供画布联调**;`@plugin_impl(mode=LOCAL,flavor=NOOP/FAKE)`,继承 MockSeam)。**TDD**:`test_noop_impl.py`(结构 + impl_registry 注册 + Rule 21 flavor + bcs_collab mock 返回结构)。
- [ ] **0.9** DI 注册 + 挂载:`di/modules/task_module.py`(Base TaskModule)+ `testing_task_module.py` + `infrastructure/community/task.py`(CommunityTaskModule 绑 Noop)+ `di/container.py` base list 追加 `TaskModule()` + `di/profile_modules.py` 三列(COMMUNITY/SINGLEBOX·TEST/CORP·CORP_TEST)+ `adapters/http/app.py` `include_router(task_router)` + `core/task/README.md` Context Boundary + `tests/architecture/test_module_boundaries.py` 加 `task` + 契约 `tests/contracts/test_task_*.py`(**7 Protocol 各一**,含 bcs_collaboration)+ 从 `test_protocol_contracts.py` EXEMPT 移除 task_*。**TDD**:架构测试绿(`container.resolve` 各 Protocol(含 BcsCollaborationProtocol)/Noop 注入/端点挂载/分层 import 无违例)。
- [ ] **0.10** 桩端到端 smoke:TestClient `POST /tasks`→Noop 返回;`GET /tasks/{id}`→Noop;`POST /events`→Noop handler。**TDD**:`test_task_endpoints_smoke.py`。

> **Phase 0 完成**:领域模型 + 6 Protocol + 9 端点骨架 + Noop + 注册 + 架构/契约/桩测试全绿,Avernet 仓单 PR 可合。**等确认 → Phase 1**。

---

## Phase 1 — 持久化层(2 Repo + 3 表 + 统一 Repository)【Avernet】

- [x] **1.1** `core/task/repository/models.py`:ORM `ac_task`/`ac_task_event`/`ac_task_execution_graph` 挂 community `core/base.py:Base`;`AutoIncrementBigInteger.with_variant(Integer,"sqlite")`;索引(env+status / env+uuid / env+task_id);`ac_task_event` append-only(无 `gmt_modified`,只 `gmt_create` `server_default=func.now()`);`ac_task_execution_graph.graph Text` + `version Int`。**TDD**:`test_orm_models.py`(tmp_path SQLite `create_all` 建表 + insert/query + `with_variant` 自增)。
- [x] **1.2** `core/task/sql/{ac_task,ac_task_event,ac_task_execution_graph}.sql`:prod DDL 参考(OceanBase/MySQL,运维手动 provision)。
- [x] **1.3** `plugins/task_repository.py` + `plugins/task_event_repository.py`:统一 ORM Repo(根目录单份,非 local/prod;`orm_session()`;event append-only 只 `db.add+flush`;seq 单 writer `get_latest_seq+1`;`claim_node` CAS `PENDING→RUNNING+assignee` 方言分支)。**TDD**:`tests/community/plugins/test_task_repository.py`(integration 真实 SQLite:CRUD/append/event seq 单调/claim_node CAS 并发模拟/upsert 方言)。
- [x] **1.4** `plugins/local/database.py` `bootstrap()` 追加 side-effect `import agentclaw.community.core.task.repository.models`。**TDD**:启动后 3 表存在。
- [x] **1.5** TaskModule/CommunityTaskModule/TestingTaskModule 绑 2 Repo→Protocol(单例,无 `@plugin_impl`)。**TDD**:`container.resolve(TaskRepo/TaskEventRepo)` 非空。
- [x] **1.6** router `/events` handler 接 `TaskEventRepo.append`(真实持久化)。**TDD**:`POST /events`→落库→`GET /tasks/{id}/history` 读出。

> **Phase 1 完成**:持久化层 + 统一 Repo(一份 body 待 ZDAS 复用)+ 3 表 + seq/claim CAS 测试绿。**等确认 → Phase 2**。

---

## Phase 2 — TaskService(定义/状态/查询/on_event)【Avernet】

- [ ] **2.1** `task_service.py` 定义组:`create_spec`(new Task + `init_root_phase(INTAKE)` + emit INTAKE_CREATED + **★ 发 `<AixUI panel>` 面板消息触发副屏弹出,plan §1.4b/§2.1,FR-OBS-11;参考 BCS `publish_state_machine_panel_event`,component 指向新画布 `taskPanel.TaskWorkflowView`,params 带 taskId**)/`append_discussion`(首条→`advance_phase(DISCUSSING)`)/`submit_plan`(spec.plan=plan + `advance_phase(PLANNED)` + emit GOAL_MODELED)/`approve`(校验 plan ready → 委派 `TaskScheduler.start`,Scheduler 暂桩)/`amend_spec`/`cancel`。**TDD**:`test_task_service_def.py`(录入期推进 INTAKE→DISCUSSING→PLANNED,P3 闭环,create_spec 发面板消息断言)。
- [ ] **2.2** TaskService 状态组:`init_root_phase`/`advance_phase`(state_machine guard)/`spawn_build_dag`(plan→骨架 Node/Edge)/`set_node_status`/`append_attempted`/`add_sibling_node`/`spawn_sub_dag`(**改为写 `SubDagRef`(bcs_run_id 引用),不跟踪 child 态,plan §1.3a;群自闭环口径不变**)/`mark_graph_status`/`mark_terminal`/`claim_node`(CAS)。**TDD**:`test_task_service_state.py`(合法/非法 guard/`spawn_build_dag` 骨架/`spawn_sub_dag` 写引用不写 child/`claim_node` CAS 并发)。
- [ ] **2.3** TaskService `on_event`:`_apply_event`(guard+落态 set Node 态 + append attempted)+ `GOAL_VERIFIED`→`_apply_goal_verdict`(PASS→`mark_graph(VERIFIED)+advance(DELIVERED)`;编排 FAIL→`mark_graph(AWAITING_HUMAN_ACCEPT)+emit HUMAN_REQUIRED`;BBS FAIL→`advance(HUNG)`)+ 编排链路转 `Scheduler.on_event`(桩)+ BBS `return`。**TDD**:`test_on_event.py`(各 kind 分流/落态/emit/不自调 check_node·check_goal)。
- [ ] **2.4** TaskService 查询组:`get_task`/`get_execution_graph`(BBS 认领读用)/`get_progress`(内含 ProgressProjector)/`get_run_history`/`subscribe`(WS stream)+ **副屏 4 方法(plan §1.4b):`get_task_graph`(产 TaskGraphView 顶层动态 DAG)/`get_node_detail`(节点详情)/`get_sub_dag`(协作群节点下钻:读 SubDagRef → SmGraphAdapter 实时映射 BCS SM run graph;非协作群或无引用→404/skip;Phase 4 接真实 adapter,此处先桩)/`subscribe_task_graph`(WS 增量)**。**TDD**:`test_query.py`(只读/不经写锁/投影结构/TaskGraphView 字段超集对齐 §1.3b 对照表/get_sub_dag 桩路径)。
- [ ] **2.5** router 接 TaskService 真实(替换 Phase 0 Noop override),含 **副屏 4 端点接查询组真实**(`get_task_graph`/`get_node_detail`/`get_sub_dag`/`subscribe_task_graph`)。**TDD**:真实 CRUD 端到端 + 状态机 guard 生效 + 副屏 graph 端点返回 TaskGraphView 真实快照。
- [ ] **2.6** CorpTaskModule 桩:corp profile 下 TaskService 仍 community 统一(corp 不重写;只占位确保 resolve)。**TDD**:corp profile resolve TaskService。

> **Phase 2 完成**:TaskService 合一(定义/状态/查询/on_event)+ invariant guard +录入期推进闭环。**等确认 → Phase 3**。

---

## Phase 3 — TaskScheduler 编排(决策 + tick/on_event 骨架)【Avernet】

- [ ] **3.1** `task_scheduler.py` 私有决策:`_route`(C1~C5 纯规则,attempted 降权 P10)/`_select_collab`(规则版,confidence<0.7 降级 `manager_worker`)/`_compute_gap`(纯规则 need_reroute/need_split,带 atomic/recompose_count 终止性)。**TDD**:`test_scheduler_decisions.py`(纯函数单测:C1~C5 各分支/attempted 降权/降级/gap 两分支)。
- [ ] **3.2** `Scheduler.start`(approve 委派):`advance(PLANNED→EXECUTING)` + `spawn_build_dag` + `mark_graph(ON_PLAZA)` + `enqueue tick`。**TDD**:`test_start.py`。
- [ ] **3.3** `Scheduler.tick`:`root_phase==EXECUTING` + 拓扑序解锁(PENDING/replan 后 PARTIAL_FAILED,前置 DONE/SKIPPED)+ recommend/dispatch 暂桩返回 + `set_node_status(RUNNING)`(调 TaskService)+ `all_done`→`advance(VALIDATING)`+emit 触发判断完成 SKILL(桩)+ 终止性(`loop_round`/MAX/连续无进展→强制终验)。**TDD**:`test_tick.py`(拓扑序/解锁条件/VALIDATING 触发/终止性)。
- [ ] **3.4** `Scheduler.on_event`:`ACCEPTANCE_FAIL`→`gap`→reroute(enqueue tick)/split(`add_sibling`);`NODE_FAILED`→R7 同 executor 重试 max_attempts(默认 2)→转重路由。**TDD**:`test_scheduler_on_event.py`。
- [ ] **3.5** router `/tick` `/approve` 接 Scheduler 真实。**TDD**:approve→start→tick 触发。

> **Phase 3 完成**:Scheduler 编排骨架 + 3 决策方法(TDD 纯函数)+ tick/on_event 流转 + 终止性。**等确认 → Phase 4**。

---

## Phase 4 — 执行 loop(deepresearch ①② + Port impl + 终验)【Avernet】

- [ ] **4.1** `decomposer_service.py`(BotDiscoverPort community impl):本地 `BotRepository` recommend + cover 计算 + attempted 不排除(P10 降权在 `_route`)。**TDD**:`test_bot_discover.py`。
- [ ] **4.2** `execution_port.py`(ExecutionPort community impl):`dispatch_single_bot`(httpx 调开源 engine,R6)/`dispatch_coop_group`(httpx 调开源 BCS,B5)/`redispatch_node`(B5 同群重派,P8)/`dispatch_bbs`(接 bbs_executor 桩,Phase 5)。**TDD**:`test_execution_port.py`(httpx Mock,各 dispatch + redispatch)。
- [ ] **4.2a** `bcs_collaboration_service.py` community impl(`BcsCollaborationProtocol` httpx,plan §2.4):`fetch_state_machine_run_graph(run_id)`→`GET /state-machine-runs/{id}/graph`、`fetch_node_detail(run_id,node_id)`→`GET /state-machine-runs/{id}/nodes/{nid}`(调本地开源 BCS;只读查询面,不持写态)。**TDD**:`test_bcs_collab_httpx.py`(httpx Mock,返回 `StateMachineRunGraphView` 结构)。
- [ ] **4.2b** `graph_adapter.py`(`SmGraphAdapter`,plan §1.3a):纯映射无副作用;`fetch_run_graph` 调 `BcsCollaborationProtocol` → `to_sub_dag_view(snap)` 按 §1.3b 对照表把 `StateMachineRunGraphView` 映射成 `TaskGraphView` 子树(nodes/edges/status/sub_status/attempt/artifact/judge_outputs/outcome/guard 全字段)+ §1.3c 状态映射。被 `TaskService.get_sub_dag` 调。**TDD**:`test_sm_graph_adapter.py`(对照表逐字段映射/状态映射/超集字段保留/SM 缺字段默认值)。
- [ ] **4.3** `decomposer_service.py`(DecomposerPort community impl):规则拆解 BFS + dedup 0.92 + confidence(<0.7 打回)。**TDD**:`test_decomposer.py`。
- [ ] **4.4** `workflow_compiler.py`:compile_to_state_machine/compile_to_workflow_pack/import_workflow_to_graph/select_collab_mode(规则版)。**TDD**:`test_workflow_compiler.py`。
- [ ] **4.5** deepresearch ① §5.2:cover<100%→`is_atomic`/`recompose_count>2` 判定→`add_sibling_node`+父 SKIPPED / `set PARTIAL_FAILED` 留待终验。接 `Scheduler.tick`。**TDD**:`test_decompose_loop.py`。
- [ ] **4.6** deepresearch ② §5.3:验收 fail→`_apply_event` 落态→`Scheduler.on_event` gap→reroute(`self._route`+dispatch/redispatch)/split + `loop_round` 终止。**TDD**:`test_reroute_loop.py`。
- [ ] **4.7** `acceptance_checker.py`(AcceptanceChecker community structured 双轨 impl:StructuredRunner+EffectsChecker;LLM judge 归 owner-bot SKILL,community 不持 prompt):作为 owner-bot SKILL 调用工具,backend 暴露工具 Protocol/端点。**TDD**:`test_acceptance_structured.py`(双轨通过/部分/不过)。
- [ ] **4.8** 终验触发:`tick all_done`→`advance(VALIDATING)`+`emit 触发判断完成 SKILL`(owner-bot 回投 `GOAL_VERIFIED`)→`on_event._apply_goal_verdict`。**TDD**:`test_goal_verify.py`(PASS→DELIVERED / 编排 FAIL→BBS 门 emit HUMAN_REQUIRED)。
- [ ] **4.9** 端到端 Case A(单 bot happy)+ Case D(搜推 fail 拆解)绿。**TDD**:`test_e2e_case_a_d.py`(integration,Mock engine/BCS httpx)。
- [ ] **4.9a** TaskService.get_sub_dag 接真实 `SmGraphAdapter`(替换 Phase 2 桩):读 SubDagRef → adapter.fetch_run_graph → to_sub_dag_view 返回 live TaskGraphView 子树。**TDD**:`test_get_sub_dag_live.py`(Mock BcsCollaborationProtocol 返回 SM graph → 映射断言;非协作群→404;无引用→skip)。

> **Phase 4 完成**:开源执行 loop 闭环(Case A/D)+ 4 Port community impl + deepresearch 两 loop + 终验触发 + 终止性 + 副屏后端可视化(SmGraphAdapter + BcsCollaborationProtocol httpx + get_sub_dag live)。**等确认 → Phase 4.5(前端画布)/ Phase 5**。

---

## Phase 4.5 — 副屏动态 workflow 画布(前端,专项)【Avernet】

> 目标:新建独立动态 workflow 画布(参考现有 `bcsPanel/StateMachineRunView` 实现扩展),落地 spec FR-OBS-01~11。分工置(plan §1.4b):**任务入口页**副屏展示任务整体执行流程(顶层动态 DAG);**协作群页**副屏维持现有 `bcsPanel/StateMachineRunView`(该群子 DAG,本 phase 不改)。前置:Phase 0~4 backend API(`GET /tasks/{id}/graph` 等)就绪。前端落点依赖 plan §10 开放问题 6 拍板,本 phase 给基准实现(任务入口页内嵌新画布 + 复用 `@aix-chat/ui` ChatLayout/UmdPanel 机制),可后续调整。

- [ ] **4.5.1** 任务入口页骨架:`src/frontend/src/pages/TaskLoop/`(路由 `/task-loop/:taskId`)+ 任务创建入口组件(提需求 → `POST /tasks`)。复用现有 ChatLayout.Panel 副屏 slot(对齐 GroupChat 页结构)。**TDD/手测**:页面渲染 + 路由 + 副屏 slot 存在。
- [ ] **4.5.2** 新建 `TaskWorkflowView` 画布组件(参考 `StateMachinRunView` 结构:graph 轮询 + 节点/边渲染 + 节点详情弹窗),消费 `GET /tasks/{id}/graph`(TaskGraphView);按 run_mode/collab_mode 渲染模态标签;状态色映射对齐 §1.3c。**TDD/手测**:Mock TaskGraphView 渲染节点/边/状态色/模态标签。
- [x] **4.5.3** 面板消息触发副屏弹出(FR-OBS-11):backend `create_spec` 发 `<AixUI panel component=taskPanel.TaskWorkflowView params={taskId}>` 消息 → 前端 `hasAixPanelContent` 命中 → `chatBridge.openPanelTab` → ChatLayout.openTab 自动展开(复用现 `@aix-chat/ui` 机制,参考 BCS `publish_state_machine_panel_event`)。**TDD/手测**:任务创建后副屏自动弹出并加载 graph。
  - Carrier transport 落地:`PanelDeliveryPort` Protocol(core.task.protocols)+ `EventBusPanelPublisher.publish` 预格式化 `<AixUI panel>` content + `TaskPanelEvent`(content+session_id)+ `TaskPanelCarrier`(Lifecycle 参与者,`startup()` 订阅 EventBus → delivery port,吞异常不阻塞 create)+ `NoopPanelDelivery`(community 默认,无 chat 推送总线)/`RecordingPanelDelivery`(test)。DI:`CommunityTaskModule.panel_delivery_port` + `task_panel_carrier` 单例 provider;lifespan 自动发现并 install。
  - 前端收敛:`openTaskPanel(taskId, title?)` helper(`TaskPanel.tsx`),community profile 由创建流直接命中 `chatBridge.openPanelTab` 弹出副屏(corp 接通 carrier transport 后两条路径收敛同一渲染器);`TaskLoop/index.tsx` `handleCreate` 创建成功即调用。无 chat 布局时静默降级。
  - 边界契约:`panel_publisher.py` 改 import `core.task.protocols`(plugins 不可 import api);`plugins/local/README.md` `internal_dependencies` 追加 `core.task.repository.models`(ORM side-effect import)。测试:`test_panel_carrier.py`(8)+ `test_task_module_wiring.py` delivery/carrier/lifecycle 用例全绿。
- [ ] **4.5.4** 节点详情(FR-OBS-04):点节点 → `GET /tasks/{id}/nodes/{nid}`(TaskNodeDetailView)→ 弹窗展示 artifacts/acceptance_result/attempted_executors/properties.error_msg 等,字段对齐 §1.3b 超集。**TDD/手测**:点节点弹窗内容。
- [ ] **4.5.5** 下钻协作群节点 = 跨页导航(FR-OBS-03/10,plan §1.3a 主交互):任务页点协作群节点 → 跳转该协作群页(`/group/:groupId`)→ 该页副屏自然展示群内 sub-DAG(现有 `bcsPanel/StateMachineRunView`,不改)。可选:`get_sub_dag` 端点做任务页内只读预览悬浮卡(实时映射,P1)。**TDD/手测**:点协作群节点跳转 + 目标页副屏展示群 workflow。
- [ ] **4.5.6** 增量 + 轮询(FR-OBS-07):WS `/tasks/{id}/graph/stream` 增量刷新为主,画布轻量轮询 `GET /tasks/{id}/graph` 兜底(对齐现画布轮询模式);下钻群页画布沿用其原轮询。**TDD/手测**:状态变更后画布刷新(WS 推 + 轮询兜底)。
- [ ] **4.5.7** 信息 cover 验证(FR-OBS-05/AC-12):对照 §1.3b 表,在任务页画布 + 协作群页画布分别验证 state_machine 画布全字段(节点 status/sub_status/attempt/artifact/judge_outputs/带 outcome 边/run 状态/final_output)均可见;自定义协作群作为"workflow 固化特例"在协作群页画布无缝展示。**TDD/手测**:对照表 checklist 逐项过。

> **Phase 4.5 完成**:副屏动态 workflow 画布(任务入口页整体 DAG + 创建即弹出 + 节点详情 + 下钻跨页 + 增量/轮询 + 信息 cover)。与 Phase 5(BBS)可并行。**等确认 → Phase 5/6**。

---

## Phase 5 — BBS(bbs_executor + 共享黑板)【Avernet】

- [ ] **5.1** `bbs_executor.py`:广场/认领/续做。**共享黑板 = `TaskExecutionGraph`(plan §2.3,非独立存储)**:BBS bot 经 `TaskService.get_execution_graph` 读图谱认领未完成节点 → `claim_node`(CAS,状态组)→ 自主执行 → 回投 → `TaskService.on_event(run_mode=BBS)` 经状态组改 `TaskExecutionGraph` 的 Node 态(同出口,原 GAP5"内部不回投"作废)。bbs_executor 只管广场/认领/续做执行细节,**不持状态、不 bypass 状态组**。**TDD**:`test_bbs_executor.py`(认领读图谱/回投落态经 on_event/bbs_executor 不写态/共享黑板即 TaskExecutionGraph 断言)。
- [ ] **5.2** `claim_node` CAS(状态组)+ BBS 回投(run_mode=BBS,经 `POST /events` → `on_event`,**on_event 内 BBS 分支 return 不转 Scheduler**)+ BBS 内 PARTIAL_FAILED 一次性重放认领(P9,1 轮,别的 BBS bot 可接)+ 不进 deepresearch 重路由。**TDD**:`test_bbs_claim_replay.py`(并发认领 CAS/重放 1 轮/仍 fail→终验 fail/on_event BBS 分支不转 Scheduler)。
- [ ] **5.3** `escalate_to_bbs`:人工门(emit `HUMAN_REQUIRED`)→用户确认 True→`dispatch_bbs`→BBS 终验 `GOAL_VERIFIED`(run_mode=BBS)→`_apply_goal_verdict`(DELIVERED/HUNG);拒绝→`cancel`(P12)。**TDD**:`test_escalate_bbs.py`(确认/拒绝/BBS PASS/BBS FAIL→HUNG)。
- [ ] **5.4** 端到端 Case B(coop state_machine)+ Case E(终验 fail→BBS)+ Case C(验收 fail 重路由)绿。**TDD**:`test_e2e_case_b_c_e.py`。

> **Phase 5 完成**:BBS 内部续做 + 两链路统一 + 5 case 全绿。**Avernet 开源版完整可跑,等确认 → Phase 6 corp**。

---

## Phase 6 — corp 适配层(teamclaw/ocb 仓)【仅此 phase 进 corp】

> 前提:Phase 0~5 开源版全绿并确认。corp 不重写业务/Repo/状态机;只把 community Noop Port override 成 corp 真实 impl,加 corp-only 鉴权/中间件。

- [ ] **6.1** `corp/di/modules/infrastructure/corp/task.py`(CorpTaskModule):把 4 Port 的 Noop override 成 corp impl(`@provider` explicit,B8);`corp_column()` + `test_corp_reuse_column()` 追加。**TDD**:`test_corp_task_module.py`(corp profile resolve 真实 impl + COMPOSITION 不 import corp 类型于 community)。
- [ ] **6.2** `corp/core/task/services/bcsfuse_{decomposer,bot_discover,acceptance_judge}_service.py`:bcsfuse httpx adapter(R1/R2/R3,P1 可选;LLM prompt 在 bcsfuse 侧)。**TDD**:`test_bcsfuse_adapters.py`(httpx Mock;P1 可后置)。
- [ ] **6.3** corp ExecutionPort(仅当派活需经 ARCA/MOSN 时):prod dispatch 经注入 prod Baas/Device Protocol;否则复用 community httpx。**TDD**:corp integration(`requires_mosn`,跳过 CI)。
- [ ] **6.4** `corp/adapters/http/task/`(可选 corp-only 路由,若需内部鉴权)+ `CorpAppServicesModule` 经 `OptionalRouters` 注入。**TDD**:corp 鉴权 endpoint。
- [ ] **6.5** engine R6 / BCS B5·B6·B2 真实接入(corp profile 下 httpx 走真实域名/MOSN)。**TDD**:integration(`requires_mosn`/`requires_services`)。
- [ ] **6.6** NotifySender 触达(钉钉卡片,corp `dingtalk_notify_sender` 已有):关键节点/交付/人工门。**TDD**:`test_notify_corp.py`(`requires_mosn`)。
- [ ] **6.7** prod 配置:`corp/configs/application-{prod,pre}.yaml` `task_loop` 块(bcsfuse base_url 等);ZDAS 3 表 DDL 手动 provision(`core/task/sql/*.sql`)。**TDD**:配置加载 + ZDAS 表存在性(`requires_zdas`)。
- [ ] **6.8** 文档收尾:README index 加 spec 目录;`restructure-analysis.md` 标注已并入 plan;`plan.md §10` 开放问题逐项闭环或转 follow-up;变更记录补 tasks/implement 节。

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
| AC-T-06 | 不裸 SQL;四层单向;无 ecb;无旧架构 import;三类 marker 正确 | 架构 + 审查 |
| AC-T-07 | 双仓红线守:中间件 adapter / skill 算法代码 不进 Avernet | 审查 |
| AC-T-08 | 副屏可视化(FR-OBS-01~11):Phase 0 SubDagRef+graph API+Protocol;Phase 2 查询组+面板消息触发;Phase 4 SmGraphAdapter+httpx+get_sub_dag live;Phase 4.5 前端画布(整体 DAG/创建即弹出/节点详情/下钻跨页/信息 cover)全落地 | 审查 + 手测 |
| AC-T-09 | AC-12 信息对照表(state_machine 画布字段 → 任务图谱字段)由 SmGraphAdapter 逐字段映射 + Phase 4.5.7 对照 checklist 验证 | 单测 + 手测 |

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-07-28 | 栖真 | 初版 tasks:6 phase 分模块 TDD;首 PR=Phase 0 领域模型+API 接口;开源(0~5)先行,corp(6)后置;顺序确认 |
| 2026-07-29 | 栖真 | 优化(tasks 修订,对接 spec FR-OBS-10/11 + plan §1.3a/1.3b/1.4b):Phase 0 加 SubDagRef(B.1)+ BcsCollaborationProtocol(0.5/0.8/0.9)+ 副屏 schemas(0.6)+ graph/nodes/sub-dag/stream 端点(0.7);Phase 2 加 create_spec 发面板消息(2.1)+ spawn_sub_dag 改引用(2.2)+ 副屏 4 查询方法(2.4)+ router 接真实(2.5);Phase 4 加 SmGraphAdapter(4.2b)+ BcsCollaborationProtocol httpx(4.2a)+ get_sub_dag live(4.9a);新增 Phase 4.5 前端副屏画布(任务入口页整体 DAG / 创建即弹出 / 节点详情 / 下钻跨页 / 增量轮询 / 信息 cover);AC-T-08/09;顶层顺序补 Phase 4.5 与 5 并行 |

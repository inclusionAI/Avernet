# Tasks — 任务模块真实case本地singlebox端到端集成用例

> 案例用权威剧本 `gwqie46v7hzr1w6h`（存储行业尽调：三阶段三模态 + FAIL治愈 + MISS升BBS + BBS认领 + STUCK→HUNG）。
> 落点：全在 Avernet `src/backend`（ocb-public 只读镜像）；内核编排零改动（仅 R3 锁模型若需，最小 seam 且经审议）。
> 资源对齐：三模态执行接入已由同学实现在 `core/task/task_runner/integration/`（`OpenApiBotAdapter`/`BcsHttpAdapter`/`TaskExecutor`/`TaskExecutorResultPoller`/`PromptFormatterImpl`/`_RunnerContextBuilder`/double/`build_integration`）。本轮缺口 = ①接线 integration 到引擎 ②补 plan/dispatch 策略 body ③补 DI/HTTP ④产 3 skill 包 ⑤写真实 case 用例 ⑥singlebox 入口门禁。

## 实现原则（对齐 AGENTS.md + 权威框架 tasks.md）
- **契约先行 / transport-agnostic**；HTTP router thin（只转协议）；composition root 选实现。
- **引擎只有一套 `ExecutionEngine`，不变**：不经子类、不引入"环境差异化引擎"。skill 安装到 bot / bot 创建 / singlebox 起停都是**前置环境准备**，不属任务执行逻辑。框架不感知 bot 装了哪些 skill、不感知 bot 怎么执行/验收，只负责：组 prompt 上下文 → 投指定 bot → 收结构化结果 → 更新图谱 → 推进。
- **策略类名/接口不变，只实现体 stub→真实**：`GapBasedPlanningStrategy`/`SearchBasedDispatchStrategy`/`DirectDispatchStrategy`/`WorkflowPlanningStrategy` 类名、`matches`/`apply` 签名、first-match-wins 策略池都不变；依赖经构造器注入（`OpenApiBotPort`/`BotDiscoverServiceProtocol`），`apply` 内 `await` 端口 IO。
- **结果回收两种**：plan/dispatch `await apply()` **同步收 result 当返回值**（引擎锁内已 await，不回投）；execute/verify 经旁路 poller → `ResultSink.report_result` → `on_report` **异步回投**。
- **协程化**：适配/投递/回投全 `async`；锁内不 await 外部 IO（投递/拉群 await 在锁外）。
- **框架零 case 知识**：节点名（`N_overview`/`N_market`…）只允许出现在 skill 产出/测试，**禁止出现在适配层/策略/框架代码**；策略经 graph 派生查询自发现目标。
- **只改 Avernet**；现有 in-process `tests/.../task/e2e/test_e2e.py` 保留为内核逻辑快测，不破坏。

---

## G1 — 接线 integration + 补 plan/dispatch 策略 body（R1 已收敛，不再做 spike）

> R1（投指令给 bot、取结构化结果）已由同学实现 `OpenApiBotPort`/`OpenApiBotAdapter`（`send_and_wait_async` public async round-trip + `send_message`/`get_run` 异步投递）。本 spec 已把 `_send_and_wait_async` 暴露为 public `send_and_wait_async`（去下划线、kw-only），代码已在分支；G0 spike 取消。

- **T1.1** **接线 integration 单 seam**：`TaskRunner.__init__(graph, execution_backend=None)` 已是接线点（`start_run` 优先走 `execution_backend.dispatch`、`form_coop_group` 委托 `execution_backend.form_coop_group`）。引擎 `_build_runner()` 返回 `TaskRunner(graph, execution_backend=build_integration(double=False, sink=callback, runner=self._runner, poller_thread=True))`（同学组合根 `core/task/task_runner/integration/__init__.py`）。**不新建 `DeliveryPort` 真实实现**——投递/拉群/assignee 填充走 `TaskExecutor` 单路。`DeliveryPort`/`set_delivery` seam 保留不动（corp 备选，本轮不填真实）。
- **T1.2** **`GapBasedPlanningStrategy`（现有 `task_plan/strategies.py`，body stub→真实）**：类名/`rule_id="gap_based"`/`priority=99`/`async matches(graph)`/`async apply(graph)->list[TaskNode]` 不变；构造器新增 `__init__(self, bot: OpenApiBotPort)`。`apply`：复用 `_find_planning_target(graph)` 自发现可规划目标（根 PENDING / PLANNING 父 / FAILED+gaps 叶，派生查询零节点名）→ 组 prompt（`{goal, context, target_node, graph_snapshot, gaps}`，owner=图派生 `owner_bot_id`）→ `await self._bot.send_and_wait_async(bot_id=owner, message=prompt)` → **同步收** `run{status,result,error}`，解析 `result.content` JSON `{children:[{node_id,instruction}]}` 或 `{}` → 造 `TaskNode[]`（PENDING，空 `RuntimeInfo`）。plan 结果当返回值直接拿（引擎锁内 `await apply()`，无回投）。
- **T1.3** **`SearchBasedDispatchStrategy`（现有 `task_dispatch/strategies.py`，body stub→真实）**：类名/`rule_id="search"`/`priority=99`/`async matches(node,graph)`/`async apply(node,graph)->SearchResult` 不变；构造器新增 `__init__(self, bot: OpenApiBotPort, discover: BotDiscoverServiceProtocol)`（`DirectDispatchStrategy` prio 10 优先匹配 `execution_config.bot`，并存于策略池）。**dispatch 是决策非查找**，两步：
  1. **框架语义预查候选集**：`BotDiscoverServiceProtocol.search_by_keyword`（语义=BCSFuse recommend `/api/v1/recommend`，经 `/api/v1/bot-public/discover` 暴露）是同步 `requests`，用 `await asyncio.to_thread(...)` 包。**按字段分别查**（`node.task_spec.metadata.title` / `goal.objective` / `context.background` 各起一次，`user_id`=任务来源用户，`top_k=10`，`min_score=0.01`，`filters={"runtime_state":["online"]}`）→ 各取候选 `items[]`（`{bot_id, bot_name, bot_desc, recommend{score, short_profile, reasons}}`）→ **合并去重按 `recommend.score` 降序取 top1 最佳匹配 + 其余作候选喂入**。
  2. **投 owner bot search skill 决策（同步 round-trip）**：组 prompt（子任务需求 `{goal, instruction, acceptances}` + 图态快照 + 候选集 candidates）→ `await self._bot.send_and_wait_async(bot_id=owner, message=prompt)` → 同步收 → 解析 → `_parse_search_result` 映射 `SearchResult` 4 态。**搜推 skill 不自取 BCSFuse**，候选集由框架预查喂入 prompt，skill 只在给定候选里决 who+how。
- **T1.4** **`GroupFormation` 扩字段**（`task_dispatch/strategies.py`，承载搜推 skill 决出的"怎么执行"细节，透传 BCS 建群；不动核心领域模型）：
  ```python
  @dataclass
  class GroupFormation:
      bot_ids: list[str]
      collab_mode: str                        # "chat"/"manager_worker"/"state_machine"
      group_name: str | None = None           # 新增: skill 决出协作群名 → BCS
      members_info: list[dict] | None = None  # 新增: [{bot_id, role, responsibility}] → BCS participants[].role
      extend_props: dict[str, Any] = field(default_factory=dict)
      # state_machine 模 → extend_props["definition_yaml"] = workflow yaml
  ```
  `SearchResult.group_formation` 携 `HIT_MULTI_BOTS` 态结果透传至 `TaskExecutor.form_coop_group`（`GroupFormation.bot_ids`/`collab_mode`/`extend_props["definition_yaml"]` → `BcsCreateGroupRequest`，见 T1.5）。
- **T1.5** **`form_coop_group` 接线 BCS 真值（同学已实现 `TaskExecutor.form_coop_group` + `BcsHttpAdapter.create_group`）**：engine 锁外 `await self._runner.form_coop_group(gf)` → 委托 `TaskExecutor.form_coop_group(gf)` → 映射 `BcsCreateGroupRequest` → `BcsHttpAdapter.create_group`（HMAC 签名）→ `BcsCreateGroupResult{group_id, session_id?, run_id?, definition_ref?}` → engine patch `assignee=group_id`/`run_mode="coop_group"`。三态映射（同学已实现，本轮验证接线）：
  - `chat` → `group_strategy` 省略（BCS 默认 chat）。
  - `manager_worker` → `group_strategy="manager_worker"`、`driver_bot=extend_props["manager_bot_id"] or bot_ids[0]`、`participants=[{manager,role:manager}]+[{worker,role:worker}]`。
  - `state_machine` → `group_strategy="state_machine"`、`collaboration_definition_yaml=extend_props["definition_yaml"]`、`participant_bindings`、`start_initial_run=False`。
  `TaskExecutor._group_meta[group_id]` 记 `{collab_mode, gf, definition_ref, session_id}` 供 `_dispatch_coop_group` 分流（chat/manager_worker 走 session 模；state_machine 走 run 模）。`group_name` 经 `BcsCreateGroupRequest.context`/`topic` 透传（当前无 `label` 字段，后续 BCS client 补或注明）。
- **T1.6** **三模态投递 + 旁路 poller 回投（同学已实现 `TaskExecutor.dispatch`/`TaskExecutorResultPoller`，接线即可）**：`TaskExecutor.dispatch(toDoTaskList)` async（`start_run` 锁外 gather+`Semaphore(8)`）按 `node.run_info.run_mode` 自适应：`single_bot`→`ensure_grant`→`context.build`→`formatter.format_execute`→`send_message` 拿 `run_id` 立返回 + poller 登记回收；`coop_group`(chat/manager_worker)→`create_session(group_id, bootstrap_prompt)` + 登记；`coop_group`(state_machine)→`start_state_machine_run` + 登记；`bbs`→no-op 记日志（bot 认领自规划执行）。回投：`TaskExecutorResultPoller` daemon 线程轮询 `get_run`/`get_group`/`get_session_messages` 到终态 → `SingleBotRunTranslator`/`BcsSessionTranslator`/`BcsStateMachineRunTranslator` → `TaskCallbackData` → `ResultSink.report_result` → `on_report`（SLA 超时→FAIL `sla_timeout`；连续 5 次端口失败→`poll_exhausted`）。**verify 模式**（节点有结构子 `get_child_tasks` 非空）经 `formatter.format_verify` 组聚合 prompt（`child_outputs+goal+acceptances`）→ 投 owner bot → poller 回投 verdict（**验收 skill 宿主=owner bot=方案A，对齐框架 §聚合收敛/§5；execute→worker bot**）。`PromptFormatterImpl` + `_RunnerContextBuilder`（经 `runner._build_context`）同学已落地，无需新增公开方法。
- **T1.7** **模块 README `## Context Boundary`**（Rule 22）：声明 `integration/` 输入 = `OpenApiBotPort`/`BcsClientPort`/`ApiKeyProvider` + graph 派生查询，输出经 `ResultSink.report_result`；不依赖 transport/案例节点名。
- **T1.8** **契约/单元测**（in-process，R2 R3 验证载体）：
  - **`test_open_api_bot_port.py`**（Rule 25）：`OpenApiBotAdapter` vs `_DoubleOpenApiBot` 契约——`send_and_wait_async` round-trip schema（`run{status,result,error}` 终态）与 `send_message`+`get_run` poll 行为对账。
  - **`integration` 三模态测**（in-process，double 驱动）：`build_integration(double=True, sink=...)` 装配 `_DoubleOpenApiBot`/`_DoubleBcsClient`，注入 `_build_runner` 后用真实 body 策略驱动 `ExecutionEngine`（`GapBasedPlanningStrategy(bot)`/`SearchBasedDispatchStrategy(bot, discover)`），断言拆解/搜推/投递/回投值正确。复用 `tests/.../task/e2e/test_e2e.py` 案例期望，但走 `TaskExecutor`+poller+`ResultSink` 真实回投链路。
- **T1.9** **R3 锁模型验证**（证物即 T1.8 / G4 用例）：`TaskExecutorResultPoller` 是 daemon 线程持自有 loop `run_until_complete` → `sink.report_result`（同步调 engine `on_report`，持 per-task `threading.RLock`）。先验证 `threading.RLock` 在「poller 线程 + 请求线程」跨线程回投下成立。若竞态，最小 seam 引入 `_lock_factory`（`asyncio.Lock` 选项，`core/task/task_center/engine.py`），否则记录"RLock 成立"。
- ✅ **G1 验收**：T1.8 绿（三模态 + 回投真实链路）；策略/适配层 `grep` 无节点名字面量（AC-8）；`send_and_wait_async` public 落地；`TASK_ENGINE=skill` 时同一套 `ExecutionEngine` 注真实 body 跑通 happy 首帧。

---

## G2 — 后端接线（DI + HTTP adapter，补运行时可达性缺口）

- **T2.1** **`adapters/http/task/schemas.py`**：DTO↔domain（`TaskInfo`/`TaskCallbackData`/`TaskExecutionGraph`/`TaskOpResult`/`TaskNodePatch` 视需）。
- **T2.2** **`adapters/http/task/router.py`（thin，Rule 22）**：
  ```python
  router = APIRouter(prefix="/openapi/v1/collaboration/tasks", tags=["task"])
  @router.post("/execute")        # TaskInfoDTO -> TaskOpResultDTO   (async, delegate TaskServiceProtocol.execute)
  @router.get("/dashboard")       # ?task_id=&node_id= -> TaskExecutionGraphDTO (delegate get_task_dashboard)
  @router.post("/callback/report")# TaskCallbackDataDTO -> {"ok": true}  (async, 调 TaskLoopCallbackProtocol.report_result)
  ```
  router 不持领域策略（grep 无图谱写/状态机逻辑），只翻译协议。
- **T2.3** **`di/modules/task_module.py`（composition root）**：装配 `TaskGraphService`（local in-mem）、`TaskHarness`、`TaskService`。**引擎始终 `ExecutionEngine`**（无子类）：`_build_runner()` 返 `TaskRunner(graph, execution_backend=build_integration(double=False, sink=callback, poller_thread=True))`；`set_strategies` 注真实 body——`eng._planner.set_strategies([WorkflowPlanningStrategy(), GapBasedPlanningStrategy(bot)])`、`eng._dispatcher.set_strategies([DirectDispatchStrategy(), SearchBasedDispatchStrategy(bot, discover)])`。`ExecutionEngine(bot=None)/OpenApiBotAdapter(keys)/BcsHttpAdapter(token)` 经 `ApiKeyProvider`(local→localhost:8888 / prod→prod URL) 配置注入；`BotDiscoverServiceProtocol` 注 `SearchBasedDispatchStrategy`。**`TASK_ENGINE=skill` env 激活真实 body；否则默认 stub `ExecutionEngine`（prod 不变）**。注册 `TaskServiceProtocol`/`TaskLoopCallbackProtocol` 供 router 注入。对齐现有 module 风格（`@inject`/`Injected[...]`/`Module(...)`）。
- **T2.4** **App include task router**（env-gated，与 `task_module` 同步开启；router-hit 进 singlebox coverage 自动命中）。
- **T2.5** **契约测 `test_task_http_adapter.py`**（Rule 25）：TestClient + noop engine 注入，验证 `execute→/dashboard→/callback/report` 协议（状态码/DTO/回投翻态）。
- ✅ **G2 验收**：T2.5 绿；router 无领域策略；prod 默认 stub `ExecutionEngine` 不变；`TaskService` 经 HTTP 可达、回投可入口。

---

## G3 — 三个真实 skill 包（`tests/.../task/singlebox_e2e/skills/`，运行时上传激活）

> 每包：`SKILL.md`（触发条件 + I/O 契约）+ 确定式 scaffold（对齐 `gwqie46v7hzr1w6h`，非自由 LLM）。**案例知识只在此**。

- **T3.1** **planning**：输入 `{goal, context, target_node, graph_snapshot, gaps}` → 输出 `{children:[{node_id, instruction}]}` 或 `{}`；按案例剧本产 root→N_overview→{N_market,N_tech,N_compete,N_customer}→N_practice_bbs→N_report，FAIL+gaps 叶产补救子。
- **T3.2** **search**：输入 `{node, catalog}` → 输出 `{outcome, bot_id?|bot_ids?, collab_mode?, group_name?, members_info?, definition_yaml?}`；按 node→worker bot 映射（多 bot→`HIT_MULTI_BOTS` + `GroupFormation`；单→`HIT_SINGLE`；已有群→`HIT_GROUP`；都不匹配→`MISS`）。
- **T3.3** **acceptance(verify)**：输入 `{child_outputs, goal, acceptances, node_instruction}` → 输出 `{verdict:PASS|FAIL, acceptances_metric, gaps?}`；内部节点产 PASS；FAIL 用例产指定 gaps 驱动治愈；根节点产终验 PASS（根 DONE）。
- **T3.4** 打包为 upload zip（对齐 `/api/skills/upload` 期望结构）。
- ✅ **G3 验收**：3 skill 包可被 `/api/skills/upload` 接受；本地 dry-run（`_DoubleOpenApiBot` 等价语义）产出与 `test_e2e.py` 案例期望一致。

---

## G4 — 真实 case 集成用例（`tests/.../task/singlebox_e2e/test_realcase_e2e.py`）

> gated：`pytest` skip 除非 `SINGLEBOX_TASK_E2E=1` 且 `GET /api/.../health` 绿。async httpx client（复用 singlebox client 模式）。

- **T4.1** **session fixture**（gated + health wait）：async httpx client；cleanup；确保 `TASK_ENGINE=skill` 生效（singlebox 起态或运行时切换）。
- **T4.2** **setup**（AC-1）：create owner bot + worker bots（供应链专家/行业信息抓取/报告聚合/实践 Bot 等，按案例 catalog）；upload 3 skill 包 → `/api/skills/upload`；activate skillset 到 owner bot；查 `/api/skills/.../active` 确认激活。
- **T4.3** **drive**（AC-2）：`POST /openapi/v1/collaboration/tasks/execute`，body = `gwqie46v7hzr1w6h` TaskInfo（`metadata.task_id` 固定；`goal.objective`=产出尽调报告；5 acceptances；`execution_config`: `MAX_DEPTH`/`BBS_MAX_DEPTH`；`owner_bot_id`=owner bot）。轮询 `GET /openapi/v1/collaboration/tasks/dashboard?task_id=`（带超时保护）至 `status ∈ {DONE, HUNG}`。
- **T4.4** **R3 并发锁验证**：观察并发回投下状态正确性（poller 线程 vs 请求线程）；若竞态落 T1.9 `_lock_factory` seam，否则记录"RLock 成立"。
- **T4.5** **assert happy（AC-3/AC-4）**：分解树 root→N_overview→{N_market,N_tech,N_compete,N_customer}→N_practice_bbs→N_report（结构由 skill 产出）；run_mode/assignee 由搜推 skill 决出（多 bot 维度→`coop_group`+`GroupFormation`；其余 `single_bot`）；执行结果经 `POST /openapi/v1/collaboration/tasks/callback/report`→`on_report` 翻态/传播；终态 root `DONE`、图 `output.result=all_done`；`GET /openapi/v1/collaboration/tasks/dashboard` 反映分解树推进。
- **T4.6** **assert recover（AC-5）**：第二 TaskInfo 或同案例注入 FAIL 路径：某叶验收 FAIL+gaps → planner 产补救子 → 重投 → PASS → 上行传播至 DONE。断言轨迹 `FAILED→PLANNING→补救子→DONE` + gaps 消解。
- **T4.7** **（可选，时间盒）BBS/STUCK**：MISS→升 BBS→BBS 认领子→DONE；STUCK→HUNG（`BBS_MAX_DEPTH` 达上限）。
- **T4.8** **AC-8 回归断言**：`grep` 框架代码（`core/task` + `adapters/http/task` + `di/modules/task_module`）无节点名字面量（`N_overview`/`N_market`… 只出现在 skill 产出/测试）。
- ✅ **G4 验收**：T4.5/T4.6 在 singlebox real 跑绿；用例 gated，默认 `pytest` skip。

---

## G5 — singlebox 运行入口与门禁

- **T5.1** **`scripts/ci/singlebox_task_e2e.sh`**：封装 `singlebox_coverage.sh --mode real --module task_e2e`（或独立起栈）→ health wait → `SINGLEBOX_TASK_E2E=1 TASK_ENGINE=skill pytest tests/.../singlebox_e2e` → 报告。
- **T5.2** **`scripts/ci/singlebox_coverage_modules.yaml`**：新增 `task_e2e` 模块 + acceptance target（指向用例），支持 `--module task_e2e` 单跑（R5）。
- **T5.3** **文档运行说明**：spec 目录补"如何起、如何单跑、gated 条件、已知限制（poller daemon 线程时效内回投、`group_name` 经 context/topic 透传）"。
- ✅ **G5 验收**：`./scripts/ci/singlebox_task_e2e.sh` 本地可复现跑绿；默认 CI/pre-push 不强制（opt-in）。

---

## 验收总览（对齐 spec §6 AC）
- AC-1 ✅ T4.2 ｜ AC-2 ✅ T4.3+T1.2/T1.3 ｜ AC-3 ✅ T1.6+T2.2 ｜ AC-4 ✅ T4.5 ｜ AC-5 ✅ T4.6 ｜ AC-6 ✅ T1.8/T2.5 ｜ AC-7 ✅ T5.1/T5.2 ｜ AC-8 ✅ T4.8

## 开放问题（plan §10，implement 阶段先收敛）
- OQ-1 激活 env（倾向 `TASK_ENGINE=skill`）｜ OQ-2 验收 skill 宿主=owner bot（方案A，已定）｜ OQ-3 coop_group 走真实 BCS（已定）｜ R3 锁模型由 T1.9/T4.4 定 ｜ R1 已由同学收敛。

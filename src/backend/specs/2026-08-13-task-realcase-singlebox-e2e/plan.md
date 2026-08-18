# 任务模块真实case本地singlebox端到端集成用例 — 技术方案 (HOW)

- **日期**: 2026-08-13
- **依赖**: `spec.md`（同目录）；权威框架 `src/backend/specs/2026-08-09-task-goal-driven-execution-framework/`
- **代码权威源**: Avernet `src/backend`（ocb-public 只读镜像，不改）

---

## 0. 现状盘点与缺口（grounded）

| 件 | 现状 | 缺口 |
|---|---|---|
| `core/task` 内核（domain/graph/engine/planner/dispatcher/runner/harness/facade） | M0–M5 落地，全链路 async，seam 齐全 | —— |
| `TaskPlanner` 策略 | `WorkflowPlanningStrategy`(yaml stub) + `GapBasedPlanningStrategy`(返 []) | 无真实拆解 |
| `TaskDispatcher` 策略 | `DirectDispatchStrategy` + `SearchBasedDispatchStrategy`(恒 MISS) | 无真实搜推 |
| `TaskRunner` 投递 | `DeliveryPort` seam + stub（记日志返 True） | 无真实投递 |
| 回投 | `TaskLoopCallback.report_result` → `on_report`（adapter 在 `callback_adapter.py`） | 仅 in-process 被调 |
| `TaskService` 接线 | **未装配**：无 `task_module.py`、无 task HTTP router、`TaskService(` 仅自身 core 构造 | **运行态不可达** |
| 现有 e2e | `tests/community/core/task/e2e/test_e2e.py`：in-process stub（`CaseDecomposer`/`CaseBotDiscover`+stub runner）跑 `gwqie46v7hzr1w6h` 全链路 | **seam 真值未验**；保留为内核快测 |
| skill 运行时 | `/api/skills`（upload/activate/skillset 安装到 bot）；skill 经 bot 引擎（OpenClaw）随指令运行 | 无"投上下文→取结构化结果"的显式 task seam |
| singlebox | `singlebox_coverage.sh --mode real` + local plugins（`engine_ext_client`/`sandbox_client`/`skill_center_client`/`process_manager`…） | 无 task e2e 模块/acceptance target |

**结论**：本轮 = ①补真实 skill-backed 适配层②补 DI/HTTP 接线③产三个真实 skill 包④写真实 case 集成用例⑤singlebox 入口与门禁。内核零改动（除 R3 锁模型若需，最小 seam）。

---

## 1. 整体集成拓扑

```
┌────────────────────── singlebox（real mode）──────────────────────┐
│  backend(8888) FastAPI                                            │
│   ┌─────────────────────────────────────────────────────────┐     │
│   │ adapters/http/task/router.py  (thin)                    │     │
│   │   POST /openapi/v1/task/execute                                 │     │
│   │   GET  /openapi/v1/task/dashboard                               │     │
│   │   POST /openapi/v1/task/callback/report   ← 真实回投HTTP入口    │     │
│   └──────────────┬──────────────────────────────────────────┘     │
│      di/modules/task_module.py (composition root)                  │
│      TaskService(graph, harness) → ExecutionEngine(注入 skill-backed 策略/投递) │
│   ┌──────────────┴──────────────────────────────────────────┐     │
│   │ ExecutionEngine(_build_runner 注 TaskExecutor)             │     │
│   │  planner: GapBasedPlanningStrategy(body真实) ─┐               │     │
│   │  dispatcher: SearchBasedDispatchStrategy ┼─► OpenApiBotPort   │
│   │   (send_and_wait_async 同步收结果)         │   (integration/) │
│   │  runner._execution_backend = TaskExecutor   │                  │     │
│   │   └─ dispatch(三模态): send_message→poller→report_result     │     │
│   │       └─► POST /openapi/v1/task/callback/report (回投闭环)          │
│   └────────────────────────────────────────────────────┘         │
│        ▲ bot/skill 真实资源（经 /api 创建+安装）：                  │
│        │  owner bot（规划/搜推/验收 skill 激活）                    │
│        │  worker bots（供应链/行业信息/报告聚合… 各 1）             │
└────────┼──────────────────────────────────────────────────────────┘
         │ async httpx
┌────────┴─────────── 集成用例 test_realcase_e2e.py ────────────────┐
│ setup: 创建 owner+worker bots；上传+激活3 skill                    │
│ drive: POST /execute(gwqie46v7hzr1w6h) → poll dashboard → assert   │
│ recover: 断言 FAIL治愈轨迹（可选 BBS/STUCK）                        │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. 真实实现适配层（`plugins/local/task/`，Rule 20 local）

> **定位原则（评审纠偏）**：任务执行引擎只有一套 `ExecutionEngine`，**不变**。skill 安装到 bot、bot 创建、singlebox 起全栈都是**前置环境准备 / 运行环境**，与任务执行逻辑无关。框架不感知 bot 装了哪些 skill、不感知 bot 内部怎么执行/验收，只负责：组装 prompt 上下文 → 投给指定 bot → 回收结构化结果 → 更新图谱 → 驱动流程往下。
>
> **策略类名/接口不变，只实现体从 stub 变真实**：`GapBasedPlanningStrategy` / `SearchBasedDispatchStrategy` / `DeliveryPort` 都是框架自带默认策略（Avernet stub），类名、方法签名、策略池结构都不变。本轮只把它们的**实现体**从 stub（返 `[]` / 恒 MISS / 记日志）改为真实实现（组 payload → 投 bot → 收结构化 result）。corp 接真实 LLM 同理替换 body，seam 不变。
>
> **结果回收分两种**：
> - **plan/dispatch 同步收 result**（`await apply()` 当返回值直接拿，引擎锁内已 await，不需异步回投）。
> - **execute/verify 经 `report_result` 异步回投**（`DeliveryPort.deliver` 投给 bot → bot 执行完凭 skill 上报 → `POST /callback/report` → `on_report` 翻态推进）。

### 2.1 引擎注入策略（`ExecutionEngine` 不变，经 seam 注入真实实现 body）
不引入引擎子类。`task_module` 装配 `ExecutionEngine` 时，`_build_runner` 注入同学已实现的 `build_integration()` 返回的 `TaskExecutor`（三模态投递+poller+回投），`set_strategies` 注入真实 plan/dispatch 策略 body（各自持 `OpenApiBotPort` 调 `send_and_wait_async`）。

```python
# task_module 装配(伪码):引擎始终 ExecutionEngine,_build_runner 注 TaskExecutor + 策略 body 真实化
def build_task_service(graph, bot: OpenApiBotPort, keys: ApiKeyProvider, report_endpoint: str):
    def _build_engine():
        eng = ExecutionEngine(graph)
        # plan/dispatch 策略 body 真实化:各自持 bot 调 send_and_wait_async(同步 round-trip 取结果)
        eng._planner.set_strategies([GapBasedPlanningStrategy(bot)])           # apply 内 await bot.send_and_wait_async(owner,"planning-prompt")
        eng._dispatcher.set_strategies([DirectDispatchStrategy(),
                                        SearchBasedDispatchStrategy(bot, discover)])  # apply 内两步:预查候选→ await bot.send_and_wait_async(owner,"search-prompt")
        # execute 投递:同学的 TaskExecutor 三模态(send_message→poller→report),经 build_integration 装配
        executor = build_integration(double=False, sink=sink, runner=eng._runner)
        eng._runner._execution_backend = executor       # runner.start_run 优先走 executor.dispatch(三模态)
        return eng
    return TaskService(graph)
# 注:TASK_ENGINE=skill 时注入真实 body;否则默认 stub(prod 不变)
# OpenApiBotPort 经 ApiKeyProvider(base_url/api_key/cookie/referer)配置注入(local:8888 / prod URL)
```

### 2.2 `GapBasedPlanningStrategy`（现有类，实现体 stub→真实）
- 类名/签名不变（`async matches` / `async apply`）。body 从 stub 返 `[]` → 真实：
- `apply(graph)`（`async`）：自发现可规划目标（根 PENDING / PLANNING 父 / FAILED+gaps 叶——复用 `_find_planning_target` 派生逻辑，**不硬编码节点名**）；组装 prompt（`{goal, context, target_node, graph_snapshot, gaps}`）→ `await self._bot.send_and_wait_async(bot_id=owner, message=prompt)` → **同步收** round-trip 结果 `run{status,result,error}` → 解析 `result.content` JSON `{children:[{node_id,instruction}]}` 或 `{}` → 造 `TaskNode[]`（PENDING，空 RuntimeInfo）。
- **plan 同步收 result 当返回值**（引擎锁内 `await apply()` 直接拿子任务列表，不需异步回投）。产出节点名来自 bot skill（案例知识在 skill，不在框架）。`send_and_wait_async` 是同学暴露的 public async round-trip 口（plan/dispatch 专用同步取结果路径）。

### 2.3 `SearchBasedDispatchStrategy`（现有类，实现体 stub→真实）
- 类名/签名不变。body 从恒 MISS → 真实。**dispatch 不是简单查找，是"读子任务需求 → 决出谁执行+怎么执行"的决策能力**——搜推 skill（owner bot LLM）在框架预查的候选集里推理决出 `SearchResult`。
- `apply(node, graph)` 两步：
  1. **框架预查候选集**（A 方案）：`await asyncio.to_thread(discover.search_by_keyword, query_text(node), owner_id, top_k=10, min_score=0.01, filters={"runtime_state":["online"]})` → `candidates.items[]`（`{bot_id, bot_name, bot_desc, recommend{score, short_profile, reasons}}`，按 score 降序）。`query_text(node)` = `title + objective + background` 整段不拆（语义搜）。候选集是搜推 skill 决策的**输入**，不是 dispatch 最终结果。
  2. **投 owner bot search skill 决策**（同步 round-trip）：组 prompt（含子任务需求 `{goal, instruction, acceptances}` + 图态快照 + 候选集 candidates）→ `await self._bot.send_and_wait_async(bot_id=owner, message=prompt)` → 同步收 round-trip 结果 `run{status,result,error}` → 解析 `result.content` JSON → `_parse_search_result` 映射 `SearchResult`。
- **`SearchResult` 4 态**（搜推 skill 决出，框架只解析透传）：
  - `HIT_SINGLE(bot_id)` —— skill 决出单 bot 够完成。
  - `HIT_MULTI_BOTS(GroupFormation)` —— skill 决出需多 bot 协同，携**怎么组织**细节（见 §2.3.1）。
  - `HIT_GROUP(group_id)` —— skill 决出已有协作群可复用。
  - `MISS(miss_reason)` —— 候选集都不匹配，触发升 BBS 链路。
- **dispatch 同步收 result 当返回值**（引擎锁内 `await apply()` 直接拿 SearchResult，不需异步回投；`DirectDispatchStrategy` priority 10 优先匹配 `execution_config.bot`）。
- **搜推 skill 不自取 BCSFuse**：候选集由框架预查喂入 prompt，skill 只在给定候选里决策 who+how，不带外部工具调用。owner bot 只装 planning/search/verify 三个 skill。

### 2.3.1 `GroupFormation` 扩字段（承载搜推 skill 决出的"怎么执行"细节，透传 BCS 建群）
现有 `GroupFormation(bot_ids, collab_mode, extend_props)` 装不下 case 要求的"协作群名 + 各 bot 角色分工 + workflow yaml"。扩 3 可选字段（不动核心领域模型）：
```python
@dataclass
class GroupFormation:                       # 现有,扩字段
    bot_ids: list[str]                      # 现有
    collab_mode: str                        # 现有,"chat"/"manager_worker"/"state_machine"(对齐 BCS group_strategy)
    group_name: str | None = None           # 新增: skill 决出的协作群名 → BCS label
    members_info: list[dict] | None = None  # 新增: [{bot_id, role, responsibility}] 分工 → BCS participants[].role
    extend_props: dict[str, Any] = field(default_factory=dict)
    # state_machine 模式 → extend_props["definition_yaml"] = workflow yaml → BCS collaboration_definition_yaml
```
case 示例（专题A）：`GroupFormation(bot_ids=["X","Y"], collab_mode="manager_worker", group_name="存储行业市场发展趋势研究群", members_info=[{bot_id:"X",role:"市场需求和规模发展分析专家",responsibility:"规模/增速/出货量"},{bot_id:"Y",role:"资本市场投资趋势分析专家",responsibility:"资本开支周期"}])`。

### 2.4 三模态执行投递 + 旁路 poller 回投（同学已实现 `integration/`，接线即可）
- **不再用 `DeliveryPort`/`HttpBotDeliveryPort`**——投递/回投由同学的 `TaskExecutor` + `TaskExecutorResultPoller` 实现（已在 `core/task/task_runner/integration/`）。`TaskRunner.__init__` 已有 `execution_backend: TaskExecutor | None` 接线点（`start_run` 优先走 `execution_backend.dispatch`）。
- `TaskExecutor.dispatch(toDoTaskList)` 三模态（async，`start_run` 锁外 gather+Semaphore 并发）：
  - `single_bot` → `ensure_grant` → `context.build` → `formatter.format_execute` → `send_message` 拿 `run_id` 立返回 → poller 登记回收。
  - `coop_group`（`collab_mode=chat/manager_worker`）→ `create_session(group_id, bootstrap_prompt)` → poller 登记回收。
  - `coop_group`（`state_machine`）→ `start_state_machine_run(group_id, definition_ref, input)` → poller 登记回收。
  - `bbs` → no-op 记日志（bot 认领自规划执行）。
- **execute/verify 经 poller 异步回投**：`TaskExecutorResultPoller` 旁路 daemon 线程轮询 `get_run`/`get_group`/`get_session_messages` 到终态 → 三翻译器（`SingleBotRunTranslator`/`BcsSessionTranslator`/`BcsStateMachineRunTranslator`）→ `TaskCallbackData` → `ResultSink.report_result` → `on_report` 翻态推进。SLA 超时→FAIL sla_timeout；连续失败→FAIL poll_exhausted。
- **verify 投递**（聚合验收 → owner bot）：节点有结构子（`get_child_tasks` 非空）时进 verify 模式，`formatter.format_verify` 组聚合 prompt（`child_outputs+goal+acceptances`）→ 投 owner bot → poller 回投 `verdict`（continue Pell 机制下一轮）。

### 2.5 `form_coop_group` 对接 BCS 真值（同学已实现 `TaskExecutor.form_coop_group` + `BcsHttpAdapter.create_group`）
- **`TaskExecutor.form_coop_group(GroupFormation) -> group_id`**（async，engine 锁外 await）：见 `integration/task_executor.py`。映射 `GroupFormation` → `BcsCreateGroupRequest` → `BcsHttpAdapter.create_group` → `BcsCreateGroupResult{group_id, session_id?, run_id?, definition_ref?}`。
  - `chat` → `group_strategy` 省略（BCS 默认 chat 模态）。
  - `manager_worker` → `group_strategy="manager_worker"`，`driver_bot=manager_bot_id`（`extend_props["manager_bot_id"]` 或 `bot_ids[0]`），`participants=[{manager,role:manager}]+[{worker,role:worker}]`。
  - `state_machine` → `group_strategy="state_machine"` + `collaboration_definition_yaml=extend_props["definition_yaml"]`（workflow yaml）+ `participant_bindings` + `start_initial_run=False`。
- **`BcsHttpAdapter`**（`integration/bcs_http_adapter.py`，HMAC 签名 auth）：三态建群已实现，singlebox `all` 起栈含 BCS 可真实建群验证。
- **`TaskExecutor._group_meta[group_id]`** 记 `{collab_mode, gf, definition_ref, session_id}` 供 `_dispatch_coop_group` 分流（chat/manager_worker 走 session 模；state_machine 走 run 模）。
- **`GroupFormation` 扩字段**：`group_name`/`members_info`/`extend_props["definition_yaml"]`（与 §2.3.1 一致，透传 BCS）。`members_info.role` 在 `manager_worker` 模由 `form_coop_group` 内按 manager/worker 角色套默认（除非 `extend_props["manager_bot_id"]` 指定）；`group_name` 在 `BcsCreateGroupRequest` 当前无 `label` 字段，经 `context`/`topic` 透传或后续 BCS client 补。
- **OQ-3（已定）**：coop_group 走真实 BCS，已落地。

### 2.6 内核 seam（同学已落地 build_context；R3 锁待验证）
- **`TaskContextBuilder` seam 已落地**：同学实现 `_RunnerContextBuilder`（`integration/prompt_formatter.py`）经 `build(task_id,node_id)` 调 `runner._build_context`（execute/verify 模式由 runner 内聚判定），已在 `TaskExecutor` 接线使用。**无需新增公开方法**。
- **R3 锁模型**：poller 模型下回投经旁路 daemon 线程 `run_until_complete` → `on_report`（同步调 `engine`，持 per-task `threading.RLock`）。先验证 `threading.RLock` 在「poller 线程 + 请求线程」跨线程回投下成立。若竞态，最小 seam 引入 `_lock_factory`（`asyncio.Lock` 选项）。待 T4 验证。

---

## 3. `OpenApiBotPort`（同学已实现 `OpenApiBotAdapter`，R1 已收敛）

```python
class OpenApiBotPort(Protocol):                # integration/ports.py(同学已落地)
    async def ensure_grant(self, bot_id: str) -> None: ...
    async def send_message(self, *, bot_id, message, metadata) -> str: ...
    async def get_run(self, run_id: str) -> dict[str, Any]: ...
    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                  timeout=180.0, poll_interval=2.0) -> dict[str, Any]: ...
```
- **two 真值口**：
  - `send_and_wait_async`（public async round-trip）→ **plan/dispatch 同步取结果**（`await` 拿终态 `run{status,result,error}`）。本 spec T0 已把 `_send_and_wait_async` 暴露为 public（去下划线）。
  - `send_message`+`get_run` → **execute 异步投递**（`TaskExecutor` 拿 `run_id` 立返回，poller 旁路轮询，见 §2.4）。
- **local 实现 `OpenApiBotAdapter`**（`integration/open_api_bot_adapter.py`，同学已实现）：`ensure_grant`→`send_message`→轮询 `get_run` 到 COMPLETED/FAILED。
- **double/_Noop 实现 `_DoubleOpenApiBot`**（`integration/double/double_open_api_bot.py`，同学已实现）：进程内模拟 grant/send/poll（不经网络），进程内摸供给单测/契约测复用。废弃此前的 `NoopSkillInvoker`/`EchoSkillInvoker` 命名。

---

## 4. 后端接线（DI + HTTP adapter，补 G2 缺口）

### 4.1 `di/modules/task_module.py`
- 装配 `TaskGraphService`（in-memory/local）、`TaskHarness`、`ApiKeyProvider`、`OpenApiBotAdapter`（`OpenApiBotPort`）、`BotService`/`BotDiscoverService`、`TaskService`。
- **引擎始终 `ExecutionEngine`**：`TASK_ENGINE=skill` 时,`_build_runner` 注入 `build_integration(double=False, sink=callback, runner=...)` 返回的 `TaskExecutor`(三模态投递+poller,同学已实现),`_build_planner/_build_dispatcher` 经 `set_strategies` 注入真实 body 策略(各自持 `OpenApiBotPort` 调 `send_and_wait_async`)。否则默认 stub `ExecutionEngine`(prod 不变)。
- **配置注入**:`OpenApiBotPort` 经 `ApiKeyProvider`(base_url/api_key/cookie/referer)local→localhost:8888 / prod→生产 URL。`report_endpoint` 同理。
- poller daemon 线程由 `build_integration(poller_thread=True)` 起动。
- 注册 `TaskServiceProtocol`、`TaskLoopCallbackProtocol` 供 router 注入。
- 对齐现有 module 风格（`@inject`、`Injected[...]`、`module = Module(...)`）。

### 4.2 `adapters/http/task/router.py`（thin）
```python
router = APIRouter(prefix="/openapi/v1/task", tags=["task"])
@router.post("/execute")        # TaskInfoDTO -> TaskOpResultDTO   (async)
@router.get("/dashboard")       # ?task_id=&node_id= -> TaskExecutionGraphDTO
@router.post("/callback/report")# TaskCallbackDataDTO -> {"ok": true}  (async, 调 callback.report_result)
```
- schema 在 `adapters/http/task/schemas.py`（DTO↔domain `TaskInfo/TaskCallbackData/TaskExecutionGraph/TaskOpResult` 转换）。
- router 不持领域策略（Rule 22）：只转换 + delegate。
- 进 singlebox coverage：`install_singlebox_coverage_middleware` 自动命中。

### 4.3 App include
- 在 router 注册表（`optional_routers.py`/bootstrap app factory）include task router；env-gated 与 task_module 同步开启。

### 4.4 契约测试（Rule 25）
- `tests/.../task/contracts/test_task_http_adapter.py`：用 TestClient + noop engine 注入，验证 execute→/dashboard→/callback/report 协议（状态码/DTO/回投翻态）。
- `tests/.../task/contracts/test_open_api_bot_port.py`：`OpenApiBotAdapter` vs `_DoubleOpenApiBot`，契约（`send_and_wait_async` round-trip schema / `send_message`+`get_run` poll）。

---

## 5. 三个真实 skill 包（`tests/.../task/singlebox_e2e/skills/`，运行时上传激活）

每包：`SKILL.md`（触发条件 + I/O 契约）+ scaffold（确定式产出，对齐 `gwqie46v7hzr1w6h`）。**案例知识只在此**。

| skill | 触发 | 输入 payload | 输出 |
|---|---|---|---|
| **planning** | owner bot 收到规划指令 | `{goal, context, target_node, graph_snapshot, gaps}` | `{children:[{node_id,instruction}]}` 或 `{}` |
| **search** | owner bot 收到搜推指令 | `{node, catalog}` | `{outcome, bot_id?‖bot_ids?, collab_mode?}` |
| **acceptance**(verify) | owner bot 收到验收指令 | `{child_outputs, goal, acceptances, node_instruction}` | `{verdict:PASS‖FAIL, acceptances_metric, gaps?}` |

- 确定式 scaffold：按 `target_node.node_id`/`node.node_id` 返回案例剧本对应子节点/匹配 bot/PASS/FAIL+gaps（与现 `CaseDecomposer`/`CaseBotDiscover` 同语义，但作为 **真实安装 skill** 运行）。
- 验收 skill 对 `gwqie46v7hzr1w6h` 内部节点产 PASS；若设注入"FAIL+gaps"用例则产 FAIL+指定 gaps 驱动治愈（AC-5）。
- skill 经 `/api/skills/upload`（zip）上传、skillset activate 安装到 owner bot；用例 setup 完成。

---

## 6. 集成用例（`tests/.../task/singlebox_e2e/test_realcase_e2e.py`）

- **gated**：`pytest` skip 除非 `SINGLEBOX_TASK_E2E=1` 且 `GET /api/.../health` 绿。
- async httpx client（复用 singlebox client 模式）。

### 6.1 setup（fixture，session 级）
1. create owner bot（`/api/bot_management` create）+ worker bots（供应链专家Bot/行业信息抓取Bot/报告聚合Bot/实践Bot，按案例 catalog）。
2. upload 3 skill 包 → `/api/skills/upload`；activate skillset 到 owner bot → 查 `/api/skills/.../active` 确认。
3. 确保 `TASK_ENGINE=skill` 生效（singlebox 起态或运行时切换）。

### 6.2 drive
- `POST /openapi/v1/task/execute`，body = `gwqie46v7hzr1w6h` TaskInfo（metadata.task_id 固定；goal.objective=产出尽调报告；5 acceptances；execution_config: MAX_DEPTH/BBS_MAX_DEPTH；source_channel_id=owner bot）。
- 轮询 `GET /openapi/v1/task/dashboard?task_id=`（带超时），至 `status ∈ {DONE, HUNG}`。

### 6.3 assert（happy，AC-4）
- 分解树：root→N_overview→{N_market,N_tech,N_compete,N_customer}→N_practice_bbs→N_report（结构由 skill 产出）。
- 节点 run_mode/assignee：搜推 skill 决出（N_market 等多 bot→coop_group；其余 single_bot）。
- 终态 root `DONE`；图 `output.result=all_done`。
- 框架代码 grep 无节点名（AC-8 回归断言）。

### 6.4 assert（recover，AC-5）
- 第二 TaskInfo（或同案例注入 FAIL 路径）：某叶验收 FAIL+gaps → planner 产补救子 → 重投 → PASS → 上行传播至 DONE。断言 `FAILED→PLANNING→补救子→DONE` 轨迹 + gaps 消解。
- 可选 BBS（MISS→升 BBS→BBS 认领子→DONE）与 STUCK→HUNG（BBS_MAX_DEPTH 达上限）作第二/三用例，时间盒内做。

### 6.5 teardown
- 清理 task/bot/skill（最佳努力；singlebox 数据可重建）。

---

## 7. singlebox 运行入口与门禁

- `scripts/ci/singlebox_task_e2e.sh`：封装 `singlebox_coverage.sh --mode real --module task_e2e`（或独立起栈）→ health wait → `SINGLEBOX_TASK_E2E=1 TASK_ENGINE=skill pytest tests/.../singlebox_e2e` → 报告。
- `singlebox_coverage_modules.yaml`：新增 `task_e2e` 模块 + acceptance target（指向用例），使其可 `--module task_e2e` 单跑（R5）。
- 默认 CI/`pytest` 不开（gated）；pre-push 不强制（opt-in）。

---

## 8. 文件落点图（全在 Avernet `src/backend`）

| 层 | 路径 | 内容 |
|---|---|---|
| 三模态执行接入(**同学已实现**) | `core/task/task_runner/integration//{ports,open_api_bot_adapter,bcs_http_adapter,task_executor,task_executor_result_poller,prompt_formatter,translators}.py` + `integration/double/*` | `OpenApiBotPort`/`BcsClientPort`/`TaskExecutor`/`poller`/double |
| plan/dispatch 策略 body | `core/task/task_plan/strategies.py` / `task_dispatch/strategies.py` | `GapBasedPlanningStrategy`/`SearchBasedDispatchStrategy` body stub→真实(持 `OpenApiBotPort` 调 `send_and_wait_async`) |
| `GroupFormation` 扩字段 | `core/task/task_dispatch/strategies.py` | +`group_name`/`members_info`/`extend_props["definition_yaml"]` |
| DI | `src/agentclaw/community/di/modules/task_module.py` | 装配 TaskService `_build_runner` 注 `TaskExecutor`/`set_strategies` 注真实 body |
| HTTP adapter（thin） | `src/agentclaw/community/adapters/http/task/{router,schemas}.py` | execute/dashboard/callback |
| app include | router 注册处（`optional_routers.py`/bootstrap） | include task router（env-gated） |
| skill 包(fixture) | `tests/community/core/task/singlebox_e2e/skills/{planning,search,acceptance}/` | SKILL.md + scaffold |
| 集成用例 | `tests/community/core/task/singlebox_e2e/test_realcase_e2e.py` | setup/drive/assert/recover/teardown |
| 契约测 | `tests/community/core/task/contracts/test_task_http_adapter.py`、`test_open_api_bot_port.py` | Rule 25 |
| 入口/门禁 | `scripts/ci/singlebox_task_e2e.sh` + `singlebox_coverage_modules.yaml` 增项 | run/gate |
| 可能内核 seam | `core/task/task_center/engine.py`（`_lock_factory` 仅 R3 需要） | 最小、待审议 |

---

## 9. 微内核宪法遵守

- **Rule 14**：`task_module.py` DI 装配；引擎选择经 env/config，无 `if is_local` 散落。
- **Rule 20**：`TaskExecutor`/`OpenApiBotAdapter`/`BcsHttpAdapter`/double 为 `integration/` 实现;prod(corp LLM/BCS)后续在 ocb 侧覆写 `build_integration` 注真实后端即可(seam 已留)。引擎始终 `ExecutionEngine`,不引入子类。
- **Rule 21**：`_DoubleOpenApiBot`/`_DoubleBcsClient`(double) + 现有 stub 策略留作 noop/mock。
- **Rule 22**：`adapters/http/task` 只转协议；新模块 README 声明 `## Context Boundary`。
- **Rule 25**：task HTTP adapter + `OpenApiBotPort`/`TaskExecutor` 契约测试。
- **框架零 case 知识**：节点名只出现在 skill 产出/测试；适配层经派生查询自发现目标，不硬编码。

---

## 10. 风险收敛（对应 spec §7）

- **R1（已定，同学实现）**：真值 = `OpenApiBotAdapter`（`send_and_wait_async` round-trip）；本 spec 已把 `_send_and_wait_async` 暴露为 public `send_and_wait_async`。
- **R2（已定）**：回投 = 旁路 poller 模型（`TaskExecutorResultPoller` daemon 线程轮询终态→`ResultSink.report_result`），确定性优先；真实异步引擎 webhook 回调列为后续硬化（不在本轮）。
- **R3（锁模型）**：先验证 `threading.RLock` 在 singlebox 回投模型下成立（集成用例即压力）；若竞态，最小 seam 引入 `_lock_factory`（`asyncio.Lock` 选项）。待 T 验证。
- **R4（确定性）**：skill 为确定式 scaffold（非自由 LLM）；可配 singlebox mock model config。
- **R5（启停开销）**：gated + `--module task_e2e` 单跑 + 复用 coverage harness。
- **R6（catalog）**：用例 setup 创建固定 worker bot 集；搜推 skill 按 node→bot 映射（案例剧本）。
- **OQ-1**：`TASK_ENGINE=skill`（或 `RUNTIME_MODE` local/dev）激活，prod 默认不变。
- **OQ-2**：验收 skill 跑在 owner bot（`source_channel_id`）。
- **OQ-3（已定）**：coop_group 走真实 BCS（singlebox 起栈含 BCS）；form_coop_group 对接 BCS POST /groups，group_strategy 三态对齐 CollaborationRuntimeDefinition。

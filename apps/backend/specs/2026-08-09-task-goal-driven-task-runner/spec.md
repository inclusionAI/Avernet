# Spec: task_runner integration 子模块（单 bot / 协作群真实执行接入）

- 日期：2026-08-13
- 分支：`feat/task-goal-driven-collab-dev`
- 范围模块：`src/backend/src/agentclaw/community/core/task/task_runner/integration/`
- 上游 spec：`specs/2026-08-09-task-goal-driven-execution-framework/`（任务目标驱动执行框架，M0–M6 已落地）
- 适用代码约定：遵循 `AGENTS.md`（不引入 `T | None` 除非 None 是契约态；必填值非可选）

---

## 1. Problem Statement

任务目标驱动执行框架（上游 spec）当前 `TaskRunner.start_run` / `form_coop_group` 是 **stub**：三模态（`single_bot` / `coop_group` / `bbs`）只记投递日志返回 `True`、`form_coop_group` 生成伪 `grp_{uuid}`，**不真实发起任何 bot 执行或协作群**。`_build_context`（指令组装）已实现但 `start_run` 未调用它。结果回投全靠测试 stub 手动 PUSH。

本 spec 设计 `task_runner/integration` 子模块，把 stub 替换为**真实执行接入**：

- **单 bot 执行**：经 BaaS **Open API**（参考 `create_api_key.py` / `send_bot_message.py` 的 `messages` 模式）——用**静态配置的 App API Key**，先校验/补 `allowed-bots` grant，再 `POST /openapi/v1/messages` 发消息，**拿到 `run_id` 即派发返回，不轮询、不等待结果**；结果由旁路 poller 回收。
- **协作群执行**：参考 corp ocb `BcsHttpClient` 的 `create_group → create_session` 模式，在 Avernet 内重写一个**自包含 BCS HTTP client**，支持 `GroupStrategy` 三态（`chat` / `manager_worker` / `state_machine`），并经轮询回收结果。
- **bbs 模式**：`TaskExecutor` 内置第三种模态分流，**仅记日志、不发起任何执行**（沿用既有 `BbsMarketPort` seam）。

约束：Avernet 边界内**自包含**——单 bot 走 BaaS HTTP Open API（**不 import corp ocb `ecb`**，**不依赖 in-repo `secbaas` BotRunner**）；BCS client 在 Avernet 内自包含重写；singlebox 测试经注入 double 跑通真实集成形态。

---

## 2. Solution 概述

采用 **方案 A**：所有模态一律「派发即返回（fire-and-forget，捕获 `run_id` 后不轮询、不等待）+ 旁路 Poller 回收结果」，集成内置后台 executor 承载所有真实 async I/O。

```
start_run(sync, list[bool]=派发是否成功)
   │  入队 TaskJob
   ▼
TaskExecutor(自有后台事件循环线程, 所有真实 async I/O; 三模态分流)
   ├─ single_bot → OpenApiBotAdapter: 静态 API key 校验/补 grant(allowed-bots) → POST /openapi/v1/messages{bot_id,message} 取 run_id
   │                 → 登记 TaskExecutorResultPoller(single_bot 模); 派发不轮询、不等待结果
   ├─ coop_group → form_coop_group = BcsHttpAdapter.create_group(建群壳, 三态分流, state_machine start_initial_run=False)
   │                 ├─ chat / manager_worker → start_run: create_group→create_session(bootstrap_prompt) → 登记 TaskExecutorResultPoller(session 模)
   │                 └─ state_machine → start_run: create_group(start_initial_run=False)→POST /groups/{id}/state-machine-runs 取 run_id
   │                                      → 登记 TaskExecutorResultPoller(run 模)
   └─ bbs        → 仅记结构化日志(不发起执行、不改节点状态、不登记 poller; 沿用既有 BbsMarketPort seam)
   │
TaskExecutorResultPoller(旁路 sidecar, 同 TaskHarness 风格; 三模态回收)
   ├─ single_bot 模: GET /openapi/v1/messages/{run_id} → status ∈ {COMPLETED,FAILED} → SingleBotRunTranslator → on_report
   ├─ session 模:    get_group + get_session_messages(since cursor) → Session.status=Completed → BcsSessionTranslator → on_report
   └─ run 模:        GET /state-machine-runs/{run_id} → run.status 终态 → BcsStateMachineRunTranslator → on_report
```

结果统一回流到编排核 `ExecutionEngine.on_report`（经 `TaskLoopCallback.report_result`），与上游 spec 的回投契约一致。**不再有入站 PUSH 回调**——单 bot 与协作群统一为「派发取 `run_id` + 旁路 poller 回收」模型。

---

## 3. Constraints / Constraints

1. **不破坏上游契约**：`TaskRunner.start_run(toDoTaskList) -> list[bool]` 签名不变（同步）；`TaskLoopCallback.report_result(TaskCallbackData)` 不变；`loop_task_id = "task_id::node_id"` 不变；`CallbackAdapter.adapt` 不变。
2. **零 case 知识红线**：integration 只经 `_build_context` + `TaskNode` 字段组装指令，不得出现 `N_market` / `N_overview` 等节点名字面量（grep 0 命中，单测断言）。
3. **_开源边界_**：integration 不 `import` corp ocb `ecb` 包；单 bot 走 BaaS **HTTP Open API**（httpx async，对齐 `send_bot_message.py` 的 `/openapi/v1/messages`），**不 import in-repo `secbaas` BotRunner**；BCS client 在 Avernet 内自包含重写（httpx async + HMAC 签名，照搬 ocb `BcsHttpClient` 模式）。
4. **必填非可选**：遵循 `AGENTS.md`——端口必填参数不加 `| None`；None 仅用于契约态（如 `session_id is None` 触发 `create_session`、`bot_id` 不在 `allowed_bots` 触发 `ensure_grant`）。
5. **幂等**：BCS `create_group` 带 `Idempotency-Key` header；`start_run` 重试安全；grant 幂等（重复 grant 同一 bot 由服务端去重）；`on_report` 本身上游已幂等。
6. **向后兼容**：不注入 `execution_backend` 时，`TaskRunner` 保持现行 stub 行为（现有 singlebox e2e 不破）。

---

## 4. Scope

### In Scope
- `integration/` 子模块全部文件（见 §6 模块布局）。
- `TaskRunner` 改造：`start_run` / `form_coop_group` 委托注入的 `execution_backend`；`_build_context` 被真实调用。
- 单 bot 真实派发（BaaS Open API + 静态 App API Key + 主动 `allowed-bots` grant）+ 旁路 poller 回收 + 翻译。
- 协作群三态（chat / manager_worker / state_machine）真实建群 + 三模 Poller + 翻译。
- `TaskExecutor` 第三模态 `bbs`（仅记日志，不执行）。
- singlebox double（`_DoubleOpenApiBot` / `_DoubleBcsClient` / `_DoubleApiKeyProvider` / `_DoubleContextProvider`），复用现有 e2e 剧本 `gwqie46v7hzr1w6h`。

### Out of Scope
- bbs 模式**真实执行**（`TaskExecutor` 仅记日志；真实 BBS 沿用 `BbsMarketPort`）。
- baas / BCS 真实部署、真实 bot catalog 搜推（`BotDiscoverPort` 真实实现在 corp）。
- BaaS Open API 的 `runs`（Bot Key 单轮）/ `messages/stream`（SSE）模式——本 spec 单 bot 仅用 `messages`（App Key 异步）模式。
- 前端画布渲染、外部 issue tracker、AI-Credit 审计产品化。
- BCS 服务化 group 模板（`service_spec`）的非回调用途——本 spec 仅在 create_group 透传可选 `service_spec`，不实现服务化注册流。
- 持久化/ORM：poller 登记表 in-memory（与 `TaskHarness._dispatched_at` 同级），不落库。

---

## 5. 参考实现锚点

### 5.1 协作群 — ocb BcsHttpClient（参考模式，自包含重写）
- 路径（corp，仅参考）：`/Users/jian.jiangj/Git/ocb/src/ecb/ecb/infrastructure/teamos/bcs/http_client.py`
- 模式：`httpx.AsyncClient` + HMAC-SHA256 签名头（`X-ECB-Token` / `X-ECB-Timestamp` / `X-ECB-Signature`，签串 `f"{ts}{method}{path}"`）。
- **关键**：ocb Python `create_group` 不暴露 `group_strategy`，但 **BCS server 端 `POST /groups` 完整接受**（见 §7.1）。本 spec 自包含 client 补齐该参数。
- 避坑：不用 ocb `BCSGroupService` 同步包装层（硬编码地址/token、吞异常、`asyncio.run` 不可在 running loop 用）——直接 `await` 自包含 async client。

### 5.2 单 bot — BaaS Open API（静态 API key + 主动 grant + `/openapi/v1/messages`）
- 参考脚本（仅参考实现模式，不改它们）：`/Users/jian.jiangj/Temp/test_tc_open_api/create_api_key.py`、`/Users/jian.jiangj/Temp/test_tc_open_api/send_bot_message.py`。
- 鉴权三件套（**静态配置**，来自 `graph.extend_props.execution_config` / singlebox 注入，**不下发到 case 知识**）：
  - `secbaas_api_key`：App API Key（`Authorization: Bearer <key>`，用于 `POST /openapi/v1/messages` 与 `GET /openapi/v1/messages/{id}`）。
  - `secbaas_api_key_prefix`：key 前 8 位（用于 `allowed-bots` 查询 / `grant` 路径段）。
  - `secbaas_cookie` / `secbaas_referer`：**登录态**（`grant` 走登录态鉴权，**不是** Bearer；stub/singlebox 可空）。
  - `secbaas_base_url`：默认 `http://localhost:8890`（singlebox BaaS 端口）。
- **grant 校验 / 补权流程**（派发前，对每个目标 `bot_id`，在 `OpenApiBotAdapter.ensure_grant` 内）：
  1. `GET /api/v1/api-keys/{prefix}/allowed-bots` → `data.allowed_bots`（`list[bot_id]`）。
  2. `bot_id` 不在列表 → `POST /api/v1/api-keys/{prefix}/allowed-bots/grant` body `{"bot_id": bot_id}`（**Cookie + Referer 鉴权**）补权。
  3. grant 失败（403 / 无登录态等不可重试）→ 该 node 派发返 `False`（编排核可 MISS / 重投）；不阻塞其它 node。
- **发消息**（炊弹）：`POST /openapi/v1/messages` body `{"bot_id": <run_info.assignee>, "message": <PromptFormatter.format_execute(ctx, node)>}`，Bearer 鉴权 → 响应 `data.message_id`。该 `message_id` 即本 spec 里的 **`run_id`**。**拿到 `run_id` 即派发成功返回，不轮询、不等待结果**——结果回收交给 `TaskExecutorResultPoller`（§7.8）。
- bot_id 格式：`<real_bot_id>:<entity_id>`（与 `allowed_bots` 列表项一致；经 `parse_bot_id`）。
- **结果回收**（poller 侧）：`GET /openapi/v1/messages/{run_id}` → `data.status`（**大小写不敏感**，prod 实测大写如 `COMPLETED`），终态 `{COMPLETED, FAILED}` → `result.content`→`data` / `error`→`fail_detail`。`TIME_OUT` 映射 `fail_detail="timeout"`。

### 5.3 框架侧回投契约（不变）
- `TaskCallbackData.loop_task_id = "task_id::node_id"`，`result: {success, data, fail_detail}`。
- `CallbackAdapter.adapt`（`task_runner/callback_adapter.py`）解析 `loop_task_id.split("::")`、`result.success/data/fail_detail` → `TaskNodePatch`。
- `TaskLoopCallback.report_result` → `ExecutionEngine.on_report`。

---

## 6. 模块布局

```
core/task/task_runner/integration/
  __init__.py                       # build_integration(real|double) 组合根装配
  ports.py                          # Port: OpenApiBotPort / BcsClientPort / ApiKeyProvider /
                                    #       TaskContextBuilder / PromptFormatter / ResultSink
  task_executor.py                  # TaskExecutor: 自有后台事件循环线程; start_run 入队; 三模态(single_bot/coop_group/bbs)分流
  open_api_bot_adapter.py           # OpenApiBotAdapter(OpenApiBotPort): 静态 API key + ensure_grant + POST/GET /openapi/v1/messages(httpx async)
  bcs_http_adapter.py               # BcsHttpAdapter(BcsClientPort): 自包含 httpx async BCS client(HMAC)
  bcs_token_provider.py             # BCS HMAC 凭据(真实/double): driver bot 签名取数
  task_executor_result_poller.py    # TaskExecutorResultPoller sidecar: 三模态回收(single_bot / session / run)
  translators.py                    # SingleBotRunTranslator / BcsSessionTranslator / BcsStateMachineRunTranslator
  double/                           # singlebox double
    __init__.py
    double_open_api_bot.py          # _DoubleOpenApiBot: grant→POST /messages→poll→终态
    double_bcs_client.py            # _DoubleBcsClient: create_group→create_session→poll→终态
    double_context_provider.py      # _DoubleApiKeyProvider / _DoubleContextProvider
```

`TaskRunner` 改造点（`task_runner/runner.py`）：
- `__init__(graph, execution_backend=None)`：新增可选 `execution_backend`（= `TaskExecutor`）。未注入时现行 stub 行为。
- `start_run`：`single_bot` / `coop_group` / `bbs` 均委托 `execution_backend.dispatch(toDoTaskList)`（`bbs` 在 executor 内仅记日志，不改节点状态）。
- `form_coop_group`：委托 `execution_backend.form_coop_group(gf)`（真实走 `BcsHttpAdapter`）。
- `_build_context`：保留，被 `PromptFormatter` 消费。

---

## 7. 详细设计

### 7.1 BCS `POST /groups` 契约（三态分流）

BCS server 端 `CreateGroupRequest`（`bcs-protocol/src/http/groups.rs`）关键字段（本 client 需支持）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `driver_bot` | `str` | normal 群必填 |
| `participants` | `list[{bot_uuid, role?}]` | 成员 |
| `group_strategy` | `"chat"\|"manager_worker"\|"state_machine"` | 省略=chat |
| `context` | `str?` | 群上下文（chat/manager_worker 走 `[GROUP CONTEXT]` 投递 driver） |
| `topic` | `str?` | 群主题 |
| `collaboration_definition_yaml` | `str?` | 内联 `CollaborationDefinition` YAML；出现强制 state_machine；别名 `definition_yaml` |
| `participant_bindings` | `map[binding_id, {source:"manual", bot_ids}]` | state_machine 槽位绑定；非空要求 yaml 同时存在 |
| `service_spec` | `dict?` | `ServiceSpec{callback_config?, timeout_seconds?, max_concurrency?}`（可选透传） |
| `start_initial_run` | `bool?` | 是否立即起初始 run，默认 true |
| `auto_start_on_service_invocation` | `bool?` | 持久化到 runtime binding |
| `originator` | `str?` | 发起者，默认 driver_bot |
| `visibility` | `"public"\|"private"?` | 默认 private |
| `idempotency_key` | header `Idempotency-Key` | 幂等去重 |

服务端校验：yaml 仅 normal 群；yaml 存在时 `group_strategy` 强制 state_machine；`participant_bindings` 非空要求 yaml；yaml 不得含顶层 `id`/`version`；`driver_bot` 必须在 yaml participants 内。

`form_coop_group(gf) -> group_id`（dispatcher 对 HIT_MULTI_BOTS 调用；上游契约仅返 `str`）= **`BcsHttpAdapter.create_group` 建群壳**，按 `GroupFormation.collab_mode` 分流（`GroupFormation` 见 `task_dispatch/protocols.py`，type 不变，仅定 `extend_props` 契约，见 §7.5）。`create_group` 只建群、**不下发任务指令**（`context=None`；state_machine `start_initial_run=False`）——指令 kickoff 在 `start_run` 经 `create_session`（chat/manager_worker）或 `start_state_machine_run`（state_machine）下发，即 ocb「先 create_group、再 create_session」模式。executor 内 `_group_meta: dict[group_id → {session_id?, definition_ref?, collab_mode, gf}]` 由 `form_coop_group` 填充，供 `start_run` 按 `assignee=group_id` 查询（`form_coop_group -> str` 契约不变，meta 仅内部用）。

- **chat**：`{driver_bot, participants}`（`group_strategy` 省略）。响应取 `group_id`。
- **manager_worker**：`{group_strategy:"manager_worker", driver_bot=<manager>, participants[{role:"manager"}/{role:"worker"}]}`。manager = `gf.extend_props.get("manager_bot_id")` or `gf.bot_ids[0]`；其余 bot 为 worker。
- **state_machine**：`{group_strategy:"state_machine", collaboration_definition_yaml=gf.extend_props["collaboration_definition_yaml"], participant_bindings=<由 gf.bot_ids 构造: {bid:{"source":"manual","bot_ids":[bid]}}>, driver_bot, start_initial_run=False}`。

### 7.2 StateMachine run_id 捕获约束（关键）

默认 `start_initial_run=true` 的 `POST /groups` 响应**不含 `run_id`**，poller 无从下手。故 state_machine 强制 `start_initial_run=False`（在 `form_coop_group` 的 `create_group` 阶段），run 启动与 `run_id` 捕获延后到 `start_run`：

1. `form_coop_group` → `create_group(start_initial_run=False)` → 取 `group_id`（dispatcher 存为 `assignee`）；definition 由服务端 `upsert_definition_with_source_yaml` 持久化，`definition_ref={id,version}` 存入 `_group_meta`。
2. `start_run`(state_machine node) → `POST /groups/{id}/state-machine-runs`，body `{definition_ref: {id, version}}` + `session_id?` + `input`（`input = {"query": PromptFormatter.format_execute(ctx, node)}`）→ `202` body `StateMachineRunView.run.run_id`。
3. 登记 `run_id` 进 `TaskExecutorResultPoller`（run 模）。

chat / manager_worker 无此问题（kickoff 经 `create_session`，结果经 session 状态回收）。

### 7.3 BCS client 端点集（自包含 client 需实现）

| 方法 | 端点 | 用途 |
|---|---|---|
| `create_group` | `POST /groups` | 建群壳（三态） |
| `create_session` | `POST /groups/{id}/sessions` | chat/manager_worker kickoff（bootstrap_prompt→`[GROUP CONTEXT]`） |
| `get_group` | `GET /groups/{id}` | 取 `latest_running_session_id`、群/session 状态 |
| `get_session_messages` | `GET /sessions/{sid}/messages?limit&since_msg_id` | 增量拉消息（chat/manager_worker） |
| `start_state_machine_run` | `POST /groups/{id}/state-machine-runs` | 显式起 run 取 `run_id` |
| `get_state_machine_run` | `GET /state-machine-runs/{run_id}` | 轮询 run 终态（state_machine） |
| `validate_definition` | `POST /collaboration/definitions/validate` | 可选 YAML 预检 |

鉴权：HMAC 签名头（`X-ECB-Token`/`X-ECB-Timestamp`/`X-ECB-Signature`）；session 内追发（本 spec 暂不用）需 Bearer bot token。

### 7.4 Port 契约（`ports.py`）

```python
class OpenApiBotPort(Protocol):
    async def ensure_grant(self, bot_id: str) -> None: ...        # 查 allowed-bots;缺则 grant(登录态)
    async def send_message(self, *, bot_id: str, message: str,
                           metadata: dict[str, Any]) -> str: ...  # run_id(=message_id)
    async def get_run(self, run_id: str) -> dict[str, Any]: ...   # {status, result, error}

class BcsClientPort(Protocol):
    async def create_group(self, req: "BcsCreateGroupRequest") -> "BcsCreateGroupResult": ...
    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str: ...  # session_id
    async def get_group(self, group_id: str) -> dict[str, Any]: ...
    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]: ...
    async def start_state_machine_run(self, group_id: str, *, definition_yaml: str | None,
                                      definition_ref: dict | None, session_id: str | None,
                                      input: dict) -> str: ...  # run_id
    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]: ...
    async def validate_definition(self, definition_yaml: str) -> None: ...

class ApiKeyProvider(Protocol):
    """静态配置的 BaaS Open API 凭据:发消息用 Bearer;grant 用登录态 Cookie/Referer。"""
    @property
    def api_key(self) -> str: ...        # Bearer(app key)用于 /openapi/v1/messages
    @property
    def api_key_prefix(self) -> str: ... # 前 8 位,用于 allowed-bots/grant 路径
    @property
    def base_url(self) -> str: ...       # 默认 http://localhost:8890
    @property
    def cookie(self) -> str: ...         # 登录态(grant 鉴权);stub/singlebox 可空
    @property
    def referer(self) -> str: ...

class TaskContextBuilder(Protocol):
    """= 现有 TaskRunner._build_context 的抽像(验收/执行双模式 dict)。"""
    def build(self, task_id: str, node_id: str) -> dict[str, Any]: ...

class PromptFormatter(Protocol):
    """把 _build_context dict + TaskNode → 指令字符串(Open API message / BCS context/bootstrap_prompt)。
    零 case: 仅用 dict 字段 + node.task_spec,不写节点名字面量。"""
    def format_execute(self, context: dict[str, Any], node: "TaskNode") -> str: ...
    def format_verify(self, context: dict[str, Any], node: "TaskNode") -> str: ...

class ResultSink(Protocol):
    """结果回流入口 = TaskLoopCallback.report_result(或 engine.on_report 经适配)。"""
    def report_result(self, data: "TaskCallbackData") -> None: ...
```

`BcsCreateGroupRequest` / `BcsCreateGroupResult` 为 `integration` 内 dataclass（`bcs_http_adapter.py`），对齐 §7.1 字段；`result` 含 `group_id`、`session_id`、`run_id?`。

### 7.5 `GroupFormation.extend_props` 契约补充（type 不变）

`GroupFormation`（`task_dispatch/protocols.py`）已有 `collab_mode: str` + `extend_props: dict`。本 spec 定义 `extend_props` 约定 key（不增字段）：

| key | 适用 | 说明 |
|---|---|---|
| `collaboration_definition_yaml` | state_machine 必填 | `CollaborationDefinition` YAML；内 `participants` 的 binding id 必须 = `gf.bot_ids` 的某项 |
| `manager_bot_id` | manager_worker 可选 | 默认 `gf.bot_ids[0]` |
| `service_spec` | 任意可选 | 透传 BCS `ServiceSpec` |
| `sla_timeout_ms` | 任意可选 | poller 超时兜底（优先 `execution_config.SLA_TIMEOUT`） |

`BcsHttpAdapter` 由 `gf.bot_ids` 自动构造 `participant_bindings = {bid: {"source":"manual","bot_ids":[bid]} for bid in gf.bot_ids}`。yaml 的 binding id 必须落在 `gf.bot_ids`——由 corp `BotDiscoverPort` 产出时保证；singlebox double 的 canned yaml 保证。

### 7.6 数据流

#### 单 bot
1. `start_run` → `TaskExecutor.enqueue(single_bot, node)` → 立即返 `True`。
2. executor（后台 loop）对 `bot_id = node.run_info.assignee`：
   a. `OpenApiBotPort.ensure_grant(bot_id)`：`GET /api/v1/api-keys/{prefix}/allowed-bots` → `allowed_bots`；`bot_id` 不在列表则 `POST .../allowed-bots/grant`（Cookie+Referer 鉴权）补权；失败 → 该 node 返 `False`。
   b. `send_message(bot_id=bot_id, message=PromptFormatter.format_execute(ctx, node), metadata={"timeout": <execution_config 超时>, "biz_task_id": task_id})`：`POST /openapi/v1/messages`（Bearer）→ `run_id`（= `message_id`，§5.2）。
   c. **拿到 `run_id` 即返回**，不轮询、不等待结果。
3. 登记 `SingleBotHandle{task_id::node_id, run_id, bot_id}` 进 `TaskExecutorResultPoller`（single_bot 模）。
4. poller：`get_run(run_id)` = `GET /openapi/v1/messages/{run_id}` → `status ∈ {COMPLETED, FAILED}` → `SingleBotRunTranslator`（`success = status==COMPLETED`；`data = result.content`；`fail_detail = error`）→ `report_result` → `on_report`。

`form_coop_group(gf)`（dispatcher 对 HIT_MULTI_BOTS 调用）= `create_group` 建群壳（§7.1），返 `group_id`（dispatcher 存为 `node.run_info.assignee`），并在 executor `_group_meta` 存 `{session_id?, definition_ref?, collab_mode, gf}`。任务 kickoff 与 poller 登记在 `start_run`（此时有 node → 可组 prompt）：

#### 协作群（chat / manager_worker）
1. `start_run` 取 `group_id = node.run_info.assignee`（HIT_MULTI_BOTS 由 `form_coop_group` 新建；HIT_GROUP 为 `BotDiscoverPort` 已知既有群——后者 `_group_meta` 无记录，直接用 `group_id`）。
2. `create_session(group_id, bootstrap_prompt=PromptFormatter.format_execute(ctx, node))` → `session_id`（`[GROUP CONTEXT]` 原子投递 driver）。**拿到 `session_id` 即返回，不等待结果。**
3. 登记 `BcsGroupHandle{task_id::node_id, group_id, session_id, collab_mode, run_id=None, since_cursor=None}` 进 `TaskExecutorResultPoller`（session 模）。
4. poller：`get_group` 看 `Session.status` + `get_session_messages(since cursor)` 累积 → `Session.status=="completed"` → `BcsSessionTranslator`（`output`→`data`，`error_message`→`fail_detail`）→ `report_result`。

#### 协作群（state_machine）
1. `start_run` 取 `group_id = node.run_info.assignee`（`form_coop_group` 已 `create_group(start_initial_run=False)`，definition 已持久化）。
2. `start_state_machine_run(group_id, definition_ref=_group_meta[group_id].definition_ref, session_id, input={"query": PromptFormatter.format_execute(ctx, node)})` → `run_id`（§7.2）。**拿到 `run_id` 即返回，不等待结果。**
3. 登记 `BcsGroupHandle{..., run_id=run_id}` 进 `TaskExecutorResultPoller`（run 模）。
4. poller run 模：`get_state_machine_run(run_id)` → `run.status ∈ {completed,failed,aborted}` → `BcsStateMachineRunTranslator`（success=completed / data=run.output / fail_detail=run.error 或 `"aborted"`）→ `report_result`。

### 7.7 TaskExecutor（异步/同步弥合）

- 自有**后台事件循环线程**（`threading.Thread` + `asyncio.new_event_loop`），所有真实 async I/O（Open API grant / send、BCS create_group / start_run / validate）在该 loop 跑。
- `dispatch(toDoTaskList) -> list[bool]`：同步入口，对每个 node 按 `node.run_info.mode`（`single_bot` / `coop_group` / `bbs`）分流，构造 `TaskJob` 投入队列（线程安全 `queue.Queue` + loop 唤醒，或 `run_coroutine_threadsafe`），立即返 `list[bool]`（= 入队 / 前置校验成功）。
- **三模态分流**：
  - `single_bot`：`OpenApiBotAdapter.ensure_grant` → `send_message` 取 `run_id` → 登记 `TaskExecutorResultPoller`（single_bot 模）。**派发只到拿到 `run_id` 为止，不轮询结果**。
  - `coop_group`：`BcsHttpAdapter` 建群 / kickoff，登记 `TaskExecutorResultPoller`（session 或 run 模），见 §7.2 / §7.6。
  - `bbs`：**仅记结构化日志**（`logger.info("[task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s", task_id, node_id, assignee)`），不发起任何执行、不改节点状态、不登记 poller；真实 BBS 沿用既有 `BbsMarketPort` seam（见 §4 Out of Scope）。
- executor 持 `ResultSink`（回投入口），`TaskExecutorResultPoller` 调 `ResultSink.report_result`。
- 异常映射：派发期 grant 403 / Open API 4xx / `BotNotAvailableError`（不可重试）→ 对该 node 返 `False`（编排核可重投 / MISS）；`BcsServerError`/`BcsTimeoutError`/`BcsRateLimitError`/Open API 429（可重试）→ 返 `False` 且 executor 内部退避重试上限 3 次后上抛。
- executor 生命周期：facade `TaskService.__init__` 装配时起线程；进程退出时 `aclose()`。

### 7.8 TaskExecutorResultPoller（旁路 sidecar）

- 同 `TaskHarness` 风格：`register(handle)`、`run_poll_loop(stop_event=None)`、`set_on_result(sink)`。
- **三模态**（按 handle 类型分流，统一回收 single_bot 与协作群结果）：
  - `SingleBotHandle`（single_bot 模）：`OpenApiBotPort.get_run(run_id)` = `GET /openapi/v1/messages/{run_id}` → `status`（大小写不敏感）∈ `{COMPLETED, FAILED}` 终态 → `SingleBotRunTranslator` → `report_result`。
  - `BcsGroupHandle` 且 `run_id` 非空（run 模）：`get_state_machine_run(run_id)` → `run.status` 终态 → `BcsStateMachineRunTranslator` → `report_result`。
  - `BcsGroupHandle` 且 `run_id` 为空（session 模）：`get_group` + `get_session_messages(since cursor)` → `Session.status=="completed"` → `BcsSessionTranslator` → `report_result`。
- polling：`interval`（默认 1s，可注入）；single_bot / session 各自游标（single_bot 无游标，run 模无游标）；5xx 退避，连续 5 次失败 → `report_result(FAIL, fail_detail="poll_exhausted")` 并注销。
- 超时：`now - handle.registered_at > sla_timeout` 且未终态 → `report_result(FAIL, fail_detail="sla_timeout")` 并注销（旁路复位交给编排核 `on_report` FAIL 链路）。
- 与 `TaskHarness` 并存：`TaskHarness` 复位 `RUNNING→PENDING`（节点 SLA）；`TaskExecutorResultPoller` 回收 single_bot + 协作群执行结果。二者独立。

### 7.9 无 PUSH 回调（设计取舍）

- 旧设计的 baas PUSH 回调（`POST /task/callback` + `BaasCallbackTranslator` + `BotRunnerPort.deliver_message(callback=...)`）**全部移除**。单 bot 改走 Open API「派发取 `run_id` + 旁路 poller 回收」（§5.2 / §7.6 / §7.8），与协作群统一为 poller 模型，减少入站路由依赖。
- 上游 facade 2 API（`execute` / `get_task_dashboard`）当前**仅 transport-agnostic Protocol**，未挂 HTTP 路由（`adapters/http/task/` 仅 stale `.pyc`）。本 spec **不新增任何入站 HTTP 路由**；结果回流一律经 `TaskExecutorResultPoller` → `ResultSink.report_result`（进程内 wired）。
- singlebox 下 `_DoubleOpenApiBot` 直接进程内模拟 grant / send / poll，不经网络。

### 7.10 翻译器（`translators.py`）

- `SingleBotRunTranslator.adapt(run_dict, loop_task_id) -> TaskCallbackData`：`loop_task_id=<登记时的 task::node>`；`success = (status or "").lower()=="completed"`；`data = (run_dict.get("result") or {}).get("content")`；`fail_detail = run_dict.get("error")`（`TIME_OUT`→`"timeout"`）。
- `BcsSessionTranslator.adapt(group_dict, messages) -> TaskCallbackData`：`success = session.status=="completed"`；`data = session.output`（缺失取末条 assistant 消息 content）；`fail_detail = session.error_message`。
- `BcsStateMachineRunTranslator.adapt(run_dict) -> TaskCallbackData`：`success = run.status=="completed"`；`data = run.output`；`fail_detail = run.error` 或 `f"aborted"`。

### 7.11 错误处理

- BCS 异常（自包含 client 自定义，对齐 ocb `exceptions.py`）：`BcsClientError`（基类）/ `BcsServerError`(5xx,可重试) / `BcsClientRequestError`(4xx,不重试) / `BcsRateLimitError`(429,带 `retry_after_s`) / `BcsTimeoutError`。
- Open API 异常（`open_api_bot_adapter.py` 自定义）：`OpenApiAuthError`(401/403,grant 失败不可重试) / `OpenApiBadRequestError`(4xx,不重试) / `OpenApiRateLimitError`(429,可重试) / `OpenApiServerError`(5xx,可重试) / `OpenApiTimeoutError`。
- 派发期（`start_run` 返回值）：可重试异常返 `False` 且 executor 退避重试；不可重试 4xx（含 grant 403）返 `False` 不重试（编排核 MISS / 重投）。
- 回收期（poller）：终态 FAIL 经 `report_result` 走编排核 `on_fail`（<MAX 补救 / ≥MAX 升 BBS）。
- 幂等：BCS `create_group`/`start_state_machine_run` 带幂等 key；Open API grant 幂等（重复 grant 同 bot 由 BaaS 服务端去重）；重复 `start_run` 同 node 由上游 `on_report` 幂等 + 图状态机护栏兜底。

---

## 8. 零 case 知识红线

- `PromptFormatter` 仅消费 `TaskContextBuilder.build` 的 dict（`mode`/`child_outputs`/`parent_spec`/`sibling_outputs`/`node_spec`/`goal`/`acceptances`）+ `TaskNode.task_spec`，**不得**出现节点名字面量。
- `BcsHttpAdapter` 的 `label=f"teamos:{task_id}:{kind}"` 中 `kind` 取自 `collab_mode`（非节点名）。
- `OpenApiBotAdapter` 的 `metadata`/grant 调用只用 `bot_id`(=`run_info.assignee`) 与静态 API key 配置，**不**出现节点名字面量。
- singlebox double 的 canned `collaboration_definition_yaml` 用泛化 binding id（如 `worker_a`），不绑定具体 case 节点名；e2e 断言 `grep` 框架源码 0 命中 `N_market`/`N_overview` 等。

---

## 9. 测试策略

### 9.1 单测（`tests/community/core/task/task_runner/integration/`）
- `test_open_api_bot_adapter.py`：httpx mock transport 验 `ensure_grant`（查 allowed-bots→缺则 grant，登录态 Cookie/Referer 头、Bearer 发消息头分离）、`POST /openapi/v1/messages` 请求体（`bot_id`/`message`）→ `run_id`、`GET /openapi/v1/messages/{id}` 终态（大小写不敏感）、异常映射。
- `test_bcs_http_adapter.py`：httpx mock transport 验三态 `create_group` 请求体（`group_strategy`/`collaboration_definition_yaml`/`participant_bindings`/`start_initial_run`）、HMAC 签名头、`Idempotency-Key`、`create_group→start_state_machine_run→get_state_machine_run` 序列、异常映射。
- `test_translators.py`：三翻译器 shape（`success/data/fail_detail`、`loop_task_id`）。
- `test_task_executor_result_poller.py`：三模态（single_bot 终态 / session 终态 / run 终态）、single_bot 状态大小写不敏感、`since_msg_id` 游标（session 模）、超时→FAIL、连续失败→FAIL 注销。
- `test_task_executor.py`：入队返 `list[bool]`、三模态分流（`bbs` 仅记日志、断言不发起执行也不登记 poller）、派发期异常→`False`（含 grant 403）、后台 loop 独立性（无 caller loop 也能跑）。

### 9.2 singlebox E2E（复用剧本 `gwqie46v7hzr1w6h`）
- `_wire_facade` 注入 integration double（`_DoubleOpenApiBot` / `_DoubleBcsClient` / `_DoubleApiKeyProvider` / `_DoubleContextProvider`）。
- `_DoubleOpenApiBot` 模拟 `ensure_grant`(allowed-bots 查/补) → `POST /messages` → `get_run` poll → `COMPLETED`，可注入 FAIL / timeout；`_DoubleApiKeyProvider` 给固定静态凭据。
- `_DoubleBcsClient` 模拟三态：chat/manager_worker `create_group→session 模 poll→Completed`；state_machine `create_group(start_initial_run=False)→start_state_machine_run→run 模 poll→Completed`，可注入 FAIL/timeout。
- 复用现有 5 类 e2e（三模态 happy→DONE / FAIL 补救治愈 / MISS 升 BBS / BBS STUCK→HUNG / dashboard 终态），验证**真实集成形态**（非纯 stub）驱动同一闭环。BBS 类用例断言 `TaskExecutor` 对 `bbs` 仅记日志、不改节点状态。

### 9.3 上游回归
- `TaskRunner` 默认无 `execution_backend` 时现行 stub 行为不破（现有 121 单测全绿）。
- fallback：`execute` 不挂 HTTP 时，`_DoubleOpenApiBot` / `_DoubleBcsClient` 经进程内 wired 回投，e2e 仍跑通。

---

## 10. 里程碑（实现计划由 writing-plans 展开）

- **R0 Port + double 骨架**：`ports.py` + `double/*` + `TaskExecutor` 线程骨架；`TaskRunner` 注入点改造（默认 stub 不破）。
- **R1 单 bot 真实链路**：`OpenApiBotAdapter`（静态 API key + `ensure_grant` + `POST /messages`）+ `SingleBotRunTranslator` + `TaskExecutorResultPoller` single_bot 模 + `TaskExecutor` `bbs` 仅记日志；单测 + singlebox bot e2e。
- **R2 BCS client（chat/manager_worker）**：自包含 `BcsHttpAdapter`（HMAC + `create_group`/`create_session`/`get_group`/`get_session_messages`）+ `BcsSessionTranslator` + `TaskExecutorResultPoller` session 模；单测。
- **R3 BCS state_machine**：`collaboration_definition_yaml` 透传 + `start_state_machine_run` + `get_state_machine_run` + `BcsStateMachineRunTranslator` + poller run 模 + `start_initial_run=False` 约束；单测。
- **R4 singlebox 三态 e2e 收口**：`_DoubleOpenApiBot`/`_DoubleBcsClient` 三态 + 复用剧本 `gwqie46v7hzr1w6h` 5 类 e2e + `bbs` 仅记日志断言；零 case grep 红线 0 命中。

---

## 11. Risks / 已知缺口

| 项 | 说明 | 处置 |
|---|---|---|
| ocb `BcsHttpClient` 缺 `group_strategy` | 纯客户端封装缺口，server 端支持 | 本 spec 自包含 client 补齐 |
| state_machine 默认 `start_initial_run=true` 不回 `run_id` | poller 无从下手 | 强制 `start_initial_run=False` + 显式 start |
| BCS 无入站 webhook | 协作群只能轮询 | `TaskExecutorResultPoller` sidecar |
| 单 bot grant 需登录态 | `grant` 走 Cookie/Referer 鉴权(`create_api_key.py`),非 Bearer | 静态配置 `secbaas_cookie`/`secbaas_referer`;stub/singlebox 可空;grant 403→该 node 返 False |
| 单 bot 结果只能轮询 | Open API `/messages` 无 PUSH 回调 | `TaskExecutorResultPoller` single_bot 模 `GET /messages/{run_id}` |
| 单 bot 与 `secbaas` 跨包身份 | 旧设计经 in-repo `BotRunner` 需 `BotChatContext` | 改走 Open API HTTP,仅需静态 API key,不 import `secbaas` |
| `group_strategy=state_machine` YAML 正确性 | 需 binding id↔bot_ids 对齐 | `validate_definition` 可选预检 + corp `BotDiscoverPort` 保证 + double canned yaml |
| ocb `BCSGroupService` 同步包装层 TD-5/6/7 | 硬编码/吞异常/`asyncio.run` | 本 spec 不复用，直接 async |
| facade 2 API 未挂 HTTP | 旧 `/task/callback` 路由方案 | 单 bot 改轮询,**本 spec 不新增任何入站路由**;poller 进程内 wired 回投 |
| 持久化 | poller 登记表 in-memory | 与 `TaskHarness` 同级；ORM 适配后续 |

---

## 12. 与上游 spec 的一致性

- `TaskRunner.start_run` 签名、`TaskLoopCallback`/`CallbackAdapter`/`TaskCallbackData` 契约**不变**。
- `form_coop_group` 由 stub 升级为真实 BCS 建群（返回 `group_id`），`GroupFormation` type 不变（仅定 `extend_props` key）。
- 单 bot 由「手动 PUSH stub」升级为「Open API 派发取 `run_id` + 旁路 poller 回收」；结果回流统一经 `on_report` → `update_task_node_info`（SSOT 写口），不绕过图级/节点级写口。
- `bbs` 模态进入 `TaskExecutor` 三模态分流（仅记日志，不改状态），真实 BBS 仍走 `BbsMarketPort`，不改。
- 零 case 知识红线、开源边界（seam + double）延续；移除 baas PUSH 回调入站路由，减少外部依赖。
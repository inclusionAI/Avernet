# Spec: task_loop callback 服务子模块（inbound PUSH 回调，单 bot workflow / bcn 协作群）

- 日期：2026-08-13
- 分支：`feat/task-goal-driven-collab-dev-jj-2`
- 范围模块：`adapters/http/task/`（新增）+ `core/task/task_runner/callback_correlation.py`（新增）+ `core/task/task_center/engine.py`（`on_start` 新增）+ `core/task/task_runner/callback_adapter.py`（`start_run` 激活）+ `di/modules/task_module.py`（新增）+ `adapters/http/app.py`（挂载 / 错误映射）
- 上游 spec：
  - `specs/2026-08-09-task-goal-driven-execution-framework/`（任务目标驱动执行框架，M0–M6 已落地）
  - `specs/2026-08-09-task-goal-driven-task-runner/`（runner integration；本 spec **修订其 §7.9**）
- 参考文档：羽雀「任务Loop执行」→「回调服务_供单bot workflow或者bcn协作群任务使用」
  （https://yuque.antfin.com/mad/enxdbg/lxg2mwgmtfqg6d95 ）
- 适用代码约定：遵循 `AGENTS.md`（不引入 `T | None` 除非 None 是契约态；必填值非可选）

---

## 1. Problem Statement

羽雀「回调服务」章节定义了**入站 PUSH 回调契约**：外部执行引擎（单 bot-有 workflow = ClawMind workflow run；bcn 协作群 = state_machine run）在任务/节点状态变更时，**主动回调 Avernet**，上报总体状态、产出、以及 workflow 内每个节点的状态与产出。回调契约给出 4 个方法签名 + `TaskCallbackData` / `TaskNodeCallbackData` 两类载荷 + 4 条 HTTP 路径。

但 Avernet 现状：

1. `TaskLoopCallbackProtocol`（`api/task/task_loop_callback.py`）与实现 `TaskLoopCallback`（`task_runner/callback_adapter.py`）**只有 `report_result` 落地**（→ `CallbackAdapter.adapt` → `engine.on_report`）。`start_run` 是 **no-op stub**，进度信号 seam 死着。
2. `adapters/http/task/` **只有 stale `.pyc`，无 `.py` 源**——没有任何入站回调路由；`TaskService` 也未进 DI、未挂 HTTP。runner-integration spec §7.9 明确「移除 PUSH、单 bot 走 poller」。
3. SSOT `TaskCallbackData`（`domain/models.py`）字段精简（`loop_task_id` / `workflow_type` / `workflow_id:int` / `instance_id:int` / `result:dict`），与羽雀丰富字段（`workflow_source` / `goal` / `status` / `is_success` / `output` / `failed_info` / `ext_info` / `node_id`）不对齐。
4. `engine.on_report` 对 `acceptance_result is None` 早退（fold-only 不翻态），`RUNNING` 现仅由内部 `_prepare_into` 派发时置位——外部 `*_start` 信号无处落脚。
5. task 级回调只有 `task_id` + `workflow_instance_id`，**无 `node_id`**，无法直接寻址 Avernet 节点。

本 spec 设计 inbound PUSH 回调服务子模块，把上述缺口补齐：HTTP 边缘 4 端点 + 边缘翻译（羽雀 schema → SSOT）+ correlation 解析（task 级 → 节点）+ `on_start` 激活 RUNNING + 鉴权 port + DI 装配，**所有写入一律 funnel 进 SSOT `update_task_node_info`**。

---

## 2. Solution 概述

**PUSH 与 poller 共存**（修订 §7.9）：

- **PUSH 回调（本 spec）**：面向**有 workflow 引擎的执行主体**——claw_mind workflow run、bcn state_machine run。这类引擎天然能回调。
- **旁路 Poller（runner spec §7.8，保留）**：面向**无 workflow 引擎的执行主体**——plain 单 bot chat-async、plain 协作群 chat session（Open API `/messages` 与 BCS chat session 无原生 PUSH）。

两条路汇入同一 sink：`TaskLoopCallback.report_result` → `engine.on_report` → `TaskGraphService.update_task_node_info`（SSOT 写口，**不绕过**）。§7.9 由「移除 PUSH」修订为「PUSH 用于 workflow 类，poller 用于无 workflow 类」。

```
外部引擎(claw_mind workflow / bcn state_machine)
   │  POST /openapi/v1/task/callback/{workflow_start|workflow_result|node_start|node_result}
   ▼
task callback router (FastAPI, 4 端点)
   ├─ CallbackAuthenticator.verify(source)         # HMAC(默认)/Noop(double)
   ├─ CallbackRequestTranslator.translate(req)      # 羽雀 schema → {disposition, TaskCallbackData, TaskNodePatch?}
   │     ├─ is_success/output/failed_info → result{success,data,fail_detail}
   │     ├─ workflow_source → workflow_type；str ids → registry 查 SSOT int ids
   │     ├─ ext_info → extend_props_patch
   │     └─ loop_task_id 解析: node 级=task::node; task 级=回声字段 or registry
   ├─ disposition=start  → svc.callback.start_run(data)  → engine.on_start(patch)   # status-direct PENDING→RUNNING
   └─ disposition=result → svc.callback.report_result(data) → engine.on_report(patch) # acceptance 终态翻转
          │
          ▼
   TaskGraphService.update_task_node_info(patch)   # SSOT 唯一写口
```

---

## 3. Constraints

1. **SSOT 不绕过**：所有入站回调一律 `on_start` / `on_report` → `update_task_node_info`；router / translator / registry 不得直写图、不得直改 `TaskNode.status`。
2. **不破上游契约**：`TaskCallbackData` / `CallbackAdapter.adapt` / `engine.on_report` / `loop_task_id="task_id::node_id"` / `TaskRunner.start_run` 签名**不变**；`on_start` 为纯新增方法；不注入 `TaskModule` 时现行行为（121 单测）不破。
3. **HTTP 边缘翻译，SSOT 保持精简**：不扩 `TaskCallbackData` 字段；羽雀丰富字段在 router 边缘翻译折叠进 SSOT 既有字段 + `extend_props_patch`。
4. **零 case 知识红线**：translator / router / registry / auth 仅消费 schema 字段 + `loop_task_id` / `node_id` / `workflow_source`，**不得**出现节点名字面量（grep 0 命中，单测断言）。
5. **开源边界**：HTTP 边缘 + 翻译 + registry + auth port 在 Avernet 自包含；HMAC 实现镜像 ocb `BcsHttpClient` 签名模式（**不 import ocb**）。
6. **必填非可选**（`AGENTS.md`）：`workflow_source` / `workflow_id` / `workflow_instance_id` / `task_id` 必填非可选；`None` 仅用于契约态（`goal` / `loop_task_id` 回声字段可空，触发 registry 兜底）。
7. **幂等**：网络重投是常态——`*_result` 重投到已终态节点、`*_start` 重投到已 RUNNING 节点，均 200 idempotent ack，不重复驱动传播/补救。

---

## 4. Scope

### In Scope
- `adapters/http/task/` 子模块：`router.py`（4 端点）/ `schemas.py`（Pydantic v2 线上 schema）/ `translator.py`（边缘翻译）/ `auth.py`（`CallbackAuthenticator` port + HMAC + Noop）。
- `core/task/task_runner/callback_correlation.py`：`CallbackCorrelationRegistry` port + in-mem impl（task 级 → 节点 / id 类型对齐）。
- `core/task/task_center/engine.py`：新增 `on_start(patch) -> NodeOpResult`（status-direct PENDING→RUNNING，幂等）。
- `core/task/task_runner/callback_adapter.py`：激活 `TaskLoopCallback.start_run` → `engine.on_start`（由 no-op 升级）。
- `di/modules/task_module.py`：`TaskModule` singleton 绑 `TaskService` + registry + auth，暴露 `TaskServiceProtocol`。
- `adapters/http/app.py`：`app.include_router(task_callback_router)`；`_DOMAIN_ERROR_STATUS_MAP` 补 `TaskStateError→409`。
- singlebox double：`NoopCallbackAuthenticator` + `_DoubleCallbackCorrelationRegistry` + 回调驱动 e2e。

### Out of Scope
- runner-integration 的 poller / BCS / Open API 真实派发链路（属 runner spec）；本 spec 只定义**入站回调面**与**派发期 correlation 登记 seam**（登记动 作由 runner integration executor / corp adapter 落地，本 spec 定 port 契约）。
- bbs 模态真实执行（沿用 `BbsMarketPort`，bbs 不回调）。
- 持久化/ORM：registry in-memory（与 `TaskHarness._dispatched_at` 同级），不落库。
- 前端/dashboard 渲染、外部 issue tracker。

---

## 5. 现有 seam 锚点（不变项）

| 锚点 | 位置 | 现状 / 契约 |
|---|---|---|
| `TaskLoopCallbackProtocol` | `api/task/task_loop_callback.py:10` | `async start_run(data)` / `async report_result(data)`；`@runtime_checkable` |
| `TaskLoopCallback` 实现 | `task_runner/callback_adapter.py:45` | `start_run` no-op；`report_result` → `adapt` → `on_report` |
| `CallbackAdapter.adapt` | `task_runner/callback_adapter.py:24` | `loop_task_id.split("::",1)`；`success/data/fail_detail` → `TaskNodePatch`；FAIL 无 fail_detail → `["unknown_gap"]` |
| `TaskCallbackData` | `domain/models.py:206` | `loop_task_id`/`workflow_type`/`workflow_id:int`/`instance_id:int`/`result:dict`（**不扩**） |
| `TaskNodePatch` | `domain/models.py:146` | `task_id`/`node_id` 必填；`status`/`acceptance_result`/`output_patch`/`extend_props_patch` 可选 |
| `engine.on_report` | `task_center/engine.py:91` | 锁内 `update_task_node_info`；`acceptance_result is None` 早退（fold-only）；PASS→传播/finish，FAIL→补救/BBS |
| `TaskGraphService.update_task_node_info` | `task_context/task_graph_service.py:200` | **SSOT 唯一节点写口**；`_ACCEPTANCE_TRANSITIONS`(RUNNING/PLANNING→DONE/FAILED) + `_DIRECT_TRANSITIONS`(PENDING→RUNNING / RUNNING→PENDING / PLANNING→DONE) |
| `Status` 枚举 | `domain/models.py:16` | `PENDING` / `PLANNING` / `RUNNING` / `DONE` / `FAILED` / `HUNG` |
| `TaskService.callback` | `task_center/task_service.py:48` | `@property` 暴露 `TaskLoopCallback`；engine 私有，入站经 `svc.callback.*` |
| `loop_task_id` 源 | `task_runner/runner.py:75` | `f"{task.task_id}::{node.node_id}"`，派发期 mint |

### 5.1 router / DI 约定（镜像既有模式）
- FastAPI + Pydantic **v2.13.4**（`uv.lock`）；`from agentclaw.community.di import Injected`。
- router 风格（镜像 `adapters/http/quality/router.py`）：`router = APIRouter(prefix="/openapi/v1/task/callback", tags=["task-callback"])`；handler `async def`，`svc: TaskServiceProtocol = Injected(TaskServiceProtocol)`。
- `DomainError` 经 `app.py:349 _DOMAIN_ERROR_STATUS_MAP` 中央映射（架构测试要求每个 `DomainError` 子类有 entry）；router 层仅做幂等 ack override 与 router-local guard。
- DI（镜像 `di/modules/quality_module.py`）：`Module` 子类，`binder.bind(Impl, to=Impl, scope=singleton)` + `@singleton @provider @inject` 暴露 Protocol。
- 挂载：prod 必需 → `app.include_router(task_callback_router)` 直连（非 `OptionalRouters`，后者仅 local/test 条件挂载）。

---

## 6. 模块布局

```
adapters/http/task/
  __init__.py                       # 导出 task_callback_router
  router.py                         # 4 端点; Injected(TaskServiceProtocol); Depends(verify_callback(source))
  schemas.py                        # Pydantic v2: TaskCallbackRequest / TaskNodeCallbackRequest / CallbackResponse
  translator.py                     # CallbackRequestTranslator: req → {disposition, TaskCallbackData, TaskNodePatch?}
  auth.py                           # CallbackAuthenticator(Protocol) + HmacCallbackAuthenticator + NoopCallbackAuthenticator
core/task/task_runner/
  callback_correlation.py           # CallbackCorrelationRegistry(Protocol) + InMemoryCallbackCorrelationRegistry
  callback_adapter.py               # TaskLoopCallback.start_run 激活 → engine.on_start(改)
core/task/task_center/
  engine.py                         # + on_start(patch) status-direct PENDING→RUNNING(新增)
di/modules/task_module.py           # TaskModule: bind TaskService/registry/auth singleton; expose TaskServiceProtocol
adapters/http/app.py                # include_router(task_callback_router); _DOMAIN_ERROR_STATUS_MAP += TaskStateError→409
```

---

## 7. 详细设计

### 7.1 HTTP 端点（honoring 羽雀原路径）

| 方法 | 路径 | 请求体 | disposition | 走向 |
|---|---|---|---|---|
| POST | `/openapi/v1/task/callback/workflow_start` | `TaskCallbackRequest` | start | `svc.callback.start_run` → `engine.on_start`（root/dispatched 节点 RUNNING） |
| POST | `/openapi/v1/task/callback/workflow_result` | `TaskCallbackRequest` | result | `svc.callback.report_result` → `engine.on_report`（root/dispatched 节点终态） |
| POST | `/openapi/v1/task/callback/node_start` | `TaskNodeCallbackRequest` | start | `svc.callback.start_run`（子节点 RUNNING） |
| POST | `/openapi/v1/task/callback/node_result` | `TaskNodeCallbackRequest` | result | `svc.callback.report_result`（子节点终态） |

- 响应统一 `CallbackResponse{success: bool, code: int, message: str}`；成功 / 幂等 ack → 200。
- 鉴权：handler `Depends(verify_callback)`，`verify_callback` 经 `Injected(CallbackAuthenticator)` 按 `body.workflow_source` 取密钥校验签名；失败 → 401。

### 7.2 线上 schema（Pydantic v2，对齐羽雀字段）

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

class TaskCallbackRequest(BaseModel):
    task_id: str
    workflow_source: Literal["claw_mind", "bcn"]
    workflow_id: str
    workflow_instance_id: str
    goal: str | None = None
    status: str                        # RUNNING / COMPLETED / FAILED / ...（大小写不敏感）
    is_success: bool
    output: dict[str, Any] | None = None
    failed_info: str | None = None
    ext_info: dict[str, Any] | None = None
    loop_task_id: str | None = None    # 回声字段:派发期透传,引擎原样回带(可选,缺失走 registry)

class TaskNodeCallbackRequest(TaskCallbackRequest):
    node_id: str                       # = Avernet 子节点 id(统一领域对象 1:1 映射)

class CallbackResponse(BaseModel):
    success: bool
    code: int = 200
    message: str = "OK"
```

> `goal` / `output` / `failed_info` / `ext_info` / `loop_task_id` 为可空契约态（None 触发兜底）；`task_id` / `workflow_source` / `workflow_id` / `workflow_instance_id` / `status` / `is_success` 必填非可选（遵 `AGENTS.md`）。

### 7.3 边缘翻译 `CallbackRequestTranslator`（SSOT 不动）

`translate(req) -> TranslatedCallback`，其中 `TranslatedCallback = {disposition: "start"|"result", data: TaskCallbackData, patch: TaskNodePatch}`：

1. **`loop_task_id` 解析**：
   - 优先用回声 `req.loop_task_id`（非空直接用）。
   - node 级：`loop_task_id = f"{task_id}::{node_id}"`（`node_id` 即 Avernet 节点 id）。
   - task 级 + 无回声：`CallbackCorrelationRegistry.resolve(source, instance_id) → (task_id, node_id, loop_task_id)`；缺失 → raise `CallbackCorrelationError`（router → 400）。
2. **`result` 折叠**：`result = {"success": req.is_success, "data": req.output, "fail_detail": req.failed_info}`（`output`/`failed_info` 为 None 则缺省 key）。
3. **`workflow_type` 映射**：`workflow_source=="claw_mind" → "single_bot"`；`"bcn" → "bcn_coop_group"`。
4. **id 类型对齐**：`workflow_id`/`instance_id` 在 SSOT 为 `int`，羽雀为 `str`。**不强转**——由 registry（派发期已存 SSOT int）返回；未登记且为 node 级（有回声 loop_task_id）时，回退 `0` 并把原 str id 存 `ext_info["_workflow_id_str"]`/`["_instance_id_str"]` 以可追溯（`adapt`/`on_report` 不消费这两个 int，仅信息字段）。
5. **`ext_info`/`goal` 折叠进 `result["_ext_info"]`**：`TaskCallbackData` 无 ext_info 字段（SSOT 精简），故 translator 把 `ext_info`（合并 `goal`→`_callback_goal`、未登记时的 `_workflow_id_str`/`_instance_id_str`）塞进 `result["_ext_info"]` dict；由 `CallbackAdapter.adapt`/`adapt_start` 折进 `extend_props_patch`（见 §7.5）。`goal` 为信息字段，不驱动状态。
6. **disposition 判定**：
   - 端点 `*_start` → `disposition="start"`。
   - 端点 `*_result` → `disposition="result"`。
   - （`status` 字段不覆写 disposition：端点语义即签名语义，与羽雀一致。）
7. **产出**：`TranslatedCallback = {disposition, data: TaskCallbackData}`（**只产 `data`**；patch 由 `CallbackAdapter.adapt`/`adapt_start` 从 `data` 构造，保持与既有 `report_result→adapt` 链路一致）。FAIL 无 `failed_info` 由 `adapt` 兜底 `["unknown_gap"]`（既有契约，不重复兜底）。

> translator **不得**出现节点名字面量；`task_id`/`node_id`/`loop_task_id` 全部来自请求或 registry。

### 7.4 `engine.on_start`（新增；status-direct PENDING→RUNNING）

`engine.on_report` 对 `acceptance_result is None` 早退（fold-only 不翻态），无法承载 `*_start` 的 RUNNING 翻转。故新增：

```python
async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
    """入站 start 回调:status-direct PENDING→RUNNING(幂等)。
    不触发传播/side-effect(纯节点态翻转)。"""
    with self._lock_for(patch.task_id):
        node = self._graph.get_node(patch.task_id, patch.node_id)      # 不存在 → NodeNotFoundError
        if node.status == Status.RUNNING:
            return NodeOpResult(task_id, node_id, success=True,
                                prev_status=Status.RUNNING, new_status=Status.RUNNING)  # 幂等 no-op
        if node.status in {Status.DONE, Status.FAILED, Status.HUNG}:
            raise TaskStateError(...)                                   # stale → router 409
        # PENDING / PLANNING → RUNNING(patch 仅带 status=RUNNING)
        return self._graph.update_task_node_info(patch)                # 校验 _DIRECT_TRANSITIONS
```

- **幂等**：已 RUNNING → no-op success；终态 → `TaskStateError`(stale)；`PENDING`→`RUNNING` 经 SSOT 校验 `_DIRECT_TRANSITIONS`。
- `PLANNING→RUNNING` 不在 `_DIRECT_TRANSITIONS`（仅 `PLANNING→DONE`）：若节点为 `PLANNING`（委托态）收到 start → `TaskStateError`→409（委托态不应被外部 start 打断；其终态由传播链路置 DONE）。
- **不**调用 `_drain` / 传播 / side-effect：start 仅置节点 RUNNING，编排核调度由既有 `_prepare_into` 路径负责（PUSH start 是「外部引擎已开始」的确认信号，与内部派发 RUNNING 并存，幂等）。

> `on_start` 与 `on_report` 共享 `_lock_for(task_id)` 锁，状态翻转顺序由 SSOT 状态机裁决，无竞态。

### 7.5 激活 `TaskLoopCallback.start_run`

`callback_adapter.py` 现 `start_run` no-op，升级为：

```python
async def start_run(self, data: TaskCallbackData) -> None:
    patch = self._adapter.adapt_start(data)   # 新增:loop_task_id split + status=RUNNING, 无 acceptance
    await self._engine.on_start(patch)
```

`CallbackAdapter.adapt_start(data) -> TaskNodePatch`：`task_id, node_id = data.loop_task_id.split("::",1)`；折 `data.result.get("_ext_info")`→`extend_props_patch`；返 `TaskNodePatch(task_id, node_id, status=Status.RUNNING, extend_props_patch=ext if ext else None)`。`adapt`（result 路径）**追加**折 `_ext_info`→`extend_props_patch`（与既有 `fail_detail` 合并；既有单测不设 `_ext_info` 故不受影响）。

`TaskLoopCallbackProtocol.start_run` 签名不变（仍 `async (data) -> None`）。

### 7.6 `CallbackCorrelationRegistry`（task 级 → 节点 + id 对齐）

```python
class CallbackCorrelationRegistry(Protocol):
    def register(self, *, source: str, workflow_id: int, instance_id: int,
                 task_id: str, node_id: str, loop_task_id: str,
                 workflow_id_str: str, instance_id_str: str) -> None: ...
    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None: ...

@dataclass(frozen=True)
class CorrelationRecord:
    task_id: str; node_id: str; loop_task_id: str
    workflow_id: int; instance_id: int       # SSOT int(供 TaskCallbackData)
```

- **登记时机**：`TaskRunner.start_run` 真实派发到 claw_mind/bcn 时（runner-integration executor 或 corp adapter），把 `(source, instance_id_str) → CorrelationRecord` 写入 registry。本 spec 定 port 契约，登记动作属 runner spec / corp adapter 落地。
- **解析**：task 级回调 `workflow_instance_id`（str）→ `resolve` → `CorrelationRecord`；缺失 → `CallbackCorrelationError`（router → 400「未登记派发，无法寻址」）。
- **回声优先**：回调若带 `loop_task_id` 回声字段，跳过 registry 寻址（但仍可查 registry 取 SSOT int id 填 `TaskCallbackData`）。
- in-mem（与 `TaskHarness._dispatched_at` 同级），不落库；线程安全（dict + lock，登记/解析低频）。

### 7.7 鉴权 `CallbackAuthenticator`

```python
class CallbackAuthenticator(Protocol):
    def verify(self, *, source: str, headers: Mapping[str, str], raw_body: bytes) -> None: ...  # 失败 raise CallbackAuthError
```

- `HmacCallbackAuthenticator`（默认，镜像 ocb `BcsHttpClient` 签名模式，**不 import ocb**）：按 `source`（`claw_mind`/`bcn`）取共享密钥；签串 `f"{timestamp}{method}{path}{body_sha256}"`；校验 `X-TaskLoop-Token` / `X-TaskLoop-Timestamp` / `X-TaskLoop-Signature`；时间戳偏移 > 阈值 → `CallbackAuthError`。
- `NoopCallbackAuthenticator`（singlebox/test）：直通。
- `verify_callback` 依赖：读 body（需在 router 注入原始 body 做签名校验，Pydantic 解析前/后均可——用 `Request` 取 `raw_body`，再 `TaskCallbackRequest.model_validate_json`）。
- 失败 → `CallbackAuthError`(`DomainError` 子类) → `_DOMAIN_ERROR_STATUS_MAP` → 401。

### 7.8 幂等与错误映射

| 场景 | 行为 | HTTP |
|---|---|---|
| `*_result` 重投，节点已 `DONE`/`FAILED` 且与 callback 一致 | `update_task_node_info` raise `TaskStateError`；router 判 prev==目标终态 → 幂等 | **200** ack |
| `*_result` 非法翻转（如 `DONE`→`FAILED` 不一致重投） | `TaskStateError` | 409 |
| `*_start` 重投，已 `RUNNING` | `on_start` no-op | 200 |
| `*_start` 命中终态 | `on_start` raise `TaskStateError`(stale) | 409 |
| `*_start` 命中 `PLANNING` | `TaskStateError`（委托态拒绝外部 start） | 409 |
| `task_id`/`node_id` 不存在 | `TaskNotFoundError`/`NodeNotFoundError` | 404 |
| task 级无回声且 registry 未登记 | `CallbackCorrelationError` | 400 |
| schema 校验失败 | Pydantic `ValidationError` | 422 |
| 鉴权失败 | `CallbackAuthError` | 401 |

- `CallbackAuthError`(401)/`CallbackCorrelationError`(400) 为 **`DomainError` 子类**（定义于 `core/errors.py`，架构测试可见），进 `_DOMAIN_ERROR_STATUS_MAP`（架构测试要求每个 `DomainError` 子类有 entry），由中央 `@app.exception_handler(DomainError)` 自动映射。
- `TaskStateError`/`TaskNotFoundError`/`NodeNotFoundError` 属 **`TaskError`（非 `DomainError`）**，中央 handler 不捕获——router 层显式 try/except 映射：`TaskNotFoundError`/`NodeNotFoundError`→404；`TaskStateError`→ result 路径 re-query 节点态,已终态(`DONE`/`FAILED`)→200 idempotent,否则 409;start 路径→409(`on_start` 已把 RUNNING no-op 内化,故 start 的 `TaskStateError` 均为 stale)。
- **幂等 ack override**：router 捕获 `TaskStateError` 后 re-query `svc.get_task_dashboard(task_id)` 取节点当前态判定;不下发 `TaskStateError` 进中央 map(避免把 `GraphIntegrityError`/`DispatchError`/`DecomposeError` 等无清晰 HTTP 码的 `TaskError` 子类强塞 `DomainError` 层次)。

### 7.9 DI 与装配

- `di/modules/task_module.py` `TaskModule(Module)`：
  - `binder.bind(TaskServiceImpl, to=TaskServiceImpl, scope=singleton)`；
  - `binder.bind(InMemoryCallbackCorrelationRegistry, ..., scope=singleton)`；
  - `binder.bind(HmacCallbackAuthenticator, ..., scope=singleton)`；
  - `@singleton @provider @inject` 暴露 `TaskServiceProtocol <- TaskServiceImpl`、`CallbackCorrelationRegistry <- InMemory...`、`CallbackAuthenticator <- Hmac...`（镜像 `QualityModule`）。
- `di/profile_modules.py` / `modules_bootstrap.py`：prod profile 登记 `TaskModule`；testing profile 可置 `NoopCallbackAuthenticator` override。
- `adapters/http/app.py`：import `task_callback_router` 并 `app.include_router(task_callback_router)`（prod 必需，直连非 OptionalRouters）；补 `TaskStateError`/`CallbackCorrelationError`/`CallbackAuthError` → status 映射。

### 7.10 `loop_task_id` 回声透传（派发期 seam）

- 派发到 claw_mind/bcn 时，Avernet 把 `loop_task_id`（+ `task_id`/`node_id`）作为 callback_token 透传给外部引擎（经 start request 的 `ext_info` / 约定字段），引擎在回调中原样回带 `loop_task_id`。
- 回声存在 → translator 跳过 registry 寻址；缺失 → registry 兜底。双保险。
- 回声透传的具体协议字段由 runner-integration spec / corp adapter 定；本 spec 定「`loop_task_id` 必须可被引擎回带」的契约。

---

## 8. 数据流（端到端）

### workflow_result（task 级，成功）
1. bcn state_machine run 完成 → `POST /openapi/v1/task/callback/workflow_result`，body `{task_id, workflow_source:"bcn", workflow_instance_id, is_success:true, output:{...}}`。
2. router：`verify_callback`（HMAC）→ `translator.translate`：
   - 回声 `loop_task_id` 或 registry `resolve("bcn", instance_id)` → `(task_id, root_node_id, loop_task_id)`。
   - `result={"success":true,"data":output}`；`acceptance=PASS`；`patch=TaskNodePatch(task_id, root_node_id, output_patch, acceptance_result=PASS)`。
3. `svc.callback.report_result(data)` → `engine.on_report(patch)` → 锁内 `update_task_node_info`（`RUNNING→DONE` via `_ACCEPTANCE_TRANSITIONS`）→ `_on_pass_collect`（传播 / `_maybe_finish_graph`）→ `_drain`。
4. 响应 200。

### node_result（node 级，失败）
1. claw_mind workflow 内某节点失败 → `POST /openapi/v1/task/callback/node_result`，`{task_id, node_id, workflow_source:"claw_mind", is_success:false, failed_info:"..."}`。
2. translator：`loop_task_id=f"{task_id}::{node_id}"`；`acceptance=FAIL(gaps=[failed_info])`。
3. `report_result` → `on_report` → `update_task_node_info`（`RUNNING→FAILED`）→ `_on_fail_collect`（`<MAX_DEPTH`→规划补救子节点；`≥MAX_DEPTH`→`_escalate_bbs` / `HUNG`）→ `_drain`。
4. 200。

### workflow_start（task 级）/ node_start
1. 外部引擎开始执行 → `POST .../workflow_start` 或 `/node_start`。
2. translator：disposition=start；`patch=TaskNodePatch(task_id, node_id, status=RUNNING)`。
3. `svc.callback.start_run` → `engine.on_start`：已 `RUNNING`→no-op 200；`PENDING`→`RUNNING`（SSOT 校验）→ 200；终态/`PLANNING`→409。

### 重投幂等
- 同一 `workflow_result` 网络重投到已 `DONE` 节点 → `update_task_node_info` raise `TaskStateError`（`DONE→DONE` 非法）→ router 判 prev=`DONE`==目标 → 200 idempotent ack，不重复传播/finish。

---

## 9. 零 case 知识红线

- `CallbackRequestTranslator` / router / registry / auth 仅消费 schema 字段 + `loop_task_id`/`node_id`/`workflow_source`，**不得**出现 `N_market`/`N_overview` 等节点名字面量。
- `workflow_source`→`workflow_type` 映射表仅含 `claw_mind`/`bcn`（执行主体类型，非节点名）。
- singlebox double 的 canned 回调用泛化 `node_id`（如 `worker_a`），不绑定具体 case 节点名；e2e 断言 `grep` 框架源码 0 命中 `N_market`/`N_overview` 等。

---

## 10. 测试策略

### 10.1 单测（`tests/community/adapters/http/task/` + `core/task/`）
- `test_translator.py`：4 端点×字段折叠（`is_success/output/failed_info`→`result`）、`workflow_source`→`workflow_type`、`ext_info`→`extend_props_patch`、disposition 判定、`loop_task_id` 解析（node 级直拼 / task 级回声 / task 级 registry 兜底 / registry 缺失→`CallbackCorrelationError`）、FAIL 无 `failed_info` 由 adapt 兜底。
- `test_callback_correlation.py`：`register`/`resolve` 往返、缺失返 None、并发安全。
- `test_engine_on_start.py`：`PENDING→RUNNING`、已 `RUNNING` no-op、`DONE/FAILED/HUNG`→`TaskStateError`(stale)、`PLANNING`→`TaskStateError`、不触发 `_drain`/传播。
- `test_callback_adapter.py`：`TaskLoopCallback.start_run` 激活（→ `on_start`，非 no-op）；`report_result` 不变（回归）。
- `test_router.py`：4 端点 happy→200；HMAC 失败→401；幂等 ack（result 重投→200, start 重投→200）；`TaskStateError` 非法→409；`NodeNotFoundError`→404；`CallbackCorrelationError`→400；schema 错→422。
- `test_auth.py`：HMAC 签名/时间戳校验、Noop 直通。

### 10.2 singlebox E2E
- `NoopCallbackAuthenticator` + `_DoubleCallbackCorrelationRegistry`（派发期预登记）+ 进程内回投。
- 模拟 claw_mind/bcn 回调驱动三态闭环：`workflow_start→workflow_result`（root 节点 RUNNING→DONE→finish）；`node_result` FAIL→补救/BBS 升级；`node_start/node_result` 子节点链路；重投幂等（result 重投不二次传播）。
- 复用既有 e2e 闭环断言（DONE / FAIL 补救 / MISS 升 BBS / dashboard 终态）。

### 10.3 上游回归
- 无 `TaskModule` 时现行 121 单测全绿（`on_start` 纯新增、`start_run` 激活不破既有 `test_start_run_is_noop`——该测试更新为断言激活后走向 `on_start`）。
- `TaskCallbackData`/`adapt`/`on_report` 契约不变 → execution-framework 回归不破。

---

## 11. 里程碑（实现计划由 writing-plans 展开）

- **R0 schema + translator + registry 骨架**：`schemas.py`/`translator.py`/`callback_correlation.py`；`engine.on_start` + 激活 `TaskLoopCallback.start_run` + `adapt_start`；`TaskModule` 装配；router 4 端点（Noop auth）；单测。
- **R1 HMAC 鉴权 + 回声透传 + 错误映射**：`HmacCallbackAuthenticator`；`loop_task_id` 回声契约；`_DOMAIN_ERROR_STATUS_MAP` 补项；幂等 ack override；单测。
- **R2 singlebox E2E 收口**：double 回调驱动三态闭环 + 重投幂等；零 case grep 红线 0 命中；上游回归全绿。

---

## 12. Risks / 已知缺口

| 项 | 说明 | 处置 |
|---|---|---|
| 与 runner spec §7.9 表面冲突 | §7.9「移除 PUSH」 | 本 spec 修订为「PUSH(workflow 类) + poller(无 workflow 类) 共存」；两路同 sink |
| `workflow_id/instance_id` SSOT int vs 羽雀 str | 类型不一致 | registry 存 SSOT int；未登记 node 级回退 0 + str 存 ext_info；`adapt`/`on_report` 不消费 |
| task 级无 `node_id` | 羽雀 task 级载荷无 node_id | 回声 `loop_task_id` 优先；registry `(source,instance_id)→node` 兜底；缺失→400 |
| `on_report` 早退 fold-only | `acceptance_result is None` 不翻态 | 新增 `on_start`（status-direct）承载 `*_start`；`on_report` 不动 |
| `PLANNING` 态收 start | 委托态不应被外部 start 打断 | `on_start` 对 `PLANNING`→`TaskStateError`→409 |
| 外部引擎鉴权 | claw_mind/bcn 回调可信度 | HMAC port（默认）；singlebox Noop；密钥按 source 配置 |
| `loop_task_id` 回声依赖外部引擎 | 引擎未必回带 | 回声优先 + registry 兜底双保险；回声契约写进 runner spec / corp adapter |
| `TaskService` 未进 DI | 现仅测试驱动 | `TaskModule` singleton 绑定 + profile 登记 |
| 持久化 | registry in-mem | 与 `TaskHarness` 同级；ORM 适配后续 |
| 重投幂等 vs 非法翻转 | `TaskStateError` 既覆盖二者 | router 按 `prev_status` 区分：一致→200, 不一致→409 |

---

## 13. 与上游 spec 的一致性

- `TaskLoopCallbackProtocol`（`start_run`/`report_result`）、`TaskCallbackData`、`CallbackAdapter.adapt`、`engine.on_report`、`loop_task_id="task_id::node_id"`、`TaskRunner.start_run` 签名**不变**。
- `on_start` 为纯新增；`TaskLoopCallback.start_run` 由 no-op 升级为真实（`test_start_run_is_noop` 同步更新为断言激活走向）。
- runner-integration spec §7.9 由「移除 PUSH」修订为「PUSH（workflow 类）+ poller（无 workflow 类）共存」，两路统一经 `on_report`/`on_start` → `update_task_node_info`（SSOT），不绕过图级/节点级写口。
- 羽雀「回调服务」4 端点 + `TaskCallbackData`/`TaskNodeCallbackData` 契约由 HTTP 边缘 schema + translator 承接，SSOT 模型保持精简不扩。
- 零 case 知识红线、开源边界（seam + double + HMAC 自包含）延续。
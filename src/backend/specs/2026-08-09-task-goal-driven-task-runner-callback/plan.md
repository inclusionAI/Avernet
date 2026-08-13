# task_loop callback 服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为单 bot workflow / bcn 协作群任务落地 inbound PUSH 回调服务（4 个 HTTP 端点 + 边缘翻译 + correlation 解析 + `on_start` 激活 RUNNING + HMAC 鉴权 + DI 装配），所有写入 funnel 进 SSOT `update_task_node_info`。

**Architecture:** PUSH 回调与 runner-integration poller 共存（修订 runner spec §7.9）：有 workflow 引擎的执行主体（claw_mind / bcn state_machine）走 PUSH 入站；无 workflow 引擎的（plain 单 bot / chat session）走 poller。两路同 sink：`TaskLoopCallback.report_result`→`engine.on_report`；新增 `engine.on_start` 承载 `*_start` 的 PENDING→RUNNING。HTTP 边缘用 Pydantic v2 schema 对齐羽雀字段，translator 折叠进精简 SSOT `TaskCallbackData`，`CallbackAdapter.adapt/adapt_start` 再组装 `TaskNodePatch`。`CallbackCorrelationRegistry` 解析 task 级回调→节点。`TaskError` 在 router 层显式映射，`CallbackAuthError`/`CallbackCorrelationError` 作 `DomainError` 子类进中央 status map。

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2.13 + injector (fastapi-injector) + threading.RLock（per-task 锁，沿用 engine 风格）。

**Spec:** `src/backend/specs/2026-08-09-task-goal-driven-task-runner-callback/spec.md`

## Global Constraints

- 遵循 `AGENTS.md`：不引入 `T | None` 除非 None 是契约态；必填值非可选（`workflow_source`/`workflow_id`/`workflow_instance_id`/`task_id`/`node_id` 必填）。
- SSOT 不绕过：所有入站回调一律 `on_start`/`on_report`→`update_task_node_info`，router/translator/registry 不得直写图、不得直改 `TaskNode.status`。
- 零 case 知识红线：新代码不得出现 `N_overview`/`N_market`/`N_aggregate`/`N_verify`/`N_report`/`N_practice`/`n_root`/`dim_` 等节点名字面量（grep 0 命中，单测断言）。
- 开源边界：HMAC 实现镜像 ocb `BcsHttpClient` 签名模式但 **不 import ocb**；社区分布不绑 corp。
- 不破上游：`TaskCallbackData`/`CallbackAdapter.adapt`/`engine.on_report`/`TaskLoopCallbackProtocol`/`loop_task_id="task_id::node_id"` 契约不变；`on_start`/`adapt_start` 为纯新增；不注入 `TaskModule` 时现行 121 单测全绿。
- 协程化约束（README）：锁内不 await；`on_start` 锁内同步写图（`update_task_node_info` 内存同步），与 `on_report` 一致。
- 测试入口：`cd src/backend && python -m pytest <test> -v`（社区仓 conftest 已就绪）。
- commit 消息结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/agentclaw/community/core/errors.py` | 新增 `CallbackAuthError`/`CallbackCorrelationError`（`DomainError` 子类） | Modify |
| `src/agentclaw/community/adapters/http/app.py` | status map 补 2 项 + 挂载 router | Modify |
| `src/agentclaw/community/core/task/task_center/engine.py` | 新增 `on_start(patch)` | Modify |
| `src/agentclaw/community/core/task/task_runner/callback_adapter.py` | 新增 `adapt_start` + `adapt` 折 `_ext_info` + 激活 `start_run` | Modify |
| `src/agentclaw/community/core/task/task_runner/callback_correlation.py` | `CorrelationRecord` + `CallbackCorrelationRegistry` Protocol + InMemory impl | Create |
| `src/agentclaw/community/adapters/http/task/__init__.py` | 导出 `task_callback_router` | Create |
| `src/agentclaw/community/adapters/http/task/schemas.py` | Pydantic v2 请求/响应 schema | Create |
| `src/agentclaw/community/adapters/http/task/translator.py` | `translate(req, disposition, registry) -> TranslatedCallback` | Create |
| `src/agentclaw/community/adapters/http/task/auth.py` | `CallbackAuthenticator` Protocol + HMAC + Noop + `verify_callback` | Create |
| `src/agentclaw/community/adapters/http/task/router.py` | 4 端点 + 错误映射 + 幂等 ack | Create |
| `src/agentclaw/community/di/modules/task_module.py` | `TaskModule` singleton 绑定 | Create |
| `src/agentclaw/community/di/profile_modules.py` | TEST/SINGLEBOX column 登记 `TaskModule()` | Modify |
| `tests/community/core/task/task_center/test_engine_on_start.py` | `on_start` 单测 | Create |
| `tests/community/core/task/task_runner/test_callback_adapter.py` | 激活 `start_run` + `adapt_start` + `_ext_info` 折叠 | Modify |
| `tests/community/core/task/task_runner/test_callback_correlation.py` | registry 单测 | Create |
| `tests/community/adapters/http/task/test_translator.py` | translator 单测 | Create |
| `tests/community/adapters/http/task/test_auth.py` | HMAC/Noop 单测 | Create |
| `tests/community/adapters/http/task/test_router.py` | 4 端点 + 错误映射 + 幂等 单测 | Create |
| `tests/community/core/test_errors.py` | 新 `DomainError` 子类可被枚举（回归） | Verify（不改） |

---

## Task 1: 新增 `DomainError` 子类 + 中央 status map

**Files:**
- Modify: `src/agentclaw/community/core/errors.py`（末尾追加 2 类）
- Modify: `src/agentclaw/community/adapters/http/app.py:349`（`_DOMAIN_ERROR_STATUS_MAP` + import）
- Test: `tests/community/architecture/test_domain_error_status_map_complete.py`（既有，跑回归）

**Interfaces:**
- Produces: `CallbackAuthError(detail: str)`（`DomainError` 子类，401）、`CallbackCorrelationError(detail: str)`（`DomainError` 子类，400）。定义在 `agentclaw.community.core.errors`，被 translator/auth/router import，被架构测试枚举。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/core/test_callback_errors.py`：

```python
from agentclaw.community.core.errors import (
    CallbackAuthError, CallbackCorrelationError, DomainError,
)


def test_callback_errors_are_domain_errors():
    assert issubclass(CallbackAuthError, DomainError)
    assert issubclass(CallbackCorrelationError, DomainError)


def test_callback_errors_carry_detail():
    e1 = CallbackAuthError("bad sig")
    assert e1.detail == "bad sig"
    e2 = CallbackCorrelationError("unregistered")
    assert e2.detail == "unregistered"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/test_callback_errors.py -v`
Expected: FAIL `ImportError: cannot import name 'CallbackAuthError'`

- [ ] **Step 3: 在 `core/errors.py` 末尾追加 2 类**

在 `core/errors.py` 末尾追加（`DomainError` 基类 `__init__(self, detail)` 已存在，子类无需自定义构造）：

```python
class CallbackAuthError(DomainError):
    """任务回调鉴权失败(签名校验不通过/时间戳超窗)。HTTP 层映射 401。"""


class CallbackCorrelationError(DomainError):
    """task 级回调无法寻址节点(无回声 loop_task_id 且 registry 未登记派发)。HTTP 层映射 400。"""
```

- [ ] **Step 4: 在 `app.py` status map 补 2 项**

`app.py` 顶部已有 `from agentclaw.community.core.errors import ...`（确认 `DomainError` 等 import 块）。把 `CallbackAuthError`/`CallbackCorrelationError` 加进该 import 块；在 `_DOMAIN_ERROR_STATUS_MAP`（`app.py:349`）追加（保持 401/400 与 `Unauthorized`/`ValidationError` 同列风格）：

```python
    CallbackAuthError:          401,
    CallbackCorrelationError:   400,
```

- [ ] **Step 5: 跑测试 + 架构回归**

Run: `cd src/backend && python -m pytest tests/community/core/test_callback_errors.py tests/community/architecture/test_domain_error_status_map_complete.py tests/community/core/test_errors.py -v`
Expected: PASS（架构测试枚举到 2 新子类且 map 覆盖）。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/errors.py src/agentclaw/community/adapters/http/app.py tests/community/core/test_callback_errors.py
git commit -m "feat(task-callback): add CallbackAuthError/CallbackCorrelationError DomainError subclasses + status map

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: `engine.on_start`（status-direct PENDING→RUNNING，幂等）

**Files:**
- Modify: `src/agentclaw/community/core/task/task_center/engine.py`（import `NodeNotFoundError`/`TaskStateError`；新增 `on_start`）
- Test: `tests/community/core/task/task_center/test_engine_on_start.py`

**Interfaces:**
- Consumes: `TaskNodePatch(task_id, node_id, status=Status.RUNNING, extend_props_patch=...)`（来自 Task 3 `adapt_start`）；`self._graph.query_task_dashboard(task_id)`、`self._graph.update_task_node_info(patch)`、`self._lock_for(task_id)`（既有）。
- Produces: `async def on_start(patch: TaskNodePatch) -> NodeOpResult`——PENDING→RUNNING（经 SSOT）；已 RUNNING→no-op `NodeOpResult(prev=RUNNING,new=RUNNING,success=True)`；`DONE/FAILED/HUNG/PLANNING`→raise `TaskStateError`；node 不存在→raise `NodeNotFoundError`。不触发 `_drain`/传播。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/core/task/task_center/test_engine_on_start.py`：

```python
from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, Status, TaskInfo, TaskNode, TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="O", acceptances=[AcceptanceCriteria(id="a1", description="done")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _engine_with_root(task_id="t1"):
    graph = TaskGraphService()
    graph.initialize_graph(_task_info(task_id))
    eng = ExecutionEngine(graph)
    # 把根节点置 RUNNING(模拟派发 _prepare_into 后)，回调 start 命中 RUNNING 为幂等
    root = next(n for n in graph.query_task_dashboard(task_id).tasks if n.node_id)
    return eng, graph, root


class TestOnStart:
    def test_pending_to_running(self):
        eng, graph, root = _engine_with_root()
        # 根初始 PENDING
        assert root.status == Status.PENDING
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        res = _run(eng.on_start(patch))
        assert res.success is True
        assert res.new_status == Status.RUNNING
        assert root.status == Status.RUNNING

    def test_already_running_is_idempotent_noop(self):
        eng, graph, root = _engine_with_root()
        root.status = Status.RUNNING  # 直接置态(测试用)
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        res = _run(eng.on_start(patch))
        assert res.success is True
        assert res.prev_status == Status.RUNNING
        assert res.new_status == Status.RUNNING

    @pytest.mark.parametrize("term", [Status.DONE, Status.FAILED, Status.HUNG, Status.PLANNING])
    def test_terminal_or_planning_raises_stale(self, term):
        eng, graph, root = _engine_with_root()
        root.status = term
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        with pytest.raises(TaskStateError):
            _run(eng.on_start(patch))

    def test_unknown_node_raises_not_found(self):
        eng, graph, root = _engine_with_root()
        patch = TaskNodePatch(task_id=root.task_id, node_id="nope", status=Status.RUNNING)
        with pytest.raises(NodeNotFoundError):
            _run(eng.on_start(patch))

    def test_start_does_not_trigger_drain_or_propagation(self):
        eng, graph, root = _engine_with_root()
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        _run(eng.on_start(patch))
        # 没有 side effect 触发：图 status 未被 finish 改动
        assert graph.query_task_dashboard(root.task_id).status == Status.PENDING
```

>若 `initialize_graph` 后根节点字段名/初始态与本测试假设不符（PENDING），先读 `task_graph_service.initialize_graph` 对齐；根节点取法 `next(n for n in graph.query_task_dashboard(task_id).tasks ...)` 与 engine `_root` 同模式。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_center/test_engine_on_start.py -v`
Expected: FAIL `AttributeError: 'ExecutionEngine' object has no attribute 'on_start'`

- [ ] **Step 3: 实现 `on_start`**

`engine.py` 顶部 import 块追加：

```python
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
```

在 `on_report` 之后（`_on_pass_collect` 之前）插入：

```python
    # ===== on_start =====
    async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
        """入站 start 回调:status-direct PENDING→RUNNING(幂等)。不触发传播/side-effect(纯节点态翻转)。
        协程化:锁内同步写图(update_task_node_info 内存同步),锁内不 await。"""
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            node = next((n for n in graph.tasks if n.node_id == patch.node_id), None)
            if node is None:
                raise NodeNotFoundError(f"on_start: node not found {patch.task_id}::{patch.node_id}")
            if node.status == Status.RUNNING:
                return NodeOpResult(
                    task_id=patch.task_id, node_id=patch.node_id, success=True,
                    prev_status=Status.RUNNING, new_status=Status.RUNNING,
                )
            if node.status in {Status.DONE, Status.FAILED, Status.HUNG, Status.PLANNING}:
                raise TaskStateError(
                    f"on_start: stale/illegal start on {node.status} node {patch.task_id}::{patch.node_id}"
                )
            # PENDING → RUNNING,经 SSOT 校验 _DIRECT_TRANSITIONS
            return self._graph.update_task_node_info(patch)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_center/test_engine_on_start.py -v`
Expected: PASS（5 用例）

- [ ] **Step 5: 上游回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/ -v`
Expected: PASS（`on_start` 纯新增，既有 121 单测不破）

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/task/task_center/engine.py tests/community/core/task/task_center/test_engine_on_start.py
git commit -m "feat(task-callback): add ExecutionEngine.on_start (PENDING→RUNNING idempotent)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: `CallbackAdapter.adapt_start` + 激活 `TaskLoopCallback.start_run` + `adapt` 折 `_ext_info`

**Files:**
- Modify: `src/agentclaw/community/core/task/task_runner/callback_adapter.py`（import `Status`；`adapt` 折 `_ext_info`；新增 `adapt_start`；激活 `start_run`）
- Test: `tests/community/core/task/task_runner/test_callback_adapter.py`（修改：`RecordingEngine` 加 `on_start`；改 `test_start_run_is_noop`；加 `_ext_info` 折叠 + `adapt_start` 用例）

**Interfaces:**
- Consumes: `engine.on_start(patch)`（Task 2）；`TaskCallbackData.loop_task_id`/`result`（既有）。
- Produces: `CallbackAdapter.adapt_start(data) -> TaskNodePatch(status=RUNNING, extend_props_patch=...)`；`TaskLoopCallback.start_run(data)` 由 no-op 升级为 `await engine.on_start(adapt_start(data))`。

- [ ] **Step 1: 写/改失败测试**

`tests/community/core/task/task_runner/test_callback_adapter.py`：给 `RecordingEngine` 加 `on_start`，并改 `TestTaskLoopCallback`：

```python
class RecordingEngine:
    def __init__(self):
        self.reports: list[TaskNodePatch] = []
        self.starts: list[TaskNodePatch] = []

    async def on_report(self, patch: TaskNodePatch):
        self.reports.append(patch)
        return patch

    async def on_start(self, patch: TaskNodePatch):
        self.starts.append(patch)
        return patch
```

把 `test_start_run_is_noop` 改为 `test_start_run_routes_to_on_start`：

```python
    def test_start_run_routes_to_on_start(self):
        engine = RecordingEngine()
        cb = TaskLoopCallback(CallbackAdapter(), engine)
        _run(cb.start_run(_data(loop_task_id="t1::c1")))
        assert engine.reports == []          # start 不走 on_report
        assert len(engine.starts) == 1
        p = engine.starts[0]
        assert (p.task_id, p.node_id) == ("t1", "c1")
        from agentclaw.community.core.task.domain.models import Status
        assert p.status == Status.RUNNING
        assert p.acceptance_result is None   # start 不带 acceptance
```

在 `TestAdapt` 加 `_ext_info` 折叠用例（先加一个 `_data_ext` helper 或直接构造 result）：

```python
    def test_adapt_folds_ext_info_into_extend_props(self):
        adapter = CallbackAdapter()
        d = _data(success=True, data="ok")
        d.result["_ext_info"] = {"k": "v"}
        patch = adapter.adapt(d)
        assert patch.extend_props_patch == {"k": "v"}

    def test_adapt_merges_ext_info_and_fail_detail(self):
        adapter = CallbackAdapter()
        d = _data(success=False, fail_detail="gap1")
        d.result["_ext_info"] = {"k": "v"}
        patch = adapter.adapt(d)
        assert patch.extend_props_patch == {"k": "v", "fail_detail": "gap1"}
```

在 `TestTaskLoopCallback` 加 `adapt_start` 用例：

```python
    def test_adapt_start_builds_running_patch_with_ext_info(self):
        from agentclaw.community.core.task.domain.models import Status
        adapter = CallbackAdapter()
        d = _data(loop_task_id="t1::c1")
        d.result["_ext_info"] = {"k": "v"}
        patch = adapter.adapt_start(d)
        assert (patch.task_id, patch.node_id) == ("t1", "c1")
        assert patch.status == Status.RUNNING
        assert patch.acceptance_result is None
        assert patch.extend_props_patch == {"k": "v"}

    def test_adapt_start_without_ext_info_has_no_extend_props(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt_start(_data(loop_task_id="t1::c1"))
        assert patch.extend_props_patch is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/test_callback_adapter.py -v`
Expected: FAIL（`adapt_start` 不存在 / `start_run` 仍 no-op / `_ext_info` 未折叠）

- [ ] **Step 3: 改 `callback_adapter.py`**

import 块加 `Status`：

```python
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Status,
    TaskCallbackData,
    TaskNodePatch,
)
```

`adapt` 末尾 `extend_props_patch` 处替换为合并 `_ext_info` + `fail_detail`：

```python
        ext = data.result.get("_ext_info") or {}
        ep_patch: dict[str, Any] = dict(ext)
        if fail_detail:
            ep_patch["fail_detail"] = fail_detail
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            output_patch={"data": out} if out is not None else None,
            acceptance_result=acceptance,
            extend_props_patch=ep_patch if ep_patch else None,
        )
```

顶部加 `from typing import Any`（若未引）。新增 `adapt_start` 并激活 `start_run`：

```python
    def adapt_start(self, data: TaskCallbackData) -> TaskNodePatch:
        """start 回调:loop_task_id split + status=RUNNING(无 acceptance);折 _ext_info→extend_props。"""
        task_id, node_id = data.loop_task_id.split("::", 1)
        ext = data.result.get("_ext_info") or {}
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.RUNNING,
            extend_props_patch=dict(ext) if ext else None,
        )
```

`TaskLoopCallback.start_run` 改为：

```python
    async def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行:适配层 adapt_start → 编排核 on_start(await)→ PENDING→RUNNING(幂等)。
        协程化:on_start async,await 不阻塞回投调用方。"""
        patch = self._adapter.adapt_start(data)
        await self._engine.on_start(patch)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/test_callback_adapter.py -v`
Expected: PASS（既有 adapt 用例 + 新 `adapt_start`/`_ext_info`/`start_run` 用例）

- [ ] **Step 5: 上游回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/callback_adapter.py tests/community/core/task/task_runner/test_callback_adapter.py
git commit -m "feat(task-callback): activate TaskLoopCallback.start_run via on_start + fold _ext_info

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: `CallbackCorrelationRegistry`（task 级→节点解析 seam）

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/callback_correlation.py`
- Test: `tests/community/core/task/task_runner/test_callback_correlation.py`

**Interfaces:**
- Produces: `CorrelationRecord(task_id, node_id, loop_task_id, workflow_id:int, instance_id:int)`；`CallbackCorrelationRegistry` Protocol（`register(...)`/`resolve(source, instance_id_str) -> CorrelationRecord | None`）；`InMemoryCallbackCorrelationRegistry`（线程安全，dict+RLock，不落库）。被 Task 6 translator 消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/core/task/task_runner/test_callback_correlation.py`：

```python
from __future__ import annotations

import threading

from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    CorrelationRecord,
    InMemoryCallbackCorrelationRegistry,
)


def _make_reg():
    return InMemoryCallbackCorrelationRegistry()


class TestRegistry:
    def test_register_then_resolve(self):
        reg = _make_reg()
        reg.register(
            source="bcn", workflow_id=7, instance_id=77,
            task_id="t1", node_id="n1", loop_task_id="t1::n1",
            workflow_id_str="w7", instance_id_str="inst77",
        )
        rec = reg.resolve("bcn", "inst77")
        assert rec == CorrelationRecord("t1", "n1", "t1::n1", 7, 77)

    def test_resolve_missing_returns_none(self):
        reg = _make_reg()
        assert reg.resolve("claw_mind", "none") is None

    def test_register_is_idempotent_overwrite(self):
        reg = _make_reg()
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n1", loop_task_id="t1::n1",
                     workflow_id_str="w7", instance_id_str="inst77")
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n2", loop_task_id="t1::n2",
                     workflow_id_str="w7", instance_id_str="inst77")
        assert reg.resolve("bcn", "inst77").node_id == "n2"

    def test_keyed_by_source_and_instance(self):
        reg = _make_reg()
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n1", loop_task_id="t1::n1",
                     workflow_id_str="w7", instance_id_str="inst77")
        reg.register(source="claw_mind", workflow_id=8, instance_id=88,
                     task_id="t2", node_id="n2", loop_task_id="t2::n2",
                     workflow_id_str="w8", instance_id_str="inst77")  # 同 instance_id_str,不同 source
        assert reg.resolve("bcn", "inst77").task_id == "t1"
        assert reg.resolve("claw_mind", "inst77").task_id == "t2"

    def test_concurrent_register_resolve(self):
        reg = _make_reg()

        def worker(i):
            reg.register(source="bcn", workflow_id=i, instance_id=i,
                         task_id=f"t{i}", node_id=f"n{i}", loop_task_id=f"t{i}::n{i}",
                         workflow_id_str=f"w{i}", instance_id_str=f"inst{i}")
            reg.resolve("bcn", f"inst{i}")

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in ts: t.start()
        for t in ts: t.join()
        assert reg.resolve("bcn", "inst25") is not None

    def test_protocol_runtime_checkable(self):
        from typing import runtime_checkable, Protocol  # noqa
        assert isinstance(_make_reg(), CallbackCorrelationRegistry)
```

> `CallbackCorrelationRegistry` 若声明为 `@runtime_checkable Protocol`，最后一条用例才成立——实现里加该装饰器。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/test_callback_correlation.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `callback_correlation.py`**

新建 `src/agentclaw/community/core/task/task_runner/callback_correlation.py`：

```python
"""task 级回调→节点寻址 registry(派发期登记,回调期 resolve)。

task 级回调(workflow_start/workflow_result)载荷无 node_id,只有 workflow_instance_id;
派发期(TaskRunner.start_run 派发到 claw_mind/bcn 时)登记 (source, instance_id_str)→节点,
回调期 resolve 得 (task_id, node_id, loop_task_id, SSOT int workflow_id/instance_id)。
in-mem(与 TaskHarness._dispatched_at 同级),不落库;线程安全(dict + RLock)。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CorrelationRecord:
    task_id: str
    node_id: str
    loop_task_id: str
    workflow_id: int          # SSOT int(供 TaskCallbackData)
    instance_id: int          # SSOT int


@runtime_checkable
class CallbackCorrelationRegistry(Protocol):
    """派发期登记 / 回调期 resolve 的寻址端口。"""

    def register(
        self, *, source: str, workflow_id: int, instance_id: int,
        task_id: str, node_id: str, loop_task_id: str,
        workflow_id_str: str, instance_id_str: str,
    ) -> None: ...

    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None: ...


class InMemoryCallbackCorrelationRegistry:
    """线程安全 in-mem 实现。key=(source, instance_id_str)。"""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], CorrelationRecord] = {}
        self._lock = threading.RLock()

    def register(
        self, *, source: str, workflow_id: int, instance_id: int,
        task_id: str, node_id: str, loop_task_id: str,
        workflow_id_str: str, instance_id_str: str,
    ) -> None:
        rec = CorrelationRecord(
            task_id=task_id, node_id=node_id, loop_task_id=loop_task_id,
            workflow_id=workflow_id, instance_id=instance_id,
        )
        with self._lock:
            self._by_key[(source, instance_id_str)] = rec

    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None:
        with self._lock:
            return self._by_key.get((source, instance_id_str))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/test_callback_correlation.py -v`
Expected: PASS（6 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/callback_correlation.py tests/community/core/task/task_runner/test_callback_correlation.py
git commit -m "feat(task-callback): add CallbackCorrelationRegistry for task-level callback routing

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: HTTP 请求/响应 schema（Pydantic v2）

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/__init__.py`
- Create: `src/agentclaw/community/adapters/http/task/schemas.py`
- Test: `tests/community/adapters/http/task/test_schemas.py`

**Interfaces:**
- Produces: `TaskCallbackRequest`、`TaskNodeCallbackRequest(TaskCallbackRequest)`（+`node_id: str`）、`CallbackResponse`。必填非可选：`task_id`/`workflow_source`/`workflow_id`/`workflow_instance_id`/`status`/`is_success`；可空契约态：`goal`/`output`/`failed_info`/`ext_info`/`loop_task_id`。`workflow_source` 用 `Literal["claw_mind","bcn"]`。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/adapters/http/task/test_schemas.py`：

```python
import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas import (
    CallbackResponse, TaskCallbackRequest, TaskNodeCallbackRequest,
)


def _base(**kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w1",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    return d


def test_task_callback_request_defaults():
    r = TaskCallbackRequest(**_base())
    assert r.goal is None and r.output is None and r.failed_info is None
    assert r.ext_info is None and r.loop_task_id is None


def test_node_callback_request_requires_node_id():
    with pytest.raises(ValidationError):
        TaskNodeCallbackRequest(**_base())  # 缺 node_id
    r = TaskNodeCallbackRequest(**_base(node_id="n1"))
    assert r.node_id == "n1"


def test_workflow_source_literal():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(**_base(workflow_source="bbs"))


def test_required_fields_enforced():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(task_id="t1", workflow_source="bcn")  # 缺必填


def test_callback_response_defaults():
    r = CallbackResponse(success=True)
    assert r.code == 200 and r.message == "OK"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_schemas.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 schema + `__init__.py`**

新建 `src/agentclaw/community/adapters/http/task/__init__.py`（暂空，Task 8 填 router 导出）：

```python
"""task_loop callback HTTP 适配层(inbound PUSH,单 bot workflow / bcn 协作群)。"""
```

新建 `src/agentclaw/community/adapters/http/task/schemas.py`：

```python
"""任务回调线上 schema(Pydantic v2,对齐羽雀 TaskCallbackData/TaskNodeCallbackData 字段)。

SSOT TaskCallbackData 保持精简(不扩);羽雀丰富字段在 translator 边缘折叠进 SSOT。
必填非可选(AGENTS.md):task_id/workflow_source/workflow_id/workflow_instance_id/status/is_success。
None 仅契约态:goal/output/failed_info/ext_info/loop_task_id(回声字段,缺失走 registry)。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskCallbackRequest(BaseModel):
    """task 级(workflow)回调载荷。"""

    task_id: str
    workflow_source: Literal["claw_mind", "bcn"]
    workflow_id: str
    workflow_instance_id: str
    goal: str | None = None
    status: str
    is_success: bool
    output: dict[str, Any] | None = None
    failed_info: str | None = None
    ext_info: dict[str, Any] | None = None
    loop_task_id: str | None = None    # 回声字段:派发期透传,引擎原样回带(可选)


class TaskNodeCallbackRequest(TaskCallbackRequest):
    """node 级回调载荷(node_id 即 Avernet 子节点 id,统一领域对象 1:1 映射)。"""

    node_id: str


class CallbackResponse(BaseModel):
    success: bool
    code: int = 200
    message: str = "OK"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_schemas.py -v`
Expected: PASS（5 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/adapters/http/task/__init__.py src/agentclaw/community/adapters/http/task/schemas.py tests/community/adapters/http/task/test_schemas.py
git commit -m "feat(task-callback): add Pydantic v2 callback request/response schemas

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: `CallbackRequestTranslator`（边缘翻译，SSOT 精简）

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/translator.py`
- Test: `tests/community/adapters/http/task/test_translator.py`

**Interfaces:**
- Consumes: `TaskCallbackRequest`/`TaskNodeCallbackRequest`（Task 5）、`CallbackCorrelationRegistry`（Task 4）、`CallbackCorrelationError`（Task 1）、SSOT `TaskCallbackData`/`AcceptanceResult`/`AcceptanceVerdict`/`Status`（domain/models）。
- Produces: `TranslatedCallback(disposition: "start"|"result", data: TaskCallbackData)`；`translate(req, disposition, registry) -> TranslatedCallback`。`data.result` 含 `success`/`data`/`fail_detail` + `_ext_info`（ext/goal/未登记 str id）；`data.loop_task_id` 解析（回声→node 直拼→registry→`CallbackCorrelationError`）；`data.workflow_type` 由 `workflow_source` 映射；`data.workflow_id`/`instance_id` 取 registry 的 SSOT int（未登记回退 0）。被 Task 8 router 消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/adapters/http/task/test_translator.py`：

```python
import pytest

from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackRequest, TaskNodeCallbackRequest,
)
from agentclaw.community.adapters.http.task.translator import translate
from agentclaw.community.core.errors import CallbackCorrelationError
from agentclaw.community.core.task.task_runner.callback_correlation import (
    InMemoryCallbackCorrelationRegistry,
)


def _reg_with(source="bcn", instance_id_str="i1", **kw):
    reg = InMemoryCallbackCorrelationRegistry()
    reg.register(source=source, workflow_id=7, instance_id=77,
                 task_id="t1", node_id="root1", loop_task_id="t1::root1",
                 workflow_id_str="w7", instance_id_str=instance_id_str, **kw)
    return reg


def _base(**kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w7",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    return d


class TestLoopTaskIdResolution:
    def test_node_level_builds_loop_task_id_from_node_id(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", output={"r": 1}))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.loop_task_id == "t1::c1"

    def test_task_level_uses_echo_loop_task_id(self):
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.loop_task_id == "t1::root1"

    def test_task_level_no_echo_resolves_via_registry(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base())  # 无 loop_task_id 回声
        tc = translate(req, "result", reg)
        assert tc.data.loop_task_id == "t1::root1"

    def test_task_level_no_echo_no_registry_raises(self):
        req = TaskCallbackRequest(**_base())
        with pytest.raises(CallbackCorrelationError):
            translate(req, "result", InMemoryCallbackCorrelationRegistry())


class TestFieldFolding:
    def test_success_folds_output_to_result_data(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", output={"r": 1}, is_success=True))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.result["success"] is True
        assert tc.data.result["data"] == {"r": 1}
        assert "fail_detail" not in tc.data.result

    def test_fail_folds_failed_info_to_fail_detail(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", is_success=False, failed_info="boom"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.result["success"] is False
        assert tc.data.result["fail_detail"] == "boom"

    def test_ext_info_and_goal_folded_into_ext(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", ext_info={"k": "v"}, goal="G"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        ext = tc.data.result["_ext_info"]
        assert ext["k"] == "v"
        assert ext["_callback_goal"] == "G"

    def test_workflow_source_maps_to_workflow_type(self):
        req = TaskNodeCallbackRequest(**_base(workflow_source="claw_mind", node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.workflow_type == "single_bot"

    def test_registry_provides_ssot_int_ids(self):
        reg = _reg_with()
        req = TaskCallbackRequest(**_base(loop_task_id="t1::root1"))
        tc = translate(req, "result", reg)
        assert tc.data.workflow_id == 7
        assert tc.data.instance_id == 77

    def test_unregistered_node_level_falls_back_zero_int_and_stashes_str(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        tc = translate(req, "result", InMemoryCallbackCorrelationRegistry())
        assert tc.data.workflow_id == 0
        assert tc.data.result["_ext_info"]["_workflow_id_str"] == "w7"
        assert tc.data.result["_ext_info"]["_instance_id_str"] == "i1"


class TestDisposition:
    def test_result_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1"))
        assert translate(req, "result", InMemoryCallbackCorrelationRegistry()).disposition == "result"

    def test_start_disposition_passes_through(self):
        req = TaskNodeCallbackRequest(**_base(node_id="c1", status="RUNNING"))
        assert translate(req, "start", InMemoryCallbackCorrelationRegistry()).disposition == "start"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_translator.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `translator.py`**

新建 `src/agentclaw/community/adapters/http/task/translator.py`：

```python
"""回调请求边缘翻译:羽雀 schema → SSOT TaskCallbackData(+disposition)。

SSOT TaskCallbackData 不扩;ext_info/goal/未登记 str id 塞进 result["_ext_info"],
由 CallbackAdapter.adapt/adapt_start 折进 extend_props_patch。零 case:仅消费 schema 字段
+ loop_task_id/node_id/workflow_source,无节点名字面量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentclaw.community.core.errors import CallbackCorrelationError
from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)

from .schemas import TaskCallbackRequest, TaskNodeCallbackRequest

_SOURCE_TO_TYPE = {"claw_mind": "single_bot", "bcn": "bcn_coop_group"}


@dataclass(frozen=True)
class TranslatedCallback:
    disposition: Literal["start", "result"]
    data: TaskCallbackData


def translate(
    req: TaskCallbackRequest,
    disposition: Literal["start", "result"],
    registry: CallbackCorrelationRegistry,
) -> TranslatedCallback:
    source = req.workflow_source
    workflow_type = _SOURCE_TO_TYPE[source]

    # loop_task_id 解析:回声 > node 直拼 > registry > CallbackCorrelationError
    loop_task_id = req.loop_task_id
    if loop_task_id is None:
        if isinstance(req, TaskNodeCallbackRequest):
            loop_task_id = f"{req.task_id}::{req.node_id}"
        else:
            rec = registry.resolve(source, req.workflow_instance_id)
            if rec is None:
                raise CallbackCorrelationError(
                    f"task-level callback unregistered: {source}/{req.workflow_instance_id}"
                )
            loop_task_id = rec.loop_task_id

    # registry 取 SSOT int id(未登记 node 级回退 0)
    rec = registry.resolve(source, req.workflow_instance_id)
    wf_id_int = rec.workflow_id if rec is not None else 0
    inst_id_int = rec.instance_id if rec is not None else 0

    # result 折叠
    result: dict = {"success": req.is_success}
    if req.output is not None:
        result["data"] = req.output
    if req.failed_info is not None:
        result["fail_detail"] = req.failed_info

    # ext_info/goal/未登记 str id → result["_ext_info"]
    ext: dict = dict(req.ext_info or {})
    if req.goal is not None:
        ext["_callback_goal"] = req.goal
    if rec is None:
        ext.setdefault("_workflow_id_str", req.workflow_id)
        ext.setdefault("_instance_id_str", req.workflow_instance_id)
    if ext:
        result["_ext_info"] = ext

    data = TaskCallbackData(
        loop_task_id=loop_task_id,
        workflow_type=workflow_type,
        workflow_id=wf_id_int,
        instance_id=inst_id_int,
        result=result,
    )
    return TranslatedCallback(disposition=disposition, data=data)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_translator.py -v`
Expected: PASS（11 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/adapters/http/task/translator.py tests/community/adapters/http/task/test_translator.py
git commit -m "feat(task-callback): add CallbackRequestTranslator (edge → SSOT TaskCallbackData)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: `CallbackAuthenticator`（HMAC + Noop + `verify_callback` dep）

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/auth.py`
- Test: `tests/community/adapters/http/task/test_auth.py`

**Interfaces:**
- Produces: `CallbackAuthenticator` Protocol（`verify(*, source, headers, raw_body) -> None`，失败 raise `CallbackAuthError`）；`HmacCallbackAuthenticator(secrets: Mapping[str,str])`（签串 `f"{timestamp}{method}{path}{body_sha256_hex}"`，头 `X-TaskLoop-Token`/`X-TaskLo- Timestamp`/`X-TaskLoop-Signature`，时间戳偏移 >300s→`CallbackAuthError`）；`NoopCallbackAuthenticator`（直通）。被 Task 8 router 在解析 body 后调用（`source` 来自已解析 body）。

> 注：router 内直接调用 `auth.verify(...)`（非 FastAPI Depends），因 `source` 需先解析 body 才能 hmac 取密钥。`verify_callback` 作为 helper 暴露但不强制 Depends。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/adapters/http/task/test_auth.py`：

```python
import hashlib
import hmac
import time

import pytest

from agentclaw.community.adapters.http.task.auth import (
    HmacCallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.core.errors import CallbackAuthError

_SECRET = "s3cr3t"


def _signed(method="POST", path="/task_loop/callback/workflow_result", body=b'{"x":1}',
            ts=None, secret=_SECRET, token="bcn"):
    ts = ts if ts is not None else str(int(time.time()))
    body_hex = hashlib.sha256(body).hexdigest()
    sign_str = f"{ts}{method}{path}{body_hex}"
    sig = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {
        "X-TaskLoop-Token": token,
        "X-TaskLoop-Timestamp": ts,
        "X-TaskLoop-Signature": sig,
    }


def test_hmac_verify_passes():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET, "claw_mind": "other"})
    h = _signed()
    auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                method="POST", path="/task_loop/callback/workflow_result")


def test_hmac_bad_signature_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    h = _signed()
    h["X-TaskLoop-Signature"] = "deadbeef"
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                    method="POST", path="/p")


def test_hmac_unknown_source_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    with pytest.raises(CallbackAuthError):
        auth.verify(source="claw_mind", headers={"X-TaskLoop-Timestamp": str(int(time.time()))},
                    raw_body=b"x", method="POST", path="/p")


def test_hmac_stale_timestamp_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET, "_max_skew_s": 300})  # type: ignore[arg-type]
    old_ts = str(int(time.time()) - 1000)
    h = _signed(ts=old_ts)
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                    method="POST", path="/p")


def test_hmac_body_tamper_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    h = _signed(body=b'{"x":1}')
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":2}',
                    method="POST", path="/p")


def test_noop_always_passes():
    NoopCallbackAuthenticator().verify(source="bcn", headers={}, raw_body=b"anything",
                                       method="POST", path="/p")
```

> `HmacCallbackAuthenticator` 接受 `max_skew_s` 配置（默认 300）。测试里 `_max_skew_s` 走构造可选参数；若用 dataclass/关键字，调整为 `HmacCallbackAuthenticator(secrets={...}, max_skew_s=300)` 并相应改测试。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_auth.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `auth.py`**

新建 `src/agentclaw/community/adapters/http/task/auth.py`：

```python
"""回调鉴权端口:HMAC(默认,镜像 BCS 出站签名) + Noop(double/singlebox)。

签串 f"{timestamp}{method}{path}{body_sha256_hex}";头 X-TaskLoop-Token/Timestamp/Signature。
不 import ocb(开源边界);自包含 hashlib/hmac 实现。失败 raise CallbackAuthError(DomainError→401)。
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping, Protocol, runtime_checkable

from agentclaw.community.core.errors import CallbackAuthError

_TOKEN_HEADER = "X-TaskLoop-Token"
_TIMESTAMP_HEADER = "X-TaskLoop-Timestamp"
_SIGNATURE_HEADER = "X-TaskLoop-Signature"
_DEFAULT_MAX_SKEW_S = 300


@runtime_checkable
class CallbackAuthenticator(Protocol):
    def verify(
        self, *, source: str, headers: Mapping[str, str], raw_body: bytes,
        method: str, path: str,
    ) -> None: ...


class HmacCallbackAuthenticator:
    """HMAC-SHA256 签名校验,按 source 取共享密钥。"""

    def __init__(self, secrets: Mapping[str, str], *, max_skew_s: int = _DEFAULT_MAX_SKEW_S) -> None:
        self._secrets = dict(secrets)
        self._max_skew_s = max_skew_s

    def verify(self, *, source, headers, raw_body, method, path) -> None:
        secret = self._secrets.get(source)
        if secret is None:
            raise CallbackAuthError(f"unknown callback source: {source}")
        ts = headers.get(_TIMESTAMP_HEADER)
        sig = headers.get(_SIGNATURE_HEADER)
        if not ts or not sig:
            raise CallbackAuthError("missing timestamp/signature header")
        try:
            ts_int = int(ts)
        except ValueError:
            raise CallbackAuthError("invalid timestamp")
        if abs(int(time.time()) - ts_int) > self._max_skew_s:
            raise CallbackAuthError("stale timestamp")
        body_hex = hashlib.sha256(raw_body).hexdigest()
        sign_str = f"{ts}{method}{path}{body_hex}"
        expected = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise CallbackAuthError("signature mismatch")


class NoopCallbackAuthenticator:
    """singlebox/test 直通(进程内可信)。"""

    def verify(self, *, source, headers, raw_body, method, path) -> None:  # noqa: D401
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_auth.py -v`
Expected: PASS（6 用例；调整 `test_hmac_stale_timestamp_raises` 的构造参数为 `max_skew_s=300` 形式若实现用关键字）

- [ ] **Step 5: 修正测试构造参数对齐实现**

若 Step 1 测试里 `HmacCallbackAuthenticator(secrets={...}, "_max_skew_s": 300)` 写法不对，改为：

```python
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET}, max_skew_s=300)
```

重跑：`cd src/backend && python -m pytest tests/community/adapters/http/task/test_auth.py -v` → PASS。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/adapters/http/task/auth.py tests/community/adapters/http/task/test_auth.py
git commit -m "feat(task-callback): add CallbackAuthenticator (HMAC + Noop)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: callback router（4 端点 + 错误映射 + 幂等 ack）

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/router.py`
- Modify: `src/agentclaw/community/adapters/http/task/__init__.py`（导出 `task_callback_router`）
- Test: `tests/community/adapters/http/task/test_router.py`

**Interfaces:**
- Consumes: `TaskServiceProtocol`（`Injected`；`.callback.start_run/report_result` + `get_task_dashboard`）、`CallbackAuthenticator`（`Injected`）、`CallbackCorrelationRegistry`（`Injected`）；`translate`（Task 6）；`CallbackResponse`（Task 5）；`TaskStateError`/`TaskNotFoundError`/`NodeNotFoundError`（domain/errors）；`Status`（domain/models）。
- Produces: `task_callback_router: APIRouter`（prefix `/task_loop/callback`，4 端点）。错误映射：`TaskNotFoundError`/`NodeNotFoundError`→404；`TaskStateError`→result 路径 re-query 已终态→200 idempotent 否则 409，start 路径→409；`CallbackAuthError`/`CallbackCorrelationError` 由中央 DomainError handler→401/400；Pydantic→422。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/adapters/http/task/test_router.py`（用 FastAPI TestClient + 一个 stub TaskService，避免 injector 全量装配）：

```python
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from agentclaw.community.adapters.http.task.router import task_callback_router
from agentclaw.community.adapters.http.task.schemas import CallbackResponse  # noqa
from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import Status, TaskExecutionGraph, TaskNode
from agentclaw.community.core.task.task_runner.callback_correlation import (
    InMemoryCallbackCorrelationRegistry,
)


class StubCallback:
    def __init__(self): self.calls = []
    async def start_run(self, data): self.calls.append(("start", data))
    async def report_result(self, data): self.calls.append(("result", data))


class StubService:
    """直接挂在 app.state 的 stub,router 经 Depends 取它(测试用 dependency_overrides)。"""
    def __init__(self):
        self.callback = StubCallback()
        self._node_status: dict[tuple[str, str], Status] = {}

    def set_node_status(self, task_id, node_id, status):
        self._node_status[(task_id, node_id)] = status

    def get_task_dashboard(self, task_id, node_id=None):
        g = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.PENDING)
        for (tid, nid), st in list(self._node_status.items()):
            if tid == task_id:
                g.tasks.append(_make_node(tid, nid, st))
        return g


def _make_node(task_id, node_id, status):
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, TaskNode, TaskSpec,
    )
    return TaskNode(node_id=node_id, task_id=task_id, status=status,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(), node_run_graph=None)  # type: ignore[arg-type]


def _body(node=False, **kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w7",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    if node:
        d.setdefault("node_id", "c1")
    return d


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(task_callback_router)
    svc = StubService()
    # router 经 Injected(...) 取;测试用 dependency_overrides 注入
    from agentclaw.community.adapters.http.task import router as r
    app.dependency_overrides[r._get_svc] = lambda: svc
    app.dependency_overrides[r._get_auth] = lambda: __import__(
        "agentclaw.community.adapters.http.task.auth", fromlist=["NoopCallbackAuthenticator"]
    ).NoopCallbackAuthenticator()
    app.dependency_overrides[r._get_registry] = lambda: InMemoryCallbackCorrelationRegistry()
    return app, svc


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app[0])


class TestRouter:
    def test_workflow_result_success(self, app, client):
        _, svc = app
        r = client.post("/task_loop/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 200
        assert svc.callback.calls[0][0] == "result"

    def test_node_result_success(self, app, client):
        _, svc = app
        r = client.post("/task_loop/callback/node_result", json=_body(node=True))
        assert r.status_code == 200
        assert svc.callback.calls[0][1].loop_task_id == "t1::c1"

    def test_workflow_start_success(self, app, client):
        _, svc = app
        r = client.post("/task_loop/callback/workflow_start", json=_body(loop_task_id="t1::root1", status="RUNNING"))
        assert r.status_code == 200
        assert svc.callback.calls[0][0] == "start"

    def test_node_start_success(self, app, client):
        _, svc = app
        r = client.post("/task_loop/callback/node_start", json=_body(node=True, status="RUNNING"))
        assert r.status_code == 200
        assert svc.callback.calls[0][0] == "start"

    def test_result_idempotent_when_already_terminal(self, app, client):
        _, svc = app
        svc.callback.report_result = _raise(TaskStateError("DONE->DONE"))
        svc.set_node_status("t1", "root1", Status.DONE)
        r = client.post("/task_loop/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 200  # 幂等 ack

    def test_result_409_when_illegal(self, app, client):
        _, svc = app
        svc.callback.report_result = _raise(TaskStateError("PENDING->DONE"))
        svc.set_node_status("t1", "root1", Status.PENDING)  # 非终态→409
        r = client.post("/task_loop/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 409

    def test_start_409_on_stale(self, app, client):
        _, svc = app
        svc.callback.start_run = _raise(TaskStateError("stale"))
        r = client.post("/task_loop/callback/node_start", json=_body(node=True, status="RUNNING"))
        assert r.status_code == 409

    def test_not_found_404(self, app, client):
        _, svc = app
        svc.callback.report_result = _raise(NodeNotFoundError("x"))
        r = client.post("/task_loop/callback/workflow_result", json=_body(loop_task_id="t1::root1"))
        assert r.status_code == 404

    def test_correlation_error_400(self, app, client):
        # task 级无回声 + 空 registry → CallbackCorrelationError
        r = client.post("/task_loop/callback/workflow_result", json=_body())  # 无 loop_task_id,registry 空
        assert r.status_code == 400

    def test_validation_422(self, app, client):
        r = client.post("/task_loop/callback/node_result", json={"task_id": "t1"})  # 缺必填
        assert r.status_code == 422


def _raise(exc):
    async def _f(data):
        raise exc
    return _f
```

> router 用三个内部 `Depends` provider（`_get_svc`/`_get_auth`/`_get_registry`）桥接 `Injected`，使测试可 `dependency_overrides`。实现见 Step 3。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_router.py -v`
Expected: FAIL `ModuleNotFoundError`（router 未建）

- [ ] **Step 3: 实现 `router.py`**

新建 `src/agentclaw/community/adapters/http/task/router.py`：

```python
"""task_loop inbound PUSH 回调 router(4 端点)。单 bot workflow / bcn 协作群→回调。

边缘:解析 body → auth.verify(source from body) → translate → disposition 分发
start_run/report_result。TaskError(TaskStateError/NotFound)router 层显式映射;
CallbackAuthError/CallbackCorrelationError(DomainError 子类)由中央 handler 映射。
幂等:result 重投到已终态节点→200 ack;start stale→409。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator, NoopCallbackAuthenticator
from agentclaw.community.adapters.http.task.schemas import (
    CallbackResponse, TaskCallbackRequest, TaskNodeCallbackRequest,
)
from agentclaw.community.adapters.http.task.translator import translate
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError, TaskNotFoundError, TaskStateError,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/task_loop/callback", tags=["task-callback"])


def _get_svc() -> TaskServiceProtocol:
    return Injected(TaskServiceProtocol)  # type: ignore[return-value]


def _get_auth() -> CallbackAuthenticator:
    return Injected(CallbackAuthenticator)  # type: ignore[return-value]


def _get_registry() -> CallbackCorrelationRegistry:
    return Injected(CallbackCorrelationRegistry)  # type: ignore[return-value]


_TERMINAL = {Status.DONE, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


async def _dispatch(
    request: Request, disposition: str, schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol, auth: CallbackAuthenticator, registry: CallbackCorrelationRegistry,
) -> CallbackResponse:
    raw = await request.body()
    try:
        req = schema_cls.model_validate_json(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid callback body")
    # source 来自已解析 body;HMAC 用原始字节
    auth.verify(source=req.workflow_source, headers=request.headers, raw_body=raw,
                method=request.method, path=request.url.path)
    tc = translate(req, disposition, registry)
    try:
        if disposition == "start":
            await svc.callback.start_run(tc.data)
        else:
            await svc.callback.report_result(tc.data)
    except (TaskNotFoundError, NodeNotFoundError):
        raise HTTPException(status_code=404, detail="task/node not found")
    except TaskStateError:
        if disposition == "result":
            cur = _find_node_status(svc, tc.data.loop_task_id)
            if cur in _TERMINAL:
                return CallbackResponse(success=True, code=200, message="idempotent")
        raise HTTPException(status_code=409, detail="illegal state transition")
    return CallbackResponse(success=True)


@router.post("/workflow_start", response_model=CallbackResponse)
async def workflow_start(
    request: Request,
    svc: TaskServiceProtocol = Depends(_get_svc),
    auth: CallbackAuthenticator = Depends(_get_auth),
    registry: CallbackCorrelationRegistry = Depends(_get_registry),
) -> CallbackResponse:
    return await _dispatch(request, "start", TaskCallbackRequest, svc, auth, registry)


@router.post("/workflow_result", response_model=CallbackResponse)
async def workflow_result(
    request: Request,
    svc: TaskServiceProtocol = Depends(_get_svc),
    auth: CallbackAuthenticator = Depends(_get_auth),
    registry: CallbackCorrelationRegistry = Depends(_get_registry),
) -> CallbackResponse:
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry)


@router.post("/node_start", response_model=CallbackResponse)
async def node_start(
    request: Request,
    svc: TaskServiceProtocol = Depends(_get_svc),
    auth: CallbackAuthenticator = Depends(_get_auth),
    registry: CallbackCorrelationRegistry = Depends(_get_registry),
) -> CallbackResponse:
    return await _dispatch(request, "start", TaskNodeCallbackRequest, svc, auth, registry)


@router.post("/node_result", response_model=CallbackResponse)
async def node_result(
    request: Request,
    svc: TaskServiceProtocol = Depends(_get_svc),
    auth: CallbackAuthenticator = Depends(_get_auth),
    registry: CallbackCorrelationRegistry = Depends(_get_registry),
) -> CallbackResponse:
    return await _dispatch(request, "result", TaskNodeCallbackRequest, svc, auth, registry)
```

`__init__.py` 改为：

```python
"""task_loop callback HTTP 适配层(inbound PUSH,单 bot workflow / bcn 协作群)。"""
from agentclaw.community.adapters.http.task.router import task_callback_router

__all__ = ["task_callback_router"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_router.py -v`
Expected: PASS（10 用例）
> 若 `Injected` 在非 injector 上下文 raise，`_get_svc` 用 `Injected(...)` 作返回值在测试会被 `dependency_overrides` 覆盖，不会实际执行 `Injected` 取值——确认 `Depends(_get_svc)` 先用 override。若 fastapi-injector 的 `Injected` 在 import 时即生成 Depends，改为直接 `Depends(_get_svc)` 形式（如上）即可被 override。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/adapters/http/task/router.py src/agentclaw/community/adapters/http/task/__init__.py tests/community/adapters/http/task/test_router.py
git commit -m "feat(task-callback): add 4-endpoint inbound callback router with idempotent ack

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: `TaskModule` DI 装配 + profile 登记 + app 挂载 router

**Files:**
- Create: `src/agentclaw/community/di/modules/task_module.py`
- Modify: `src/agentclaw/community/di/profile_modules.py`（TEST/SINGLEBOX column 登记 `TaskModule()`）
- Modify: `src/agentclaw/community/adapters/http/app.py`（import + `app.include_router(task_callback_router)`）
- Test: `tests/community/di/test_task_module.py`

**Interfaces:**
- Produces: `TaskModule(Module)`——singleton 绑 `TaskGraphService`（零参）、`TaskService`（`@inject` 注入 graph）、`InMemoryCallbackCorrelationRegistry`、`NoopCallbackAuthenticator`，并 `@provider` 暴露 `TaskServiceProtocol`/`CallbackCorrelationRegistry`/`CallbackAuthenticator`。community TEST/SINGLEBOX profile 可解析 `Injected(TaskServiceProtocol)` 等。router 被挂进 app。

> 社区分布只绑 `NoopCallbackAuthenticator`（进程内可信）。CORP/prod 的 `HmacCallbackAuthenticator` + 真实密钥由 corp adapter 落地（seam，本任务不绑）。

- [ ] **Step 1: 写失败测试**

新建 `tests/community/di/test_task_module.py`：

```python
from injector import Injector

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.di.modules.task_module import TaskModule


def test_task_module_binds_singletons():
    inj = Injector([TaskModule()])
    assert isinstance(inj.get(TaskServiceProtocol).__class__.__name__, str)  # resolves
    assert isinstance(inj.get(CallbackCorrelationRegistry), InMemoryCallbackCorrelationRegistry)
    assert isinstance(inj.get(CallbackAuthenticator), NoopCallbackAuthenticator)
    # singleton:两次取同对象
    assert inj.get(TaskServiceProtocol) is inj.get(TaskServiceProtocol)
    assert inj.get(CallbackCorrelationRegistry) is inj.get(CallbackCorrelationRegistry)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/di/test_task_module.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `task_module.py`**

新建 `src/agentclaw/community/di/modules/task_module.py`：

```python
"""TaskModule — task_loop callback 服务 DI 装配(社区 TEST/SINGLEBOX profile)。

Singleton 绑定:TaskGraphService(零参)、TaskService(@inject 注入 graph;harness 默认 None)、
InMemoryCallbackCorrelationRegistry、NoopCallbackAuthenticator(社区进程内可信)。
@provider 暴露 Protocol:TaskServiceProtocol/CallbackCorrelationRegistry/CallbackAuthenticator。
CORP/prod 的 HmacCallbackAuthenticator + 真实密钥由 corp adapter 覆写(seam)。
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)


class TaskModule(Module):
    """社区 TEST/SINGLEBOX profile 的 task callback 服务绑定。"""

    def configure(self, binder: Binder) -> None:
        binder.bind(TaskGraphService, to=TaskGraphService, scope=singleton)
        binder.bind(TaskService, to=TaskService, scope=singleton)
        binder.bind(InMemoryCallbackCorrelationRegistry, to=InMemoryCallbackCorrelationRegistry, scope=singleton)
        binder.bind(NoopCallbackAuthenticator, to=NoopCallbackAuthenticator, scope=singleton)

    @singleton
    @provider
    @inject
    def _task_service_protocol(self, svc: TaskService) -> TaskServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _registry(self, r: InMemoryCallbackCorrelationRegistry) -> CallbackCorrelationRegistry:
        return r

    @singleton
    @provider
    @inject
    def _auth(self, a: NoopCallbackAuthenticator) -> CallbackAuthenticator:
        return a
```

> `TaskService.__init__(graph, harness=None)`：`harness` 有默认值，injector 只注入无默认值的 `graph`（`TaskGraphService` 已绑），`harness` 留 None。若 injector 对带默认参数也尝试注入而失败，改为显式 provider：`def _task_service(self, graph: TaskGraphService) -> TaskService: return TaskService(graph)`，并把 `binder.bind(TaskService,...)` 去掉。

- [ ] **Step 4: profile_modules.py 登记**

`di/profile_modules.py` 的 `modules_for` TEST/SINGLEBOX 分支（`column = _common_test_doubles() + [...]`，约 L130）顶部 import 块加：

```python
        from agentclaw.community.di.modules.task_module import TaskModule
```

`column` 列表里加 `TaskModule(),`（建议放在列表末尾，`TestAppServicesModule()` 之后）。

- [ ] **Step 5: app.py 挂载 router**

`adapters/http/app.py` 顶部 router import 块（与 `quality_router` 等同级）加：

```python
from agentclaw.community.adapters.http.task import task_callback_router
```

在 mandatory `app.include_router(...)` 块（`app.include_router(quality_router)` 附近，约 L768）后加一行：

```python
app.include_router(task_callback_router)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/di/test_task_module.py -v`
Expected: PASS

- [ ] **Step 7: 全模块回归**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/ tests/community/core/task/ tests/community/di/test_task_module.py tests/community/architecture/test_domain_error_status_map_complete.py -v`
Expected: PASS（router 现经 injector 也可装配；app import 不破）

- [ ] **Step 8: Commit**

```bash
git add src/agentclaw/community/di/modules/task_module.py src/agentclaw/community/di/profile_modules.py src/agentclaw/community/adapters/http/app.py tests/community/di/test_task_module.py
git commit -m "feat(task-callback): wire TaskModule DI + mount callback router in app

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 10: 零 case grep 红线 + 全量回归收口

**Files:**
- Test: 既有零 case 架构测试（若有）/ 新增断言

**Interfaces:**
- 验证新模块源码 0 命中节点名字面量；全量 task + http + di 回归绿。

- [ ] **Step 1: 写零 case 断言测试**

新建 `tests/community/adapters/http/task/test_zero_case.py`：

```python
from pathlib import Path

_BASE = Path("src/agentclaw/community/adapters/http/task")
_FILES = ["schemas.py", "translator.py", "auth.py", "router.py", "__init__.py"]
_FORBIDDEN = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]


def test_no_node_name_literals():
    hits = []
    for f in _FILES:
        src = (_BASE / f).read_text()
        hits += [f"{f}:{tok}" for tok in _FORBIDDEN if tok in src]
    assert hits == [], f"task callback 出现写死节点名: {hits}"
```

- [ ] **Step 2: 跑测试**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/task/test_zero_case.py -v`
Expected: PASS

- [ ] **Step 3: 全量回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/ tests/community/adapters/http/task/ tests/community/di/test_task_module.py tests/community/architecture/ -v`
Expected: PASS（121 既有 + 新增全绿）

- [ ] **Step 4: pre-push lint（SAST 默认）**

Run: `cd src/backend && git push --dry-run 2>&1 | tail -20`（或按 AGENTS.md 的 pre-push target contract）
Expected: lint-only 通过（`OCB_PRE_PUSH_RUN_CI` 未设时跳过重测试）

- [ ] **Step 5: Commit**

```bash
git add tests/community/adapters/http/task/test_zero_case.py
git commit -m "test(task-callback): assert zero case-name literals in callback modules

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Notes（已自审）

- **Spec 覆盖**：§7.1 4 端点→Task 8；§7.2 schema→Task 5；§7.3 translator→Task 6；§7.4 on_start→Task 2；§7.5 激活 start_run→Task 3；§7.6 registry→Task 4；§7.7 HMAC→Task 7；§7.8 错误映射/幂等→Task 8；§7.9 DI→Task 9；§7.10 loop_task_id 回声透传→translator 回声分支（Task 6）+ 派发期登记 seam（Task 4 port，登记动作属 runner spec/corp adapter，本计划只定 port）。
- **类型一致**：`translate(req, disposition, registry) -> TranslatedCallback(disposition, data)`；`adapt_start(data) -> TaskNodePatch`；`on_start(patch) -> NodeOpResult`；`_get_svc/_get_auth/_get_registry` 三个 Depends provider 名在 router 与 test_router 一致；`CorrelationRecord` 字段在 registry/translator/test 一致。
- **placeholder 扫描**：无 TBD/TODO；所有代码块为可执行内容。`TaskService.__init__` 注入 harness 的两种兜底显式写在 Task 9 Step 3 注释里。
- **已知偏离 spec（已同步 spec）**：`TaskStateError` 不进中央 map（非 DomainError），router 层映射（spec §7.8 已改）；`ext_info/goal` 走 `result["_ext_info"]`（spec §7.3/§7.5 已改）。

## R2 singlebox E2E（后续，依赖 runner-integration 派发期登记 seam 落地）

runner-integration spec 的派发期 `CallbackCorrelationRegistry.register(...)` 登记动作落地后，补 singlebox e2e：`NoopCallbackAuthenticator` + 派发期预登记 + 进程内回投驱动 `workflow_start→workflow_result`（root RUNNING→DONE→finish）、`node_result` FAIL→补救/BBS 升级、重投幂等。复用既有 e2e 剧本断言。本计划范围到 R1（含 HMAC + 单测 + DI 装配）为止。
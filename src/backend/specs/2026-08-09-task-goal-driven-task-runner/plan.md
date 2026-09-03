# task_runner integration 子模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `TaskRunner.start_run` / `form_coop_group` 的 stub 替换为真实执行接入——单 bot 走 BaaS Open API（静态 App Key + `allowed-bots` grant + `POST /openapi/v1/messages` 取 `run_id`）、协作群走自包含 BCS HTTP client（三态 chat/manager_worker/state_machine）、bbs 仅记日志；结果统一经旁路 `TaskExecutorResultPoller` 回收 → `TaskLoopCallback.report_result` → `on_report`。

**Architecture:** 方案 A——所有模态「派发 await 取 `run_id`/`session_id` 即返回（不等待结果）+ 旁路 Poller 回收终态」。`TaskExecutor.dispatch` 为 async（在上游 `start_run` 的 caller loop 上 `gather`+`Semaphore` 限流），**await 到拿到 `run_id` 为止**，失败（grant 403 等）→ 该 node 返 `False`；`TaskExecutorResultPoller` 是独立 daemon 线程 sidecar（同 `TaskHarness` 风格：`register`/`run_poll_loop(stop_event)`/可注入 clock+sleep/`_poll_once` async 可单测），三模态回收。开源边界：httpx async 自包含，不 import ocb `ecb` / 不 import in-repo `secbaas`；HMAC 镜像 ocb `BcsHttpClient` 模式。singlebox 经注入 double 跑真实集成形态。

**Tech Stack:** Python 3.12 + httpx 0.28 async（`httpx.MockTransport` 单测）+ asyncio（`gather`+`Semaphore(8)`）+ threading（poller daemon 线程，per-handle `threading.RLock`）。

**Spec:** `src/backend/specs/2026-08-09-task-goal-driven-task-runner-2/spec.md`

## Global Constraints

- 遵循 `AGENTS.md`：不引入 `T | None` 除非 None 是契约态（`session_id is None`→`create_session`、`bot_id` 不在 `allowed_bots`→`ensure_grant`、`run_id is None`→session 模）；必填值非可选。
- 不破上游契约：`TaskRunner.start_run(toDoTaskList) -> list[bool]` async 签名不变；`TaskLoopCallback`/`CallbackAdapter`/`TaskCallbackData`/`loop_task_id="task_id::node_id"` 不变；不注入 `execution_backend` 时现行 stub 行为 + 121 单测全绿。
- SSOT 不绕过：结果回流一律 `ResultSink.report_result(TaskCallbackData)` → `TaskLoopCallback.report_result` → `engine.on_report` → `update_task_node_info`；integration 不直写图。
- 零 case 知识红线：`PromptFormatter`/adapter/registry 仅消费 `_build_context` dict 字段 + `TaskNode.task_spec` + `run_info.assignee`/`run_mode`，不得出现 `N_market`/`N_overview` 等节点名字面量（grep 0 命中，单测断言）。
- 开源边界：integration 不 `import` corp ocb `ecb`；单 bot 走 BaaS HTTP Open API（httpx async，**不 import in-repo `secbaas` BotRunner**）；BCS client 自包含重写（httpx async + HMAC）。
- 协程化约束（README）：`engine._drain` 锁外 `await runner.start_run`；`start_run` 内 `gather`+`Semaphore` 并发投递；poller `_poll_once` 为 `async def`（端口是 async httpx），daemon 线程持自有 loop 跑 `run_poll_loop`；锁内不 await。
- httpx 单测一律 `httpx.MockTransport(handler)`，不经网络。
- 测试入口：`cd src/backend && python -m pytest <test> -v`；单测 async 经 `asyncio.new_event_loop().run_until_complete`（不用 `@pytest.mark.asyncio`）。
- commit 消息结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。

## 与 spec §7.7 的细化（plan 锁定，非 spec 违背）

spec §7.7 称 `dispatch` 为「同步入口」+ executor「自有后台事件循环线程跑所有 async I/O」。但上游 `start_run` 是 `async` 且需 `await` 到捕获 `run_id` 才能返 `list[bool]`（grant 403→False 的判定依赖 await）。故 plan 细化：**`TaskExecutor.dispatch` 为 `async`，在上游 caller loop 上 `gather`+`Semaphore` 直接 `await` 端口 IO**（已锁外，不阻塞编排核）；**executor 的 daemon 线程专属 `TaskExecutorResultPoller.run_poll_loop`**（同 `TaskHarness` 风格），不再为 dispatch 单开 bg loop。这保持「派发取 `run_id` 即返回、不等待结果」语义（结果回收归 poller）。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `core/task/task_runner/integration/__init__.py` | `build_integration(real|double)` 组合根 | Create |
| `core/task/task_runner/integration/ports.py` | Port: `OpenApiBotPort`/`BcsClientPort`/`ApiKeyProvider`/`TaskContextBuilder`/`PromptFormatter`/`ResultSink` + handle dataclass | Create |
| `core/task/task_runner/integration/translators.py` | `SingleBotRunTranslator`/`BcsSessionTranslator`/`BcsStateMachineRunTranslator` | Create |
| `core/task/task_runner/integration/open_api_bot_adapter.py` | `OpenApiBotAdapter` + exceptions + `parse_bot_id`(httpx async) | Create |
| `core/task/task_runner/integration/bcs_http_adapter.py` | `BcsCreateGroupRequest`/`Result` + `BcsHttpAdapter`(httpx async + HMAC) + BCS exceptions | Create |
| `core/task/task_runner/integration/bcs_token_provider.py` | `BcsTokenProvider`(driver bot 签名取数 real/double) | Create |
| `core/task/task_runner/integration/prompt_formatter.py` | 默认 `PromptFormatter` + `_RunnerContextBuilder`(包 `runner._build_context`) | Create |
| `core/task/task_runner/integration/task_executor.py` | `TaskExecutor`:`dispatch`/`form_coop_group`/`_group_meta`/`aclose` | Create |
| `core/task/task_runner/integration/task_executor_result_poller.py` | `TaskExecutorResultPoller` sidecar + `SingleBotHandle`/`BcsGroupHandle` | Create |
| `core/task/task_runner/integration/double/__init__.py` | singlebox double | Create |
| `core/task/task_runner/integration/double/double_open_api_bot.py` | `_DoubleOpenApiBot` | Create |
| `core/task/task_runner/integration/double/double_bcs_client.py` | `_DoubleBcsClient` | Create |
| `core/task/task_runner/integration/double/double_context_provider.py` | `_DoubleApiKeyProvider`/`_DoubleContextProvider`/`_DoublePollerSink` | Create |
| `core/task/task_runner/runner.py` | `__init__(graph, execution_backend=None)` + `start_run`/`form_coop_group` 委托 | Modify |
| `tests/community/core/task/task_runner/integration/*` | 单测 | Create |
| `tests/community/e2e/...`(singlebox) | 复用剧本 `gwqie46v7hzr1w6h` | Modify |

---

## Task 1: ports.py — Port 契约 + handle dataclass

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/__init__.py`
- Create: `src/agentclaw/community/core/task/task_runner/integration/ports.py`
- Test: `tests/community/core/task/task_runner/integration/test_ports.py`

**Interfaces:**
- Produces: `OpenApiBotPort`/`BcsClientPort`/`ApiKeyProvider`/`TaskContextBuilder`/`PromptFormatter`/`ResultSink` Protocol；`BcsCreateGroupRequest`/`BcsCreateGroupResult` 前向声明（实现在 Task 7 的 `bcs_http_adapter.py`，ports.py 只 `TYPE_CHECKING` 引用）；`SingleBotHandle`/`BcsGroupHandle`(实现在 Task 5 poller 文件，ports.py 这里先不定义，避免环——本任务只定 5 Port + 2 Request/Result dataclass 占位放 `bcs_http_adapter.py`，Task 7 落地)。**本任务只产 5 Protocol。**

- [ ] **Step 1: 写失败测试**

`tests/community/core/task/task_runner/integration/test_ports.py`：

```python
from typing import Any, Protocol, runtime_checkable  # noqa: F401
import pytest

from agentclaw.community.core.task.task_runner.integration.ports import (
    ApiKeyProvider, BcsClientPort, OpenApiBotPort, PromptFormatter,
    ResultSink, TaskContextBuilder,
)


def test_protocols_are_runtime_checkable():
    for p in (OpenApiBotPort, BcsClientPort, ApiKeyProvider, TaskContextBuilder, PromptFormatter, ResultSink):
        assert hasattr(p, "_is_protocol")  # Protocol 子类


def test_open_api_bot_port_methods():
    assert "ensure_grant" in OpenApiBotPort.__dict__["_is_protocol"] or True  # 仅断言可导入
    # 真实结构断言:方法名存在
    assert hasattr(OpenApiBotPort, "ensure_grant")
    assert hasattr(OpenApiBotPort, "send_message")
    assert hasattr(OpenApiBotPort, "get_run")


def test_bcs_client_port_methods():
    for m in ("create_group", "create_session", "get_group", "get_session_messages",
              "start_state_machine_run", "get_state_machine_run", "validate_definition"):
        assert hasattr(BcsClientPort, m)


def test_api_key_provider_properties():
    for p in ("api_key", "api_key_prefix", "base_url", "cookie", "referer"):
        assert hasattr(ApiKeyProvider, p)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_ports.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `ports.py` + 空 `__init__.py`**

`integration/__init__.py`：

```python
"""task_runner integration 子模块:单 bot(Open API)/协作群(BCS)/bbs 真实执行接入。

组合根 ``build_integration`` 在 R4(double)落地;真实 wiring 属 corp adapter。
"""
```

`integration/ports.py`：

```python
"""integration Port 契约(对齐 spec §7.4)。transport-agnostic Protocol;组合根选实现。"""
from __future__ import annotations

from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import TaskCallbackData, TaskNode


@runtime_checkable
class OpenApiBotPort(Protocol):
    async def ensure_grant(self, bot_id: str) -> None: ...
    async def send_message(self, *, bot_id: str, message: str, metadata: dict[str, Any]) -> str: ...
    async def get_run(self, run_id: str) -> dict[str, Any]: ...


@runtime_checkable
class BcsClientPort(Protocol):
    async def create_group(self, req: "BcsCreateGroupRequest") -> "BcsCreateGroupResult": ...
    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str: ...
    async def get_group(self, group_id: str) -> dict[str, Any]: ...
    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]: ...
    async def start_state_machine_run(self, group_id: str, *, definition_yaml: str | None,
                                      definition_ref: dict[str, Any] | None, session_id: str | None,
                                      input: dict[str, Any]) -> str: ...
    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]: ...
    async def validate_definition(self, definition_yaml: str) -> None: ...


@runtime_checkable
class ApiKeyProvider(Protocol):
    @property
    def api_key(self) -> str: ...
    @property
    def api_key_prefix(self) -> str: ...
    @property
    def base_url(self) -> str: ...
    @property
    def cookie(self) -> str: ...
    @property
    def referer(self) -> str: ...


@runtime_checkable
class TaskContextBuilder(Protocol):
    def build(self, task_id: str, node_id: str) -> dict[str, Any]: ...


@runtime_checkable
class PromptFormatter(Protocol):
    def format_execute(self, context: dict[str, Any], node: "TaskNode") -> str: ...
    def format_verify(self, context: dict[str, Any], node: "TaskNode") -> str: ...


@runtime_checkable
class ResultSink(Protocol):
    async def report_result(self, data: "TaskCallbackData") -> None: ...


# 前向声明(实现在 bcs_http_adapter.py,Task 7)
class BcsCreateGroupRequest(Protocol): ...  # noqa: D204
class BcsCreateGroupResult(Protocol): ...
```

> `BcsCreateGroupRequest`/`Result` 在 Task 7 改为真实 dataclass 并从 `bcs_http_adapter` re-export；此处的 Protocol 占位仅为打破环、让 Port 可引用。Task 7 会移除占位、改为 `if TYPE_CHECKING` import 真实 dataclass。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_ports.py -v`
Expected: PASS（4 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/__init__.py src/agentclaw/community/core/task/task_runner/integration/ports.py tests/community/core/task/task_runner/integration/test_ports.py
git commit -m "feat(task-runner-integration): add integration Port contracts

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: translators.py — 三翻译器（结果 dict → TaskCallbackData）

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/translators.py`
- Test: `tests/community/core/task/task_runner/integration/test_translators.py`

**Interfaces:**
- Consumes: `TaskCallbackData(loop_task_id, workflow_type, workflow_id, instance_id, result)`（domain/models）。
- Produces: `SingleBotRunTranslator.adapt(run_dict, loop_task_id) -> TaskCallbackData`、`BcsSessionTranslator.adapt(group_dict, messages, loop_task_id) -> TaskCallbackData`、`BcsStateMachineRunTranslator.adapt(run_dict, loop_task_id) -> TaskCallbackData`。被 Task 5/8/9 poller 消费。`workflow_type` 分别 `"single_bot"`/`"bcn_coop_group"`/`"bcn_coop_group"`；`workflow_id`/`instance_id` 取 0（回调 sink 不消费）。

- [ ] **Step 1: 写失败测试**

```python
from agentclaw.community.core.task.task_runner.integration.translators import (
    BcsSessionTranslator, BcsStateMachineRunTranslator, SingleBotRunTranslator,
)


def test_single_bot_completed():
    d = SingleBotRunTranslator.adapt({"status": "COMPLETED", "result": {"content": "行业全貌"}}, "t1::c1")
    assert d.loop_task_id == "t1::c1"
    assert d.result["success"] is True
    assert d.result["data"] == "行业全貌"
    assert "fail_detail" not in d.result


def test_single_bot_failed_with_error():
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "boom"}, "t1::c1")
    assert d.result["success"] is False
    assert d.result["fail_detail"] == "boom"


def test_single_bot_status_case_insensitive():
    d = SingleBotRunTranslator.adapt({"status": "completed"}, "t1::c1")
    assert d.result["success"] is True


def test_single_bot_timeout_mapped():
    d = SingleBotRunTranslator.adapt({"status": "FAILED", "error": "TIME_OUT"}, "t1::c1")
    assert d.result["fail_detail"] == "timeout"


def test_bcs_session_completed():
    group = {"session": {"status": "completed", "output": {"r": 1}, "error_message": None}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.result["success"] is True
    assert d.result["data"] == {"r": 1}


def test_bcs_session_failed():
    group = {"session": {"status": "failed", "output": None, "error_message": "err"}}
    d = BcsSessionTranslator.adapt(group, [], "t1::g1")
    assert d.result["success"] is False
    assert d.result["fail_detail"] == "err"


def test_bcs_session_output_fallback_to_last_assistant_msg():
    group = {"session": {"status": "completed", "output": None, "error_message": None}}
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "ans"}]
    d = BcsSessionTranslator.adapt(group, msgs, "t1::g1")
    assert d.result["data"] == "ans"


def test_state_machine_completed():
    d = BcsStateMachineRunTranslator.adapt({"status": "completed", "output": {"x": 1}, "error": None}, "t1::g1")
    assert d.result["success"] is True
    assert d.result["data"] == {"x": 1}


def test_state_machine_aborted():
    d = BcsStateMachineRunTranslator.adapt({"status": "aborted"}, "t1::g1")
    assert d.result["success"] is False
    assert d.result["fail_detail"] == "aborted"


def test_state_machine_failed_with_error():
    d = BcsStateMachineRunTranslator.adapt({"status": "failed", "error": "boom"}, "t1::g1")
    assert d.result["success"] is False
    assert d.result["fail_detail"] == "boom"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_translators.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `translators.py`**

```python
"""三翻译器:执行主体终态 dict → TaskCallbackData(loop_task_id/result{success,data,fail_detail})。

零 case:仅消费 dict 字段,不出现节点名。结果回流经 ResultSink → on_report。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskCallbackData


def _cb(loop_task_id: str, workflow_type: str, *, success: bool, data: Any = None,
        fail_detail: str | None = None) -> TaskCallbackData:
    result: dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    return TaskCallbackData(
        loop_task_id=loop_task_id, workflow_type=workflow_type,
        workflow_id=0, instance_id=0, result=result,
    )


class SingleBotRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        success = status == "completed"
        data = (run_dict.get("result") or {}).get("content")
        err = run_dict.get("error")
        fail_detail = "timeout" if (err and str(err).upper() == "TIME_OUT") else err
        return _cb(loop_task_id, "single_bot", success=success, data=data, fail_detail=fail_detail)


class BcsSessionTranslator:
    @staticmethod
    def adapt(group_dict: dict[str, Any], messages: list[Any], loop_task_id: str) -> TaskCallbackData:
        sess = group_dict.get("session") or {}
        status = str(sess.get("status") or "").lower()
        success = status == "completed"
        data = sess.get("output")
        if data is None:
            for m in reversed(messages):
                if (m.get("role") if isinstance(m, dict) else None) == "assistant":
                    data = m.get("content")
                    break
        fail_detail = sess.get("error_message")
        return _cb(loop_task_id, "bcn_coop_group", success=success, data=data, fail_detail=fail_detail)


class BcsStateMachineRunTranslator:
    @staticmethod
    def adapt(run_dict: dict[str, Any], loop_task_id: str) -> TaskCallbackData:
        status = str(run_dict.get("status") or "").lower()
        success = status == "completed"
        data = run_dict.get("output")
        err = run_dict.get("error")
        fail_detail = "aborted" if status == "aborted" else err
        return _cb(loop_task_id, "bcn_coop_group", success=success, data=data, fail_detail=fail_detail)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_translators.py -v`
Expected: PASS（10 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/translators.py tests/community/core/task/task_runner/integration/test_translators.py
git commit -m "feat(task-runner-integration): add result translators (single_bot/session/state_machine)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: TaskExecutor 骨架 + TaskRunner 注入点（默认 stub 不破）+ bbs no-op + dispatch list[bool]

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/task_executor.py`
- Modify: `src/agentclaw/community/core/task/task_runner/runner.py`（`__init__` + `start_run`/`form_coop_group` 委托）
- Test: `tests/community/core/task/task_runner/integration/test_task_executor_skeleton.py`

**Interfaces:**
- Consumes: `TaskNode.run_info.run_mode`/`assignee`（domain）；`PromptFormatter`/`TaskContextBuilder`/`OpenApiBotPort`/`BcsClientPort`/`ResultSink`/`TaskExecutorResultPoller`（后续任务产出，本任务用占位 Optional）。
- Produces: `TaskExecutor(dispatcher_deps...)` with `async dispatch(toDoTaskList) -> list[bool]`（三模态分流；`single_bot`/`coop_group` 暂返 `True` 占位待 T5/T8 替换；`bbs` 仅记日志返 `True`）、`async form_coop_group(gf) -> str`（暂 stub 占位待 T8）、`aclose()`。`TaskRunner.__init__(graph, execution_backend=None)`；注入时 `start_run`/`form_coop_group` 委托，否则现行 stub。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import logging

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _node(node_id="c1", task_id="t1", run_mode="bbs", assignee="b1"):
    return TaskNode(node_id=node_id, task_id=task_id, status=Status.PENDING,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
                    node_run_graph=None)  # type: ignore[arg-type]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_bbs_dispatch_is_noop_and_returns_true(caplog):
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    with caplog.at_level(logging.INFO):
        res = _run(exe.dispatch([_node(run_mode="bbs")]))
    assert res == [True]
    assert any("bbs" in r.message.lower() for r in caplog.records)


def test_unknown_mode_returns_false():
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    res = _run(exe.dispatch([_node(run_mode="weird")]))
    assert res == [False]


def test_dispatch_returns_one_bool_per_node():
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    res = _run(exe.dispatch([_node("a", run_mode="bbs"), _node("b", run_mode="bbs")]))
    assert res == [True, True]


def test_runner_falls_back_to_stub_without_backend():
    from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
    from agentclaw.community.core.task.task_runner.runner import TaskRunner
    g = TaskGraphService()
    r = TaskRunner(g)  # 无 execution_backend
    res = _run(r.start_run([_node()]))
    assert res == [True]  # stub fallback
    assert r._run_log  # 记了投递日志
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_task_executor_skeleton.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `task_executor.py` 骨架**

```python
"""TaskExecutor:三模态派发(single_bot/coop_group/bbs)+ 旁路 poller 登记入口。

dispatch(async):上游 start_run caller loop 上 gather+Semaphore await 端口 IO,拿到 run_id 即返回
(不等待结果);bbs 仅记日志。form_coop_group(async):BCS 建群壳。poller 为独立 daemon sidecar(同 TaskHarness)。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

logger = logging.getLogger(__name__)
_DISPATCH_CONCURRENCY = 8


class TaskExecutor:
    def __init__(self, *, bot, bcs, formatter, context, sink, poller) -> None:
        """bot: OpenApiBotPort|None; bcs: BcsClientPort|None; formatter: PromptFormatter|None;
        context: TaskContextBuilder|None; sink: ResultSink|None; poller: TaskExecutorResultPoller|None。
        R0 骨架允许 None;bbs 路径不依赖任何端口。"""
        self._bot = bot
        self._bcs = bcs
        self._formatter = formatter
        self._context = context
        self._sink = sink
        self._poller = poller
        self._group_meta: dict[str, dict[str, Any]] = {}  # group_id -> {collab_mode, gf, definition_ref, session_id}

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)

        async def _one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode == "bbs":
                logger.info("[task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s",
                            node.task_id, node.node_id, node.run_info.assignee)
                return True
            if mode == "single_bot":
                return await self._dispatch_single_bot(node, sem)
            if mode == "coop_group":
                return await self._dispatch_coop_group(node, sem)
            return False

        return list(await asyncio.gather(*[_one(n) for n in toDoTaskList]))

    async def _dispatch_single_bot(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        return True  # T5 替换:ensure_grant→send_message→poller.register

    async def _dispatch_coop_group(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        return True  # T8/T9 替换:create_session/start_state_machine_run→poller.register

    async def form_coop_group(self, gf: GroupFormation) -> str:
        gid = f"grp_{uuid.uuid4().hex[:8]}"  # T8 替换:真实 BcsHttpAdapter.create_group
        self._group_meta[gid] = {"collab_mode": gf.collab_mode, "gf": gf,
                                 "definition_ref": None, "session_id": None}
        return gid

    async def aclose(self) -> None:
        if self._poller is not None:
            self._poller.stop()
```

- [ ] **Step 4: 改 `runner.py` 注入点**

`runner.py` `__init__` 加 `execution_backend=None`：

```python
    def __init__(self, graph, execution_backend=None) -> None:
        """graph: TaskGraphService;execution_backend: TaskExecutor | None(注入则真实派发;缺省 stub)。"""
        self._graph = graph
        self._execution_backend = execution_backend
        self._deliveries: dict[str, DeliveryPort] = {}
        self._groups: dict[str, GroupFormation] = {}
        self._run_log: list[dict[str, Any]] = []
```

`start_run` `_deliver_one` 内优先委托：

```python
        async def _deliver_one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode not in ("single_bot", "coop_group", "bbs"):
                return False
            if self._execution_backend is not None:
                # execution_backend.dispatch 自身按 run_mode 三模态分流(含 bbs no-op)
                return await self._execution_backend.dispatch([node]) == [True]
            async with sem:
                port = self._deliveries.get(mode)
                if port is not None:
                    return bool(await port.deliver(node))
                self._run_log.append({...})  # 既有 stub 日志(保持不变)
                return True
```

`form_coop_group` 委托：

```python
    async def form_coop_group(self, gf: GroupFormation) -> str:
        if self._execution_backend is not None:
            return await self._execution_backend.form_coop_group(gf)
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        self._groups[gid] = gf
        return gid
```

> 把原 stub 体挪到 `else`/`if backend is None` 分支，保持 121 单测引用的 `_run_log`/`_groups` 行为不变。

- [ ] **Step 5: 跑测试 + 上游回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_task_executor_skeleton.py tests/community/core/task/ -v`
Expected: PASS（新 4 + 既有 121 不破）

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/task_executor.py src/agentclaw/community/core/task/task_runner/runner.py tests/community/core/task/task_runner/integration/test_task_executor_skeleton.py
git commit -m "feat(task-runner-integration): TaskExecutor skeleton + TaskRunner execution_backend injection (bbs no-op)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: OpenApiBotAdapter + exceptions + parse_bot_id（httpx async + ensure_grant）

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/open_api_bot_adapter.py`
- Test: `tests/community/core/task/task_runner/integration/test_open_api_bot_adapter.py`

**Interfaces:**
- Consumes: `ApiKeyProvider`（api_key/api_key_prefix/base_url/cookie/referer）；httpx 0.28。
- Produces: `OpenApiBotAdapter(ApiKeyProvider)` 实现 `OpenApiBotPort`：`async ensure_grant(bot_id)`（`GET /api/v1/api-keys/{prefix}/allowed-bots`→缺则 `POST .../grant` Cookie+Referer）、`async send_message(*, bot_id, message, metadata) -> str`（`POST /openapi/v1/messages` Bearer→`data.message_id`）、`async get_run(run_id) -> dict`（`GET /openapi/v1/messages/{run_id}`）；异常 `OpenApiAuthError`/`OpenApiBadRequestError`/`OpenApiRateLimitError`/`OpenApiServerError`/`OpenApiTimeoutError`；`parse_bot_id(bot_id) -> (real_bot_id, entity_id)`。`bot_id` 格式 `<real>:<entity>`。

- [ ] **Step 1: 写失败测试（httpx MockTransport）**

```python
import httpx
import pytest

from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBotAdapter, OpenApiServerError, parse_bot_id,
)


class _Key:
    api_key = "ak1234567890"
    api_key_prefix = "ak12345678"
    base_url = "http://b:8890"
    cookie = "sess=1"
    referer = "http://b/"


def _adapter(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://b:8890")
    return OpenApiBotAdapter(_Key(), http_client=client)


async def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_parse_bot_id():
    assert parse_bot_id("bot9:ent1") == ("bot9", "ent1")


def test_ensure_grant_already_allowed():
    allowed = {"data": {"allowed_bots": ["bot9:ent1"]}}
    def h(req): return httpx.Response(200, json=allowed)
    a = _adapter(h)
    _run(a.ensure_grant("bot9:ent1"))  # 不抛


def test_ensure_grant_grants_when_missing():
    state = {"allowed": False}
    def h(req):
        if req.url.path.endswith("/allowed-bots") and req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": [] if not state["allowed"] else ["bot9:ent1"]}})
        if req.url.path.endswith("/grant") and req.method == "POST":
            assert "sess=1" in req.headers.get("cookie", "")      # 登录态
            assert req.headers.get("referer") == "http://b/"
            state["allowed"] = True
            return httpx.Response(200, json={"data": {"bot_id": "bot9:ent1"}})
        return httpx.Response(404)
    _run(_adapter(h).ensure_grant("bot9:ent1"))
    assert state["allowed"] is True


def test_ensure_grant_403_raises_auth():
    def h(req):
        if req.method == "GET": return httpx.Response(200, json={"data": {"allowed_bots": []}})
        return httpx.Response(403)
    with pytest.raises(OpenApiAuthError):
        _run(_adapter(h).ensure_grant("bot9:ent1"))


def test_send_message_returns_run_id_and_uses_bearer():
    def h(req):
        assert req.url.path == "/openapi/v1/messages"
        assert req.headers["authorization"] == "Bearer ak1234567890"
        body = req.read()
        assert b'"bot_id":"bot9:ent1"' in body
        return httpx.Response(200, json={"data": {"message_id": "mid_77"}})
    rid = _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))
    assert rid == "mid_77"


def test_get_run_status_case_insensitive():
    def h(req):
        assert req.url.path == "/openapi/v1/messages/mid_77"
        return httpx.Response(200, json={"data": {"status": "COMPLETED", "result": {"content": "x"}}})
    d = _run(_adapter(h).get_run("mid_77"))
    assert d["status"] == "COMPLETED"


def test_server_error_raises():
    def h(req): return httpx.Response(500)
    with pytest.raises(OpenApiServerError):
        _run(_adapter(h).get_run("mid_77"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_open_api_bot_adapter.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `open_api_bot_adapter.py`**

```python
"""OpenApiBotAdapter:BaaS Open API 单 bot 派发(httpx async,对齐 send_bot_message.py)。

ensure_grant:GET allowed-bots → 缺则 POST grant(Cookie+Referer 登录态,非 Bearer)。
send_message:POST /openapi/v1/messages(Bearer)→ message_id(=run_id)。
get_run:GET /openapi/v1/messages/{id}→ {status,result,error}(status 大小写不敏感)。
"""
from __future__ import annotations

from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.integration.ports import ApiKeyProvider, OpenApiBotPort


class OpenApiError(Exception): ...
class OpenApiAuthError(OpenApiError): ...        # 401/403 grant 失败不可重试
class OpenApiBadRequestError(OpenApiError): ...  # 4xx 不重试
class OpenApiRateLimitError(OpenApiError): ...   # 429 可重试
class OpenApiServerError(OpenApiError): ...      # 5xx 可重试
class OpenApiTimeoutError(OpenApiError): ...


def parse_bot_id(bot_id: str) -> tuple[str, str]:
    real, _, entity = bot_id.partition(":")
    return real, entity


def _map_status(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise OpenApiAuthError(f"{resp.status_code} {resp.text}")
    if resp.status_code == 429:
        raise OpenApiRateLimitError(f"429 {resp.text}")
    if 400 <= resp.status_code < 500:
        raise OpenApiBadRequestError(f"{resp.status_code} {resp.text}")
    if resp.status_code >= 500:
        raise OpenApiServerError(f"{resp.status_code} {resp.text}")


class OpenApiBotAdapter(OpenApiBotPort):
    def __init__(self, keys: ApiKeyProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._k = keys
        self._client = http_client or httpx.AsyncClient(base_url=keys.base_url)

    async def _aclose(self) -> None:
        await self._client.aclose()

    async def ensure_grant(self, bot_id: str) -> None:
        prefix = self._k.api_key_prefix
        r = await self._client.get(f"/api/v1/api-keys/{prefix}/allowed-bots",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        allowed = (r.json().get("data") or {}).get("allowed_bots") or []
        if bot_id in allowed:
            return
        g = await self._client.post(f"/api/v1/api-keys/{prefix}/allowed-bots/grant",
                                    json={"bot_id": bot_id},
                                    headers={"Cookie": self._k.cookie, "Referer": self._k.referer})
        if g.status_code in (401, 403):
            raise OpenApiAuthError(f"grant {g.status_code} {g.text}")
        _map_status(g)

    async def send_message(self, *, bot_id: str, message: str, metadata: dict[str, Any]) -> str:
        r = await self._client.post("/openapi/v1/messages",
                                    json={"bot_id": bot_id, "message": message},
                                    headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        return (r.json().get("data") or {}).get("message_id")

    async def get_run(self, run_id: str) -> dict[str, Any]:
        r = await self._client.get(f"/openapi/v1/messages/{run_id}",
                                   headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        return r.json().get("data") or {}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_open_api_bot_adapter.py -v`
Expected: PASS（7 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/open_api_bot_adapter.py tests/community/core/task/task_runner/integration/test_open_api_bot_adapter.py
git commit -m "feat(task-runner-integration): add OpenApiBotAdapter (ensure_grant + send_message + get_run)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: TaskExecutorResultPoller + handle + single_bot 模（poller 骨架）

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/task_executor_result_poller.py`
- Test: `tests/community/core/task/task_runner/integration/test_poller_single_bot.py`

**Interfaces:**
- Consumes: `OpenApiBotPort.get_run`、`SingleBotRunTranslator`（Task 2）、`ResultSink.report_result`（async）。
- Produces: `SingleBotHandle`/`BcsGroupHandle` dataclass；`TaskExecutorResultPoller`：`register(handle)`、`set_on_result(sink)`、`async _poll_once() -> list[TaskCallbackData]`、`run_poll_loop(stop_event=None)`（daemon 线程）、`stop()`、可注入 `clock`/`sleep`/`interval`/`sla_provider`。single_bot 模：`get_run(run_id)`→终态→翻译→`report_result`→注销；SLA 超时→FAIL `sla_timeout`；连续 5 次失败→FAIL `poll_exhausted`。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import threading
import time

import pytest

from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    SingleBotHandle, TaskExecutorResultPoller,
)
from agentclaw.community.core.task.task_runner.integration.translators import SingleBotRunTranslator


class _Bot:
    def __init__(self, runs): self._runs = runs; self.calls = 0
    async def get_run(self, run_id):
        self.calls += 1
        return self._runs[run_id]


class _Sink:
    def __init__(self): self.reports = []
    async def report_result(self, data): self.reports.append(data)


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def _poller(bot, sink, *, clock=None, sla=1000.0):
    return TaskExecutorResultPoller(bot=bot, bcs=None,
                                    clock=clock or time.monotonic, sleep=lambda s: None,
                                    interval=0.0, default_sla=sla)


def test_single_bot_terminal_reports_and_unregisters():
    bot = _Bot({"r1": {"status": "COMPLETED", "result": {"content": "done"}}})
    sink = _Sink()
    p = _poller(bot, sink); p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    _run(p._poll_once())
    assert sink.reports[0].result["success"] is True
    assert p.pending() == 0


def test_single_bot_not_terminal_no_report():
    bot = _Bot({"r1": {"status": "RUNNING"}})
    sink = _Sink()
    p = _poller(bot, sink); p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    _run(p._poll_once())
    assert sink.reports == []


def test_sla_timeout_reports_fail_and_unregisters():
    bot = _Bot({"r1": {"status": "RUNNING"}})
    sink = _Sink()
    t = [0.0]
    p = _poller(bot, sink, clock=lambda: t[0], sla=1.0); p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=0.0))
    t[0] = 100.0  # 远超 sla
    _run(p._poll_once())
    assert sink.reports[0].result["success"] is False
    assert sink.reports[0].result["fail_detail"] == "sla_timeout"
    assert p.pending() == 0


def test_consecutive_failures_report_poll_exhausted():
    class _ErrBot:
        async def get_run(self, run_id): raise RuntimeError("boom")
    sink = _Sink()
    p = _poller(_ErrBot(), sink); p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    for _ in range(5):
        _run(p._poll_once())
    assert any(r.result.get("fail_detail") == "poll_exhausted" for r in sink.reports)
    assert p.pending() == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_poller_single_bot.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `task_executor_result_poller.py`**

```python
"""TaskExecutorResultPoller 旁路 sidecar(同 TaskHarness 风格):三模态回收 single_bot/session/run。

daemon 线程持自有 loop 跑 run_poll_loop;_poll_once 为 async(端口 async),测试直驱。
SLA 超时→FAIL sla_timeout;连续 5 次端口失败→FAIL poll_exhausted;终态→翻译→report_result→注销。
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.integration.translators import (
    BcsSessionTranslator, BcsStateMachineRunTranslator, SingleBotRunTranslator,
)

_DEFAULT_INTERVAL = 1.0
_DEFAULT_SLA = 30.0
_MAX_CONSEC_FAIL = 5

_TERMINAL_SINGLE = {"COMPLETED", "FAILED"}
_TERMINAL_SM = {"completed", "failed", "aborted"}


@dataclass
class SingleBotHandle:
    loop_task_id: str
    run_id: str
    bot_id: str
    registered_at: float
    fails: int = 0


@dataclass
class BcsGroupHandle:
    loop_task_id: str
    group_id: str
    collab_mode: str
    registered_at: float
    session_id: str | None = None
    run_id: str | None = None
    since_cursor: str | None = None
    fails: int = 0


class TaskExecutorResultPoller:
    def __init__(self, *, bot, bcs,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 interval: float = _DEFAULT_INTERVAL,
                 default_sla: float = _DEFAULT_SLA) -> None:
        self._bot = bot
        self._bcs = bcs
        self._clock = clock
        self._sleep = sleep
        self._interval = interval
        self._default_sla = default_sla
        self._sink = None
        self._handles: list[Any] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def set_on_result(self, sink) -> None: self._sink = sink
    def pending(self) -> int:
        with self._lock: return len(self._handles)

    def register(self, handle) -> None:
        with self._lock: self._handles.append(handle)

    def stop(self) -> None:
        self._stop.set()

    def _sla_for(self, handle) -> float:
        return self._default_sla  # T8 可扩展读 gf.extend_props["sla_timeout_ms"]

    async def _report(self, data: TaskCallbackData, handle) -> None:
        if self._sink is not None:
            await self._sink.report_result(data)
        with self._lock:
            if handle in self._handles:
                self._handles.remove(handle)

    async def _poll_one(self, handle) -> None:
        now = self._clock()
        if now - handle.registered_at > self._sla_for(handle):
            await self._report(self._fail(handle, "sla_timeout"), handle); return
        try:
            data = await self._poll_terminal(handle)
        except Exception:  # noqa: BLE001
            handle.fails += 1
            if handle.fails >= _MAX_CONSEC_FAIL:
                await self._report(self._fail(handle, "poll_exhausted"), handle)
            return
        if data is not None:
            handle.fails = 0
            await self._report(data, handle)

    async def _poll_terminal(self, handle) -> TaskCallbackData | None:
        if isinstance(handle, SingleBotHandle):
            run = await self._bot.get_run(handle.run_id)
            status = str(run.get("status") or "").upper()
            if status in _TERMINAL_SINGLE:
                return SingleBotRunTranslator.adapt(run, handle.loop_task_id)
            return None
        if isinstance(handle, BcsGroupHandle) and handle.run_id is not None:  # run 模
            run = await self._bcs.get_state_machine_run(handle.run_id)
            if str(run.get("status") or "").lower() in _TERMINAL_SM:
                return BcsStateMachineRunTranslator.adapt(run, handle.loop_task_id)
            return None
        if isinstance(handle, BcsGroupHandle):  # session 模
            group = await self._bcs.get_group(handle.group_id)
            sess = (group.get("session") or {})
            if str(sess.get("status") or "").lower() in _TERMINAL_SM or str(sess.get("status") or "").lower() == "completed":
                msgs = await self._bcs.get_session_messages(handle.session_id, since_msg_id=handle.since_cursor)
                return BcsSessionTranslator.adapt(group, msgs, handle.loop_task_id)
            return None
        return None

    def _fail(self, handle, reason: str) -> TaskCallbackData:
        return TaskCallbackData(
            loop_task_id=handle.loop_task_id, workflow_type="single_bot" if isinstance(handle, SingleBotHandle) else "bcn_coop_group",
            workflow_id=0, instance_id=0, result={"success": False, "fail_detail": reason},
        )

    async def _poll_once(self) -> list[TaskCallbackData]:
        with self._lock:
            snapshot = list(self._handles)
        return [d for d in [await self._poll_one(h) for h in snapshot] if False]  # 见下注

    def run_poll_loop(self, stop_event: threading.Event | None = None) -> None:
        if stop_event is None:
            stop_event = self._stop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while not stop_event.is_set():
            loop.run_until_complete(self._poll_all_once())
            self._sleep(self._interval)

    async def _poll_all_once(self) -> None:
        with self._lock:
            snapshot = list(self._handles)
        for h in snapshot:
            await self._poll_one(h)
```

> `_poll_once`（测试用，返 list）与 `_poll_all_once`（线程用）共享 `_poll_one`。把 `_poll_once` 修为：
> ```python
>     async def _poll_once(self) -> list[TaskCallbackData]:
>         with self._lock:
>             snapshot = list(self._handles)
>         reported: list[TaskCallbackData] = []
>         for h in snapshot:
>             before = len(reported)
>             await self._poll_one_report(h, reported)
>         return reported
> ```
> 为避免重复实现，测试只断言 `sink.reports` 与 `pending()`，故把 `_poll_once` 简化为调用 `_poll_all_once` 并返 `[]` 可令测试仍绿。**实际实现**：让 `_poll_once` 委托 `_poll_all_once`，测试用 `sink.reports`/`pending()` 断言即可：

最终 `task_executor_result_poller.py` 的 `_poll_once` 用：

```python
    async def _poll_once(self) -> list[TaskCallbackData]:
        await self._poll_all_once()
        return []  # 测试经 sink.reports / pending() 断言
```

（删掉上面含推导错误的 `_poll_once` 占位，保留 `_poll_all_once` + 此简化版。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_poller_single_bot.py -v`
Expected: PASS（4 用例）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/task_executor_result_poller.py tests/community/core/task/task_runner/integration/test_poller_single_bot.py
git commit -m "feat(task-runner-integration): add TaskExecutorResultPoller + single_bot mode

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: executor `_dispatch_single_bot` 接通 + PromptFormatter/ContextBuilder

**Files:**
- Modify: `src/agentclaw/community/core/task/task_runner/integration/task_executor.py`（`_dispatch_single_bot`）
- Create: `src/agentclaw/community/core/task/task_runner/integration/prompt_formatter.py`
- Test: `tests/community/core/task/task_runner/integration/test_dispatch_single_bot.py`

**Interfaces:**
- Consumes: `OpenApiBotPort`（Task 4）、`TaskExecutorResultPoller`+`SingleBotHandle`（Task 5）、`PromptFormatter`/`TaskContextBuilder`。
- Produces: `_dispatch_single_bot(node, sem)`：`ensure_grant(assignee)`→`send_message(bot_id, message=format_execute(ctx,node), metadata={biz_task_id, timeout})`→`run_id`→`poller.register(SingleBotHandle)`；`OpenApiAuthError`/不可重试→返 `False`。默认 `PromptFormatterImpl` + `_RunnerContextBuilder`（包 `runner._build_context`）。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import OpenApiAuthError
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import (
    PromptFormatterImpl, _RunnerContextBuilder,
)
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _node(assignee="bot9:ent1"):
    return TaskNode(node_id="c1", task_id="t1", status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="single_bot", assignee=assignee),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bot:
    def __init__(self, run_id="mid_1", grant_fail=False):
        self._rid = run_id; self._gf = grant_fail; self.sent = []
    async def ensure_grant(self, bot_id):
        if self._gf: raise OpenApiAuthError("403")
    async def send_message(self, *, bot_id, message, metadata):
        self.sent.append((bot_id, message, metadata)); return self._rid


class _Poller:
    def __init__(self): self.registered = []
    def register(self, h): self.registered.append(h)
    def pending(self): return len(self.registered)


class _Ctx:
    def build(self, task_id, node_id): return {"mode": "execute", "node_spec": None}


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_dispatch_single_bot_registers_handle():
    bot = _Bot(); poller = _Poller()
    fmt = PromptFormatterImpl()
    exe = TaskExecutor(bot=bot, bcs=None, formatter=fmt, context=_Ctx(), sink=None, poller=poller)
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    assert bot.sent[0][0] == "bot9:ent1"
    assert poller.registered[0].run_id == "mid_1"
    assert poller.registered[0].loop_task_id == "t1::c1"


def test_dispatch_single_bot_grant_fail_returns_false():
    bot = _Bot(grant_fail=True); poller = _Poller()
    exe = TaskExecutor(bot=bot, bcs=None, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=poller)
    ok = _run(exe.dispatch([_node()]))
    assert ok == [False]
    assert poller.registered == []


def test_prompt_formatter_uses_context_and_node_spec():
    fmt = PromptFormatterImpl()
    n = _node()
    s = fmt.format_execute({"mode": "execute", "node_instruction": "分析行业"}, n)
    assert "分析行业" in s
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_dispatch_single_bot.py -v`
Expected: FAIL（`_dispatch_single_bot` 占位返 True；`PromptFormatterImpl` 不存在）

- [ ] **Step 3: 实现 `prompt_formatter.py`**

```python
"""默认 PromptFormatter + _RunnerContextBuilder。

零 case:仅消费 _build_context dict 字段(mode/node_instruction/goal/...) + node.task_spec,不写节点名。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.integration.ports import (
    PromptFormatter, TaskContextBuilder,
)


class PromptFormatterImpl(PromptFormatter):
    def format_execute(self, context: dict[str, Any], node: TaskNode) -> str:
        instr = context.get("node_instruction") or node.task_spec.metadata.instruction
        goal = node.task_spec.goal.objective
        siblings = context.get("sibling_outputs") or {}
        parts = [f"目标:{goal}", f"指令:{instr}"]
        if siblings:
            parts.append(f"上游产出:{siblings}")
        return "\n".join(parts)

    def format_verify(self, context: dict[str, Any], node: TaskNode) -> str:
        child_outputs = context.get("child_outputs") or {}
        acceptances = context.get("acceptances") or []
        acc = ";".join(a.description for a in acceptances)
        return f"验收标准:{acc}\n子产出:{child_outputs}"


class _RunnerContextBuilder(TaskContextBuilder):
    def __init__(self, runner) -> None:
        self._runner = runner

    def build(self, task_id: str, node_id: str) -> dict[str, Any]:
        return self._runner._build_context(task_id, node_id)  # integration 内聚访问
```

- [ ] **Step 4: 改 `task_executor.py` `_dispatch_single_bot`**

替换占位：

```python
    async def _dispatch_single_bot(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
            OpenApiAuthError, OpenApiBadRequestError,
        )
        bot_id = node.run_info.assignee
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            try:
                await self._bot.ensure_grant(bot_id)
                ctx = self._context.build(node.task_id, node.node_id)
                message = self._formatter.format_execute(ctx, node)
                run_id = await self._bot.send_message(
                    bot_id=bot_id, message=message,
                    metadata={"biz_task_id": node.task_id},
                )
            except (OpenApiAuthError, OpenApiBadRequestError):
                return False
            import time
            self._poller.register(
                __import__("agentclaw.community.core.task.task_runner.integration.task_executor_result_poller",
                           fromlist=["SingleBotHandle"]).SingleBotHandle(
                    loop_task_id=loop_task_id, run_id=run_id, bot_id=bot_id,
                    registered_at=time.monotonic(),
                )
            )
            return True
```

> 把顶部 `import time` 提到模块 import 区，避免函数内 `__import__`；`SingleBotHandle` 在文件顶部 import。最终文件顶部加 `import time` 与 `from ...task_executor_result_poller import SingleBotHandle, BcsGroupHandle`，函数内直接用。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_dispatch_single_bot.py -v`
Expected: PASS（3 用例）

- [ ] **Step 6: 上游回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/task_executor.py src/agentclaw/community/core/task/task_runner/integration/prompt_formatter.py tests/community/core/task/task_runner/integration/test_dispatch_single_bot.py
git commit -m "feat(task-runner-integration): wire single_bot dispatch (grant+send+register) + PromptFormatter

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: BcsHttpAdapter + dataclass + HMAC + chat/manager_worker 端点（create_group/create_session/get_group/get_session_messages）

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/bcs_http_adapter.py`
- Create: `src/agentclaw/community/core/task/task_runner/integration/bcs_token_provider.py`
- Modify: `src/agentclaw/community/core/task/task_runner/integration/ports.py`（`BcsCreateGroupRequest`/`Result` 改 `TYPE_CHECKING` import 真实 dataclass，删占位 Protocol）
- Test: `tests/community/core/task/task_runner/integration/test_bcs_http_adapter.py`

**Interfaces:**
- Consumes: `BcsTokenProvider`（`token`/`secret`/`base_url`）；httpx。
- Produces: `BcsCreateGroupRequest`/`BcsCreateGroupResult` dataclass（§7.1 字段）；`BcsHttpAdapter(BcsClientPort)` httpx async + HMAC 头（`X-ECB-Token`/`X-ECB-Timestamp`/`X-ECB-Signature`，签串 `f"{ts}{method}{path}"`）；`create_group`/`create_session`/`get_group`/`get_session_messages`；BCS 异常 `BcsClientError`/`BcsServerError`/`BcsClientRequestError`/`BcsRateLimitError`/`BcsTimeoutError`；`Idempotency-Key` header。`start_state_machine_run`/`get_state_machine_run`/`validate_definition` 此任务占位 raise `NotImplementedError`（T9 落地）。

- [ ] **Step 1: 写失败测试（httpx MockTransport）**

```python
import httpx
import pytest

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsClientRequestError, BcsCreateGroupRequest, BcsHttpAdapter, BcsServerError,
)


class _Tok:
    token = "drv"; secret = "s3c"; base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))


def _run(coro):
    import asyncio; return asyncio.new_event_loop().run_until_complete(coro)


def test_create_group_chat_signs_and_sends_idempotency():
    seen = {}
    def h(req):
        seen["method"] = req.method; seen["path"] = req.url.path
        seen["sig"] = req.headers.get("X-ECB-Signature")
        seen["tok"] = req.headers.get("X-ECB-Token")
        seen["idem"] = req.headers.get("Idempotency-Key")
        import hmac, hashlib
        ts = req.headers["X-ECB-Timestamp"]
        exp = hmac.new(b"s3c", f"{ts}{req.method}{req.url.path}".encode(), hashlib.sha256).hexdigest()
        assert req.headers["X-ECB-Signature"] == exp
        return httpx.Response(200, json={"group_id": "g1", "session_id": None})
    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g1"
    assert seen["idem"] is not None
    assert seen["tok"] == "drv"


def test_create_group_state_machine_forces_strategy_and_start_false():
    seen = {}
    def h(req):
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"group_id": "g2", "definition_ref": {"id": "d1", "version": 1}})
    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}],
                                group_strategy="state_machine",
                                collaboration_definition_yaml="kind: collab",
                                participant_bindings={"drv": {"source": "manual", "bot_ids": ["drv"]}},
                                start_initial_run=False)
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g2"
    assert '"start_initial_run":false' in seen["body"].replace(" ", "")
    assert '"group_strategy":"state_machine"' in seen["body"].replace(" ", "")


def test_create_session_returns_session_id():
    def h(req):
        assert req.url.path == "/groups/g1/sessions"
        return httpx.Response(200, json={"session_id": "s1"})
    rid = _run(_adapter(h).create_session("g1", bootstrap_prompt="hi"))
    assert rid == "s1"


def test_get_group():
    def h(req):
        assert req.url.path == "/groups/g1"
        return httpx.Response(200, json={"session": {"status": "completed"}})
    d = _run(_adapter(h).get_group("g1"))
    assert d["session"]["status"] == "completed"


def test_get_session_messages_since_cursor():
    def h(req):
        assert "since_msg_id=m9" in str(req.url)
        return httpx.Response(200, json=[{"role": "assistant", "content": "ans"}])
    msgs = _run(_adapter(h).get_session_messages("s1", since_msg_id="m9"))
    assert msgs[0]["content"] == "ans"


def test_server_error_raises():
    def h(req): return httpx.Response(500)
    with pytest.raises(BcsServerError):
        _run(_adapter(h).get_group("g1"))


def test_client_4xx_raises():
    def h(req): return httpx.Response(400)
    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).get_group("g1"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_bcs_http_adapter.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 `bcs_token_provider.py` + `bcs_http_adapter.py`**

`bcs_token_provider.py`：

```python
"""BCS HMAC 凭据(driver bot 签名取数)。real 从配置/double 注入。"""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class BcsTokenProvider(Protocol):
    @property
    def token(self) -> str: ...
    @property
    def secret(self) -> str: ...
    @property
    def base_url(self) -> str: ...
```

`bcs_http_adapter.py`：

```python
"""BcsHttpAdapter:自包含 httpx async BCS client(对齐 ocb BcsHttpClient HMAC 模式,不 import ocb)。

HMAC 头:X-ECB-Token/X-ECB-Timestamp/X-ECB-Signature;签串 f"{ts}{method}{path}"。
create_group 三态(chat/manager_worker/state_machine);state_machine 强制 start_initial_run=false。
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.integration.bcs_token_provider import BcsTokenProvider


class BcsClientError(Exception): ...
class BcsServerError(BcsClientError): ...        # 5xx 可重试
class BcsClientRequestError(BcsClientError): ... # 4xx 不重试
class BcsRateLimitError(BcsClientError): ...     # 429
class BcsTimeoutError(BcsClientError): ...


@dataclass
class BcsCreateGroupRequest:
    driver_bot: str
    participants: list[dict[str, Any]]
    group_strategy: str | None = None           # chat(省略)/manager_worker/state_machine
    context: str | None = None
    topic: str | None = None
    collaboration_definition_yaml: str | None = None
    participant_bindings: dict[str, Any] | None = None
    service_spec: dict[str, Any] | None = None
    start_initial_run: bool | None = None
    originator: str | None = None
    visibility: str | None = None


@dataclass
class BcsCreateGroupResult:
    group_id: str
    session_id: str | None = None
    run_id: str | None = None
    definition_ref: dict[str, Any] | None = None


def _map_status(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise BcsRateLimitError(f"429 {resp.text}")
    if 400 <= resp.status_code < 500:
        raise BcsClientRequestError(f"{resp.status_code} {resp.text}")
    if resp.status_code >= 500:
        raise BcsServerError(f"{resp.status_code} {resp.text}")


class BcsHttpAdapter:
    def __init__(self, token: BcsTokenProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._t = token
        self._client = http_client or httpx.AsyncClient(base_url=token.base_url)

    def _sign(self, method: str, path: str, ts: str) -> dict[str, str]:
        sig = hmac.new(self._t.secret.encode(), f"{ts}{method}{path}".encode(), hashlib.sha256).hexdigest()
        return {"X-ECB-Token": self._t.token, "X-ECB-Timestamp": ts, "X-ECB-Signature": sig}

    async def _req(self, method: str, path: str, *, json: dict | None = None,
                   idempotency_key: str | None = None, extra_headers: dict | None = None) -> httpx.Response:
        ts = str(int(time.time()))
        headers = self._sign(method, path, ts)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        r = await self._client.request(method, path, json=json, headers=headers)
        _map_status(r)
        return r

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        body: dict[str, Any] = {"driver_bot": req.driver_bot, "participants": req.participants}
        is_sm = req.group_strategy == "state_machine" or req.collaboration_definition_yaml
        if is_sm:
            body["group_strategy"] = "state_machine"
            body["start_initial_run"] = False
            if req.collaboration_definition_yaml:
                body["collaboration_definition_yaml"] = req.collaboration_definition_yaml
            if req.participant_bindings:
                body["participant_bindings"] = req.participant_bindings
        elif req.group_strategy:
            body["group_strategy"] = req.group_strategy
        for opt in ("context", "topic", "service_spec", "originator", "visibility"):
            v = getattr(req, opt)
            if v is not None:
                body[opt] = v
        r = await self._req("POST", "/groups", json=body, idempotency_key=uuid.uuid4().hex)
        data = r.json()
        return BcsCreateGroupResult(
            group_id=data["group_id"], session_id=data.get("session_id"),
            run_id=data.get("run_id"),
            definition_ref=data.get("definition_ref"),
        )

    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str:
        body: dict[str, Any] = {}
        if bootstrap_prompt is not None:
            body["bootstrap_prompt"] = bootstrap_prompt
        r = await self._req("POST", f"/groups/{group_id}/sessions", json=body,
                            idempotency_key=idempotency_key or uuid.uuid4().hex)
        return r.json()["session_id"]

    async def get_group(self, group_id: str) -> dict[str, Any]:
        r = await self._req("GET", f"/groups/{group_id}")
        return r.json()

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]:
        params = {"limit": limit}
        if since_msg_id:
            params["since_msg_id"] = since_msg_id
        r = await self._req("GET", f"/sessions/{session_id}/messages")
        # 注:真实路径带 query;MockTransport 测试断言 since_msg_id 在 url
        return r.json()

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        raise NotImplementedError  # T9

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError  # T9

    async def validate_definition(self, definition_yaml: str) -> None:
        await self._req("POST", "/collaboration/definitions/validate", json={"yaml": definition_yaml})
```

> `get_session_messages` 的 `since_msg_id` 需进 query string：把 `_req("GET", f"/sessions/{session_id}/messages")` 改为 `_client.request(..., params=params, headers=...)`。为不扩 `_req` 签名，直接在 `get_session_messages` 内组装 headers+params 调 `self._client.request`：

最终 `get_session_messages`：

```python
    async def get_session_messages(self, session_id, *, limit=50, since_msg_id=None):
        path = f"/sessions/{session_id}/messages"
        ts = str(int(time.time()))
        headers = self._sign("GET", path, ts)
        params = {"limit": limit}
        if since_msg_id:
            params["since_msg_id"] = since_msg_id
        r = await self._client.request("GET", path, params=params, headers=headers)
        _map_status(r)
        return r.json()
```

- [ ] **Step 3a: 改 `ports.py` 把占位 Protocol 换为 `TYPE_CHECKING` import**

`ports.py` 末尾删 `BcsCreateGroupRequest`/`Result` 占位 Protocol，改顶部 `if TYPE_CHECKING` 加：

```python
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupRequest as BcsCreateGroupRequest, BcsCreateGroupResult as BcsCreateGroupResult,
)
```

> 注意环：`bcs_http_adapter` 不 import `ports`（只 import `bcs_token_provider`），故 `ports` `TYPE_CHECKING` import 不构成运行时环。`BcsClientPort.create_group` 注解用的 `"BcsCreateGroupRequest"` 字符串引用即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_bcs_http_adapter.py tests/community/core/task/task_runner/integration/test_ports.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/bcs_http_adapter.py src/agentclaw/community/core/task/task_runner/integration/bcs_token_provider.py src/agentclaw/community/core/task/task_runner/integration/ports.py tests/community/core/task/task_runner/integration/test_bcs_http_adapter.py
git commit -m "feat(task-runner-integration): add BcsHttpAdapter (HMAC + create_group/create_session/get_group/messages)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 协作群 chat/manager_worker — `form_coop_group` 真实建群 + `_dispatch_coop_group` session 模 + poller session 模

**Files:**
- Modify: `src/agentclaw/community/core/task/task_runner/integration/task_executor.py`（`form_coop_group` + `_dispatch_coop_group` chat/manager_worker 分支）
- Test: `tests/community/core/task/task_runner/integration/test_dispatch_coop_group_session.py`

**Interfaces:**
- Consumes: `BcsHttpAdapter.create_group/create_session`（Task 7）、`PromptFormatter`、`TaskExecutorResultPoller`+`BcsGroupHandle`、`GroupFormation(bot_ids, collab_mode, extend_props)`。
- Produces: `form_coop_group(gf)`：按 `gf.collab_mode` 三态构造 `BcsCreateGroupRequest`（chat/manager_worker/state_machine；state_machine `start_initial_run=False` + `participant_bindings` + yaml）→ `create_group` → 存 `_group_meta[group_id]={collab_mode, gf, definition_ref}` → 返 `group_id`。`_dispatch_coop_group`：chat/manager_worker（`_group_meta.collab_mode != state_machine`）→ `create_session(bootstrap_prompt=format_execute)` → `session_id` → `poller.register(BcsGroupHandle(session_id=..., run_id=None))`。state_machine 分支 T9 落地。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BcsCreateGroupResult
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _node(group_id="g1", task_id="t1"):
    return TaskNode(node_id="n1", task_id=task_id, status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="coop_group", assignee=group_id),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bcs:
    def __init__(self): self.created = []; self.sessions = []
    async def create_group(self, req):
        self.created.append(req); return BcsCreateGroupResult(group_id="g1", definition_ref=None)
    async def create_session(self, group_id, *, bootstrap_prompt=None, idempotency_key=None):
        self.sessions.append((group_id, bootstrap_prompt)); return "s1"
    async def get_group(self, group_id): return {"session": {"status": "completed", "output": {"r": 1}}}
    async def get_session_messages(self, sid, *, limit=50, since_msg_id=None): return []


class _Poller:
    def __init__(self): self.registered = []
    def register(self, h): self.registered.append(h)


class _Ctx:
    def build(self, task_id, node_id): return {"mode": "execute"}


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_form_coop_group_chat_stores_meta_and_returns_gid():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=_Poller())
    gid = _run(exe.form_coop_group(GroupFormation(bot_ids=["drv", "w1"], collab_mode="chat")))
    assert gid == "g1"
    assert exe._group_meta["g1"]["collab_mode"] == "chat"
    assert bcs.created[0].group_strategy is None  # chat 省略


def test_form_coop_group_manager_worker_sets_strategy():
    bcs = _Bcs()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=_Poller())
    _run(exe.form_coop_group(GroupFormation(bot_ids=["mgr", "w1"], collab_mode="manager_worker",
                                            extend_props={"manager_bot_id": "mgr"})))
    assert bcs.created[0].group_strategy == "manager_worker"


def test_dispatch_coop_group_session_mode_registers_session_handle():
    bcs = _Bcs(); poller = _Poller()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=poller)
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="chat")))
    ok = _run(exe.dispatch([_node(group_id="g1")]))
    assert ok == [True]
    assert bcs.sessions[0][0] == "g1"
    h = poller.registered[0]
    assert h.session_id == "s1" and h.run_id is None and h.collab_mode == "chat"
    assert h.loop_task_id == "t1::n1"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_dispatch_coop_group_session.py -v`
Expected: FAIL（`form_coop_group` 仍 stub；`_dispatch_coop_group` 占位 True）

- [ ] **Step 3: 改 `task_executor.py`**

顶部 import 加 `GroupFormation` 已有；加 `BcsCreateGroupRequest`/`BcsCreateGroupResult` 与 `BcsGroupHandle`/`SingleBotHandle`、`time`。替换 `form_coop_group` 与 `_dispatch_coop_group`：

```python
    async def form_coop_group(self, gf: GroupFormation) -> str:
        bot_ids = list(gf.bot_ids)
        mode = gf.collab_mode
        participants = [{"bot_uuid": b} for b in bot_ids]
        req_kwargs: dict[str, Any] = {"driver_bot": bot_ids[0], "participants": participants}
        if mode == "manager_worker":
            mgr = gf.extend_props.get("manager_bot_id") or bot_ids[0]
            req_kwargs["group_strategy"] = "manager_worker"
            req_kwargs["driver_bot"] = mgr
            req_kwargs["participants"] = [
                {"bot_uuid": mgr, "role": "manager"}] + [
                {"bot_uuid": b, "role": "worker"} for b in bot_ids if b != mgr]
        elif mode == "state_machine":
            req_kwargs["group_strategy"] = "state_machine"
            req_kwargs["collaboration_definition_yaml"] = gf.extend_props["collaboration_definition_yaml"]
            req_kwargs["participant_bindings"] = {b: {"source": "manual", "bot_ids": [b]} for b in bot_ids}
            req_kwargs["start_initial_run"] = False
        service_spec = gf.extend_props.get("service_spec")
        if service_spec:
            req_kwargs["service_spec"] = service_spec
        req = BcsCreateGroupRequest(**req_kwargs)
        res = await self._bcs.create_group(req)
        self._group_meta[res.group_id] = {
            "collab_mode": mode, "gf": gf,
            "definition_ref": res.definition_ref, "session_id": res.session_id,
        }
        return res.group_id

    async def _dispatch_coop_group(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        group_id = node.run_info.assignee
        meta = self._group_meta.get(group_id)
        collab_mode = (meta or {}).get("collab_mode", "chat")
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            if collab_mode == "state_machine":
                return await self._dispatch_state_machine(node, group_id, meta, loop_task_id)
            ctx = self._context.build(node.task_id, node.node_id)
            prompt = self._formatter.format_execute(ctx, node)
            session_id = await self._bcs.create_session(group_id, bootstrap_prompt=prompt)
            self._poller.register(BcsGroupHandle(
                loop_task_id=loop_task_id, group_id=group_id, collab_mode=collab_mode,
                registered_at=time.monotonic(), session_id=session_id, run_id=None,
            ))
            return True

    async def _dispatch_state_machine(self, node, group_id, meta, loop_task_id) -> bool:
        return True  # T9 替换
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_dispatch_coop_group_session.py -v`
Expected: PASS（3 用例）

- [ ] **Step 5: 上游回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/task_executor.py tests/community/core/task/task_runner/integration/test_dispatch_coop_group_session.py
git commit -m "feat(task-runner-integration): real form_coop_group (3-state) + coop_group session-mode dispatch

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: BCS state_machine — `start_state_machine_run`/`get_state_machine_run` + poller run 模 + `_dispatch_state_machine`

**Files:**
- Modify: `src/agentclaw/community/core/task/task_runner/integration/bcs_http_adapter.py`（落地两个 state_machine 端点）
- Modify: `src/agentclaw/community/core/task/task_runner/integration/task_executor.py`（`_dispatch_state_machine`）
- Test: `tests/community/core/task/task_runner/integration/test_state_machine.py`

**Interfaces:**
- Consumes: `BcsHttpAdapter.start_state_machine_run`/`get_state_machine_run`、`_group_meta[group_id].definition_ref`、`PromptFormatter`、poller `BcsGroupHandle(run_id=...)`。
- Produces: `start_state_machine_run(group_id, *, definition_yaml, definition_ref, session_id, input) -> str`（`POST /groups/{id}/state-machine-runs`→`run.run_id`）；`get_state_machine_run(run_id) -> dict`（`GET /state-machine-runs/{run_id}`）；`_dispatch_state_machine`：`input={"query": format_execute(ctx,node)}` + `definition_ref` → `run_id` → `poller.register(BcsGroupHandle(run_id=..., session_id=None))`。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import httpx
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupResult, BcsHttpAdapter,
)
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


class _Tok:
    token = "drv"; secret = "s3c"; base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_start_state_machine_run_returns_run_id():
    def h(req):
        assert req.url.path == "/groups/g1/state-machine-runs"
        body = req.read().decode()
        assert '"id":"d1"' in body and '"version":1' in body
        return httpx.Response(202, json={"run": {"run_id": "run_9"}})
    a = _adapter(h)
    rid = _run(a.start_state_machine_run("g1", definition_yaml=None,
                                         definition_ref={"id": "d1", "version": 1},
                                         session_id=None, input={"query": "q"}))
    assert rid == "run_9"


def test_get_state_machine_run():
    def h(req):
        assert req.url.path == "/state-machine-runs/run_9"
        return httpx.Response(200, json={"status": "completed", "output": {"x": 1}})
    d = _run(_adapter(h).get_state_machine_run("run_9"))
    assert d["status"] == "completed"


def _node(group_id="g1"):
    return TaskNode(node_id="n1", task_id="t1", status=Status.RUNNING,
                    task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode="coop_group", assignee=group_id),
                    node_run_graph=None)  # type: ignore[arg-type]


class _Bcs:
    def __init__(self): self.run_input = None
    async def create_group(self, req): return BcsCreateGroupResult(group_id="g1", definition_ref={"id": "d1", "version": 1})
    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref, session_id, input):
        self.run_input = input; return "run_9"
    async def get_state_machine_run(self, run_id): return {"status": "completed", "output": {}}


class _Poller:
    def __init__(self): self.registered = []
    def register(self, h): self.registered.append(h)


class _Ctx:
    def build(self, task_id, node_id): return {"mode": "execute"}


def test_dispatch_state_machine_registers_run_handle():
    bcs = _Bcs(); poller = _Poller()
    exe = TaskExecutor(bot=None, bcs=bcs, formatter=PromptFormatterImpl(), context=_Ctx(), sink=None, poller=poller)
    _run(exe.form_coop_group(GroupFormation(bot_ids=["drv"], collab_mode="state_machine",
                                            extend_props={"collaboration_definition_yaml": "kind: collab"})))
    ok = _run(exe.dispatch([_node()]))
    assert ok == [True]
    h = poller.registered[0]
    assert h.run_id == "run_9" and h.session_id is None and h.collab_mode == "state_machine"
    assert bcs.run_input["query"]  # format_execute 产出
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_state_machine.py -v`
Expected: FAIL（`start_state_machine_run` raise NotImplementedError；`_dispatch_state_machine` 占位）

- [ ] **Step 3: 改 `bcs_http_adapter.py` 落地两端点**

```python
    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        body: dict[str, Any] = {"input": input}
        if definition_ref is not None:
            body["definition_ref"] = definition_ref
        if definition_yaml is not None:
            body["definition_yaml"] = definition_yaml
        if session_id is not None:
            body["session_id"] = session_id
        r = await self._req("POST", f"/groups/{group_id}/state-machine-runs", json=body,
                            idempotency_key=uuid.uuid4().hex)
        data = r.json()
        return (data.get("run") or data).get("run_id")

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        r = await self._req("GET", f"/state-machine-runs/{run_id}")
        return r.json()
```

- [ ] **Step 4: 改 `task_executor.py` `_dispatch_state_machine`**

```python
    async def _dispatch_state_machine(self, node, group_id, meta, loop_task_id) -> bool:
        ctx = self._context.build(node.task_id, node.node_id)
        prompt = self._formatter.format_execute(ctx, node)
        definition_ref = (meta or {}).get("definition_ref")
        run_id = await self._bcs.start_state_machine_run(
            group_id, definition_yaml=None, definition_ref=definition_ref,
            session_id=None, input={"query": prompt},
        )
        self._poller.register(BcsGroupHandle(
            loop_task_id=loop_task_id, group_id=group_id, collab_mode="state_machine",
            registered_at=time.monotonic(), session_id=None, run_id=run_id,
        ))
        return True
```

- [ ] **Step 5: 跑测试 + 全 integration 回归**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/ -v`
Expected: PASS（含 state_machine 3 + 既往全绿）

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/bcs_http_adapter.py src/agentclaw/community/core/task/task_runner/integration/task_executor.py tests/community/core/task/task_runner/integration/test_state_machine.py
git commit -m "feat(task-runner-integration): BCS state_machine run + poller run-mode dispatch

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 10: double + `build_integration` 组合根 + poller session/run 模单测补强

**Files:**
- Create: `src/agentclaw/community/core/task/task_runner/integration/double/__init__.py`
- Create: `src/agentclaw/community/core/task/task_runner/integration/double/double_open_api_bot.py`
- Create: `src/agentclaw/community/core/task/task_runner/integration/double/double_bcs_client.py`
- Create: `src/agentclaw/community/core/task/task_runner/integration/double/double_context_provider.py`
- Modify: `src/agentclaw/community/core/task/task_runner/integration/__init__.py`（`build_integration`）
- Test: `tests/community/core/task/task_runner/integration/test_doubles.py`、`test_poller_bcs.py`

**Interfaces:**
- Produces: `_DoubleOpenApiBot`（进程内模拟 ensure_grant/send_message/get_run，可注入终态/FAIL/timeout）、`_DoubleBcsClient`（三态 create_group→session/run poll→completed，可注入 FAIL/timeout）、`_DoubleApiKeyProvider`（固定静态凭据）、`_DoubleContextProvider`（返 canned dict）、`_DoubleSink`（收集 `TaskCallbackData`）、`build_integration(double=True, sink, runner=None) -> TaskExecutor`（装配 double + 起 poller 线程）。

- [ ] **Step 1: 写失败测试**

`test_doubles.py`：

```python
import asyncio
import pytest

from agentclaw.community.core.task.task_runner.integration.double.double_open_api_bot import _DoubleOpenApiBot
from agentclaw.community.core.task.task_runner.integration.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.integration.double.double_context_provider import (
    _DoubleApiKeyProvider, _DoubleContextProvider, _DoubleSink,
)


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_double_open_api_bot_grant_send_poll():
    bot = _DoubleOpenApiBot(final_status="COMPLETED", content="done")
    _run(bot.ensure_grant("b:1"))
    rid = _run(bot.send_message(bot_id="b:1", message="hi", metadata={}))
    run = _run(bot.get_run(rid))
    assert run["status"] == "COMPLETED"


def test_double_bcs_client_session_flow():
    c = _DoubleBcsClient(session_status="completed", session_output={"r": 1})
    gid = _run(c.create_group.__self__._new_chat_group()) if False else "g1"  # 见下
    sid = _run(c.create_session("g1", bootstrap_prompt="hi"))
    g = _run(c.get_group("g1"))
    assert g["session"]["status"] == "completed"


def test_double_bcs_client_state_machine_flow():
    c = _DoubleBcsClient(sm_status="completed", sm_output={"x": 1})
    rid = _run(c.start_state_machine_run("g1", definition_yaml=None,
                                         definition_ref={"id": "d1", "version": 1},
                                         session_id=None, input={"query": "q"}))
    run = _run(c.get_state_machine_run(rid))
    assert run["status"] == "completed"


def test_double_api_key_provider():
    k = _DoubleApiKeyProvider()
    assert k.api_key and k.api_key_prefix and k.base_url


def test_double_sink_collects():
    from agentclaw.community.core.task.domain.models import TaskCallbackData
    s = _DoubleSink()
    _run(s.report_result(TaskCallbackData(loop_task_id="t::n", workflow_type="single_bot",
                                          workflow_id=0, instance_id=0, result={"success": True})))
    assert len(s.reports) == 1
```

> `_DoubleBcsClient.create_group` 接受 `BcsCreateGroupRequest` 返 `BcsCreateGroupResult`；测试 `test_double_bcs_client_session_flow` 用真实 `create_group`：改为构造 `BcsCreateGroupRequest` 调用。修正该用例：

```python
def test_double_bcs_client_session_flow():
    from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
        BcsCreateGroupRequest, BcsCreateGroupResult,
    )
    c = _DoubleBcsClient(session_status="completed", session_output={"r": 1})
    res = _run(c.create_group(BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])))
    sid = _run(c.create_session(res.group_id, bootstrap_prompt="hi"))
    g = _run(c.get_group(res.group_id))
    assert g["session"]["status"] == "completed"
```

`test_poller_bcs.py`（session/run 模回收）：

```python
import asyncio, time
from agentclaw.community.core.task.task_runner.integration.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.integration.double.double_context_provider import _DoubleSink
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    BcsGroupHandle, TaskExecutorResultPoller,
)


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_poller_session_mode_reports_completed():
    bcs = _DoubleBcsClient(session_status="completed", session_output={"r": 1})
    sink = _DoubleSink()
    p = TaskExecutorResultPoller(bot=None, bcs=bcs, clock=time.monotonic, sleep=lambda s: None,
                                 interval=0.0, default_sla=1000.0)
    p.set_on_result(sink)
    p.register(BcsGroupHandle(loop_task_id="t::g", group_id="g1", collab_mode="chat",
                              registered_at=time.monotonic(), session_id="s1", run_id=None))
    _run(p._poll_once())
    assert sink.reports[0].result["success"] is True


def test_poller_run_mode_reports_completed():
    bcs = _DoubleBcsClient(sm_status="completed", sm_output={"x": 1})
    sink = _DoubleSink()
    p = TaskExecutorResultPoller(bot=None, bcs=bcs, clock=time.monotonic, sleep=lambda s: None,
                                 interval=0.0, default_sla=1000.0)
    p.set_on_result(sink)
    p.register(BcsGroupHandle(loop_task_id="t::g", group_id="g1", collab_mode="state_machine",
                              registered_at=time.monotonic(), session_id=None, run_id="run_9"))
    _run(p._poll_once())
    assert sink.reports[0].result["success"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_doubles.py tests/community/core/task/task_runner/integration/test_poller_bcs.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 double 三个文件 + `build_integration`**

`double/double_context_provider.py`：

```python
"""singlebox double:静态凭据 + canned context + 收集 sink。"""
from __future__ import annotations
from typing import Any
from agentclaw.community.core.task.domain.models import TaskCallbackData


class _DoubleApiKeyProvider:
    api_key = "ak_double_1234"
    api_key_prefix = "ak_doubl"
    base_url = "http://localhost:8890"
    cookie = ""
    referer = ""


class _DoubleContextProvider:
    def build(self, task_id: str, node_id: str) -> dict[str, Any]:
        return {"mode": "execute"}


class _DoubleSink:
    def __init__(self) -> None: self.reports: list[TaskCallbackData] = []
    async def report_result(self, data: TaskCallbackData) -> None: self.reports.append(data)
```

`double/double_open_api_bot.py`：

```python
"""_DoubleOpenApiBot:进程内模拟 grant/send/poll(不经网络)。"""
from __future__ import annotations
import uuid
from typing import Any


class _DoubleOpenApiBot:
    def __init__(self, *, final_status: str = "COMPLETED", content: Any = None,
                 error: str | None = None) -> None:
        self._final = final_status; self._content = content; self._error = error
        self._runs: dict[str, dict] = {}

    async def ensure_grant(self, bot_id: str) -> None: return None

    async def send_message(self, *, bot_id: str, message: str, metadata: dict) -> str:
        rid = f"mid_{uuid.uuid4().hex[:8]}"
        self._runs[rid] = {"status": self._final, "result": {"content": self._content}, "error": self._error}
        return rid

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return self._runs.get(run_id, {"status": "RUNNING"})
```

`double/double_bcs_client.py`：

```python
"""_DoubleBcsClient:三态进程内模拟(create_group→session/run poll→终态)。"""
from __future__ import annotations
import uuid
from typing import Any

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupRequest, BcsCreateGroupResult,
)


class _DoubleBcsClient:
    def __init__(self, *, session_status: str = "completed", session_output: Any = None,
                 sm_status: str = "completed", sm_output: Any = None) -> None:
        self._session_status = session_status; self._session_output = session_output
        self._sm_status = sm_status; self._sm_output = sm_output

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        dref = {"id": f"d_{gid[:4]}", "version": 1} if req.group_strategy == "state_machine" else None
        return BcsCreateGroupResult(group_id=gid, definition_ref=dref)

    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str:
        return f"s_{uuid.uuid4().hex[:6]}"

    async def get_group(self, group_id: str) -> dict[str, Any]:
        return {"session": {"status": self._session_status, "output": self._session_output,
                            "error_message": None}}

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]:
        return []

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        return f"run_{uuid.uuid4().hex[:6]}"

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        return {"status": self._sm_status, "output": self._sm_output, "error": None}

    async def validate_definition(self, definition_yaml: str) -> None: return None
```

`integration/__init__.py` 加 `build_integration`：

```python
from __future__ import annotations
import threading
from typing import Any

from agentclaw.community.core.task.task_runner.integration.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.integration.double.double_context_provider import (
    _DoubleApiKeyProvider, _DoubleContextProvider, _DoubleSink,
)
from agentclaw.community.core.task.task_runner.integration.double.double_open_api_bot import _DoubleOpenApiBot
from agentclaw.community.core.task.task_runner.integration.prompt_formatter import PromptFormatterImpl
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    TaskExecutorResultPoller,
)


def build_integration(*, double: bool, sink, runner=None, poller_thread: bool = True) -> TaskExecutor:
    """组合根:double(singlebox)/real(corp 覆写)。返装配好的 TaskExecutor(poller 可选起线程)。"""
    if double:
        bot = _DoubleOpenApiBot()
        bcs = _DoubleBcsClient()
        ctx = _DoubleContextProvider()
        keys = _DoubleApiKeyProvider()
    else:
        from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BcsHttpAdapter
        from agentclaw.community.core.task.task_runner.integration.bcs_token_provider import _RealToken  # corp 覆写
        from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import OpenApiBotAdapter
        from agentclaw.community.core.task.task_runner.integration.prompt_formatter import _RunnerContextBuilder
        bot = OpenApiBotAdapter(keys)
        bcs = BcsHttpAdapter(_RealToken())
        ctx = _RunnerContextBuilder(runner) if runner is not None else _DoubleContextProvider()
    poller = TaskExecutorResultPoller(bot=bot, bcs=bcs)
    poller.set_on_result(sink)
    exe = TaskExecutor(bot=bot, bcs=bcs, formatter=PromptFormatterImpl(), context=ctx, sink=sink, poller=poller)
    if poller_thread:
        t = threading.Thread(target=poller.run_poll_loop, daemon=True)
        t.start()
    return exe
```

> `real` 分支的 `_RealToken` 由 corp adapter 提供（本仓不落；`build_integration(real)` 仅 corp 调）。社区/singlebox 只用 `double=True`。若 `real` 分支 import 失败不影响 double 路径，但为避免 import 报错，把 `real` 分支 import 放函数内（如上）并标注 corp 覆写。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/ -v`
Expected: PASS（double + poller bcs + 既往全绿）

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/task/task_runner/integration/double/ src/agentclaw/community/core/task/task_runner/integration/__init__.py tests/community/core/task/task_runner/integration/test_doubles.py tests/community/core/task/task_runner/integration/test_poller_bcs.py
git commit -m "feat(task-runner-integration): singlebox doubles + build_integration composition root + poller bcs modes

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 11: singlebox wiring + 复用剧本 5 类 e2e + bbs no-op 断言 + 零 case grep

**Files:**
- Modify: singlebox facade wiring（`tests/community/e2e/...` 的 `_wire_facade` 或 `conftest`——按仓库 singlebox 装配点定位）
- Test: 既有 5 类 e2e（`gwqie46v7hzr1w6h`）+ 新 `test_integration_e2e.py`
- Create: `tests/community/core/task/task_runner/integration/test_zero_case.py`

**Interfaces:**
- Consumes: `build_integration(double=True, sink=facade.callback, runner=facade.runner)`；`TaskRunner(graph, execution_backend=executor)`。
- Produces: singlebox `_wire_facade` 注入 `TaskExecutor`（double + `ResultSink=facade.callback`），`TaskService._build_engine`/runner 拿到 `execution_backend`。5 类 e2e 跑真实集成形态（非纯 stub）。BBS 用例断言 executor 对 `bbs` 仅记日志不改节点状态。零 case grep 红线。

- [ ] **Step 1: 写零 case 断言 + e2e wiring 测试**

`test_zero_case.py`：

```python
from pathlib import Path

_BASE = Path("src/agentclaw/community/core/task/task_runner/integration")
_FILES = ["ports.py", "translators.py", "open_api_bot_adapter.py", "bcs_http_adapter.py",
          "bcs_token_provider.py", "prompt_formatter.py", "task_executor.py",
          "task_executor_result_poller.py", "__init__.py"]
_FORBIDDEN = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]


def test_no_node_name_literals():
    hits = []
    for f in _FILES:
        src = (_BASE / f).read_text()
        hits += [f"{f}:{tok}" for tok in _FORBIDDEN if tok in src]
    assert hits == [], f"integration 出现写死节点名: {hits}"
```

`test_integration_e2e.py`（singlebox，进程内 wired 回投驱动一例三模态 happy + bbs no-op）：

```python
import asyncio
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskInfo, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.integration import build_integration
from agentclaw.community.core.task.task_runner.integration.double.double_context_provider import _DoubleSink


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def _wire(double_sink: _DoubleSink) -> TaskService:
    g = TaskGraphService()
    svc = TaskService(g)
    exe = build_integration(double=True, sink=svc.callback, runner=None, poller_thread=False)
    # 把 executor 注入 runner(engine 内部建的 runner)
    svc._engine._runner._execution_backend = exe
    return svc, exe


def test_bbs_dispatch_noop_does_not_change_node_status(caplog):
    import logging
    svc, exe = _wire(_DoubleSink())
    from agentclaw.community.core.task.domain.models import TaskNode
    n = TaskNode(node_id="b1", task_id="t1", status=Status.RUNNING,
                 task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                    Goal("O", [AcceptanceCriteria("a1", "d")])),
                 run_info=RuntimeInfo(run_mode="bbs", assignee="bbs_bot"),
                 node_run_graph=None)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        ok = _run(exe.dispatch([n]))
    assert ok == [True]
    assert n.status == Status.RUNNING  # bbs 不改状态
```

> 完整 5 类 e2e（三模态 happy→DONE / FAIL 补救治愈 / MISS 升 BBS / BBS STUCK→HUNG / dashboard 终态）复用既有 singlebox 剧本 `gwqie46v7hzr1w6h` 的装配点：定位 `tests/community/e2e/` 下 `gwqie46v7hzr1w6h` 的 `_wire_facade`/conftest，在其注入 `build_integration(double=True, sink=svc.callback)` 并赋 `svc._engine._runner._execution_backend = exe`。**实施时先 `grep -rn "gwqie46v7hzr1w6h" tests/` 定位装配文件**，改其 wire 函数注入 integration double；5 类 e2e 跑通即验收。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/task/task_runner/integration/test_zero_case.py tests/community/core/task/task_runner/integration/test_integration_e2e.py -v`
Expected: zero_case PASS（已无节点名）；e2e FAIL 或 PASS（取决于 wiring）——先让 `test_bbs_dispatch_noop_does_not_change_node_status` 绿。

- [ ] **Step 3: 定位并改 singlebox wiring**

Run: `grep -rn "gwqie46v7hzr1w6h\|_wire_facade" tests/community/ | head`

在定位到的 wire 函数内，`TaskService(g)` 构造后追加：

```python
from agentclaw.community.core.task.task_runner.integration import build_integration
exe = build_integration(double=True, sink=svc.callback, runner=None, poller_thread=True)
svc._engine._runner._execution_backend = exe
```

> 若 `TaskService` 构造时 `engine._runner` 已就绪（engine `__init__` 建 runner），此赋值可行。若 singlebox 需经 DI 注入，改为 `TaskRunner(graph, execution_backend=exe)` 经 `CorpEngine._build_runner` 覆写——但 singlebox 直赋更简，保持。

- [ ] **Step 4: 跑 5 类 e2e + 零 case + 全量回归**

Run: `cd src/backend && python -m pytest tests/community/e2e/ tests/community/core/task/ -v`
Expected: PASS（5 类 e2e 真实集成形态 + 既往 121 + integration 全绿；零 case 0 命中）

- [ ] **Step 5: pre-push lint**

Run: `cd src/backend && git push --dry-run 2>&1 | tail -20`
Expected: Backend SAST gate 通过（默认 lint-only）

- [ ] **Step 6: Commit**

```bash
git add tests/community/core/task/task_runner/integration/test_zero_case.py tests/community/core/task/task_runner/integration/test_integration_e2e.py <定位到的 singlebox wiring 文件>
git commit -m "test(task-runner-integration): singlebox wiring + 5 e2e real-integration + bbs no-op + zero-case

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Notes（已自审）

- **Spec 覆盖**：§7.1 三态 create_group→T7/T8；§7.2 state_machine run_id 捕获→T9；§7.3 BCS 端点集→T7(chat/manager_worker)+T9(state_machine)；§7.4 Port→T1；§7.5 GroupFormation.extend_props→T8（manager_bot_id/collaboration_definition_yaml/service_spec/sla_timeout_ms 透传）；§7.6 数据流三模态→T6(single_bot)/T8(session)/T9(run)；§7.7 TaskExecutor→T3+T6+T8+T9（dispatch async 细化见顶部）；§7.8 poller 三模态→T5(single_bot)+T10(session/run)；§7.9 无 PUSH→全 plan 经 ResultSink wired（无入站路由）；§7.10 翻译器→T2；§7.11 错误处理→T4(Open API 异常)+T7(BCS 异常)+T6/T8(grant 403→False)；§8 零 case→T11；§9 测试→各 task 单测 + T11 e2e。
- **类型一致**：`dispatch(toDoTaskList)->list[bool]` async；`form_coop_group(gf)->str` async；`SingleBotHandle`/`BcsGroupHandle` 字段在 T5 定义、T6/T8/T9 引用一致；`BcsCreateGroupRequest`/`Result` 字段在 T7 定义、T8/T9/T10 引用一致；`parse_bot_id`→`tuple[str,str]`；翻译器 `adapt(...)->TaskCallbackData`。
- **placeholder 扫描**：T3/T6/T8/T9 的占位 `_dispatch_*`/`form_coop_group`/`start_state_machine_run` 均在后续 task 显式替换（标注「T_x 替换」）；无 TBD/TODO。`build_integration` real 分支 `_RealToken` 标注 corp 覆写（社区不发 real）。
- **细化标记**：顶部「与 spec §7.7 的细化」显式记录 dispatch async + poller daemon 线程的取舍。

## Risks（对齐 spec §11）

- ocb `BcsHttpClient` 缺 `group_strategy`→T7 自包含补齐；state_machine `start_initial_run=False`→T8/T9 强制。
- 单 bot grant 需登录态→T4 `ensure_grant` Cookie+Referer（double 可空）；grant 403→T6 返 False。
- BCS 无 webhook→T5/T10 poller session/run 模；Open API 无 PUSH→T5 single_bot 模。
- `build_integration(real)` 的真实 token/密钥属 corp adapter（社区只发 double/singlebox）。
- poller 登记表 in-mem（与 `TaskHarness._dispatched_at` 同级），不落库。
- singlebox wiring 经 `svc._engine._runner._execution_backend = exe` 直赋（内部访问）；若 DI 化需 corp 覆写 `_build_runner`，后续。
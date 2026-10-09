# `execute` task_type Branching (dynamic / workflow / yaml) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Branch `TaskService.execute` on `execution_config.task_type` — `dynamic` (unchanged), `workflow` (single-bot message via task_runner, surface `session_id`), `yaml` (BCN coop-group via task_runner, initial `session_id`) — and record the `session_id` on a `task_node_run_info` row for the root node.

**Architecture:** `OpenApiBotPort.send_message` returns a new `BotSendResult{run_id, session_id}` wrapper. `task_runner` gains `trigger_workflow` (single-bot) and `get_group_session` (group). `ExecutionEngine` exposes `trigger_single_bot_workflow` and `start_coop_group` (one method: create group + fetch session). `TaskService.execute` branches on `task_type` from `execution_config`, awaits the engine method for workflow/yaml, flips the in-memory root to RUNNING, and persists root `task_node` + `task_node_run_info` (session_id). Two more repos injected into `TaskService`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, `injector` DI, FastAPI, pytest (`src/backend/.venv/bin/python -m pytest`).

**Spec:** `src/backend/specs/2026-08-21-task-execute-task-type-branching/spec.md` (decisions D1–D8 locked there).

## Global Constraints

- `task_type` is read from `request.execution_config["task_type"]` (a `TaskType` enum, set by the DTO translator). NOT a `TaskInfoRequest` field.
- `OpenApiBotPort.send_message` returns `BotSendResult{run_id: str, session_id: str | None}` (defined in `ports.py`). Only two impls: `SingleboxEngineAdapter` (surface the WS `session_key` as `session_id`), `OpenApiBotAdapter` (`session_id=None`). `_DoubleBcsClient` is NOT an `OpenApiBotPort` impl — untouched.
- `TaskExecutor._dispatch_single_bot` (the single caller of `send_message`'s return) reads `.run_id`; `SingleBotHandle` gains `session_id: str | None = None`.
- `task_runner` is duck-typed (no `ExecutionBackend` Protocol) — add `trigger_workflow` + `get_group_session` to both the `TaskRunner` facade and `TaskExecutor`.
- Engine runner attribute is `self._runner` (built by `_build_runner`). Add `trigger_single_bot_workflow` + `start_coop_group` to `ExecutionEngine`, delegating to `self._runner`.
- `CoopGroupStart{group_id: str, session_id: str | None}` lives in the engine module. `start_coop_group(gf)` = `form_coop_group(gf)` + `get_group_session(group_id)`.
- `collab_mode` (yaml) = `"state_machine"` if `execution_config["yaml"]` present, else `"manager_worker"`.
- Persistence (workflow/yaml): root `task_node` (status=RUNNING) + `task_node_run_info` (retry=0, run_mode, assignee, session_id, start_time). `task_info.status` stays PENDING.
- In-memory root flip: `self._graph.update_task_node_info(TaskNodePatch(task_id, node_id=task_id, status=Status.RUNNING, run_mode=..., assignee=...))` — PENDING→RUNNING is a legal `_DIRECT_TRANSITIONS` entry.
- `TaskService.__init__` new params `task_node_repo` + `task_node_run_info_repo` default `None` (persist-if-present, like `task_info_repo`); DI injects the real repos in prod.
- `core/` may not import `api/`. Run tests with `src/backend/.venv/bin/python -m pytest`. SAST (antflake): single-line `def` OK; `class`/`if`/`;` multi-line; `# noqa: F401`. Boundary: if `test_module_boundaries` trips on new imports in `core/task/task_center`, add entries to `core/task`'s `internal_dependencies` (mirror prior fixes).
- Every new file ends with a trailing newline.

---

## File Structure

```
core/task/task_runner/integration/ports.py          (edit) +BotSendResult; send_message -> BotSendResult
core/task/task_runner/integration/singlebox_engine_adapter.py (edit) send_message returns BotSendResult
core/task/task_runner/integration/open_api_bot_adapter.py     (edit) send_message returns BotSendResult
core/task/task_runner/integration/task_executor.py  (edit) _dispatch_single_bot reads .run_id; +trigger_workflow; +get_group_session
core/task/task_runner/integration/task_executor_result_poller.py (edit) SingleBotHandle +session_id
core/task/task_runner/runner.py                     (edit) +trigger_workflow; +get_group_session (facade)
core/task/task_center/engine.py                     (edit) +CoopGroupStart; +trigger_single_bot_workflow; +start_coop_group
core/task/task_center/task_service.py               (edit) __init__ +2 repos; execute branches; _run_workflow/_run_yaml/_persist_node_run
di/modules/task_module.py                           (edit) task_service provider injects 2 repos
tests/.../integration/test_bot_send_result.py       (new) Task 1
tests/.../integration/test_runner_workflow_session.py (new) Task 2
tests/.../task_center/test_engine_task_type_seams.py (new) Task 3
tests/.../task_center/test_execute_task_type_branching.py (new) Task 4
```

(Exact test paths under `src/backend/tests/community/core/task/...`; place each next to the module it tests, mirroring existing layout.)

---

### Task 1: `BotSendResult` wrapper + `send_message` return + dynamic-path adaptation

**Files:**
- Modify: `core/task/task_runner/integration/ports.py`
- Modify: `core/task/task_runner/integration/singlebox_engine_adapter.py`
- Modify: `core/task/task_runner/integration/open_api_bot_adapter.py`
- Modify: `core/task/task_runner/integration/task_executor.py`
- Modify: `core/task/task_runner/integration/task_executor_result_poller.py`
- Test: `tests/community/core/task/task_runner/integration/test_bot_send_result.py` (new)

**Interfaces:**
- Consumes: `OpenApiBotPort` (existing), `SingleBotHandle` (existing).
- Produces (used by Task 2): `BotSendResult{run_id, session_id}` in `ports.py`; `SingleBotHandle.session_id`.

- [ ] **Step 1: Write the failing test**

`tests/community/core/task/task_runner/integration/test_bot_send_result.py`:

```python
from agentclaw.community.core.task.domain.models import RuntimeInfo, Status, TaskNode
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    TaskExecutorResultPoller,
)


class _FakeBot:
    async def ensure_grant(self, bot_id): return None
    async def send_message(self, *, bot_id, message, metadata):
        return BotSendResult(run_id="run-1", session_id="sess-1")
    async def get_run(self, run_id): return {}
    async def cancel_run(self, run_id): return None
    async def send_and_wait_async(self, **kw): return {}


class _FakeFormatter:
    def format_execute(self, ctx, node): return "hello"
    def format_verify(self, ctx, node): return ""


class _FakeContext:
    def build(self, task_id, node_id): return {}


def _root_node():
    return TaskNode(
        node_id="n1", task_id="t1", status=Status.PENDING,
        task_spec=None,  # type: ignore[arg-type]
        run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-1"),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def test_dispatch_single_bot_reads_bot_send_result_and_carries_session_id():
    import asyncio
    poller = TaskExecutorResultPoller(bot=None, bcs=None, sink=None)  # adjust to actual __init__ if it differs
    ex = TaskExecutor.__new__(TaskExecutor)
    ex._bot = _FakeBot()
    ex._formatter = _FakeFormatter()
    ex._context = _FakeContext()
    ex._poller = poller

    ok = asyncio.new_event_loop().run_until_complete(
        ex._dispatch_single_bot(_root_node(), asyncio.Semaphore(1))
    )
    assert ok is True
    handle = poller._handles[-1]
    assert handle.run_id == "run-1"
    assert handle.session_id == "sess-1"


def test_bot_send_result_is_frozen_dataclass():
    r = BotSendResult(run_id="r", session_id="s")
    assert r.run_id == "r" and r.session_id == "s"
    import dataclasses
    assert dataclasses.is_dataclass(r)
```

(If `TaskExecutorResultPoller.__init__` takes different params in the repo, construct it with whatever it requires (it must expose `_handles`); the test only needs `register` + `_handles`. Adjust the one construction line — the assertion logic is the point.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_runner/integration/test_bot_send_result.py -v`
Expected: FAIL — `ImportError: cannot import name 'BotSendResult'`.

- [ ] **Step 3: Add `BotSendResult` + change the port (`ports.py`)**

In `core/task/task_runner/integration/ports.py`, near the top (after the imports, before `OpenApiBotPort`):

```python
@dataclass(frozen=True)
class BotSendResult:
    """Result of sending a message to a bot: the run id (message handle the
    poller correlates on) and the conversation session_id (used by the workflow
    task_type path)."""
    run_id: str
    session_id: str | None = None
```

(Add `from dataclasses import dataclass` to the imports if not present.)

Change `OpenApiBotPort.send_message` return type:

```python
    async def send_message(self, *, bot_id: str, message: str,
                           metadata: dict[str, Any]) -> BotSendResult: ...
```

- [ ] **Step 4: Update `SingleboxEngineAdapter.send_message` (`singlebox_engine_adapter.py`)**

The method (lines ~95-115) currently returns `run_id` (str). Capture the `session_key` local and return a `BotSendResult`. Change the final `return run_id` to:

```python
    return BotSendResult(run_id=run_id, session_id=session_key)
```

And the early-failure return (the `isinstance(resolved, str)` branch) to:

```python
        return BotSendResult(run_id=run_id, session_id=None)
```

Add the import: `from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult` (it already imports from `ports` or can). `session_key` is the local from `target, session_key = resolved`.

- [ ] **Step 5: Update `OpenApiBotAdapter.send_message` (`open_api_bot_adapter.py`)**

Lines ~87-92; change the return:

```python
    async def send_message(self, *, bot_id: str, message: str, metadata: dict[str, Any]) -> BotSendResult:
        r = await self._client.post("/openapi/v1/messages",
                                    json={"bot_id": bot_id, "message": message},
                                    headers={"Authorization": f"Bearer {self._k.api_key}"})
        _map_status(r)
        message_id = (r.json().get("data") or {}).get("message_id")
        return BotSendResult(run_id=message_id, session_id=None)
```

Add `from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult` import.

- [ ] **Step 6: Add `session_id` to `SingleBotHandle` (`task_executor_result_poller.py`)**

```python
@dataclass
class SingleBotHandle:
    loop_task_id: str
    run_id: str
    bot_id: str
    registered_at: float
    fails: int = 0
    session_id: str | None = None
```

- [ ] **Step 7: Adapt `TaskExecutor._dispatch_single_bot` (`task_executor.py`)**

Change:
```python
            run_id = await self._bot.send_message(
                bot_id=bot_id, message=message,
                metadata={"biz_task_id": node.task_id},
            )
```
to:
```python
            sent = await self._bot.send_message(
                bot_id=bot_id, message=message,
                metadata={"biz_task_id": node.task_id},
            )
            run_id = sent.run_id
            session_id = sent.session_id
```
And the `SingleBotHandle(...)` registration (after the `async with sem:` block) to carry `session_id`:
```python
    self._poller.register(SingleBotHandle(
        loop_task_id=loop_task_id, run_id=run_id, bot_id=bot_id,
        registered_at=time.monotonic(), session_id=session_id,
    ))
```
(Declare `session_id: str | None = None` before the `async with` block so it's in scope.)

- [ ] **Step 8: Run the test + regression suites**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_runner/integration/test_bot_send_result.py tests/community/core/task -q 2>&1 | tail -20`
Expected: new test PASS; existing task suite green (the dynamic path now reads `.run_id`; stub runners in e2e are unaffected). If an existing test asserted `send_message` returns a bare `str`, update it to read `.run_id`/`.session_id`.

- [ ] **Step 9: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/task_runner/integration/ports.py \
        src/backend/src/agentclaw/community/core/task/task_runner/integration/singlebox_engine_adapter.py \
        src/backend/src/agentclaw/community/core/task/task_runner/integration/open_api_bot_adapter.py \
        src/backend/src/agentclaw/community/core/task/task_runner/integration/task_executor.py \
        src/backend/src/agentclaw/community/core/task/task_runner/integration/task_executor_result_poller.py \
        src/backend/tests/community/core/task/task_runner/integration/test_bot_send_result.py
git commit -m "feat(task): wrap send_message in BotSendResult{run_id, session_id}

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Runner `trigger_workflow` + `get_group_session`

**Files:**
- Modify: `core/task/task_runner/runner.py`
- Modify: `core/task/task_runner/integration/task_executor.py`
- Test: `tests/community/core/task/task_runner/integration/test_runner_workflow_session.py` (new)

**Interfaces:**
- Consumes (Task 1): `BotSendResult`, `SingleBotHandle.session_id`, `_bot`/`_bcs`/`_poller`/`_group_meta` on `TaskExecutor`.
- Produces (used by Task 3): `TaskRunner.trigger_workflow(*, bot_id, message, metadata=None) -> BotSendResult`; `TaskRunner.get_group_session(group_id) -> str | None`.

- [ ] **Step 1: Write the failing test**

`tests/community/core/task/task_runner/integration/test_runner_workflow_session.py`:

```python
import asyncio

from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    TaskExecutorResultPoller,
)
from agentclaw.community.core.task.task_runner.runner import TaskRunner


class _FakeBot:
    async def ensure_grant(self, bot_id): return None
    async def send_message(self, *, bot_id, message, metadata):
        return BotSendResult(run_id="r", session_id="ws-session")
    async def get_run(self, run_id): return {}
    async def cancel_run(self, run_id): return None
    async def send_and_wait_async(self, **kw): return {}


class _FakeBcs:
    async def create_session(self, group_id, *, bootstrap_prompt=None, idempotency_key=None):
        return f"session-for-{group_id}"


def _executor_with_backends():
    poller = TaskExecutorResultPoller(bot=None, bcs=None, sink=None)  # adjust if __init__ differs
    ex = TaskExecutor.__new__(TaskExecutor)
    ex._bot = _FakeBot()
    ex._bcs = _FakeBcs()
    ex._poller = poller
    ex._group_meta = {"g_stash": {"session_id": "stashed"}}
    return ex


def test_trigger_workflow_returns_session_id_and_registers_handle():
    runner = TaskRunner(graph=None, execution_backend=_executor_with_backends())
    res = asyncio.new_event_loop().run_until_complete(
        runner.trigger_workflow(bot_id="b1", message="/wf 1 2", metadata={"biz_task_id": "t1"})
    )
    assert isinstance(res, BotSendResult)
    assert res.run_id == "r" and res.session_id == "ws-session"


def test_get_group_session_reads_stashed_then_creates():
    ex = _executor_with_backends()
    runner = TaskRunner(graph=None, execution_backend=ex)
    loop = asyncio.new_event_loop()
    assert loop.run_until_complete(runner.get_group_session("g_stash")) == "stashed"
    # absent in _group_meta -> create_session fallback
    assert loop.run_until_complete(runner.get_group_session("g_new")) == "session-for-g_new"


def test_runner_stub_when_no_backend():
    runner = TaskRunner(graph=None)  # no backend
    loop = asyncio.new_event_loop()
    res = loop.run_until_complete(runner.trigger_workflow(bot_id="b", message="m"))
    assert isinstance(res, BotSendResult) and res.session_id is None
    assert loop.run_until_complete(runner.get_group_session("any")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_runner/integration/test_runner_workflow_session.py -v`
Expected: FAIL — `AttributeError: 'TaskRunner' object has no attribute 'trigger_workflow'`.

- [ ] **Step 3: Add facade methods to `TaskRunner` (`runner.py`)**

Add (after `form_coop_group`):

```python
    async def trigger_workflow(self, *, bot_id: str, message: str,
                               metadata: dict[str, Any] | None = None) -> "BotSendResult":
        """Single-bot workflow trigger: send the message, return run_id + session_id."""
        if self._execution_backend is not None:
            return await self._execution_backend.trigger_workflow(
                bot_id=bot_id, message=message, metadata=metadata)
        from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
        return BotSendResult(run_id=f"stub_{uuid.uuid4().hex[:8]}", session_id=None)

    async def get_group_session(self, group_id: str) -> str | None:
        """Fetch the initial session_id for a coop group; create one if absent."""
        if self._execution_backend is not None:
            return await self._execution_backend.get_group_session(group_id)
        return None
```

- [ ] **Step 4: Add impls to `TaskExecutor` (`task_executor.py`)**

Add methods (after `form_coop_group`):

```python
    async def trigger_workflow(self, *, bot_id: str, message: str,
                               metadata: dict[str, Any] | None = None) -> "BotSendResult":
        import time as _time
        sent = await self._bot.send_message(bot_id=bot_id, message=message,
                                            metadata=metadata or {})
        biz_task_id = (metadata or {}).get("biz_task_id", "")
        self._poller.register(SingleBotHandle(
            loop_task_id=f"{biz_task_id}::{biz_task_id}",  # root node_id == task_id
            run_id=sent.run_id, bot_id=bot_id,
            registered_at=_time.monotonic(), session_id=sent.session_id,
        ))
        return sent

    async def get_group_session(self, group_id: str) -> str | None:
        meta = self._group_meta.get(group_id)
        sid = (meta or {}).get("session_id")
        if sid is None and self._bcs is not None:
            sid = await self._bcs.create_session(group_id)
        return sid
```

(Ensure `SingleBotHandle` is imported in `task_executor.py` — it already is, used by `_dispatch_single_bot`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_runner/integration/test_runner_workflow_session.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/task_runner/runner.py \
        src/backend/src/agentclaw/community/core/task/task_runner/integration/task_executor.py \
        src/backend/tests/community/core/task/task_runner/integration/test_runner_workflow_session.py
git commit -m "feat(task): add TaskRunner.trigger_workflow + get_group_session

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `ExecutionEngine` seams — `trigger_single_bot_workflow` + `start_coop_group`

**Files:**
- Modify: `core/task/task_center/engine.py`
- Test: `tests/community/core/task/task_center/test_engine_task_type_seams.py` (new)

**Interfaces:**
- Consumes (Task 2): `TaskRunner.trigger_workflow`, `TaskRunner.get_group_session`, `TaskRunner.form_coop_group` (existing). Engine holds `self._runner`.
- Produces (used by Task 4): `ExecutionEngine.trigger_single_bot_workflow(*, task_id, bot_id, message) -> BotSendResult`; `ExecutionEngine.start_coop_group(gf) -> CoopGroupStart`.

- [ ] **Step 1: Write the failing test**

`tests/community/core/task/task_center/test_engine_task_type_seams.py`:

```python
import asyncio

from agentclaw.community.core.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_center.engine import CoopGroupStart, ExecutionEngine
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult


class _FakeRunner:
    async def trigger_workflow(self, *, bot_id, message, metadata=None):
        return BotSendResult(run_id="r", session_id="ws-s")
    async def form_coop_group(self, gf):
        return "grp-1"
    async def get_group_session(self, group_id):
        return "sess-for-grp-1"


def _engine_with_fake_runner():
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng._runner = _FakeRunner()
    return eng


def test_trigger_single_bot_workflow_returns_bot_send_result():
    eng = _engine_with_fake_runner()
    res = asyncio.new_event_loop().run_until_complete(
        eng.trigger_single_bot_workflow(task_id="t1", bot_id="b1", message="/wf 1")
    )
    assert isinstance(res, BotSendResult)
    assert res.session_id == "ws-s"


def test_start_coop_group_creates_then_fetches_session():
    eng = _engine_with_fake_runner()
    start = asyncio.new_event_loop().run_until_complete(
        eng.start_coop_group(GroupFormation(bot_ids=["b1"], collab_mode="state_machine"))
    )
    assert isinstance(start, CoopGroupStart)
    assert start.group_id == "grp-1"
    assert start.session_id == "sess-for-grp-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_center/test_engine_task_type_seams.py -v`
Expected: FAIL — `ImportError: cannot import name 'CoopGroupStart'`.

- [ ] **Step 3: Add `CoopGroupStart` + engine methods (`engine.py`)**

Near the top of `engine.py` (after imports, before `ExecutionEngine`):

```python
@dataclass(frozen=True)
class CoopGroupStart:
    """Result of starting a BCN coop group: the group id + its initial session_id."""
    group_id: str
    session_id: str | None
```

(Add `from dataclasses import dataclass` to engine.py imports if absent.)

Inside `class ExecutionEngine`, add (the engine already holds `self._runner`; `GroupFormation` is imported in the engine already — confirm/import `from agentclaw.community.core.task_dispatch.strategies import GroupFormation`):

```python
    async def trigger_single_bot_workflow(self, *, task_id: str, bot_id: str,
                                          message: str) -> "BotSendResult":
        """Single-bot workflow trigger; returns the conversation session_id."""
        return await self._runner.trigger_workflow(
            bot_id=bot_id, message=message, metadata={"biz_task_id": task_id})

    async def start_coop_group(self, gf: "GroupFormation") -> CoopGroupStart:
        """Create the BCN coop group and fetch its initial session_id by default."""
        group_id = await self._runner.form_coop_group(gf)
        session_id = await self._runner.get_group_session(group_id)
        return CoopGroupStart(group_id=group_id, session_id=session_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_center/test_engine_task_type_seams.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/task_center/engine.py \
        src/backend/tests/community/core/task/task_center/test_engine_task_type_seams.py
git commit -m "feat(task): add ExecutionEngine.trigger_single_bot_workflow + start_coop_group

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `execute` branches on `task_type` + persist `task_node`/`task_node_run_info` + DI

**Files:**
- Modify: `core/task/task_center/task_service.py`
- Modify: `di/modules/task_module.py`
- Test: `tests/community/core/task/task_center/test_execute_task_type_branching.py` (new)
- Modify (if needed): existing `tests/community/core/task/task_center/test_task_service.py` and `e2e/test_e2e.py` (only if their facade construction breaks from the new `__init__` params — they default to `None`, so likely no change).

**Interfaces:**
- Consumes (Tasks 1–3): `ExecutionEngine.trigger_single_bot_workflow`/`start_coop_group` (`CoopGroupStart`); `TaskType` enum; `TaskNodeRepositoryProtocol.insert`/`TaskNodeRecord`; `TaskNodeRunInfoRepositoryProtocol.insert`/`TaskNodeRunInfoRecord`; `TaskGraphService.update_task_node_info`/`TaskNodePatch`.
- Produces: `execute` branches on `task_type`; workflow/yaml persist the session_id.

- [ ] **Step 1: Write the failing test**

`tests/community/core/task/task_center/test_execute_task_type_branching.py`:

```python
import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
import agentclaw.community.core.task.repository.models  # noqa: F401
import agentclaw.community.core.task_queue.repository.models  # noqa: F401
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.task.domain.models import Status, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.engine import CoopGroupStart
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


class _SqliteDB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)
    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db; db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()


class _FakeEngine:
    """Stands in for ExecutionEngine; records calls."""
    def __init__(self, graph):
        self._graph = graph
        self.workflow_session = "wf-session-1"
        self.group_start = CoopGroupStart(group_id="grp-1", session_id="yaml-session-1")
        self.calls: list[tuple] = []
    async def on_execute(self, task_id):
        self.calls.append(("on_execute", task_id))


def _request(task_type: TaskType, **xec) -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="a", acceptance="d")]),
        ),
        source_type="api",
        owner_user_id="u1",
        owner_bot_id="b1",
        execution_config={"task_type": task_type, **xec},
    )


def _repos():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = _SqliteDB(eng)
    return TaskNodeRepository(db), TaskNodeRunInfoRepository(db)


def _service(task_type_stub_engine):
    graph = TaskGraphService()
    node_repo, run_repo = _repos()
    svc = TaskService(graph, task_info_repo=None, task_node_repo=node_repo,
                      task_node_run_info_repo=run_repo, task_id_provider=lambda: "t1")
    svc._engine = task_type_stub_engine  # inject the fake engine
    return svc, node_repo, run_repo


def test_execute_workflow_persists_session_id():
    eng = _FakeEngine(graph=None)
    # patch the engine method used by execute:
    async def trig(*, task_id, bot_id, message):
        eng.calls.append(("workflow", task_id, bot_id, message))
        from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
        return BotSendResult(run_id="r", session_id=eng.workflow_session)
    eng.trigger_single_bot_workflow = trig
    svc, node_repo, run_repo = _service(eng)

    result = asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.WORKFLOW, workflow_id="wf", args=["1", "2"])))
    assert result.success is True and result.task_id == "t1"
    run = run_repo.get_latest("t1", "t1")
    assert run is not None and run.run_mode == "single_bot" and run.session_id == "wf-session-1"
    assert run.assignee == "b1"
    node = node_repo.get("t1", "t1")
    assert node is not None and node.status is Status.RUNNING
    assert ("workflow", "t1", "b1", "/wf 1 2") in eng.calls


def test_execute_yaml_persists_session_id_with_state_machine():
    eng = _FakeEngine(graph=None)
    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode, gf.extend_props.get("definition_yaml")))
        return eng.group_start
    eng.start_coop_group = start
    svc, node_repo, run_repo = _service(eng)

    result = asyncio.new_event_loop().run_until_complete(
        svc.execute(_request(TaskType.YAML, yaml="def: x", participant_bot_ids=["b2"])))
    assert result.success is True
    run = run_repo.get_latest("t1", "t1")
    assert run.run_mode == "coop_group" and run.session_id == "yaml-session-1" and run.assignee == "grp-1"
    assert ("yaml", "state_machine", "def: x") in eng.calls


def test_execute_yaml_without_yaml_uses_manager_worker():
    eng = _FakeEngine(graph=None)
    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode))
        return eng.group_start
    eng.start_coop_group = start
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.YAML)))
    assert ("yaml", "manager_worker") in eng.calls


def test_execute_dynamic_unchanged():
    eng = _FakeEngine(graph=None)
    svc, _, _ = _service(eng)
    result = asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.DYNAMIC)))
    assert result.success is True
    assert ("on_execute", "t1") in eng.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_center/test_execute_task_type_branching.py -v`
Expected: FAIL — `TypeError: TaskService.__init__() got an unexpected keyword argument 'task_node_repo'`.

- [ ] **Step 3: Extend `TaskService.__init__` + imports (`task_service.py`)**

Add imports near the existing repository/types imports:
```python
import time

from agentclaw.community.core.repository.protocols.task import (
    TaskInfoRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, NodeOpResult, Status, TaskExecutionGraph, TaskNode, TaskNodePatch,
    TaskOpResult, TaskSpec, TaskSummary, TaskType,
)
from agentclaw.community.core.task.repository.types import (
    TaskInfoRecord, TaskNodeRecord, TaskNodeRunInfoRecord,
)
```
(Remove the now-duplicated single-name imports these replace; keep `Callable`, `IntegrityError`, `uuid`, `asyncio`, `logging`.)

`__init__` — add two params (default `None`):
```python
    def __init__(self, graph, harness=None, *, bot=None, bcs=None, discover=None,
                 bcs_identity=None, task_info_repo: TaskInfoRepositoryProtocol | None = None,
                 task_id_provider: Callable[[], str] | None = None,
                 task_node_repo: TaskNodeRepositoryProtocol | None = None,
                 task_node_run_info_repo: TaskNodeRunInfoRepositoryProtocol | None = None) -> None:
```
Store them:
```python
        self._task_info_repo = task_info_repo
        self._task_id_provider = task_id_provider or (lambda: str(uuid.uuid4()))
        self._task_node_repo = task_node_repo
        self._run_info_repo = task_node_run_info_repo
```

- [ ] **Step 4: Branch `execute` on `task_type`**

Replace the tail of `execute` (after `graph = self._graph.initialize_graph(task_info)` and the existing `logger.info(...)` line) with the branch. Keep the task_info persist + initialize_graph + logger lines as-is. The new tail:

```python
    task_type = request.execution_config.get("task_type")
    if task_type == TaskType.WORKFLOW:
        return await self._run_workflow(task_id, request, task_info, graph.run_id)
    if task_type == TaskType.YAML:
        return await self._run_yaml(task_id, request, task_info, graph.run_id)
    # dynamic (default): fire-and-forget on_execute
    if self._harness is not None:
        self._harness.register(task_id)
    bg = asyncio.create_task(self._engine.on_execute(task_id))
    self._bg_tasks.add(bg)
    bg.add_done_callback(self._on_bg_done)
    return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)
```

Add the two helpers + the persistence helper (after `execute`):

```python
    async def _run_workflow(self, task_id, request, task_info, run_id):
        ec = request.execution_config
        wf_id = ec.get("workflow_id")
        args = ec.get("args", [])
        message = f"/{wf_id} " + " ".join(args) if wf_id else " ".join(args)
        try:
            session_id = await self._engine.trigger_single_bot_workflow(
                task_id=task_id, bot_id=request.owner_bot_id, message=message)
        except Exception as exc:
            return TaskOpResult(task_id=task_id, success=False,
                                error=f"workflow trigger failed: {exc}", run_id=run_id)
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id, node_id=task_id, status=Status.RUNNING,
            run_mode="single_bot", assignee=request.owner_bot_id))
        self._persist_node_run(task_id, task_info, run_mode="single_bot",
                               assignee=request.owner_bot_id, session_id=session_id)
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_yaml(self, task_id, request, task_info, run_id):
        from agentclaw.community.core.task_dispatch.strategies import GroupFormation
        ec = request.execution_config
        has_yaml = bool(ec.get("yaml"))
        gf = GroupFormation(
            bot_ids=[request.owner_bot_id, *ec.get("participant_bot_ids", [])],
            collab_mode="state_machine" if has_yaml else "manager_worker",
            group_name=ec.get("group_name", f"task-{task_id}"),
            members_info=[], extend_props={"definition_yaml": ec.get("yaml")},
        )
        try:
            start = await self._engine.start_coop_group(gf)
        except Exception as exc:
            return TaskOpResult(task_id=task_id, success=False,
                                error=f"yaml group failed: {exc}", run_id=run_id)
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id, node_id=task_id, status=Status.RUNNING,
            run_mode="coop_group", assignee=start.group_id))
        self._persist_node_run(task_id, task_info, run_mode="coop_group",
                               assignee=start.group_id, session_id=start.session_id)
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    def _persist_node_run(self, task_id, task_info, *, run_mode, assignee, session_id):
        if self._task_node_repo is not None:
            self._task_node_repo.insert(TaskNodeRecord(
                id=0, task_id=task_id, node_id=task_id,
                task_spec=task_info.task_spec.to_dict(), status=Status.RUNNING))
        if self._run_info_repo is not None:
            now_ms = int(time.time() * 1000)
            self._run_info_repo.insert(TaskNodeRunInfoRecord(
                id=0, node_id=task_id, task_id=task_id, run_mode=run_mode, assignee=assignee,
                output=None, acceptance_result=None, retry=0, session_id=session_id,
                extend_props=None, start_time=now_ms, update_time=now_ms, end_time=None))
```

- [ ] **Step 5: Wire the two repos in DI (`task_module.py`)**

Add to the import block:
```python
from agentclaw.community.core.repository.protocols.task import (
    TaskInfoRepositoryProtocol, TaskNodeRepositoryProtocol, TaskNodeRunInfoRepositoryProtocol,
)
```
In the `task_service` provider signature, add two params and forward them:
```python
    @singleton
    @provider
    @inject
    def task_service(
        self,
        graph: TaskGraphService,
        discover: BotDiscoverServiceProtocol,
        bot_public: BotPublicServiceProtocol,
        injector: Injector,
        task_info_repo: TaskInfoRepositoryProtocol,
        task_node_repo: TaskNodeRepositoryProtocol,
        task_node_run_info_repo: TaskNodeRunInfoRepositoryProtocol,
    ) -> TaskService:
        ...
        return TaskService(
            graph, harness=harness, bot=bot, bcs=bcs, discover=discover_port,
            bcs_identity=bcs_identity, task_info_repo=task_info_repo,
            task_node_repo=task_node_repo, task_node_run_info_repo=task_node_run_info_repo,
        )
```

- [ ] **Step 6: Run the new test + the existing task suites + boundaries**

Run:
```
cd src/backend && .venv/bin/python -m pytest \
  tests/community/core/task/task_center/test_execute_task_type_branching.py \
  tests/community/core/task/task_center/test_task_service.py \
  tests/community/core/task/e2e/test_e2e.py \
  tests/community/architecture/test_repository_contracts.py \
  tests/community/architecture/test_module_boundaries.py \
  -q 2>&1 | tail -25
```
Expected: all PASS. (Existing facade tests default the two new repos to `None` → dynamic path unchanged.) If `test_module_boundaries` trips on `core/task/task_center` importing `core.repository.protocols.task` (already imported for task_info) or `core/task/repository.types`, add the missing entries to `core/task`'s `internal_dependencies` in its `README.md` and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/task_center/task_service.py \
        src/backend/src/agentclaw/community/di/modules/task_module.py \
        src/backend/tests/community/core/task/task_center/test_execute_task_type_branching.py
git commit -m "feat(task): branch execute on task_type; persist session_id for workflow/yaml

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Full community suite**

```bash
cd src/backend && .venv/bin/python -m pytest tests/community -q -p no:cacheprovider 2>&1 | tail -15
```
Expected: green (the dynamic path is unchanged; workflow/yaml are additive branches). If a failure names a caller of `send_message` treating it as `str`, update it to read `.run_id`/`.session_id` (grep: `grep -rn "\.send_message(" src/agentclaw/community`).

- [ ] **Step 2: Lint touched files**

```bash
cd src/backend && .venv/bin/python -m flake8 \
  src/agentclaw/community/core/task/task_runner/integration/ports.py \
  src/agentclaw/community/core/task/task_runner/integration/singlebox_engine_adapter.py \
  src/agentclaw/community/core/task/task_runner/integration/open_api_bot_adapter.py \
  src/agentclaw/community/core/task/task_runner/integration/task_executor.py \
  src/agentclaw/community/core/task/task_runner/integration/task_executor_result_poller.py \
  src/agentclaw/community/core/task/task_runner/runner.py \
  src/agentclaw/community/core/task/task_center/engine.py \
  src/agentclaw/community/core/task/task_center/task_service.py \
  src/agentclaw/community/di/modules/task_module.py 2>/dev/null || echo "flake8 not available — antflake deferred to pre-push/CI"
```
Expected: no violations.

- [ ] **Step 3: If green, report done. If a regression, fix in a new commit.**

---

## Self-Review (run before handoff)

**1. Spec coverage:**
- D1 (BotSendResult{run_id, session_id} wrapper; send_message returns it; dynamic reads .run_id) → Task 1. ✓
- D2 (one engine method start_coop_group = form_coop_group + get_group_session; runner get_group_session reads _group_meta / create_session) → Tasks 2–3. ✓
- D3 (root task_node RUNNING + task_node_run_info session_id; task_info stays PENDING) → Task 4 `_persist_node_run`. ✓
- D4 (collab_mode state_machine if yaml else manager_worker) → Task 4 `_run_yaml` + test. ✓
- D5 (branch after task_info persist + initialize_graph) → Task 4 `execute`. ✓
- D6 (workflow/yaml awaited inline; dynamic fire-and-forget) → Task 4. ✓
- D7 (engine seams trigger_single_bot_workflow / start_coop_group; task_runner impl) → Tasks 2–3. ✓
- D8 (inject TaskNodeRepository + TaskNodeRunInfoRepository) → Task 4 Step 5. ✓

**2. Placeholder scan:** no TBD/TODO. The "adjust to actual `__init__` if it differs" notes on `TaskExecutorResultPoller(...)` construction are concrete (the test only needs `register` + `_handles`); the implementer adapts the one construction line. No code placeholders.

**3. Type consistency:** `BotSendResult` defined in Task 1 (`ports.py`), used in Tasks 1–3. `CoopGroupStart` defined in Task 3 (`engine.py`), used in Tasks 3–4. `trigger_workflow(*, bot_id, message, metadata=None) -> BotSendResult` (Task 2) matches the engine call in Task 3. `get_group_session(group_id) -> str | None` (Task 2) matches Task 3. `start_coop_group(gf) -> CoopGroupStart` (Task 3) matches Task 4's `start.group_id`/`start.session_id`. `SingleBotHandle.session_id` (Task 1) used in Tasks 1–2. `TaskNodeRecord`/`TaskNodeRunInfoRecord` fields match the persistence layer (Task 4). `TaskType.WORKFLOW`/`YAML`/`DYNAMIC` (SCREAMING, from the prior plan) used in Task 4. Consistent.
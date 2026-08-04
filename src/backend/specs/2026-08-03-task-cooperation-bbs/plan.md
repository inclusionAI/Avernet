# BBS 自主接单 skill 实现计划 (plan.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 BBS 自主接单做成 bot/agent 自有的内容 skill，复用现有 task API，最小扩展 claim/release 路由 + 兜底租期清扫器 + 409 映射 + SubtaskState 直出，节点状态机零改动。

**Architecture:** 内容 skill `bbs-relay-pickup`（SKILL.md + references，`allowed_tools:[exec]`，跨引擎）经 REST 直调 `/api/tasks/*`；backend 在不改 9 态状态机的前提下，把现有 `TaskService.claim_node` CAS 暴露成 `POST /claim`、新增 `POST /release`（立即让出）与 `expire_lease`（兜底租期到期收回），写回仍走现有 `POST /events` 回投通道。崩溃/卡死由后台清扫器按全局 `BBS_LEASE_FALLBACK_SECONDS` 兜底接力。

**Tech Stack:** Python 3.12、FastAPI、injector（DI）、pydantic、pytest（含 TestClient + fastapi_injector）。仓库层级：`adapters/http → api → core`，core 不导入 api；状态机 `IllegalTransitionError` 经 app 异常映射成 HTTP 409。

**Spec:** `src/backend/specs/2026-08-03-task-cooperation-bbs/spec.md`（本计划是其 HOW）。

## Global Constraints

- Python 代码首行 `from __future__ import annotations`；用 `Optional[T]`，不写 `T | None`；必填值非可选；枚举用 `StrEnum`。
- 不改 `core/task/domain/state_machine.py` 的 `NODE_TRANSITIONS` / `GRAPH_TRANSITIONS`（节点状态机零改动）。接力复用现成 `RUNNING→FAILED`、`FAILED→RUNNING`。
- backend 分层不破：core 不导入 `api.task`；新方法先进 core `TaskService`（及 `core/task/protocols.py` 的 `TaskService` Protocol），api 层 `api/task/service_api.py:TaskServiceProtocol` 仅加同名方法（`*args,**kwargs`），由 `tests/community/architecture/test_task_service_api_conformance.py` 自动校验。
- 状态唯一写口不变：新方法（claim/release/expire）仍经 `_emit` 追加事件 + `_apply_event` fold + `save`，与 `claim_node` 同形；不绕过 `on_event`/guard。
- 事件优先：release/expire 落 `EventKind.NODE_RELEASED`（新增），fold 不泵 scheduler tick、不升 `HUMAN_REQUIRED`。
- 鉴权本期不做（沿用 task 路由裸奔现状，spec §7.2）；`T_fallback` 取值与 `max_attempts` 关系为 plan 评审项（本计划先给常量 + 不计 attempt 策略）。
- 测试用现有 `InMemoryTaskRepo` / `InMemoryTaskEventRepo`（`plugins/community/task/in_memory_repos.py`）+ `RecordingPanelPublisher`；service 构造 `TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())`。
- 跑测试：`cd src/backend && pytest tests/community/<path> -v`（仓内 pytest 根在 `src/backend`）。

---

## File Structure

新增 / 改动文件与职责：

| 文件 | 职责 | 动作 |
|---|---|---|
| `core/task/domain/events.py` | 事件枚举 + 事件 dataclass | 新增 `EventKind.NODE_RELEASED` + `NodeReleased` dataclass |
| `core/task/domain/state_machine.py` | 状态机（不改转移表） | 仅被引用，不改动 |
| `core/task/protocols.py` | core Port/Protocol | `DispatchResult` 加 `lease_until`；`TaskService` Protocol 加 `release_node` / `expire_lease` / `sweep_expired_leases`；`TaskRepo`（TaskService 构造依赖的仓库 Protocol）加 `find_expired_lease_nodes` |
| `core/task/services/task_service.py` | 唯一写口 | `claim_node` 加 `run_mode` 参 + 设 `lease_until`；新增 `release_node` / `expire_lease` / `sweep_expired_leases`；`_apply_event` 加 `NODE_RELEASED` fold 分支；`_node_view` 直出 SubtaskState；模块常量 `BBS_LEASE_FALLBACK_SECONDS` + `_utcnow()` 时钟缝 |
| `core/task/services/lease_sweeper.py` | 后台清扫器薄封装 | 新建 `LeaseSweeper.sweep_once()` |
| `plugins/community/task/in_memory_repos.py` | 测试用 InMemory 仓库 | `InMemoryTaskRepo` 加 `find_expired_lease_nodes` |
| `adapters/http/task/schemas.py` | pydantic 模型 | 新增 `ClaimRequest` / `ClaimResponse` / `ReleaseRequest` / `ReleaseResponse`；`TaskNodeDetailView` 加 SubtaskState 字段 |
| `adapters/http/task/router.py` | HTTP 路由 | 新增 `POST /{task_id}/nodes/{node_id}/claim`、`POST /{task_id}/nodes/{node_id}/release` |
| `adapters/http/app.py` | 全局异常映射 | 新增 `@app.exception_handler(IllegalTransitionError) → 409` |
| `di/...`（task 模块 DI） | 绑定 | 绑 `LeaseSweeper`（若需调度则另系分） |
| `tests/community/core/task/services/test_event_fold.py` | 事件 fold TDD | 加 `NODE_RELEASED` 用例 |
| `tests/community/core/task/services/test_task_service_state.py` | service TDD | 加 lease/release/expire/sweep 用例 |
| `tests/community/adapters/http/task/test_router.py` | 路由 TDD | 加 claim/release/409/403 + SubtaskState 直出用例 |
| `tests/community/core/task/services/test_lease_sweeper.py` | 清扫器 TDD | 新建 |
| `tests/community/core/task/services/test_bbs_pickup_integration.py` | 集成场景 TDD | 新建（race/handoff/crash） |
| `skill/bbs-relay-pickup/SKILL.md` + `references/` | 内容 skill | 新建（本计划内落 authored 源，发布到 skill center 另系分） |

> skill authored 源放在 `src/backend/specs/2026-08-03-task-cooperation-bbs/skill/bbs-relay-pickup/`（随 spec 评审版本化）；发布（`local://` 上传或 `git://` 同步到 skill center 激活管线）属部署步骤，不在本计划代码范围内。

---

## Task 1: NODE_RELEASED 事件 + fold 分支

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/domain/events.py`
- Modify: `src/backend/src/agentclaw/community/core/task/services/task_service.py:479-610`（`_apply_event`）
- Test: `src/backend/tests/community/core/task/services/test_event_fold.py`

**Interfaces:**
- Consumes: `EventKind`（events.py）、`_apply_event`（task_service.py）、`NodeStatus`/`Node`（models.py）
- Produces: `EventKind.NODE_RELEASED = "node.released"`；fold 行为：`RUNNING→FAILED`、清 `assignee`、写 `node.properties["release_outcome"] = outcome`、**不动 `graph.status`、不升 HUMAN**；payload `outcome ∈ {"handoff","lease_expired"}`

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/services/test_event_fold.py 追加
def test_node_released_fold_running_to_failed_no_escalation(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node
    # 节点当前 RUNNING + 有 assignee
    before = svc.get(task_id).execution_graph.status
    svc.on_event({"task_id": task_id, "kind": "node.released",
                  "payload": {"node_id": node_id, "outcome": "handoff"}})
    task = svc.get(task_id)
    node = next(n for n in task.execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.assignee is None
    assert node.properties.get("release_outcome") == "handoff"
    # 不升人工:graph 状态不变(未被推到 HUMAN_REQUIRED)
    assert task.execution_graph.status is before
```

（`svc_and_running_node` fixture 在本文件已有 `_graph_with_n1` + `claim_node` 基础上构造：见 Step 3 给出。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/core/task/services/test_event_fold.py::test_node_released_fold_running_to_failed_no_escalation -v`
Expected: FAIL（`EventKind("node.released")` 抛 ValueError，或 fold 不转 FAILED）

- [ ] **Step 3: 加事件枚举 + dataclass + fold 分支**

```python
# events.py: 在 EventKind 枚举里(HANG_CANCELLED 之后)加
    NODE_RELEASED = "node.released"   # BBS 接单让出/兜底收回(§10.4/§10.3):RUNNING→FAILED
                                      # 不泵 scheduler tick、不升 HUMAN_REQUIRED。outcome ∈ {handoff,lease_expired}

# events.py: 在 NodeFailed 之后加 dataclass
@dataclass
class NodeReleased(TaskEvent):
    kind: EventKind = EventKind.NODE_RELEASED
    node_id: str = ""
    outcome: str = "handoff"  # handoff(主动让出) | lease_expired(清扫器收回)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "outcome": self.outcome}
```

```python
# task_service.py _apply_event: 在 BBS_CONFIRMED/HANG_CANCELLED 分支之后、"Unknown kinds" 之前加
        if kind == EventKind.NODE_RELEASED:
            # BBS 接单让出/兜底收回(§10.4/§10.3):RUNNING→FAILED,可接力,不升人工、不泵 tick。
            node = self._find_node(task, node_id)
            if node is not None and node.status is NodeStatus.RUNNING:
                require_node_transition(node.status, NodeStatus.FAILED)
                node.status = NodeStatus.FAILED
                node.assignee = None
                node.properties["release_outcome"] = str(payload.get("outcome") or "handoff")
            return
```

fixture（若文件中尚无）：

```python
# test_event_fold.py 头部已有 _service/_planned_task/_graph_with_n1;追加
@pytest.fixture
def svc_and_running_node():
    svc = _service()
    task = _graph_with_n1(svc)  # _planned_task + init_execution_graph + add_node n1(PENDING)
    svc.claim_node(task.id, "n1", "bot-A", run_mode=RunMode.BBS)
    return svc, task.id, "n1"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_event_fold.py::test_node_released_fold_running_to_failed_no_escalation -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/domain/events.py \
        src/backend/src/agentclaw/community/core/task/services/task_service.py \
        src/backend/tests/community/core/task/services/test_event_fold.py
git commit -m "feat(task): NODE_RELEASED event fold RUNNING→FAILED (BBS 让出/兜底,不升人工)"
```

---

## Task 2: claim_node 设 lease_until + run_mode 参 + DispatchResult.lease_until

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/protocols.py:72-80`（`DispatchResult`）+ `:172`（`TaskService.claim_node` Protocol）
- Modify: `src/backend/src/agentclaw/community/core/task/services/task_service.py`（模块常量/时钟缝 + `claim_node` 240-275）
- Test: `src/backend/tests/community/core/task/services/test_task_service_state.py`

**Interfaces:**
- Consumes: `claim_node`（现有）、`DispatchResult`、`Node.properties`
- Produces: `BBS_LEASE_FALLBACK_SECONDS: int` 常量、`_utcnow() -> datetime` 模块函数（测试可 monkeypatch）；`claim_node(task_id, node_id, executor_id, run_mode: Optional[RunMode] = None)`；`DispatchResult.lease_until: Optional[str]`；claim 在 `node.properties["lease_until"]` 记 ISO 截止时间

- [ ] **Step 1: 写失败测试**

```python
# test_task_service_state.py 追加
def test_claim_node_sets_lease_until_and_run_mode(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node
    from agentclaw.community.core.task.services import task_service as ts_mod
    import datetime as _dt
    frozen = _dt.datetime(2026, 8, 4, 12, 0, tzinfo=_dt.timezone.utc)
    monkey = _Monkey(ts_mod)  # 见 Step 3 的轻量 monkeypatch helper
    monkey.set_utcnow(frozen)
    res = svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    expected = (frozen + _dt.timedelta(seconds=ts_mod.BBS_LEASE_FALLBACK_SECONDS)).isoformat()
    assert res.run_mode is RunMode.BBS
    assert res.lease_until == expected
    node = next(n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id)
    assert node.properties.get("lease_until") == expected
    assert node.run_mode is RunMode.BBS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py::test_claim_node_sets_lease_until_and_run_mode -v`
Expected: FAIL（`claim_node` 不接受 `run_mode`，`DispatchResult` 无 `lease_until`）

- [ ] **Step 3: 实现**

```python
# task_service.py 顶部 import 区加
from datetime import datetime, timedelta, timezone

# 模块级(类外,常量 + 时钟缝)
BBS_LEASE_FALLBACK_SECONDS: int = 3600  # 兜底租期(全局,非 bot 预测);崩溃检出延迟上界。取值评审项(spec §7.2)

def _utcnow() -> datetime:
    """UTC now seam — tests monkeypatch this to control lease expiry."""
    return datetime.now(timezone.utc)
```

```python
# protocols.py DispatchResult 加字段
@dataclass
class DispatchResult:
    node_id: str
    executor_id: str
    run_mode: RunMode
    accept_token: str = ""
    dispatched_at: str = ""
    lease_until: Optional[str] = None  # BBS 兜底租期截止 ISO(系统按 T_fallback 设,非 bot 预测)

# protocols.py TaskService.claim_node Protocol 签名加 run_mode
    def claim_node(
        self, task_id: str, node_id: str, executor_id: str,
        run_mode: Optional[RunMode] = None,
    ) -> DispatchResult: ...
```

```python
# task_service.py claim_node 改签名 + 设 lease
    def claim_node(
        self, task_id: str, node_id: str, executor_id: str,
        run_mode: Optional[RunMode] = None,
    ) -> Optional[DispatchResult]:
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task_id}")
        require_node_transition(node.status, NodeStatus.RUNNING)
        node.status = NodeStatus.RUNNING
        node.assignee = executor_id
        node.run_mode = run_mode or node.run_mode or RunMode.SINGLE_BOT
        lease_at = _utcnow() + timedelta(seconds=BBS_LEASE_FALLBACK_SECONDS)
        lease_iso = lease_at.isoformat()
        node.properties["lease_until"] = lease_iso
        node.attempted_executors.append(self._attempt_record(executor_id, node))
        self._emit(task, EventKind.NODE_RUNNING, node_id=node_id, from_status=NodeStatus.PENDING.value)
        token = _new_accept_token()
        self._task_repo.save(task)
        logger.info("[Task] task=%s claim_node node=%s → running executor=%s run_mode=%s lease_until=%s",
                    task_id, node_id, executor_id, node.run_mode.value, lease_iso)
        return DispatchResult(
            node_id=node_id, executor_id=executor_id, run_mode=node.run_mode,
            accept_token=token, lease_until=lease_iso,
        )
```

测试用 monkeypatch helper（轻量，避免引入 pytest fixture 复杂度）：

```python
# test_task_service_state.py 头部加
class _Monkey:
    """Swap module-level _utcnow for a test, restore on exit."""
    def __init__(self, mod): self._mod, self._orig = mod, mod._utcnow
    def set_utcnow(self, dt):
        self._mod._utcnow = lambda: dt
    def __exit__(self, *exc): self._mod._utcnow = self._orig
    def __enter__(self): return self
```

Step 1 测试改用 `with _Monkey(ts_mod) as m: m.set_utcnow(frozen); res = svc.claim_node(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py::test_claim_node_sets_lease_until_and_run_mode -v`
Expected: PASS

- [ ] **Step 5: 回归现有 claim 用例不破**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py -v`
Expected: PASS（`run_mode` 有默认值，现有不带 `run_mode` 的调用不破）

- [ ] **Step 6: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/protocols.py \
        src/backend/src/agentclaw/community/core/task/services/task_service.py \
        src/backend/tests/community/core/task/services/test_task_service_state.py
git commit -m "feat(task): claim_node 设兜底 lease_until + run_mode 参(BBS 自主接单)"
```

---

## Task 3: TaskService.release_node + expire_lease（+ Protocol/conformance）

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/services/task_service.py`（新增两方法，置于 `claim_node` 之后、query face 之前）
- Modify: `src/backend/src/agentclaw/community/core/task/protocols.py`（`TaskService` Protocol 加 `release_node` / `expire_lease`）
- Modify: `src/backend/src/agentclaw/community/api/task/service_api.py:24-52`（`TaskServiceProtocol` 加 `release_node`）
- Test: `src/backend/tests/community/core/task/services/test_task_service_state.py`

**Interfaces:**
- Consumes: Task 1 的 `NODE_RELEASED` fold、Task 2 的 `lease_until`、`_emit`/`_find_node`/`_load`
- Produces: `release_node(task_id, node_id, executor_id) -> Optional[Task]`（仅 assignee；非 assignee 抛 `Forbidden`）；`expire_lease(task_id, node_id) -> Optional[Task]`（清扫器调，outcome=`lease_expired`，不需 assignee）

- [ ] **Step 1: 写失败测试**

```python
# test_task_service_state.py 追加
def test_release_node_by_assignee_handoff(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node  # n1 已被 bot-A claim
    task = svc.release_node(task_id, node_id, "bot-A")
    node = next(n for n in task.execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.assignee is None
    assert node.properties.get("release_outcome") == "handoff"

def test_release_node_rejects_non_assignee(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node
    from agentclaw.community.core.errors import Forbidden
    with pytest.raises(Forbidden):
        svc.release_node(task_id, node_id, "bot-B")  # 不是持有者

def test_expire_lease_marks_lease_expired(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node
    task = svc.expire_lease(task_id, node_id)
    node = next(n for n in task.execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("release_outcome") == "lease_expired"

def test_release_then_reclaim_relays(svc_and_running_node):
    svc, task_id, node_id = svc_and_running_node
    svc.release_node(task_id, node_id, "bot-A")
    # 接力:bot-B claim 同一节点(FAILED→RUNNING 合法)
    res = svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)
    assert res is not None
    assert res.executor_id == "bot-B"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py -k "release_node or expire_lease or release_then_reclaim" -v`
Expected: FAIL（方法不存在）

- [ ] **Step 3: 实现**

```python
# task_service.py import Forbidden
from agentclaw.community.core.errors import Forbidden  # 顶部 import 区

# task_service.py: claim_node 之后加
    def release_node(
        self, task_id: str, node_id: str, executor_id: str
    ) -> Optional[Task]:
        """BBS 接单主动让出(§10.4):仅当前 assignee 可调;RUNNING→FAILED(outcome=handoff),
        下个 bot 立即接力。不泵 scheduler tick、不升 HUMAN(经 NODE_RELEASED fold)。"""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task_id}")
        if node.assignee != executor_id:
            raise Forbidden(f"only assignee {node.assignee} may release node {node_id}")
        require_node_transition(node.status, NodeStatus.FAILED)
        node.status = NodeStatus.FAILED
        node.assignee = None
        node.properties["release_outcome"] = "handoff"
        self._emit(task, EventKind.NODE_RELEASED, node_id=node_id, outcome="handoff")
        self._task_repo.save(task)
        logger.info("[Task] task=%s release_node node=%s by=%s → failed(handoff)",
                    task_id, node_id, executor_id)
        return self._task_repo.get_by_id(task_id)

    def expire_lease(self, task_id: str, node_id: str) -> Optional[Task]:
        """兜底租期到期收回(§10.3,清扫器调):RUNNING→FAILED(outcome=lease_expired)。
        节点可能已被 bot release/complete → 此时 RUNNING→FAILED 非法,吞 IllegalTransitionError。"""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            return None
        try:
            require_node_transition(node.status, NodeStatus.FAILED)
        except IllegalTransitionError:
            return self._task_repo.get_by_id(task_id)  # 已非 RUNNING,无需收回
        node.status = NodeStatus.FAILED
        node.assignee = None
        node.properties["release_outcome"] = "lease_expired"
        self._emit(task, EventKind.NODE_RELEASED, node_id=node_id, outcome="lease_expired")
        self._task_repo.save(task)
        logger.info("[Task] task=%s expire_lease node=%s → failed(lease_expired)", task_id, node_id)
        return self._task_repo.get_by_id(task_id)
```

```python
# protocols.py: TaskService Protocol 加(claim_node 之后)
    def release_node(self, task_id: str, node_id: str, executor_id: str) -> Optional[Task]:
        """BBS 主动让出(仅 assignee):RUNNING→FAILED(handoff),不升人工。"""
        ...
    def expire_lease(self, task_id: str, node_id: str) -> Optional[Task]:
        """兜底租期到期收回(清扫器):RUNNING→FAILED(lease_expired)。"""
        ...
```

```python
# api/task/service_api.py: TaskServiceProtocol 加(on_event 之后、claim_node 旁)
    def release_node(self, *args: Any, **kwargs: Any) -> Any: ...
```

（`expire_lease` 不经 HTTP，只 core/清扫器用，无需进 api Protocol。`sweep_expired_leases` 同理不进 api Protocol，见 Task 7。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py -k "release_node or expire_lease or release_then_reclaim" -v`
Expected: PASS

- [ ] **Step 5: conformance 校验通过（api Protocol 新方法在 core 存在）**

Run: `cd src/backend && pytest tests/community/architecture/test_task_service_api_conformance.py -v`
Expected: PASS（`release_node` 已在 core `TaskService`）

- [ ] **Step 6: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/services/task_service.py \
        src/backend/src/agentclaw/community/core/task/protocols.py \
        src/backend/src/agentclaw/community/api/task/service_api.py \
        src/backend/tests/community/core/task/services/test_task_service_state.py
git commit -m "feat(task): release_node(主动让出) + expire_lease(兜底收回) BBS 接力通路"
```

---

## Task 4: IllegalTransitionError → HTTP 409 映射

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/app.py`（新增异常 handler）

**Interfaces:**
- Consumes: `IllegalTransitionError`（`core/task/domain/state_machine.py`）、`_trace_headers`/`_is_public_api`/`_public_error_envelope`（app.py 现有）
- Produces: 全局 handler `@app.exception_handler(IllegalTransitionError)` 返回 **409** `{"detail": ...}`（claim/release 并发冲突、源态非法均经此）

- [ ] **Step 1: 写失败测试**

```python
# tests/community/adapters/http/task/test_router.py 追加(用真实 service,见 Task 5 的 _real_service)
def test_illegal_transition_maps_to_409(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    # 第一个 claim 200;第二个并发 claim 同节点 → IllegalTransitionError → 409
    r1 = client.post(f"/api/tasks/{task_id}/nodes/{node_id}/claim",
                     json={"executor_id": "bot-A", "run_mode": "bbs"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/tasks/{task_id}/nodes/{node_id}/claim",
                     json={"executor_id": "bot-B", "run_mode": "bbs"})
    assert r2.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/adapters/http/task/test_router.py::test_illegal_transition_maps_to_409 -v`
Expected: FAIL（路由尚未实现 → 404；或 409 handler未注册时返 500）

- [ ] **Step 3: 实现 handler**

```python
# app.py: 在 _domain_error_handler 之后、_http_exception_handler 之前加
from agentclaw.community.core.task.domain.state_machine import (  # noqa: E402
    IllegalTransitionError,
)

@app.exception_handler(IllegalTransitionError)
async def _illegal_transition_handler(
    request: Request, exc: IllegalTransitionError,
) -> JSONResponse:
    """状态机非法转移(claim/release 并发冲突、源态非法)→ 409 Conflict,
    使 skill 可据 409 换下一候选(spec FR-EXT-03)。IllegalTransitionError 是
    ValueError 子类(非 DomainError),默认落 catch-all 500,故单独注册。"""
    status = 409
    logger.info("[IllegalTransition 409] %s %s: %s", request.method, request.url.path, exc)
    if _is_public_api(request):
        return _public_error_envelope(status, request)
    return JSONResponse(
        status_code=status,
        content={"detail": str(exc)},
        headers=_trace_headers(request),
    )
```

> 注：Task 5 才加 claim 路由本体与 `real_client_with_running_node` fixture；本任务的测试在 Task 5 完成后才能真正通过。本步骤先落 handler，Task 5 落路由后二者同测。

- [ ] **Step 4: 提交（路由前的 handler 落盘，Task 5 一起验证）**

```bash
git add src/backend/src/agentclaw/community/adapters/http/app.py
git commit -m "feat(http): IllegalTransitionError → 409 Conflict(BBS claim/release 冲突可辨)"
```

---

## Task 5: claim / release REST 路由 + schemas

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/task/schemas.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/task/router.py`
- Test: `src/backend/tests/community/adapters/http/task/test_router.py`

**Interfaces:**
- Consumes: Task 2 `claim_node(run_mode=...)` 返回 `DispatchResult{...,lease_until}`、Task 3 `release_node`、Task 4 的 409 handler、`Injected(TaskServiceProtocol)`
- Produces: `POST /api/tasks/{task_id}/nodes/{node_id}/claim`（body `{executor_id, run_mode?}`，200 `ClaimResponse`/409）、`POST /api/tasks/{task_id}/nodes/{node_id}/release`（body `{executor_id}`，200 `ReleaseResponse`/403/409）；`TaskRouterProtocol` 已含 `release_node`（Task 3）

- [ ] **Step 1: 写失败测试（含 Task 4 的 409 用例 + release + 403）**

```python
# test_router.py: 真实 service 桩(替代 _StubTaskService,使 CAS/409/403 真跑)
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo, InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import RecordingPanelPublisher
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.core.task.domain.models import RunMode, SubTaskSpec

def _real_service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())

def _task_with_pending_node(svc: TaskService) -> tuple[str, str]:
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.init_execution_graph(task)
    svc.add_node(task.id, SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.BBS), "n_execute_start")
    return t.id, "n1"

def _client_with_real(svc: TaskService) -> TestClient:
    from agentclaw.community.api.task import TaskSchedulerProtocol, TaskServiceProtocol
    from agentclaw.community.adapters.http.task.router import router
    app = FastAPI(); app.include_router(router)
    inj = Injector([])
    inj.binder.bind(TaskServiceProtocol, to=svc, scope=singleton)
    inj.binder.bind(TaskSchedulerProtocol, to=_StubTaskScheduler(), scope=singleton)
    attach_injector(app, inj)
    return TestClient(app)

@pytest.fixture
def real_client_with_running_node():
    svc = _real_service()
    task_id, node_id = _task_with_pending_node(svc)
    return _client_with_real(svc), task_id, node_id

def test_claim_returns_200_with_lease(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    r = client.post(f"/api/tasks/{task_id}/nodes/{node_id}/claim",
                    json={"executor_id": "bot-A", "run_mode": "bbs"})
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == node_id
    assert body["run_mode"] == "bbs"
    assert body["lease_until"]  # 非空 ISO

def test_release_by_assignee_returns_200(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    client.post(f"/api/tasks/{task_id}/nodes/{node_id}/claim",
                json={"executor_id": "bot-A", "run_mode": "bbs"})
    r = client.post(f"/api/tasks/{task_id}/nodes/{node_id}/release",
                    json={"executor_id": "bot-A"})
    assert r.status_code == 200
    assert r.json()["outcome"] == "handoff"

def test_release_non_assignee_returns_403(real_client_with_running_node):
    client, task_id, node_id = real_client_with_running_node
    client.post(f"/api/tasks/{task_id}/nodes/{node_id}/claim",
                json={"executor_id": "bot-A", "run_mode": "bbs"})
    r = client.post(f"/api/tasks/{task_id}/nodes/{node_id}/release",
                    json={"executor_id": "bot-B"})
    assert r.status_code == 403
```

（`test_illegal_transition_maps_to_409` 见 Task 4 Step 1，此处一并运行。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/adapters/http/task/test_router.py -k "claim or release or illegal_transition" -v`
Expected: FAIL（路由/schema 不存在）

- [ ] **Step 3: 加 schemas**

```python
# schemas.py 追加
class ClaimRequest(BaseModel):
    executor_id: str
    run_mode: str = "bbs"  # SINGLE_BOT | COOP_GROUP | BBS;BBS 自主接单默认 bbs

class ClaimResponse(BaseModel):
    node_id: str
    executor_id: str
    run_mode: str
    accept_token: str = ""
    lease_until: Optional[str] = None

class ReleaseRequest(BaseModel):
    executor_id: str
    idempotency_key: Optional[str] = None

class ReleaseResponse(BaseModel):
    node_id: str
    status: str
    outcome: str  # handoff
```

- [ ] **Step 4: 加路由**

```python
# router.py: import 区加 ClaimRequest, ClaimResponse, ReleaseRequest, ReleaseResponse
# 在 get_node_detail 路由之前(画布端点区前)加
@router.post("/{task_id}/nodes/{node_id}/claim", response_model=ClaimResponse)
def claim_node_route(
    task_id: str, node_id: str, req: ClaimRequest,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    """BBS 自主接单抢占(§10.1):CAS 源态→RUNNING,系统按 T_fallback 设兜底 lease。
    并发冲突/源态非法 → 409(IllegalTransitionError, app handler)。"""
    from agentclaw.community.core.task.domain.models import RunMode
    try:
        run_mode = RunMode(req.run_mode)
    except ValueError:
        run_mode = RunMode.BBS
    result = service.claim_node(task_id, node_id, req.executor_id, run_mode=run_mode)
    if result is None:
        raise HTTPException(status_code=404, detail="task/node not found")
    return ClaimResponse(
        node_id=result.node_id, executor_id=result.executor_id,
        run_mode=result.run_mode.value, accept_token=result.accept_token,
        lease_until=result.lease_until,
    )


@router.post("/{task_id}/nodes/{node_id}/release", response_model=ReleaseResponse)
def release_node_route(
    task_id: str, node_id: str, req: ReleaseRequest,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    """BBS 接单主动让出(§10.4):仅 assignee 可调;RUNNING→FAILED(handoff)立即接力。
    非 assignee → 403(Forbidden);源态非法 → 409。"""
    task = service.release_node(task_id, node_id, req.executor_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    node = next((n for n in task.execution_graph.nodes if n.node_id == node_id), None)
    status = node.status.value if node is not None else "unknown"
    outcome = (node.properties.get("release_outcome") if node is not None else "handoff") or "handoff"
    return ReleaseResponse(node_id=node_id, status=status, outcome=outcome)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/adapters/http/task/test_router.py -k "claim or release or illegal_transition" -v`
Expected: PASS（200 + lease、200 handoff、403 非 assignee、409 并发冲突）

- [ ] **Step 6: 路由表注册校验**

```python
# test_router.py 追加
def test_router_registers_claim_release_routes():
    from agentclaw.community.adapters.http.task.router import router
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/tasks/{task_id}/nodes/{node_id}/claim" in paths
    assert "/api/tasks/{task_id}/nodes/{node_id}/release" in paths
```

Run: `cd src/backend && pytest tests/community/adapters/http/task/test_router.py::test_router_registers_claim_release_routes -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/backend/src/agentclaw/community/adapters/http/task/schemas.py \
        src/backend/src/agentclaw/community/adapters/http/task/router.py \
        src/backend/tests/community/adapters/http/task/test_router.py
git commit -m "feat(task): POST /claim + /release 路由(BBS 自主接单抢占/让出,409/403)"
```

---

## Task 6: SubtaskState 直出（get_node_detail，FR-EXT-05）

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/services/task_service.py`（`get_node_detail` 361-474 + `_node_view` 905-937）
- Modify: `src/backend/src/agentclaw/community/adapters/http/task/schemas.py`（`TaskNodeDetailView`）
- Test: `src/backend/tests/community/core/task/services/test_task_service_state.py`

**Interfaces:**
- Consumes: `TaskExecutionGraph.state.subtasks[node_id]`（`SubtaskState`：`intermediate_results`/`gap_records`/`artifacts`）、`_node_view`
- Produces: `get_node_detail` 返回的 dict 含 `intermediate_results: list[dict]` / `gap_records: list[dict]` / `artifacts: list[dict]`；`TaskNodeDetailView` 对应字段

- [ ] **Step 1: 写失败测试**

```python
# test_task_service_state.py 追加
def test_get_node_detail_exposes_subtask_state(svc):
    from agentclaw.community.core.task.domain.models import SubtaskState
    task = _graph_with_n1(svc)
    # 直接往 State 分区写一条中间结果(模拟前序 bot checkpoint)
    g = svc.get(task.id).execution_graph
    g.state.subtasks["n1"] = SubtaskState(node_id="n1", status=NodeStatus.RUNNING,
                                          intermediate_results=[{"step": 1, "note": "done-30pct"}])
    svc._task_repo.save(svc.get(task.id))
    detail = svc.get_node_detail(task.id, "n1")
    assert detail["intermediate_results"] == [{"step": 1, "note": "done-30pct"}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py::test_get_node_detail_exposes_subtask_state -v`
Expected: FAIL（`get_node_detail` 不返回 `intermediate_results`）

- [ ] **Step 3: 实现**

```python
# task_service.py _node_view 返回 dict 末尾加(precision from SubtaskState)
        # SubtaskState 直出(FR-EXT-05):接力 bot 看到前序已 commit 的中间结果/gap/产出。
        st = None
        graph = None
        # _node_view 只拿到 Node;SubtaskState 在 graph.state.subtasks,由 get_node_detail 注入。
        # 此处读 caller 传入的 ctx(get_node_detail 设 node_view 的 subtask_state 字段)。
        subtask_state = n.properties.get("__subtask_state__")  # 由 get_node_detail 填
        if isinstance(subtask_state, dict):
            pass  # 已含 intermediate_results/gap_records/artifacts
```

> 更干净做法：`get_node_detail` 在调 `_node_view` 后，从 `task.execution_graph.state.subtasks.get(node_id)` 取 `SubtaskState`，把其 `intermediate_results` / `gap_records` / `artifacts`（list[ArtifactRef]→dict）merge 进 view dict。`_node_view` 不动 SubtaskState 读取，避免给 `_node_view` 加 graph 依赖：

```python
# task_service.py get_node_detail 内(组装 detail dict 之后)return 之前)加
        st = task.execution_graph.state.subtasks.get(node_id) if task.execution_graph else None
        if st is not None:
            view["intermediate_results"] = list(st.intermediate_results)
            view["gap_records"] = [
                {"node_id": gr.node_id, "round": gr.round,
                 "unmet_criteria": list(gr.unmet_criteria),
                 "verdict": (gr.verdict.value if gr.verdict else None), "at": gr.at}
                for gr in st.gap_records
            ]
            view["artifacts"] = [
                {"name": a.name, "location": a.location, "type": a.type}
                for a in st.artifacts
            ]
        else:
            view.setdefault("intermediate_results", [])
            view.setdefault("gap_records", [])
            view.setdefault("artifacts", view.get("artifacts") or [])
```

（`get_node_detail` 现有实现把 `_node_view(n)` 的 dict 作为返回；在 return 前注入上述字段。实现者按现有 `get_node_detail` 结构把这段 merge 进去。）

```python
# schemas.py TaskNodeDetailView 加字段
    intermediate_results: list[dict] = []
    gap_records: list[dict] = []
    # artifacts 已可能存在;若未声明则补
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_task_service_state.py::test_get_node_detail_exposes_subtask_state -v`
Expected: PASS

- [ ] **Step 5: 路由层 schema 校验（TaskNodeDetailView 接受新字段）**

Run: `cd src/backend && pytest tests/community/adapters/http/task/test_router.py tests/community/adapters/http/task/test_schemas.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/services/task_service.py \
        src/backend/src/agentclaw/community/adapters/http/task/schemas.py \
        src/backend/tests/community/core/task/services/test_task_service_state.py
git commit -m "feat(task): get_node_detail 直出 SubtaskState(BBS 接力可见前序轨迹)"
```

---

## Task 7: 兜底租期清扫器（repo 扫描 + TaskService.sweep + LeaseSweeper）

**Files:**
- Modify: `src/backend/src/agentclaw/community/plugins/community/task/in_memory_repos.py`（`InMemoryTaskRepo` 加 `find_expired_lease_nodes`）
- Modify: `src/backend/src/agentclaw/community/core/task/protocols.py`（`TaskRepo` Protocol 加 `find_expired_lease_nodes`）
- Modify: `src/backend/src/agentclaw/community/core/task/services/task_service.py`（加 `sweep_expired_leases`）
- Create: `src/backend/src/agentclaw/community/core/task/services/lease_sweeper.py`
- Test: `src/backend/tests/community/core/task/services/test_lease_sweeper.py`

**Interfaces:**
- Consumes: Task 2 `lease_until`（`node.properties["lease_until"]`）、Task 3 `expire_lease`、`_utcnow()`
- Produces: `InMemoryTaskRepo.find_expired_lease_nodes(now_iso) -> list[tuple[str,str]]`；`TaskService.sweep_expired_leases() -> int`；`LeaseSweeper.sweep_once() -> int`

- [ ] **Step 1: 写失败测试**

```python
# test_lease_sweeper.py 新建
from __future__ import annotations
import datetime as _dt
import pytest
from agentclaw.community.core.task.services import TaskService, task_service as ts_mod
from agentclaw.community.core.task.services.lease_sweeper import LeaseSweeper
from agentclaw.community.core.task.domain.models import NodeStatus, RunMode, SubTaskSpec
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo, InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import RecordingPanelPublisher

def _svc_with_running_node():
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())
    t = svc.create(title="t"); svc.clarify(t.id, {"summary": "s"}); svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id); svc.init_execution_graph(task)
    svc.add_node(task.id, SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.BBS), "n_execute_start")
    svc.claim_node(t.id, "n1", "bot-A", run_mode=RunMode.BBS)
    return svc, t.id, "n1"

def test_sweep_expires_past_lease():
    svc, task_id, node_id = _svc_with_running_node()
    sweeper = LeaseSweeper(svc)
    # 冻到兜底租期之后
    future = _dt.datetime(2099, 1, 1, tzinfo=_dt.timezone.utc)
    orig = ts_mod._utcnow
    ts_mod._utcnow = lambda: future
    try:
        count = sweeper.sweep_once()
    finally:
        ts_mod._utcnow = orig
    assert count == 1
    node = next(n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("release_outcome") == "lease_expired"
    assert node.assignee is None

def test_sweep_skips_unexpired_lease():
    svc, task_id, node_id = _svc_with_running_node()
    # 不动时钟(claim 刚发生,lease 在未来):sweep 应回收 0
    assert LeaseSweeper(svc).sweep_once() == 0
    node = next(n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.RUNNING
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && pytest tests/community/core/task/services/test_lease_sweeper.py -v`
Expected: FAIL（`LeaseSweeper` / `find_expired_lease_nodes` 不存在）

- [ ] **Step 3: 实现 repo 扫描**

```python
# in_memory_repos.py InMemoryTaskRepo 加
    def find_expired_lease_nodes(self, now_iso: str) -> list[tuple[str, str]]:
        """扫所有 RUNNING 且 lease_until < now 的节点 → [(task_id, node_id)](清扫器用)。"""
        from datetime import datetime
        from agentclaw.community.core.task.domain.models import NodeStatus
        try:
            now = datetime.fromisoformat(now_iso)
        except ValueError:
            return []
        expired: list[tuple[str, str]] = []
        for task in self._store.values():
            g = task.execution_graph
            if g is None:
                continue
            for n in g.nodes:
                if n.status is not NodeStatus.RUNNING:
                    continue
                lu = n.properties.get("lease_until")
                if not lu:
                    continue
                try:
                    if datetime.fromisoformat(lu) < now:
                        expired.append((task.id, n.node_id))
                except ValueError:
                    continue
        return expired
```

```python
# protocols.py: TaskRepo Protocol 加(locate TaskRepo via `from ... import TaskRepo` in task_service.py)
    def find_expired_lease_nodes(self, now_iso: str) -> list[tuple[str, str]]:
        """RUNNING 且 lease_until < now 的 (task_id, node_id) 对(清扫器用)。"""
        ...
```

> 实现者定位 `TaskRepo` Protocol 定义：`grep -rn "class TaskRepo\b\|^TaskRepo = " src/backend/src/agentclaw/community/core/task/`，在定义处加方法。InMemory 实现已在上。

- [ ] **Step 4: 实现 TaskService.sweep_expired_leases + LeaseSweeper**

```python
# task_service.py: expire_lease 之后加
    def sweep_expired_leases(self) -> int:
        """兜底租期清扫(§10.3):扫所有过期 RUNNING 节点 → expire_lease。返回收回数。"""
        pairs = self._task_repo.find_expired_lease_nodes(_utcnow().isoformat())
        for task_id, node_id in pairs:
            try:
                self.expire_lease(task_id, node_id)
            except Exception:  # noqa: BLE001 — 清扫不因单节点失败中断
                logger.exception("[Task] sweep expire_lease failed task=%s node=%s", task_id, node_id)
        return len(pairs)
```

```python
# lease_sweeper.py 新建
"""兜底租期清扫器(§10.3):周期性扫过期 RUNNING 节点收回→接力,防 bot 崩溃卡死。

机械薄封装:只调 TaskService.sweep_expired_leases()。周期触发(常驻定时器/调度)
属部署接入,不在本类。Avernet 规则:from __future__ import annotations;Optional[T];@inject。"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.protocols import TaskService


class LeaseSweeper:
    """BBS 兜底租期清扫器(无状态;状态在事件日志+图谱)。"""

    @inject
    def __init__(self, task_service: TaskService) -> None:
        self._svc = task_service

    def sweep_once(self) -> int:
        """扫一次过期租约,返回收回节点数。"""
        return self._svc.sweep_expired_leases()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_lease_sweeper.py -v`
Expected: PASS（过期→收回 lease_expired；未过期→0）

- [ ] **Step 6: 注册 DI（绑定 LeaseSweeper，便于后续调度注入）**

```python
# 定位 task DI 模块:grep -rn "bind(TaskServiceProtocol\|CommunityTaskModule" src/backend/src/agentclaw/community/di/
# 在 TaskService 绑定旁加(示例,按实际 DI 模块语法)
from agentclaw.community.core.task.services.lease_sweeper import LeaseSweeper
binder.bind(LeaseSweeper, to=LeaseSweeper(task_service_impl), scope=singleton)
```

> 周期触发（如 APScheduler / asyncio loop 每 N 秒调 `sweep_once()`）是部署接入，不在本计划代码范围（spec §7.2 评审项）；DI 绑定后调度器即可注入调用。

- [ ] **Step 7: 提交**

```bash
git add src/backend/src/agentclaw/community/plugins/community/task/in_memory_repos.py \
        src/backend/src/agentclaw/community/core/task/protocols.py \
        src/backend/src/agentclaw/community/core/task/services/task_service.py \
        src/backend/src/agentclaw/community/core/task/services/lease_sweeper.py \
        src/backend/src/agentclaw/community/di/ \
        src/backend/tests/community/core/task/services/test_lease_sweeper.py
git commit -m "feat(task): 兜底租期清扫器 LeaseSweeper + find_expired_lease_nodes(崩溃自愈接力)"
```

---

## Task 8: 内容 skill `bbs-relay-pickup`（SKILL.md + references）

**Files:**
- Create: `src/backend/specs/2026-08-03-task-cooperation-bbs/skill/bbs-relay-pickup/SKILL.md`
- Create: `src/backend/specs/2026-08-03-task-cooperation-bbs/skill/bbs-relay-pickup/references/task-api.md`
- Create: `src/backend/specs/2026-08-03-task-cooperation-bbs/skill/bbs-relay-pickup/references/judge-rubric.md`
- Create: `src/backend/specs/2026-08-03-task-cooperation-bbs/skill/bbs-relay-pickup/references/idempotency.md`

**Interfaces:**
- Consumes: 本计划 Task 1-7 的 REST（`/claim` `/release` `/events` `/nodes/{id}`）+ 现有读面
- Produces: 一个跨引擎内容 skill（SKILL.md + 3 个 references）；发布到 skill center 另系分

> 这是 agent-facing 内容（markdown），无单测；验收靠人工评审 + 集成场景测试（Task 9）驱动其指令正确性。

- [ ] **Step 1: 写 SKILL.md**

```markdown
---
name: bbs-relay-pickup
description: 被唤醒时从任务中心自主拉单、取状态与剩余事项、自判能否全/部做完、claim 抢占后执行、经回投写回结果与状态。BBS 接力:让出或崩溃后下个 bot 沿已完成轨迹续做。
allowed_tools: [exec]
---

# BBS 自主接单接力 pickup

你被唤醒去"看看有没有活"。按下面的 loop 跑一遍(一次 pass):

## 硬约束(不可绕过)
- **claim 成功之前绝不干活。** 没拿到 claim 的活不是你的;抢不到(409)就换下一个。
- 干完(完成或做不完)立即释放:完成 → `node.accepted`/`goal.verified`;做不完 → `POST /release`。不预测工期、不续租。

## 一次 pass 的 6 步
1. 取任务列表:`exec` 调 `GET $TASK_API/api/tasks?user_id=$BOT_ID&limit=50` → 逐个 `GET .../tasks/{id}/graph` 筛出"有可接续节点"的任务(图态 BBS_ACTIVE/RUNNING,存在 PENDING 或可接力 FAILED 节点)。
2. 取状态+剩余事项:`GET .../tasks/{id}`(9 态 status + 五要素)+ `.../nodes/{node_id}`(读 `targets_acceptance` vs `acceptance_result` 算剩余项;`intermediate_results` 是前序 bot 已成轨迹)。
3. 自判:看"目标+验收+当前图谱+剩余项 vs 自身能力" → `full | partial | skip`。判据见 references/judge-rubric.md。
4. 若 full/partial → **claim**:`POST .../tasks/{id}/nodes/{node_id}/claim` body `{"executor_id":"$BOT_ID","run_mode":"bbs"}`。
   - 200 → 进步骤 5(用返回的 `accept_token` 作后续 `idempotency_key` 前缀)。
   - 409 → 被别人抢了,换下一个候选。
5. 干活(原生能力)。**长活须周期 checkpoint**:每隔一段 `POST .../tasks/{id}/events` body `{"kind":"state.updated","payload":{"scope":"node_id","semantics":"append","patch":{"intermediate_results":[{...}]}}}` —— 超过兜底租期被收回时,已 checkpoint 部分不丢,下个 bot 续做。
6. 写回(经 `POST .../tasks/{id}/events`,payload 不必显式带 run_mode,系统按 bbs 处理;若需显式带 `{"run_mode":"bbs"}` 放 payload):
   - 完成(节点级):`{"kind":"node.accepted","payload":{"node_id":"...","verifier":"$BOT_ID"}}` → 节点 DONE。
   - 做不完(立即让出):先 commit 已完成中间结果(state.updated append),再 `POST .../tasks/{id}/nodes/{node_id}/release` body `{"executor_id":"$BOT_ID"}` → 立即 FAILED(handoff),下个 bot 接力。
   - 任务全完:`{"kind":"goal.verified","payload":{"verifier":"$BOT_ID","verdict":"pass"}}` → 图 DONE。

claim 输了、无可做、或本次 pass 结束 → 等下次唤醒。

环境变量:`$TASK_API`(任务中心 base URL)、`$BOT_ID`(本 bot 标识)。

详见 references/。
```

- [ ] **Step 2: 写 references/task-api.md**

```markdown
# 任务中心 REST 速查(BBS 接单用)

base = `$TASK_API`;所有调用带 `--no-buffer -sS` + `--json` 解析。

## 读面(复用)
- `GET /api/tasks?user_id=$BOT_ID&limit=50` — 任务列表
- `GET /api/tasks/{id}` — 详情(status 9 态 + spec 五要素)
- `GET /api/tasks/{id}/graph` — 图谱(status + nodes + edges)
- `GET /api/tasks/{id}/nodes/{node_id}` — 节点详情(含 `intermediate_results`/`gap_records` BBS 直出;`targets_acceptance` vs `acceptance_result` 算剩余项)
- `GET /api/tasks/{id}/history?after_seq=N` — 事件日志增量

## 写回(复用回投通道)
- `POST /api/tasks/{id}/events` body `{"kind":<EventKind>,"payload":{...}}`
  - `state.updated`(中间结果/checkpoint):`{"scope":"node_id","semantics":"append","patch":{"intermediate_results":[...]}}`
  - `node.accepted`(节点完成):`{"node_id":"...","verifier":"$BOT_ID"}`
  - `goal.verified`(任务完成):`{"verifier":"$BOT_ID","verdict":"pass"}`
  - payload 可附 `run_mode:"bbs"`。

## 抢占/让出(新增)
- `POST /api/tasks/{id}/nodes/{node_id}/claim` body `{"executor_id":"$BOT_ID","run_mode":"bbs"}` → 200 `ClaimResponse{node_id,executor_id,run_mode,accept_token,lease_until}` / 409(被占)/ 404。
- `POST /api/tasks/{id}/nodes/{node_id}/release` body `{"executor_id":"$BOT_ID"}` → 200 `ReleaseResponse{node_id,status,outcome:"handoff"}` / 403(非 assignee)/ 409。

## 事件 kind 白名单(非法 kind 被 on_event 静默 no-op,必命中下列)
task.created / task.clarified / node.dispatched / node.running / node.accepted / node.rejected / node.failed / node.released / goal.verified / goal.rejected / state.updated / loop.rerouted / execution.attempted / node.added / edge.added / node.aggregated / node.hang / bbs.confirmed / hang.cancelled / task.cancelled / task.plan_requested
```

- [ ] **Step 3: 写 references/judge-rubric.md**

```markdown
# 自判判据(full / partial / skip)

输入:目标(`spec.goal.objective`)、验收标准(`targets_acceptance` / `goal.acceptances`)、当前图谱已完成轨迹(`intermediate_results`、节点的 `acceptance_result`)、剩余项(验收未达 + 未 DONE 的 PENDING/可接力 FAILED 节点)。

- **full**:剩余项我全能做,且有把握过验收 → claim,做到 `node.accepted`/`goal.verified`。
- **partial**:只能做一部分(能力/时段不够) → claim,做能做的并周期 checkpoint;做到不再前进时 `state.updated` append + `release` 让出,把更新后剩余项留给下一个 bot。**不把 partial 当失败**。
- **skip**:剩余项我完全做不了/超出能力 → 不 claim,换下一个候选。

判定靠你(LLM)对"任务内容 vs 自身能力"的判断,没有确定性代码兜。拿不准时倾向 partial(做一点+让出)而非 skip,保证广场进度推进。
```

- [ ] **Step 4: 写 references/idempotency.md**

```markdown
# 幂等与接力约定

## 抢占级(防多 bot 同做一件事)
- 干活前必须 `POST /claim`。服务端 CAS(源态→RUNNING)保证只有一个 bot 赢;输者 409,换下一个。
- 你拿不到 claim 以外的写口改 assignee/节点态 → 无法绕过抢占。

## 接力级(多 bot 续做同一节点)
- 让出(release)或崩溃后,节点回 FAILED(可接力,不升人工);下个 bot `claim` 同节点(FAILED→RUNNING),经 `GET /nodes/{id}` 看到 `intermediate_results` 续做,**不重做已完成部分**。
- 崩溃:你挂了没人 release → 系统兜底租期到期清扫器自动收回(`outcome=lease_expired`)→ 已 checkpoint 的中间结果保留 → 下个 bot 接力。

## 写回防重放
- `state.updated`/`node.accepted`/`goal.verified` 的 payload 尽量带 `idempotency_key`(claim 返回的 `accept_token` + 步骤序),重放不双写。

## 长活 checkpoint
- 干活超过兜底租期会被收回;故长活每隔一段 `state.updated` append 中间结果,被收回后下个 bot 从 checkpoint 续做(长活=分段接力)。
```

- [ ] **Step 5: 提交**

```bash
git add src/backend/specs/2026-08-03-task-cooperation-bbs/skill/
git commit -m "feat(skill): bbs-relay-pickup 内容 skill(SKILL.md + references,跨引擎 BBS 自主接单)"
```

> 发布到 skill center（`local://` 上传 / `git://` 同步 + 激活到 bot 的 active skills 目录）是部署步骤,不在本计划代码范围。

---

## Task 9: 集成场景测试（race / handoff / crash-接力）

**Files:**
- Create: `src/backend/tests/community/core/task/services/test_bbs_pickup_integration.py`

**Interfaces:**
- Consumes: Task 1-7 全部（claim/release/expire/sweep/事件/lease）
- Produces: 端到端验收:多 bot 抢占恰一赢、release 立即接力、崩溃(过期)接力(轨迹保留)

- [ ] **Step 1: 写集成测试**

```python
# test_bbs_pickup_integration.py 新建
from __future__ import annotations
import datetime as _dt
import pytest
from agentclaw.community.core.task.services import TaskService, task_service as ts_mod
from agentclaw.community.core.task.services.lease_sweeper import LeaseSweeper
from agentclaw.community.core.task.domain.models import NodeStatus, RunMode, SubTaskSpec
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo, InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import RecordingPanelPublisher
from agentclaw.community.core.task.domain.state_machine import IllegalTransitionError


def _svc_with_node(tmp_id="n1"):
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())
    t = svc.create(title="t"); svc.clarify(t.id, {"summary": "s"}); svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id); svc.init_execution_graph(task)
    svc.add_node(task.id, SubTaskSpec(node_id=tmp_id, spec="a", run_mode=RunMode.BBS), "n_execute_start")
    return svc, t.id, tmp_id


def test_race_only_one_wins():
    svc, task_id, node_id = _svc_with_node()
    r1 = svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    assert r1 is not None and r1.executor_id == "bot-A"
    with pytest.raises(IllegalTransitionError):
        svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)  # CAS 输


def test_handoff_immediate_relay_with_trajectory():
    svc, task_id, node_id = _svc_with_node()
    svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    # bot-A checkpoint 30%
    svc.on_event({"task_id": task_id, "kind": "state.updated",
                  "payload": {"scope": node_id, "semantics": "append",
                              "patch": {"intermediate_results": [{"step": 1, "pct": 30}]}}})
    svc.release_node(task_id, node_id, "bot-A")  # 立即让出
    # bot-B 接力,看到 30% 轨迹
    svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)
    detail = svc.get_node_detail(task_id, node_id)
    assert any(r.get("pct") == 30 for r in detail["intermediate_results"])


def test_crash_relay_after_lease_expiry():
    svc, task_id, node_id = _svc_with_node()
    svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    svc.on_event({"task_id": task_id, "kind": "state.updated",
                  "payload": {"scope": node_id, "semantics": "append",
                              "patch": {"intermediate_results": [{"step": 1, "pct": 50}]}}})
    # bot-A 崩溃(不 release) → 冻到租期之后清扫
    future = _dt.datetime(2099, 1, 1, tzinfo=_dt.timezone.utc)
    orig = ts_mod._utcnow; ts_mod._utcnow = lambda: future
    try:
        assert LeaseSweeper(svc).sweep_once() == 1
    finally:
        ts_mod._utcnow = orig
    node = next(n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id)
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("release_outcome") == "lease_expired"
    # bot-B 接力,50% 轨迹保留
    svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)
    detail = svc.get_node_detail(task_id, node_id)
    assert any(r.get("pct") == 50 for r in detail["intermediate_results"])
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd src/backend && pytest tests/community/core/task/services/test_bbs_pickup_integration.py -v`
Expected: PASS（3 场景全绿）

- [ ] **Step 3: 全 task 域回归**

Run: `cd src/backend && pytest tests/community/core/task/ tests/community/adapters/http/task/ tests/community/architecture/test_task_service_api_conformance.py -v`
Expected: PASS（新功能 + 现有用例不破）

- [ ] **Step 4: 提交**

```bash
git add src/backend/tests/community/core/task/services/test_bbs_pickup_integration.py
git commit -m "test(task): BBS 接单集成场景(race 恰一赢/handoff 立即接力/crash 过期接力+轨迹保留)"
```

---

## Self-Review（plan 作者自查）

**1. Spec 覆盖**：逐条对照 spec FR：
- FR-PICK-01 列表 → Task 8 SKILL.md 步骤1（读面复用，无新路由，符合"复用"）✅
- FR-PICK-02 状态+剩余 → Task 8 步骤2 + Task 6 SubtaskState 直出 ✅
- FR-PICK-03 自判 → Task 8 + judge-rubric ✅
- FR-PICK-04 执行/checkpoint → Task 8 步骤5 ✅
- FR-PICK-05 写回 → Task 8 步骤6（复用 /events）✅
- FR-PICK-06 pass 边界 → Task 8 SKILL.md ✅
- FR-IDEM-01 CAS → Task 2/5（claim 路由包 claim_node CAS）✅
- FR-IDEM-02 兜底租期 → Task 2（BBS_LEASE_FALLBACK_SECONDS + lease_until）✅
- FR-IDEM-03 接力 → Task 1 fold + Task 3 release/expire + Task 9 集成 ✅
- FR-IDEM-04 partial 立即 release → Task 3/5/8 ✅
- FR-IDEM-05 崩溃 → Task 7 sweeper + Task 9 ✅
- FR-EXT-01 claim 路由 → Task 5 ✅
- FR-EXT-02 release 路由 → Task 3/5 ✅
- FR-EXT-03 409 → Task 4 ✅
- FR-EXT-04 清扫器 → Task 7 ✅
- FR-EXT-05 SubtaskState 直出 → Task 6 ✅
- FR-EXT-06 可接力集 {PENDING, FAILED} → claim_node 源态＝{PENDING,FAILED,HUNG} 现成(require_node_transition)，Task 2 注释 + Task 9 验证 FAILED→RUNNING ✅（HUNG 不纳入主路径，spec 一致）
- FR-SKILL-01~04 → Task 8 ✅
- AC-03/04/05/06/07/08 → Task 1-9 覆盖 ✅

**2. Placeholder 扫描**：Task 6 Step 3 第一段代码块标注"更干净做法"为意图说明，最终落的是第二段 `get_node_detail` merge 代码（无 TBD）。Task 7 Step 3/6 用 `grep` 指引定位 `TaskRepo` Protocol 与 DI 模块定义（发现的写法，非占位）。无 "TODO/实现 later/add error handling" 类占位。

**3. 类型一致性**：
- `claim_node(task_id, node_id, executor_id, run_mode: Optional[RunMode] = None)` —— Task 2 定义、Task 5 路由调用、Task 7/9 测试调用，签名一致 ✅
- `DispatchResult.lease_until: Optional[str]` —— Task 2 定义、Task 5 ClaimResponse 读取 ✅
- `release_node(task_id, node_id, executor_id)` —— Task 3 定义、Task 5 调用一致 ✅
- `expire_lease(task_id, node_id)` —— Task 3 定义、Task 7 sweep 调用一致 ✅
- `find_expired_lease_nodes(now_iso) -> list[tuple[str,str]]` —— Task 7 repo/Protocol 一致 ✅
- `EventKind.NODE_RELEASED = "node.released"`、payload `outcome` —— Task 1 定义、Task 3 `_emit` 传 `outcome=`、Task 8 references 一致 ✅
- `Forbidden`（非 assignee）→ 403、`IllegalTransitionError` → 409 —— Task 3 raise Forbidden、Task 4 handler、Task 5 测试断言 一致 ✅

**4. 范围**：单一实现计划可驱动落地；skill 发布、定时调度接入、鉴权、ORM 仓库的 `find_expired_lease_nodes`（prod 扫描器数据源，InMemory 已覆盖逻辑）列为部署/后续项，不在本计划代码范围（已在各 Task 注明）。

---

## Execution Handoff

Plan complete and saved to `src/backend/specs/2026-08-03-task-cooperation-bbs/plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个 Task 派一个 fresh subagent，Task 间两段评审，迭代快。
**2. Inline Execution** — 本会话内按 executing-plans 批量执行，带 checkpoint 评审。

Which approach?

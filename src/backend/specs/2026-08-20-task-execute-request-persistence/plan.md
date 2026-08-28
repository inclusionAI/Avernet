# Task `execute` Request Object + `task_info` Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch `TaskService.execute` from the internal `TaskInfo` to a flattened `TaskInfoRequest`, generate `task_id` server-side, and persist a `task_info` row before `initialize_graph`.

**Architecture:** New external contract (`TaskInfoRequest` + `TaskSourceType`/`TaskType` enums) in the domain layer (`core/` may not import `api/`, so the contract lives where `core/` can reach it). `execute` generates `task_id` via an injected `task_id_provider` (default `uuid4`), converts the request to the internal `TaskInfo`, inserts a `TaskInfoRecord` (status `PENDING`) via the already-bound `TaskInfoRepositoryProtocol`, then runs the existing in-memory graph flow unchanged. The two execute HTTP routes switch to a new `TaskInfoRequestDTO`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, `injector` DI, pydantic, FastAPI, pytest (project venv `src/backend/.venv/bin/python`).

**Spec:** `src/backend/specs/2026-08-20-task-execute-request-persistence/spec.md` (decisions D1–D4 locked there).

## Global Constraints

- **`task_id` server-generated `uuid4`** in production via `TaskService.task_id_provider` (default `lambda: str(uuid.uuid4())`); tests inject a deterministic provider. Used as `task_info` PK + root `node_id` + returned. **Breaking:** clients no longer send `task_id`.
- **Persisted `task_spec` = domain shape** via a new `TaskSpec.to_dict()` (`metadata{task_id,title,instruction}`, `goal.acceptances[{id,description}]`).
- **`TaskInfo.owner_bot_id = request.owner_bot_id`**; `source_type = request.source_type`. `TaskInfo` shape is otherwise unchanged.
- **Insert failure → `TaskOpResult(success=False, error=...)`**, skip `initialize_graph`.
- **Domain is zero-transport-dep** (`core/task/domain/`): dataclasses/`StrEnum`/stdlib only. `TaskInfoRequest` + enums live there because `core/` may not import `api/`. `api/` and `adapters/http/` import from there.
- **`core/` may not import `api/`.** `TaskServiceProtocol` (in `api/`) imports `TaskInfoRequest` from `core.task.domain.requests` at runtime (the existing protocol already imports domain types at runtime — mirroring that).
- **Repo methods are sync** (`orm_session()`); `execute` is async and calls `insert` inline (mirrors the existing inline sync `initialize_graph`).
- **`TaskInfoRepositoryProtocol`** is already bound by `TaskPersistenceModule` (base DI list) — inject it into the `task_module.task_service` provider.
- **Removable:** only `TaskInfoDTO` + `task_info_from_dto` are execute-only. `TaskSpecDTO`/`MetadataDTO`/`ContextDTO`/`GoalDTO`/`AcceptanceCriteriaDTO`/`task_spec_from_dto` stay (used by `graph_to_dto` + `BbsAttachDTO`).
- **Translator imports stay function-level** (mirror the existing `task_info_from_dto`/`task_spec_from_dto` pattern) so they don't trip module-level boundary checks.
- **Boundary test risk:** adding module-level imports to `core/task/task_center/task_service.py` (`core.repository.protocols.task`, `core.task.repository.types`, `core.task.domain.requests`) and `api/task/task_service.py` (`core.task.domain.requests`) may trip `test_module_boundaries`. If it does, add the missing entries to that package's `internal_dependencies` in its `README.md` (mirror the `plugins/local` fix already in the tree).
- **SAST (antflake):** single-line `def` tolerated; `class`/`if`/`;` multi-line; side-effect imports `# noqa: F401`. Run tests with `src/backend/.venv/bin/python -m pytest`.
- Every new file ends with a trailing newline.

---

## File Structure

```
core/task/domain/
├── models.py            (edit) +TaskSourceType, +TaskType StrEnums; +to_dict() on
│                                 Metadata/Context/AcceptanceCriteria/Goal/TaskSpec
└── requests.py          (new)  TaskInfoRequest + Request{Metadata,Context,Acceptance,Goal,TaskSpec};
                                 TaskInfoRequest.to_task_info(task_id) -> TaskInfo

core/task/task_center/task_service.py (edit) +inject TaskInfoRepositoryProtocol + task_id_provider;
                                       execute(request: TaskInfoRequest) persist-first; run_execute
api/task/task_service.py               (edit) TaskServiceProtocol.execute(request: TaskInfoRequest)
di/modules/task_module.py              (edit) task_service provider @injects TaskInfoRepositoryProtocol

adapters/http/task/schemas.py          (edit) +TaskInfoRequestDTO + nested DTOs + ExecutionConfigDTO
                                       + task_info_request_from_dto; remove TaskInfoDTO + task_info_from_dto
adapters/http/openapi_v1/task/router.py (edit) execute_task → TaskInfoRequestDTO
adapters/http/task/router.py            (edit) execute_task_internal → TaskInfoRequestDTO

tests/community/core/task/domain/test_requests.py               (new)  to_task_info + to_dict unit tests
tests/community/core/task/task_center/test_execute_persist.py   (new)  execute persists task_info row
tests/community/core/task/task_center/test_task_service.py      (edit) build TaskInfoRequest + provider
tests/community/core/task/e2e/test_e2e.py                       (edit) build TaskInfoRequest + provider
```

---

### Task 1: Domain — enums, `TaskInfoRequest`, `to_task_info`, `TaskSpec.to_dict()`

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/domain/models.py`
- Create: `src/backend/src/agentclaw/community/core/task/domain/requests.py`
- Test: `src/backend/tests/community/core/task/domain/test_requests.py`

**Interfaces:**
- Consumes: existing domain types (`TaskInfo`, `TaskSpec`, `Metadata`, `Context`, `Goal`, `AcceptanceCriteria`) in `models.py`.
- Produces (used by Task 2): `TaskSourceType`, `TaskType` (in `models.py`); `TaskInfoRequest` + nested `Request*` + `TaskInfoRequest.to_task_info(task_id: str) -> TaskInfo` (in `requests.py`); `TaskSpec.to_dict() -> dict` (in `models.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/community/core/task/domain/test_requests.py`:

```python
from agentclaw.community.core.task.domain.models import TaskSourceType, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg", extend_props={"k": 1}),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="acc-text")]),
        ),
        source_type=TaskSourceType.CoopGroup,
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config={"task_type": TaskType.Workflow, "workflow_id": "wf-1"},
    )


def test_to_task_info_maps_fields_and_acceptance_to_description():
    ti = _request().to_task_info("tid-123")
    m = ti.task_spec.metadata
    assert m.task_id == "tid-123"
    assert m.title == "T" and m.instruction == "do"
    assert ti.task_spec.context.background == "bg"
    assert ti.task_spec.context.extend_props == {"k": 1}
    assert ti.task_spec.goal.objective == "o"
    assert ti.task_spec.goal.acceptances[0].id == "ac1"
    assert ti.task_spec.goal.acceptances[0].description == "acc-text"  # acceptance → description
    assert ti.source_type == "coop_group"          # source_type.value
    assert ti.owner_bot_id == "B1"                    # owner_bot_id (D3)
    assert ti.execution_config["workflow_id"] == "wf-1"


def test_task_spec_to_dict_is_domain_shape():
    ti = _request().to_task_info("tid-123")
    d = ti.task_spec.to_dict()
    assert d["metadata"] == {"task_id": "tid-123", "title": "T", "instruction": "do"}
    assert d["context"] == {"background": "bg", "extend_props": {"k": 1}}
    assert d["goal"]["objective"] == "o"
    assert d["goal"]["acceptances"] == [{"id": "ac1", "description": "acc-text"}]


def test_enums_values():
    assert {e.value for e in TaskSourceType} == {"bot", "coop_group", "api"}
    assert {e.value for e in TaskType} == {"yaml", "workflow", "dynamic"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/domain/test_requests.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentclaw.community.core.task.domain.requests'`.

- [ ] **Step 3: Add enums + `to_dict()` to `models.py`**

In `core/task/domain/models.py`, add the two enums beside `RelationType` (after the `NodeAction` class):

```python
class TaskSourceType(StrEnum):
    """触发渠道类型(bot / 协作群 / 开放 API)。"""

    BOT = "bot"
    COOP_GROUP = "coop_group"
    API = "api"


class TaskType(StrEnum):
    """任务类型(yaml / workflow / dynamic)。"""

    YAML = "yaml"
    WORKFLOW = "workflow"
    DYNAMIC = "dynamic"
```

Add `to_dict()` methods (domain shape — with `task_id` and `description`, NOT the request shape):

```python
# on Metadata
def to_dict(self) -> dict[str, Any]:
    return {"task_id": self.task_id, "title": self.title, "instruction": self.instruction}

# on Context
def to_dict(self) -> dict[str, Any]:
    return {"background": self.background, "extend_props": dict(self.extend_props)}

# on AcceptanceCriteria
def to_dict(self) -> dict[str, Any]:
    return {"id": self.id, "description": self.description}

# on Goal
def to_dict(self) -> dict[str, Any]:
    return {"objective": self.objective, "acceptances": [a.to_dict() for a in self.acceptances]}

# on TaskSpec
def to_dict(self) -> dict[str, Any]:
    return {
        "metadata": self.metadata.to_dict(),
        "context": self.context.to_dict(),
        "goal": self.goal.to_dict(),
    }
```

(`Any` is already imported in `models.py`; `dict[str, Any]` is valid in 3.12.)

- [ ] **Step 4: Create `core/task/domain/requests.py`**

```python
"""External execute-request contract for the collaboration-task module.

Flattened, externally-facing request object (aligns with the open execute
contract). Lives in the domain layer because ``core/`` may not import ``api/``;
the service protocol (``api/``) and the HTTP DTO (``adapters/http/``) import
from here. Pure dataclass — zero transport/framework deps, per the domain rule.

``task_id`` is NOT part of the request: the service generates it (via an
injected ``task_id_provider``) and passes it into :meth:`to_task_info`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    TaskInfo,
    TaskSpec,
)


@dataclass(frozen=True)
class RequestMetadata:
    title: str
    instruction: str


@dataclass(frozen=True)
class RequestContext:
    background: str
    extend_props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestAcceptance:
    id: str
    acceptance: str


@dataclass(frozen=True)
class RequestGoal:
    objective: str
    acceptances: list[RequestAcceptance] = field(default_factory=list)


@dataclass(frozen=True)
class RequestTaskSpec:
    metadata: RequestMetadata
    context: RequestContext
    goal: RequestGoal


@dataclass(frozen=True)
class TaskInfoRequest:
    task_spec: RequestTaskSpec
    source_type: "TaskSourceType"  # noqa: F821 — defined in models.py
    owner_user_id: str
    owner_bot_id: str
    execution_config: dict[str, Any] = field(default_factory=dict)

    def to_task_info(self, task_id: str) -> TaskInfo:
        """Map the request onto the internal ``TaskInfo`` (server-supplied ``task_id``).

        ``acceptance`` → domain ``AcceptanceCriteria.description``;
        ``source_type`` = ``source_type``; ``owner_bot_id`` = ``owner_bot_id`` (D3).
        """
        from agentclaw.community.core.task.domain.models import TaskSourceType  # noqa: F811

        return TaskInfo(
            task_spec=TaskSpec(
                metadata=Metadata(task_id=task_id,
                                  title=self.task_spec.metadata.title,
                                  instruction=self.task_spec.metadata.instruction),
                context=Context(background=self.task_spec.context.background,
                                extend_props=dict(self.task_spec.context.extend_props)),
                goal=Goal(objective=self.task_spec.goal.objective,
                          acceptances=[AcceptanceCriteria(id=a.id, description=a.acceptance)
                                       for a in self.task_spec.goal.acceptances]),
            ),
            source_type=self.source_type.value,
            owner_bot_id=self.owner_bot_id,
            execution_config=dict(self.execution_config),
        )
```

(The annotation `"TaskSourceType"` is a forward string; the runtime import inside `to_task_info` is not strictly needed since the caller passes a real `TaskSourceType` — drop the inner import if it causes a lint complaint. `source_type.value` works because `TaskSourceType` is a `StrEnum`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/domain/test_requests.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/domain/models.py \
        src/backend/src/agentclaw/community/core/task/domain/requests.py \
        src/backend/tests/community/core/task/domain/test_requests.py
git commit -m "feat(task): add TaskInfoRequest contract + TaskSpec.to_dict in domain

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Service vertical — `execute(request)` + persist + protocol + DI + HTTP DTO/routers + tests

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/task/task_center/task_service.py`
- Modify: `src/backend/src/agentclaw/community/api/task/task_service.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/task_module.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/task/schemas.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/task/router.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/task/router.py`
- Modify: `src/backend/tests/community/core/task/task_center/test_task_service.py`
- Modify: `src/backend/tests/community/core/task/e2e/test_e2e.py`
- Test: `src/backend/tests/community/core/task/task_center/test_execute_persist.py` (new)

**Interfaces:**
- Consumes (Task 1): `TaskInfoRequest.to_task_info(task_id)`, `TaskSourceType`/`TaskType`, `TaskSpec.to_dict()`. Also the existing `TaskInfoRepositoryProtocol.insert(record)` / `TaskInfoRecord` (from the persistence layer already in the tree).
- Produces: `TaskService.execute(request: TaskInfoRequest) -> TaskOpResult` (persist-first); `TaskServiceProtocol.execute`签名一致; routers pass `TaskInfoRequestDTO`.

**Why one task:** `execute`'s signature, the protocol, the two HTTP routes, the DTO, and the unit tests that call `execute` all move together — the execute endpoint is only consistent when all of them change in lockstep.

- [ ] **Step 1: Write the failing persist test**

Create `tests/community/core/task/task_center/test_execute_persist.py`:

```python
"""execute persists a task_info row (status PENDING, domain-shape task_spec)
before initialize_graph, and returns the server-generated task_id."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
import agentclaw.community.core.task.repository.models  # noqa: F401  register task_info table
import agentclaw.community.core.task_queue.repository.models  # noqa: F401  idx_status sibling table
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import Status, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


class _SqliteDB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return TaskInfoRepository(_SqliteDB(eng))


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="acc")]),
        ),
        source_type="api",
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config={"task_type": TaskType.Dynamic},
    )


def _service(repo, task_id="persist-tid"):
    # task_id_provider gives a deterministic id; TaskService defaults to uuid4 in prod.
    return TaskService(TaskGraphService(), task_info_repo=repo, task_id_provider=lambda: task_id)


def test_execute_persists_task_info_row(repo):
    facade = _service(repo, task_id="persist-tid")
    result = facade.execute(_request())
    import asyncio
    asyncio.new_event_loop().run_until_complete(facade.drain_background())  # settle bg frame
    assert result.success is True
    assert result.task_id == "persist-tid"
    row = repo.get("persist-tid")
    assert row is not None
    assert row.status is Status.PENDING
    assert row.source_type == "api"
    assert row.owner_user_id == "U1" and row.owner_bot_id == "B1"
    assert row.task_spec["metadata"]["task_id"] == "persist-tid"
    assert row.task_spec["goal"]["acceptances"] == [{"id": "ac1", "description": "acc"}]


def test_execute_persist_failure_returns_failure(repo):
    # Pre-insert the same task_id so the execute insert hits uk_task_id.
    from agentclaw.community.core.task.repository.types import TaskInfoRecord
    repo.insert(TaskInfoRecord(
        id=0, task_id="persist-tid", source_type="api", owner_user_id="U1", owner_bot_id="B1",
        execution_config={"task_type": "dynamic"}, task_spec={"metadata": {"task_id": "persist-tid"}},
        status=Status.PENDING,
    ))
    facade = _service(repo, task_id="persist-tid")
    result = facade.execute(_request())
    assert result.success is False
    assert result.task_id == "persist-tid"
    assert result.error is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/core/task/task_center/test_execute_persist.py -v`
Expected: FAIL — `TypeError: TaskService.__init__() got an unexpected keyword argument 'task_info_repo'` (and `execute` still expects `TaskInfo`).

- [ ] **Step 3: Modify `TaskService` (`core/task/task_center/task_service.py`)**

Adjust the imports (add; drop now-unused `TaskInfo`):

```python
import asyncio
import logging
import uuid
from typing import Callable

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, NodeOpResult, Status, TaskExecutionGraph, TaskNode, TaskNodePatch,
    TaskOpResult, TaskSpec, TaskSummary,
)
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)
```

`__init__` gains two keyword params (DI supplies `task_info_repo` in prod; tests may pass `None` to skip persist):

```python
    def __init__(self, graph, harness=None, *, bot=None, bcs=None, discover=None,
                 bcs_identity=None, task_info_repo: TaskInfoRepositoryProtocol | None = None,
                 task_id_provider: Callable[[], str] | None = None) -> None:
        self._graph = graph
        self._harness = harness
        self._bcs_identity = bcs_identity
        self._task_info_repo = task_info_repo
        self._task_id_provider = task_id_provider or (lambda: str(uuid.uuid4()))
        self._engine = self._build_engine(bot=bot, bcs=bcs, discover=discover)
        self._bg_tasks: set[asyncio.Task] = set()
        self._callback = TaskLoopCallback(CallbackAdapter(), self._engine)
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)
            import threading as _t
            _t.Thread(target=self._harness.run_poll_loop, daemon=True, name="task-harness").start()
            logger.info("[task-service] harness 旁路巡检线程已启动(SLA 超时/FAILED 重派/PENDING 派发超时重搜推)")
```

Replace `execute` (persist-first):

```python
    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        """提交执行任务:生成 task_id → 持久化 task_info(PENDING)→ initialize_graph →
        后台 on_execute 首帧推进,立即返回 TaskOpResult(含 task_id + run_id)。

        持久化失败(IntegrityError,如 task_id 冲突)→ 返回 success=False,不建图。"""
        task_id = self._task_id_provider()
        task_info = request.to_task_info(task_id)
        if self._task_info_repo is not None:
            record = TaskInfoRecord(
                id=0,
                task_id=task_id,
                source_type=request.source_type.value,
                owner_user_id=request.owner_user_id,
                owner_bot_id=request.owner_bot_id,
                execution_config=dict(request.execution_config),
                task_spec=task_info.task_spec.to_dict(),
                status=Status.PENDING,
            )
            try:
                self._task_info_repo.insert(record)
            except IntegrityError as exc:
                return TaskOpResult(task_id=task_id, success=False, error=f"persist failed: {exc}")
        graph = self._graph.initialize_graph(task_info)
        logger.info("[execute] task=%s source=%s title=%s → initialize(run_id=%s)+on_execute(后台推进)",
                    task_id, task_info.owner_bot_id,
                    task_info.task_spec.metadata.title, graph.run_id)
        if self._harness is not None:
            self._harness.register(task_id)
        bg = asyncio.create_task(self._engine.on_execute(task_id))
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)
```

`run_execute` helper:

```python
def run_execute(facade: TaskService, request: TaskInfoRequest) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(request))
```

- [ ] **Step 4: Update the protocol (`api/task/task_service.py`)**

Change the import (drop `TaskInfo`, add `TaskInfoRequest`) and the `execute` signature:

```python
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    NodeOpResult,
    TaskExecutionGraph,
    TaskNode,
    TaskOpResult,
    TaskSpec,
    TaskSummary,
)
```

```python
    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        """提交执行任务:持久化 task_info(PENDING)→ initialize_graph(根 PENDING)→ 编排核 on_execute
        首帧推进。task_id 服务端生成(uuid4)。返回 TaskOpResult(含 task_id + run_id)。"""
        ...
```

- [ ] **Step 5: Wire DI (`di/modules/task_module.py`)**

In the `task_service` `@provider` (around line 47), add an injected `task_info_repo` param and forward it. Add the import at the top of the file:

```python
from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
```

Then the provider:

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
    ) -> TaskService:
        bot, bcs = self._resolve_ports()
        discover_port = self._resolve_discover(default=discover, bot_public=bot_public)
        bcs_identity = None
        if bcs is not None:
            from agentclaw.community.core.task.task_runner.integration.bcs_bot_identity_resolver import (
                BotServiceBcsBotIdentityResolver,
            )
            bcs_identity = BotServiceBcsBotIdentityResolver(injector.get(BotServiceProtocol))
        harness = TaskHarness(graph)
        return TaskService(
            graph, harness=harness, bot=bot, bcs=bcs, discover=discover_port,
            bcs_identity=bcs_identity, task_info_repo=task_info_repo,
        )
```

(`task_id_provider` is left default = `uuid4` in prod — do not pass it.)

- [ ] **Step 6: HTTP schemas (`adapters/http/task/schemas.py`)**

Add the new DTO family + translator; remove `TaskInfoDTO` + `task_info_from_dto`.

Add to the imports at the top:

```python
from pydantic import BaseModel, ConfigDict, Field
```

Add the new request DTOs (after the existing `TaskSpecDTO` block, before `BbsClaimDTO`):

```python
class RequestMetadataDTO(BaseModel):
    title: str = Field("", description="任务标题")
    instruction: str = Field("", description="核心执行指令(Prompt)")


class RequestAcceptanceDTO(BaseModel):
    id: str = Field(..., description="验收标准唯一标识")
    acceptance: str = Field("", description="验收标准具体描述")


class RequestGoalDTO(BaseModel):
    objective: str = Field("", description="任务目标描述")
    acceptances: list[RequestAcceptanceDTO] = Field(default_factory=list, description="验收标准列表")


class RequestTaskSpecDTO(BaseModel):
    metadata: RequestMetadataDTO
    context: ContextDTO = Field(default_factory=ContextDTO)
    goal: RequestGoalDTO = Field(default_factory=RequestGoalDTO)


class ExecutionConfigDTO(BaseModel):
    """执行配置(task_type 必填;yaml/workflow_id 可选;其余键允许透传)。"""
    model_config = ConfigDict(extra="allow")
    task_type: Literal["yaml", "workflow", "dynamic"] = Field(..., description="任务类型")
    yaml: str | dict[str, Any] | None = Field(None, description="yaml 内联或引用")
    workflow_id: str | None = Field(None, description="workflow id")


class TaskInfoRequestDTO(BaseModel):
    """POST .../collaboration/tasks/execute 请求体(对外扁平契约;task_id 服务端生成)。"""
    task_spec: RequestTaskSpecDTO
    source_type: Literal["bot", "coop_group", "api"] = Field("bot", description="触发渠道类型")
    owner_user_id: str = Field(..., description="userId")
    owner_bot_id: str = Field(..., description="botId")
    execution_config: ExecutionConfigDTO = Field(
        default_factory=lambda: ExecutionConfigDTO(task_type="dynamic"),
        description="执行配置(task_type/yaml/workflow_id + 透传键)",
    )
```

Add the translator (function-level domain imports, mirroring `task_spec_from_dto`):

```python
def task_info_request_from_dto(dto: TaskInfoRequestDTO):
    """TaskInfoRequestDTO → domain TaskInfoRequest(Rule 22:adapter 唯一写翻译位)。"""
    from agentclaw.community.core.task.domain.models import TaskSourceType, TaskType
    from agentclaw.community.core.task.domain.requests import (
        RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
        RequestTaskSpec, TaskInfoRequest,
    )
    ec = dto.execution_config
    execution_config: dict[str, Any] = dict(ec.model_dump(exclude_none=True))
    execution_config["task_type"] = TaskType(ec.task_type)
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title=dto.task_spec.metadata.title,
                                     instruction=dto.task_spec.metadata.instruction),
            context=RequestContext(background=dto.task_spec.context.background,
                                   extend_props=dict(dto.task_spec.context.extend_props)),
            goal=RequestGoal(objective=dto.task_spec.goal.objective,
                             acceptances=[RequestAcceptance(id=a.id, acceptance=a.acceptance)
                                          for a in dto.task_spec.goal.acceptances]),
        ),
        source_type=TaskSourceType(dto.source_type),
        owner_user_id=dto.owner_user_id,
        owner_bot_id=dto.owner_bot_id,
        execution_config=execution_config,
    )
```

Delete the `TaskInfoDTO` class and the `task_info_from_dto` function.

- [ ] **Step 7: Update the two routers**

`adapters/http/openapi_v1/task/router.py` — in the import block from `schemas`, replace `TaskInfoDTO` and `task_info_from_dto` with `TaskInfoRequestDTO` and `task_info_request_from_dto`:

```python
from agentclaw.community.adapters.http.task.schemas import (
    TaskExecutionGraphDTO,
    TaskInfoRequestDTO,
    TaskOpResultDTO,
    TaskSummaryDTO,
    graph_to_dto,
    op_result_to_dto,
    summary_to_dto,
    task_info_request_from_dto,
)
```

`execute_task`:

```python
@router.post("/execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def execute_task(
    body: TaskInfoRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务。task_id 服务端生成;持久化 task_info(PENDING)→ initialize_graph → on_execute 首帧。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ ``@envelope_errors`` 映射 409。"""
    task_request = task_info_request_from_dto(body)
    result = await service.execute(task_request)
    return envelope(op_result_to_dto(result), request)
```

`adapters/http/task/router.py` — in its schemas import, replace `TaskInfoDTO` and `task_info_from_dto` with `TaskInfoRequestDTO` and `task_info_request_from_dto` (keep `task_spec_from_dto` — it's used by `bbs/attach`):

```python
from agentclaw.community.adapters.http.task.schemas import (
    BbsAttachDTO,
    BbsClaimDTO,
    BbsResultDTO,
    TaskCallbackDataDTO,
    TaskCallbackRequest,
    TaskExecutionGraphDTO,
    TaskInfoRequestDTO,
    TaskNodeCallbackRequest,
    TaskOpResultDTO,
    TaskSummaryDTO,
    acceptance_result_from_dto,
    callback_from_dto,
    graph_to_dto,
    op_result_to_dto,
    summary_to_dto,
    task_info_request_from_dto,
    task_spec_from_dto,
)
```

`execute_task_internal`:

```python
@router.post("/execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def execute_task_internal(
    body: TaskInfoRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务(内部副本)。task_id 服务端生成;持久化 task_info(PENDING)→ initialize_graph → on_execute。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ ``@envelope_errors`` 映射 409。"""
    task_request = task_info_request_from_dto(body)
    result = await service.execute(task_request)
    return envelope(op_result_to_dto(result), request)
```

- [ ] **Step 8: Update `test_task_service.py`**

The `task_id_provider` seam keeps the existing `"t1"`/`"t3"` assertions valid. Add a `_task_info_request` helper, thread `task_id_provider` through `_CaseTaskService`/`_build_facade`, and swap the execute input at call sites.

Add the helper near `_task_info`:

```python
def _task_info_request(task_id: str = "t1", max_depth: int = 3):
    """TaskInfoRequest for execute (task_id is supplied by the provider, not the request)."""
    from agentclaw.community.core.task.domain.models import TaskSourceType
    from agentclaw.community.core.task.domain.requests import (
        RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
        RequestTaskSpec, TaskInfoRequest,
    )
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="d")]),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="u1",
        owner_bot_id="b1",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3},
    )
```

`_CaseTaskService.__init__` — add `task_id_provider` and forward:

```python
    def __init__(self, graph, planner_factory=None, discover_bot="bot1", runner=None,
                 harness=None, task_id_provider=None):
        self._case_planner_factory = planner_factory or (lambda g: [])
        self._case_discover_bot = discover_bot
        self._case_runner = runner
        super().__init__(graph, harness=harness, task_id_provider=task_id_provider)
```

`_build_facade` — add `task_id_provider` defaulting to `lambda: "t1"` and forward it:

```python
def _build_facade(svc=None, *, decomposer=None, discover=None, runner=None,
                  harness=None, verify=None, bbs=None, task_id_provider=None) -> tuple:
    from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
    svc = svc or TaskGraphService()
    factory = None
    if decomposer is not None:
        if callable(decomposer) and not hasattr(decomposer, "apply"):
            factory = decomposer
        elif hasattr(decomposer, "_factory"):
            factory = decomposer._factory
    facade = _CaseTaskService(
        svc, planner_factory=factory,
        discover_bot=getattr(discover, "bot_id", "bot1") if discover else "bot1",
        runner=runner, harness=harness,
        task_id_provider=task_id_provider or (lambda: "t1"),
    )
    return facade, svc, None, None, discover, facade._engine._runner
```

`_exec` — rename param (calls `facade.execute(request)` unchanged):

```python
def _exec(facade, request):
    async def _go():
        r = await facade.execute(request)
        await facade.drain_background()
        return r
    return _run(_go())
```

Call-site swaps (every `_exec(facade, _task_info(...))` for the EXECUTE input becomes `_exec(facade, _task_info_request(...))`). The `_task_info` helper stays for `_child`/node `task_spec`. Concretely:

- `TestExecute.test_execute_first_frame`: `_exec(facade, _task_info())` → `_exec(facade, _task_info_request())`.
- `TestExecute.test_execute_no_plan_gap_closed_finishes`: same swap.
- `TestGetDashboard.test_returns_full_graph`: `_exec(facade, _task_info())` → `_exec(facade, _task_info_request())`.
- `TestGetDashboard.test_subtree_projection`: same.
- `TestCallback.test_report_result_flips_node_via_callback`: `_exec(facade, _task_info())` → `_exec(facade, _task_info_request())`.
- `TestHarnessWiring.test_execute_registers_with_harness`: `_exec(facade, _task_info())` → `_exec(facade, _task_info_request())`.
- `TestAcceptanceViaReport.test_root_terminal_pass_via_gap_closed`: `_exec(facade, _task_info())` → `_exec(facade, _task_info_request())`.
- `TestBbsEscalationNoMarket.test_bbs_escalation_marks_bbs_mode_no_market_publish`: replace
  ```python
  ti = _task_info("t3")
  ti.execution_config["MAX_DEPTH"] = 1
  facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1", "t3")])
  _exec(facade, ti)
  ```
  with
  ```python
  facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1", "t3")],
                            task_id_provider=lambda: "t3")
  _exec(facade, _task_info_request("t3", max_depth=1))
  ```

The `"t1"`/`"t3"` assertions, `harness._registered` checks, and `loop_task_id="t1::c1"` callbacks stay unchanged (the provider makes the id deterministic).

- [ ] **Step 9: Update `test_e2e.py`**

Add a `_task_info_request` helper mirroring `_task_info`'s shape (default `task_id="t_case"`), thread `task_id_provider` through the e2e `_CaseTaskService` (the e2e subclass of `TaskService` — add the same `task_id_provider` param forwarding to `super().__init__`), and swap `_exec(facade, _task_info("t_case"))` → `_exec(facade, _task_info_request("t_case"))`, passing `task_id_provider=lambda: "t_case"` where the e2e facade is built. `CaseDecomposer(task_id="t_case")` stays unchanged (the provider guarantees the id).

`_task_info_request` for e2e:

```python
def _task_info_request(task_id: str = "t_case", *, max_depth: int = 3):
    from agentclaw.community.core.task.domain.models import TaskSourceType
    from agentclaw.community.core.task.domain.requests import (
        RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
        RequestTaskSpec, TaskInfoRequest,
    )
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="存储行业尽调", instruction="produce a DD report"),
            context=RequestContext(background="存储行业"),
            goal=RequestGoal(
                objective="产出一份尽调报告",
                acceptances=[RequestAcceptance(id=f"ac{i}", acceptance=f"d{i}") for i in range(1, 6)],
            ),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="owner_user",
        owner_bot_id="owner_bot",
        execution_config={"MAX_DEPTH": max_depth},
    )
```

In each e2e `_exec(facade, _task_info("t_case"))` call site, switch to `_exec(facade, _task_info_request("t_case"))` and ensure the e2e facade is built with `task_id_provider=lambda: "t_case"` (the e2e `_CaseTaskService`/builder forwards it to `TaskService`). Lines that built `_task_info("t_case")` purely to read `.task_spec` for `_node(...)` stay as `_task_info(...)` (unchanged — that's node `task_spec`, not execute input).

- [ ] **Step 10: Run the affected suites**

Run:
```
cd src/backend && .venv/bin/python -m pytest \
  tests/community/core/task/domain/test_requests.py \
  tests/community/core/task/task_center/test_execute_persist.py \
  tests/community/core/task/task_center/test_task_service.py \
  tests/community/core/task/e2e/test_e2e.py \
  tests/community/architecture/test_repository_contracts.py \
  -v
```
Expected: all PASS.

- [ ] **Step 11: Boundary check + fix if tripped**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/architecture/test_module_boundaries.py -v`

If it fails with an undeclared-import message for `core/task/task_center` (importing `core.repository.protocols.task` / `core.task.repository.types` / `core.task.domain.requests`) or for `api/task` (importing `core.task.domain.requests`), add the missing entries to that package's `internal_dependencies` in its `README.md` (mirror the `plugins/local` fix in commit `9ff98a358`). For `core/task`, the likely entries: `- agentclaw.community.core.repository.protocols.task`, `- agentclaw.community.core.task.repository.types`, `- agentclaw.community.core.task.domain.requests`. Re-run until green.

- [ ] **Step 12: Commit**

```bash
git add -A src/backend/src src/backend/tests
git commit -m "feat(task): execute takes TaskInfoRequest and persists task_info before init

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full community suite**

```bash
cd src/backend && .venv/bin/python -m pytest tests/community -q -p no:cacheprovider 2>&1 | tail -15
```
Expected: all green (the execute path now persists; no regression). If a failure names a caller still passing `TaskInfo` to `execute`, update it to `TaskInfoRequest` (search: `grep -rn "\.execute(" tests/community`).

- [ ] **Step 2: Lint the touched files**

```bash
cd src/backend && .venv/bin/python -m flake8 \
  src/agentclaw/community/core/task/domain/requests.py \
  src/agentclaw/community/core/task/task_center/task_service.py \
  src/agentclaw/community/api/task/task_service.py \
  src/agentclaw/community/di/modules/task_module.py \
  src/agentclaw/community/adapters/http/task/schemas.py \
  src/agentclaw/community/adapters/http/openapi_v1/task/router.py \
  src/agentclaw/community/adapters/http/task/router.py \
  tests/community/core/task 2>/dev/null || echo "flake8 not available — antflake deferred to pre-push/CI"
```
Expected: no violations (antflake rules: single-line `def` OK; `class`/`if`/`;` multi-line; `# noqa: F401` on side-effect imports).

- [ ] **Step 3: If Steps 1–2 green, report done. If a regression, fix in a new commit.**

---

## Self-Review (run before handoff)

**1. Spec coverage:**
- D1 server-generated task_id (uuid4) → `task_id_provider` default + `execute` uses it; returned in `TaskOpResult`. ✓ (Task 2 Step 3)
- D2 persisted task_spec = domain shape → `TaskSpec.to_dict()` (Task 1) used in `execute`. ✓
- D3 `TaskInfo.owner_bot_id = request.owner_bot_id`, `source_type = request.source_type` → `to_task_info`. ✓ (Task 1)
- D4 insert failure → `TaskOpResult(success=False, error=...)`, skip init → `execute` try/except `IntegrityError`. ✓ (Task 2 Step 3, tested Step 1 `test_execute_persist_failure_returns_failure`)
- Spec §3 field mappings (acceptance→description, no task_id in request, source_type/owner_user_id/owner_bot_id, execution_config.task_type) → `to_task_info` + DTO. ✓
- Spec §4 file layout → matches. ✓
- Spec §9 HTTP breaking change (new DTO, remove TaskInfoDTO/task_info_from_dto, keep TaskSpecDTO family) → Task 2 Steps 6–7. ✓
- Spec §10 tests (update test_task_service/test_e2e, new persist test) → Task 2 Steps 1, 8, 9. ✓
- Spec §11 out of scope (nodes/relations/graph) → not touched. ✓

**2. Placeholder scan:** no TBD/TODO. `test_e2e` Step 9 says "the e2e `_CaseTaskService`/builder forwards it" — the e2e facade subclass must add the `task_id_provider` param; if the implementer finds the e2e uses a differently-named builder, mirror the `test_task_service` change (add the param, forward to `super().__init__`, default `lambda: "t_case"`). This is a concrete instruction, not a placeholder.

**3. Type consistency:** `TaskInfoRequest.to_task_info(task_id: str) -> TaskInfo` defined in Task 1, used in Task 2 `execute`. `TaskService.execute(request: TaskInfoRequest)` matches `TaskServiceProtocol.execute(request: TaskInfoRequest)`. `task_id_provider: Callable[[], str] | None`. `TaskInfoRepositoryProtocol.insert(record)` / `TaskInfoRecord` field names (`task_id`, `source_type`, `owner_user_id`, `owner_bot_id`, `execution_config`, `task_spec`, `status`) match the persistence layer built earlier. `TaskSourceType`/`TaskType` defined once in `models.py`, imported in `requests.py`/schemas/protocol. `task_info_request_from_dto` produces `TaskInfoRequest`; routers call `service.execute(task_request)`. `run_execute(facade, request)` matches the new param. Names are consistent across tasks.
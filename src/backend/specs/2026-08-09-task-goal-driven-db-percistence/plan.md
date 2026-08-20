# Task Persistence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the 5 collaboration-task tables (`task_info`, `task_node`, `task_node_run_info`, `task_node_relation`, `task_callback`) behind a `task_queue`-style repository layer, auto-wired in DI, without touching the in-memory `TaskGraphService`.

**Architecture:** Microkernel + SPI. Repositories are domain persistence (one impl each, in `core/repository/`), consuming the `DatabasePlugin` SPI port — the per-profile engine swap (SqliteDB/CommunityDatabase/ZdasDB) happens one layer below and is reused unchanged. New `TaskPersistenceModule` is profile-independent; only the injected `DatabasePlugin` differs.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (classic `declarative_base()`), `injector` + `fastapi-injector` DI, OceanBase/MySQL in prod + in-memory SQLite in tests.

**Spec:** `src/backend/specs/2026-08-09-task-goal-driven-db-percistence/spec.md`

## Global Constraints

- **Table names verbatim, NO `ac_` prefix:** `task_info`, `task_node`, `task_node_run_info`, `task_node_relation`, `task_callback`. ORM `__tablename__` matches the DDL exactly.
- **`task_callback.node_id` is `varchar(128) NOT NULL`** (DDL fix D5.1; was `varchar(512) DEFAULT NULL`).
- **`task_node_run_info` is 1:N by `retry`**; "latest" = `max(retry)` (D5.2).
- **`acceptance_result` stored as JSON** of `AcceptanceResult` (D5.2).
- **One repository per table, no merging** (D4) — 5 protocols + 5 impls.
- **Required values stay non-optional** (AGENTS.md): a DB column that is `NOT NULL` maps to a non-`Optional` record field; `DEFAULT NULL` columns map to `Optional[...]` where `None` is a real state. Do not use `T | None` for values that cannot be `None` in contract.
- **ORM `Base`** from `agentclaw.community.core.base`. Autoincrement BIGINT PKs use `BigInteger().with_variant(Integer, "sqlite")`. Identifier columns in unique keys use `String(n).with_variant(mysql.VARCHAR(n, collation="utf8mb4_bin"), "mysql")` so OceanBase PAD-SPACE cannot merge two distinct ids.
- **Protocols:** `from __future__ import annotations`; `@runtime_checkable` `Protocol`; every member `@abstractmethod`; **all** `agentclaw.community.core.*` imports under `if TYPE_CHECKING:`; contracts only, no concrete classes.
- **Implementations:** `class XRepo(XRepoProtocol)` (declare the Protocol as a base — the architecture test enforces this); `@inject def __init__(self, db: DatabasePlugin)`; `with self._db.orm_session() as db:` per method; return record dataclasses, never ORM objects; ` IntegrityError` propagates from unique-key conflicts.
- **README `provides` stays in lockstep:** every protocol and impl class name added to `core/repository/README.md` `provides:` YAML in the same task that creates the class, or `test_readme_provides_lists_the_real_public_surface` fails.
- **DDL is operator-provisioned** (the app never runs it in prod); SQLite tests build tables via `Base.metadata.create_all`. `BLOCK_SIZE 16384 LOCAL`/`GLOBAL` are OceanBase-only and ORM-unrepresentable — they live only in the DDL files.
- **Tests run with** `src/backend/.venv/bin/python -m pytest` (project venv).
- **SAST:** antflake tolerates single-line `def` but blocks `class`/`if`/`;` on one line; `F401`/`F841` are not blocked, but mark side-effect imports `# noqa: F401` anyway.

---

## File Structure

```
core/task/repository/
├── __init__.py                 (Task 1) package marker, empty
├── models.py                   (Task 1) 5 ORM models + to_record()
├── types.py                    (Task 1) 5 frozen record dataclasses + TaskNodeRunInfoUpdate + projections
└── sql/                        (Task 7) 5 DDL files
    ├── 2026_08_20_task_info.sql
    ├── 2026_08_20_task_node.sql
    ├── 2026_08_20_task_node_run_info.sql
    ├── 2026_08_20_task_node_relation.sql
    └── 2026_08_20_task_callback.sql

core/repository/protocols/
└── task.py                     (Task 1) 5 Protocol classes

core/repository/implementations/
└── task/
    ├── __init__.py             (Task 2) package marker, empty
    ├── task_info_repository.py        (Task 2)
    ├── task_node_repository.py        (Task 3)
    ├── task_node_run_info_repository.py (Task 4)
    ├── task_node_relation_repository.py (Task 5)
    └── task_callback_repository.py    (Task 6)

di/modules/task_persistence_module.py  (Task 8) binds 5 Protocol→Impl, singleton
di/container.py                        (Task 8) +import, +TaskPersistenceModule() in base list
plugins/local/database.py             (Task 8) +side-effect import of task models in SqliteDB.bootstrap()
core/repository/README.md             (Tasks 1–6) +provides names in lockstep

tests/community/repository/task/
├── conftest.py                 (Task 2) InMemorySqliteDB stub + engine/db fixtures
├── test_models_schema.py       (Task 1)
├── test_task_info_repository.py        (Task 2)
├── test_task_node_repository.py        (Task 3)
├── test_task_node_run_info_repository.py (Task 4)
├── test_task_node_relation_repository.py (Task 5)
├── test_task_callback_repository.py    (Task 6)
└── test_task_persistence_module.py     (Task 8)
```

**Why these boundaries:** `models.py` + `types.py` + `protocols/task.py` are one foundation (Task 1) — they have no behavior to test alone, so Task 1's test is a schema-build smoke test + the existing architecture-contract suite. Each repository (Tasks 2–6) is one test cycle and one reviewer gate, exercising its own model. DI wiring (Task 8) and DDL artifacts (Task 7) are independent of repo behavior.

---

### Task 1: Persistence scaffolding — ORM models, record types, protocols

**Files:**
- Create: `src/backend/src/agentclaw/community/core/task/repository/__init__.py`
- Create: `src/backend/src/agentclaw/community/core/task/repository/models.py`
- Create: `src/backend/src/agentclaw/community/core/task/repository/types.py`
- Create: `src/backend/src/agentclaw/community/core/repository/protocols/task.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add 5 protocol names to `provides:`; add `agentclaw.community.core.task` to `internal_dependencies:`)
- Test: `src/backend/tests/community/repository/task/test_models_schema.py`

**Interfaces:**
- Consumes: `agentclaw.community.core.base.Base`; domain enums/dataclasses from `agentclaw.community.core.task.domain.models` (`Status`, `AcceptanceVerdict`, `RelationType`, `AcceptanceResult`, `Relation`).
- Produces (used by Tasks 2–6, 8):
  - `agentclaw.community.core.task.repository.types.TaskInfoRecord`, `TaskNodeRecord`, `TaskNodeRunInfoRecord`, `TaskNodeRelationRecord`, `TaskCallbackRecord` (frozen dataclasses), `TaskNodeRunInfoUpdate`.
  - `agentclaw.community.core.task.repository.models.TaskInfoModel`, `TaskNodeModel`, `TaskNodeRunInfoModel`, `TaskNodeRelationModel`, `TaskCallbackModel` (each `.to_record()`).
  - `agentclaw.community.core.repository.protocols.task.TaskInfoRepositoryProtocol`, `TaskNodeRepositoryProtocol`, `TaskNodeRunInfoRepositoryProtocol`, `TaskNodeRelationRepositoryProtocol`, `TaskCallbackRepositoryProtocol`.

- [ ] **Step 1: Write the failing schema test**

Create `tests/community/repository/task/test_models_schema.py`:

```python
"""Smoke test: the 5 task ORM models register on Base.metadata and build real
SQLite tables with the expected columns and unique indexes."""
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the 5 models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


def _engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


def test_five_tables_build_with_key_columns():
    inspector = inspect(_engine())
    for table in (
        "task_info", "task_node", "task_node_run_info",
        "task_node_relation", "task_callback",
    ):
        assert inspector.has_table(table), f"missing table {table}"

    cols = {c["name"] for c in inspector.get_columns("task_callback")}
    # D5.1 fix: node_id is NOT NULL varchar(128).
    assert "node_id" in cols
    node_id = next(c for c in inspector.get_columns("task_callback") if c["name"] == "node_id")
    assert not node_id["nullable"], "task_callback.node_id must be NOT NULL"

    # Unique indexes present.
    def uniques(table):
        return {tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
    assert ("task_id",) in uniques("task_info")
    assert ("task_id", "node_id", "retry") in uniques("task_node_run_info")
    assert ("src_node_id", "dst_node_id") in uniques("task_node_relation")
    assert ("run_id", "node_id") in uniques("task_callback")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_models_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentclaw.community.core.task.repository.models'`.

- [ ] **Step 3: Create `core/task/repository/__init__.py` (empty package marker)**

```python
"""collaboration-task persistence layer (ORM models + record dataclasses).

Repositories live under ``core/repository/`` per the consolidation
(see ``core/repository/README.md``); this package holds only the ORM models
and the table-faithful record dataclasses they project to."""
```

- [ ] **Step 4: Create `core/task/repository/types.py`**

```python
"""Table-faithful record dataclasses + projection helpers for the 5 task tables.

Mirrors ``core/task_queue/types.py``: frozen dataclasses returned by repositories
(never ORM objects). Structured TEXT columns hold parsed JSON (``dict``) or raw
``str``; enum columns hold the domain enum. Projections onto domain dataclasses
are provided where the mapping needs no nested (de)serialization (``Relation``,
``AcceptanceResult``). ``TaskSpec``/``TaskInfo``/``RuntimeInfo`` projections are
deferred — the domain dataclasses have no (de)serialization and the full graph
state has no persistence home yet (spec §3); the records hold parsed dicts so the
projection can be added later without a schema change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Relation,
    RelationType,
    Status,
)


@dataclass(frozen=True)
class TaskInfoRecord:
    id: int
    task_id: str
    source_type: str
    owner_user_id: str
    owner_bot_id: str
    execution_config: Optional[dict[str, Any]]
    task_spec: dict[str, Any]
    status: Status
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskNodeRecord:
    id: int
    task_id: str
    node_id: str
    task_spec: dict[str, Any]
    status: Status
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskNodeRunInfoRecord:
    id: int
    node_id: str
    task_id: str
    run_mode: Optional[str]
    assignee: Optional[str]
    output: Optional[dict[str, Any]]
    acceptance_result: Optional[dict[str, Any]]
    retry: int
    session_id: Optional[str]
    extend_props: Optional[dict[str, Any]]
    start_time: Optional[int]
    update_time: Optional[int]
    end_time: Optional[int]
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None

    def to_acceptance_result(self) -> Optional[AcceptanceResult]:
        """Project the ``acceptance_result`` JSON dict onto the domain type."""
        if self.acceptance_result is None:
            return None
        return AcceptanceResult(
            verdict=AcceptanceVerdict(self.acceptance_result["verdict"]),
            acceptances_metric=list(self.acceptance_result.get("acceptances_metric", [])),
            gaps=list(self.acceptance_result.get("gaps", [])),
        )


@dataclass
class TaskNodeRunInfoUpdate:
    """Partial update for ``task_node_run_info``. ``None`` means leave the row
    unchanged (mirrors the domain ``TaskNodePatch``/``TaskGraphPatch`` idiom)."""

    run_mode: Optional[str] = None
    assignee: Optional[str] = None
    output: Optional[dict[str, Any]] = None
    acceptance_result: Optional[dict[str, Any]] = None
    session_id: Optional[str] = None
    extend_props: Optional[dict[str, Any]] = None
    start_time: Optional[int] = None
    update_time: Optional[int] = None
    end_time: Optional[int] = None


@dataclass(frozen=True)
class TaskNodeRelationRecord:
    id: int
    task_id: str
    src_node_id: str
    dst_node_id: str
    relation_type: RelationType
    extend_props: Optional[dict[str, Any]]
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None

    def to_relation(self) -> Relation:
        """Clean 1:1 projection onto domain ``Relation``."""
        return Relation(
            src_id=self.src_node_id,
            dst_id=self.dst_node_id,
            type=self.relation_type,
            extend_props=dict(self.extend_props) if self.extend_props else {},
        )


@dataclass(frozen=True)
class TaskCallbackRecord:
    id: int
    invoker: str
    run_id: str
    node_id: str
    main_session_id: str
    status: Optional[str]
    orig_callback_data: str
    execution_graph: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    result_success: Optional[bool]
    exec_error: Optional[str]
    extend_props: Optional[dict[str, Any]]
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None
```

- [ ] **Step 5: Create `core/task/repository/models.py`**

```python
"""ORM models for the 5 collaboration-task tables.

Mirrors ``core/task_queue/repository/models.py``: ``Base`` from ``core/base``,
``with_variant(Integer, "sqlite")`` for autoincrement BIGINT PKs, ``utf8mb4_bin``
on identifier columns in unique keys (so OceanBase PAD-SPACE cannot merge two
distinct ids), and ``Index(..., unique=True)`` for unique keys. OceanBase-only
modifiers (``BLOCK_SIZE``/``LOCAL``/``GLOBAL``) are ORM-unrepresentable and live
only in ``core/task/sql/*.sql``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.domain.models import RelationType, Status
from agentclaw.community.core.task.repository.types import (
    TaskCallbackRecord,
    TaskInfoRecord,
    TaskNodeRecord,
    TaskNodeRelationRecord,
    TaskNodeRunInfoRecord,
)

# SQLite autoincrements only on INTEGER PRIMARY KEY; BigInteger renders BIGINT
# and breaks SQLite autoincrement. with_variant keeps BIGINT on MySQL/OceanBase.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


def _binary_string(length: int):
    """utf8mb4_bin on MySQL so unique-index comparisons are byte-for-byte
    (the default ci collation would fold distinct ids). SQLite stays plain."""
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


_TASK_ID = _binary_string(128)
_NODE_ID = _binary_string(128)
_RUN_ID = _binary_string(512)
_SESSION_ID = _binary_string(256)
_ASSIGNEE = _binary_string(1024)
_USER_ID = _binary_string(256)


def _loads(text: Optional[str]) -> Optional[dict[str, Any]]:
    return json.loads(text) if text else None


class TaskInfoModel(Base):
    __tablename__ = "task_info"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    source_type = Column(String(128), nullable=False)
    owner_user_id = Column(_USER_ID, nullable=False)
    owner_bot_id = Column(_USER_ID, nullable=False)
    execution_config = Column(Text, nullable=True)
    task_spec = Column(Text, nullable=False)
    status = Column(String(64), nullable=False)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("uk_task_id", "task_id", unique=True),
        Index("idx_status", "status", "gmt_modified"),
    )

    def to_record(self) -> TaskInfoRecord:
        return TaskInfoRecord(
            id=self.id,
            task_id=self.task_id,
            source_type=self.source_type,
            owner_user_id=self.owner_user_id,
            owner_bot_id=self.owner_bot_id,
            execution_config=_loads(self.execution_config),
            task_spec=_loads(self.task_spec),
            status=Status(self.status),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeModel(Base):
    __tablename__ = "task_node"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    task_spec = Column(Text, nullable=False)
    status = Column(String(64), nullable=False)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_task_status", "task_id", "status"),
    )

    def to_record(self) -> TaskNodeRecord:
        return TaskNodeRecord(
            id=self.id,
            task_id=self.task_id,
            node_id=self.node_id,
            task_spec=_loads(self.task_spec),
            status=Status(self.status),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeRunInfoModel(Base):
    __tablename__ = "task_node_run_info"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    run_mode = Column(String(64), nullable=True)
    assignee = Column(_ASSIGNEE, nullable=True)
    output = Column(Text, nullable=True)
    acceptance_result = Column(Text, nullable=True)
    retry = Column(Integer, nullable=False, default=0)
    session_id = Column(_SESSION_ID, nullable=True)
    extend_props = Column(Text, nullable=True)
    start_time = Column(BigInteger, nullable=True)
    update_time = Column(BigInteger, nullable=True)
    end_time = Column(BigInteger, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("uk_task_node", "task_id", "node_id", "retry", unique=True),
        Index("idx_task", "task_id"),
        Index("idx_assignee", "assignee"),
        Index("idx_run_mode_status_time", "run_mode", "start_time"),
    )

    def to_record(self) -> TaskNodeRunInfoRecord:
        return TaskNodeRunInfoRecord(
            id=self.id,
            node_id=self.node_id,
            task_id=self.task_id,
            run_mode=self.run_mode,
            assignee=self.assignee,
            output=_loads(self.output),
            acceptance_result=_loads(self.acceptance_result),
            retry=self.retry,
            session_id=self.session_id,
            extend_props=_loads(self.extend_props),
            start_time=self.start_time,
            update_time=self.update_time,
            end_time=self.end_time,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskNodeRelationModel(Base):
    __tablename__ = "task_node_relation"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    task_id = Column(_TASK_ID, nullable=False)
    src_node_id = Column(_NODE_ID, nullable=False)
    dst_node_id = Column(_NODE_ID, nullable=False)
    relation_type = Column(
        String(64), nullable=False, default=RelationType.DEPENDENCY.value
    )
    extend_props = Column(Text, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("uk_src_dst", "src_node_id", "dst_node_id", unique=True),
        Index("idx_src", "task_id", "src_node_id"),
    )

    def to_record(self) -> TaskNodeRelationRecord:
        return TaskNodeRelationRecord(
            id=self.id,
            task_id=self.task_id,
            src_node_id=self.src_node_id,
            dst_node_id=self.dst_node_id,
            relation_type=RelationType(self.relation_type),
            extend_props=_loads(self.extend_props),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class TaskCallbackModel(Base):
    __tablename__ = "task_callback"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    invoker = Column(String(128), nullable=False)
    run_id = Column(_RUN_ID, nullable=False)
    node_id = Column(_NODE_ID, nullable=False)  # D5.1: NOT NULL, varchar(128)
    main_session_id = Column(_SESSION_ID, nullable=False)
    status = Column(String(64), nullable=True)
    orig_callback_data = Column(Text, nullable=False)
    execution_graph = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    result_success = Column(Boolean, nullable=True)
    exec_error = Column(Text, nullable=True)
    extend_props = Column(Text, nullable=True)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("uk_workflow_instance", "run_id", "node_id", unique=True),
        Index("idx_session_id", "main_session_id"),
    )

    def to_record(self) -> TaskCallbackRecord:
        return TaskCallbackRecord(
            id=self.id,
            invoker=self.invoker,
            run_id=self.run_id,
            node_id=self.node_id,
            main_session_id=self.main_session_id,
            status=self.status,
            orig_callback_data=self.orig_callback_data,
            execution_graph=_loads(self.execution_graph),
            result=_loads(self.result),
            result_success=self.result_success,
            exec_error=self.exec_error,
            extend_props=_loads(self.extend_props),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
```

- [ ] **Step 6: Create `core/repository/protocols/task.py`**

```python
"""Repository contracts for the collaboration-task persistence layer.

Every member is ``@abstractmethod`` (an implementation that omits one fails at
construction naming the missing member). Domain imports are ``TYPE_CHECKING``
-only — see ``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import Status
    from agentclaw.community.core.task.repository.types import (
        TaskCallbackRecord,
        TaskInfoRecord,
        TaskNodeRecord,
        TaskNodeRelationRecord,
        TaskNodeRunInfoRecord,
        TaskNodeRunInfoUpdate,
    )


@runtime_checkable
class TaskInfoRepositoryProtocol(Protocol):
    """Durable store for the ``task_info`` table (task-level record)."""

    @abstractmethod
    def insert(self, record: "TaskInfoRecord") -> "TaskInfoRecord":
        """Insert one row keyed by ``task_id``. Raises ``IntegrityError`` on a
        duplicate ``task_id`` (no upsert — mirror ``task_queue`` insert-then-
        classify-conflict). Returns the stored record (with ``id``/``gmt_*``)."""
        ...

    @abstractmethod
    def get(self, task_id: str) -> Optional["TaskInfoRecord"]:
        """Return the row for ``task_id``, or ``None``."""
        ...

    @abstractmethod
    def update_status(self, task_id: str, status: "Status") -> bool:
        """Set ``status`` on the row for ``task_id``. Returns ``True`` iff a row
        was updated."""
        ...

    @abstractmethod
    def list_by_status(
        self,
        status: "Status",
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list["TaskInfoRecord"]:
        """Rows in ``status``, newest ``gmt_modified`` first (dashboard query)."""
        ...


@runtime_checkable
class TaskNodeRepositoryProtocol(Protocol):
    """Durable store for the ``task_node`` table (node spec + status)."""

    @abstractmethod
    def insert(self, record: "TaskNodeRecord") -> "TaskNodeRecord":
        """Insert one node row. A node is inserted once per ``(task_id, node_id)``."""
        ...

    @abstractmethod
    def get(self, task_id: str, node_id: str) -> Optional["TaskNodeRecord"]:
        """Return the node, or ``None``."""
        ...

    @abstractmethod
    def update_status(self, task_id: str, node_id: str, status: "Status") -> bool:
        """Set ``status`` on the node. Returns ``True`` iff a row was updated."""
        ...

    @abstractmethod
    def list_nodes(self, task_id: str) -> list["TaskNodeRecord"]:
        """All nodes for ``task_id``."""
        ...

    @abstractmethod
    def list_by_status(
        self,
        task_id: Optional[str],
        status: "Status",
        *,
        limit: int = 100,
    ) -> list["TaskNodeRecord"]:
        """Nodes in ``status``; optionally scoped to ``task_id``."""
        ...


@runtime_checkable
class TaskNodeRunInfoRepositoryProtocol(Protocol):
    """Durable store for ``task_node_run_info`` (1:N by ``retry`` per node)."""

    @abstractmethod
    def insert(self, record: "TaskNodeRunInfoRecord") -> "TaskNodeRunInfoRecord":
        """Insert one run-info row keyed by ``(task_id, node_id, retry)``. Raises
        ``IntegrityError`` on a duplicate of that triple."""
        ...

    @abstractmethod
    def update(
        self,
        task_id: str,
        node_id: str,
        retry: int,
        patch: "TaskNodeRunInfoUpdate",
    ) -> bool:
        """Apply the non-``None`` fields of ``patch`` to the row identified by
        ``(task_id, node_id, retry)``. Writes ``update_time`` when any field
        changes. Returns ``True`` iff a row was updated."""
        ...

    @abstractmethod
    def get_latest(self, task_id: str, node_id: str) -> Optional["TaskNodeRunInfoRecord"]:
        """Return the row with ``max(retry)`` for the node, or ``None``."""
        ...

    @abstractmethod
    def get_by_retry(
        self, task_id: str, node_id: str, retry: int
    ) -> Optional["TaskNodeRunInfoRecord"]:
        """Return the exact ``(task_id, node_id, retry)`` row, or ``None``."""
        ...

    @abstractmethod
    def list_by_task(self, task_id: str) -> list["TaskNodeRunInfoRecord"]:
        """All run-info rows for ``task_id``."""
        ...

    @abstractmethod
    def list_by_assignee(self, assignee: str, *, limit: int = 100) -> list["TaskNodeRunInfoRecord"]:
        """Rows whose ``assignee`` matches (backs ``idx_assignee``)."""
        ...

    @abstractmethod
    def list_by_run_mode(
        self,
        run_mode: str,
        *,
        start_time_since: Optional[int] = None,
        limit: int = 100,
    ) -> list["TaskNodeRunInfoRecord"]:
        """Rows in ``run_mode``; optionally ``start_time >= start_time_since``
        (backs ``idx_run_mode_status_time``; this table has no ``status`` column)."""
        ...


@runtime_checkable
class TaskNodeRelationRepositoryProtocol(Protocol):
    """Durable store for ``task_node_relation`` (the decomposition-tree edges)."""

    @abstractmethod
    def add_relations(self, records: list["TaskNodeRelationRecord"]) -> int:
        """Insert all edges. Raises ``IntegrityError`` on a duplicate
        ``(src_node_id, dst_node_id)``. Returns the count inserted."""
        ...

    @abstractmethod
    def list_relations(self, task_id: str) -> list["TaskNodeRelationRecord"]:
        """All edges for ``task_id``."""
        ...

    @abstractmethod
    def children(self, src_node_id: str) -> list["TaskNodeRelationRecord"]:
        """Edges whose parent is ``src_node_id``."""
        ...

    @abstractmethod
    def parents(self, dst_node_id: str) -> list["TaskNodeRelationRecord"]:
        """Edges whose child is ``dst_node_id``."""
        ...


@runtime_checkable
class TaskCallbackRepositoryProtocol(Protocol):
    """Append-only audit store for received task callbacks (``task_callback``)."""

    @abstractmethod
    def insert(self, rec: "TaskCallbackRecord") -> "TaskCallbackRecord":
        """Insert one callback row keyed by ``(run_id, node_id)``. Raises
        ``IntegrityError`` on a duplicate (``node_id`` is NOT NULL)."""
        ...

    @abstractmethod
    def get(self, run_id: str, node_id: str) -> Optional["TaskCallbackRecord"]:
        """Return the callback, or ``None``."""
        ...

    @abstractmethod
    def list_by_session(
        self, main_session_id: str, *, limit: int = 100
    ) -> list["TaskCallbackRecord"]:
        """Callbacks for ``main_session_id`` (backs ``idx_session_id``)."""
        ...
```

- [ ] **Step 7: Add the 5 protocol names to `core/repository/README.md`**

In the `## Context Boundary` → ```` ```yaml ```` → `provides:` list, add a `# task` group among the protocol entries (placement is not significant — the test reads `provides` as one set). Insert after the `# platform` protocol block (which already lists `TaskQueueRepositoryProtocol`):

```yaml
  # task
  - TaskInfoRepositoryProtocol
  - TaskNodeRepositoryProtocol
  - TaskNodeRunInfoRepositoryProtocol
  - TaskNodeRelationRepositoryProtocol
  - TaskCallbackRepositoryProtocol
```

In the same YAML's `internal_dependencies:` list, add:

```yaml
  - agentclaw.community.core.task
```

(Do NOT add the 5 implementation names yet — they do not exist until Tasks 2–6, and `test_readme_provides_lists_the_real_public_surface` requires `declared == actual` at every commit.)

- [ ] **Step 8: Run the schema test + the architecture-contract suite**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_models_schema.py tests/community/architecture/test_repository_contracts.py -v`
Expected: both PASS. (The contract suite passes because the 5 protocols are abstract, `TYPE_CHECKING`-only, and now listed in `provides`; no implementations exist yet so `test_every_implementation_declares_its_protocol` has nothing to scan.)

- [ ] **Step 9: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/repository/__init__.py \
        src/backend/src/agentclaw/community/core/task/repository/models.py \
        src/backend/src/agentclaw/community/core/task/repository/types.py \
        src/backend/src/agentclaw/community/core/repository/protocols/task.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/test_models_schema.py
git commit -m "feat(task): add task persistence ORM models, records, and protocols

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: TaskInfoRepository + shared test conftest

**Files:**
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/__init__.py`
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/task_info_repository.py`
- Create: `src/backend/tests/community/repository/task/conftest.py`
- Create: `src/backend/tests/community/repository/task/test_task_info_repository.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add `TaskInfoRepository` to `provides:`)

**Interfaces:**
- Consumes: `TaskInfoRepositoryProtocol` (Task 1), `TaskInfoModel`/`TaskInfoRecord` (Task 1), `DatabasePlugin`.
- Produces: `TaskInfoRepository` (used by Task 8 DI). Methods: `insert`, `get`, `update_status`, `list_by_status` (signatures in Task 1 protocol).

- [ ] **Step 1: Create the shared `conftest.py`**

`tests/community/repository/task/conftest.py`:

```python
"""Shared SQLite harness for task repository tests.

Mirrors tests/community/repository/platform/test_task_queue_repository.py: a real
in-memory SQLite engine, Base.metadata.create_all, and a minimal orm_session
stub that commits on clean exit / rolls back on error — same semantics the prod
DatabasePlugin gives, so the single ORM body behaves identically here."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the 5 task models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


class InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering orm_session()."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    return InMemorySqliteDB(engine)
```

- [ ] **Step 2: Write the failing test**

`tests/community/repository/task/test_task_info_repository.py`:

```python
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)


def _record(task_id: str = "T-1", status: Status = Status.PENDING) -> TaskInfoRecord:
    return TaskInfoRecord(
        id=0,
        task_id=task_id,
        source_type="bot",
        owner_user_id="U-1",
        owner_bot_id="B-1",
        execution_config={"max_depth": 3},
        task_spec={"metadata": {"task_id": task_id, "title": "t", "instruction": "do"}},
        status=status,
    )


def test_insert_then_get_roundtrips(db):
    repo = TaskInfoRepository(db)
    stored = repo.insert(_record())
    assert stored.id > 0
    assert stored.task_id == "T-1"
    assert stored.status is Status.PENDING
    assert stored.task_spec["metadata"]["title"] == "t"
    assert stored.gmt_create is not None

    again = repo.get("T-1")
    assert again == stored


def test_duplicate_task_id_raises(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1"))
    with pytest.raises(IntegrityError):
        repo.insert(_record("T-1"))


def test_update_status_returns_rowcount_truth(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    assert repo.update_status("T-1", Status.RUNNING) is True
    assert repo.get("T-1").status is Status.RUNNING
    assert repo.update_status("missing", Status.RUNNING) is False


def test_list_by_status(db):
    repo = TaskInfoRepository(db)
    repo.insert(_record("T-1", Status.PENDING))
    repo.insert(_record("T-2", Status.RUNNING))
    repo.insert(_record("T-3", Status.PENDING))
    pending = repo.list_by_status(Status.PENDING)
    assert {r.task_id for r in pending} == {"T-1", "T-3"}
    assert repo.list_by_status(Status.DONE) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_info_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: ...implementations.task.task_info_repository`.

- [ ] **Step 4: Create the package marker + implementation**

`core/repository/implementations/task/__init__.py`:

```python
"""ORM implementations of the task repository protocols (core/repository/protocols/task.py)."""
```

`core/repository/implementations/task/task_info_repository.py`:

```python
"""``TaskInfoRepositoryProtocol`` implementation for the ``task_info`` table."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.models import TaskInfoModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskInfoRepository(TaskInfoRepositoryProtocol):
    """Unified ORM implementation for ``task_info`` (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskInfoModel

    @staticmethod
    def _to_row(record: TaskInfoRecord) -> TaskInfoModel:
        return TaskInfoModel(
            task_id=record.task_id,
            source_type=record.source_type,
            owner_user_id=record.owner_user_id,
            owner_bot_id=record.owner_bot_id,
            execution_config=(
                json.dumps(record.execution_config)
                if record.execution_config is not None
                else None
            ),
            task_spec=json.dumps(record.task_spec),
            status=record.status.value,
        )

    def insert(self, record: TaskInfoRecord) -> TaskInfoRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, task_id: str) -> Optional[TaskInfoRecord]:
        with self._db.orm_session() as db:
            row = db.query(self._model).filter(self._model.task_id == task_id).first()
            return row.to_record() if row else None

    def update_status(self, task_id: str, status: Status) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .update({"status": status.value}, synchronize_session=False)
            )
        return count > 0

    def list_by_status(
        self,
        status: Status,
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.status == status.value)
            if gmt_modified_since is not None:
                q = q.filter(self._model.gmt_modified >= gmt_modified_since)
            rows = q.order_by(self._model.gmt_modified.desc()).limit(limit).all()
            return [r.to_record() for r in rows]
```

- [ ] **Step 5: Add `TaskInfoRepository` to README `provides:`**

In `core/repository/README.md` `provides:` YAML, in the implementations region (the part of the list introduced by the comment line `# Implementations — implementations/<domain>/.`), add a `# task` group:

```yaml
  # task
  - TaskInfoRepository
```

- [ ] **Step 6: Run test to verify it passes + contract suite green**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_info_repository.py tests/community/architecture/test_repository_contracts.py -v`
Expected: PASS (impl declares its protocol base; `provides` now includes `TaskInfoRepository`).

- [ ] **Step 7: Commit**

```bash
git add src/backend/src/agentclaw/community/core/repository/implementations/task/__init__.py \
        src/backend/src/agentclaw/community/core/repository/implementations/task/task_info_repository.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/conftest.py \
        src/backend/tests/community/repository/task/test_task_info_repository.py
git commit -m "feat(task): add TaskInfoRepository for task_info persistence

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: TaskNodeRepository

**Files:**
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_repository.py`
- Create: `src/backend/tests/community/repository/task/test_task_node_repository.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add `TaskNodeRepository` to `provides:`)

**Interfaces:**
- Consumes: `TaskNodeRepositoryProtocol` (Task 1), `TaskNodeModel`/`TaskNodeRecord` (Task 1), `DatabasePlugin`, `conftest` `db` fixture (Task 2).
- Produces: `TaskNodeRepository`. Methods: `insert`, `get`, `update_status`, `list_nodes`, `list_by_status`.

- [ ] **Step 1: Write the failing test**

`tests/community/repository/task/test_task_node_repository.py`:

```python
import pytest

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskNodeRecord
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)


def _node(node_id: str = "N-1", task_id: str = "T-1", status=Status.PENDING) -> TaskNodeRecord:
    return TaskNodeRecord(
        id=0,
        task_id=task_id,
        node_id=node_id,
        task_spec={"metadata": {"task_id": task_id, "title": "n", "instruction": "do"}},
        status=status,
    )


def test_insert_get_roundtrip(db):
    repo = TaskNodeRepository(db)
    stored = repo.insert(_node())
    assert stored.id > 0
    assert repo.get("T-1", "N-1") == stored


def test_update_status_truth(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", status=Status.PENDING))
    assert repo.update_status("T-1", "N-1", Status.RUNNING) is True
    assert repo.get("T-1", "N-1").status is Status.RUNNING
    assert repo.update_status("T-1", "missing", Status.DONE) is False


def test_list_nodes_and_by_status(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", status=Status.PENDING))
    repo.insert(_node("N-2", status=Status.RUNNING))
    repo.insert(_node("N-3", status=Status.PENDING))
    assert {n.node_id for n in repo.list_nodes("T-1")} == {"N-1", "N-2", "N-3"}
    assert {n.node_id for n in repo.list_by_status("T-1", Status.PENDING)} == {"N-1", "N-3"}


def test_list_by_status_unscoped(db):
    repo = TaskNodeRepository(db)
    repo.insert(_node("N-1", "T-1", Status.RUNNING))
    repo.insert(_node("N-2", "T-2", Status.RUNNING))
    assert len(repo.list_by_status(None, Status.RUNNING)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: ...task_node_repository`.

- [ ] **Step 3: Create the implementation**

`core/repository/implementations/task/task_node_repository.py`:

```python
"""``TaskNodeRepositoryProtocol`` implementation for the ``task_node`` table."""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import TaskNodeRepositoryProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.models import TaskNodeModel
from agentclaw.community.core.task.repository.types import TaskNodeRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRepository(TaskNodeRepositoryProtocol):
    """Unified ORM implementation for ``task_node`` (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeModel

    @staticmethod
    def _to_row(record: TaskNodeRecord) -> TaskNodeModel:
        return TaskNodeModel(
            task_id=record.task_id,
            node_id=record.node_id,
            task_spec=json.dumps(record.task_spec),
            status=record.status.value,
        )

    def insert(self, record: TaskNodeRecord) -> TaskNodeRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, task_id: str, node_id: str) -> Optional[TaskNodeRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id, self._model.node_id == node_id)
                .first()
            )
            return row.to_record() if row else None

    def update_status(self, task_id: str, node_id: str, status: Status) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                )
                .update({"status": status.value}, synchronize_session=False)
            )
        return count > 0

    def list_nodes(self, task_id: str) -> list[TaskNodeRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def list_by_status(
        self,
        task_id: Optional[str],
        status: Status,
        *,
        limit: int = 100,
    ) -> list[TaskNodeRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.status == status.value)
            if task_id is not None:
                q = q.filter(self._model.task_id == task_id)
            rows = q.limit(limit).all()
            return [r.to_record() for r in rows]
```

- [ ] **Step 4: Add `TaskNodeRepository` to README `provides:`**

Append `- TaskNodeRepository` under the `# task` implementations group started in Task 2.

- [ ] **Step 5: Run test to verify it passes + contract suite green**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_repository.py tests/community/architecture/test_repository_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_repository.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/test_task_node_repository.py
git commit -m "feat(task): add TaskNodeRepository for task_node persistence

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: TaskNodeRunInfoRepository (1:N by retry + partial update)

**Files:**
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_run_info_repository.py`
- Create: `src/backend/tests/community/repository/task/test_task_node_run_info_repository.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add `TaskNodeRunInfoRepository` to `provides:`)

**Interfaces:**
- Consumes: `TaskNodeRunInfoRepositoryProtocol` (Task 1), `TaskNodeRunInfoModel`/`TaskNodeRunInfoRecord`/`TaskNodeRunInfoUpdate` (Task 1), `DatabasePlugin`, `conftest` `db` fixture.
- Produces: `TaskNodeRunInfoRepository`. Methods: `insert`, `update`, `get_latest`, `get_by_retry`, `list_by_task`, `list_by_assignee`, `list_by_run_mode`.

- [ ] **Step 1: Write the failing test**

`tests/community/repository/task/test_task_node_run_info_repository.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.repository.types import (
    TaskNodeRunInfoRecord,
    TaskNodeRunInfoUpdate,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)


def _run(task_id="T-1", node_id="N-1", retry=0, **kw) -> TaskNodeRunInfoRecord:
    base = dict(
        id=0, node_id=node_id, task_id=task_id, run_mode="single_bot",
        assignee="B-1", output=None, acceptance_result=None, retry=retry,
        session_id=None, extend_props=None, start_time=1000, update_time=None,
        end_time=None,
    )
    base.update(kw)
    return TaskNodeRunInfoRecord(**base)


def test_insert_get_by_retry(db):
    repo = TaskNodeRunInfoRepository(db)
    stored = repo.insert(_run(retry=0))
    assert stored.id > 0
    assert repo.get_by_retry("T-1", "N-1", 0) == stored


def test_duplicate_triple_raises(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0))
    with pytest.raises(IntegrityError):
        repo.insert(_run(retry=0))
    # same node, different retry is allowed (1:N).
    repo.insert(_run(retry=1))


def test_get_latest_is_max_retry(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0, start_time=1))
    repo.insert(_run(retry=2, start_time=2))
    repo.insert(_run(retry=1, start_time=3))
    latest = repo.get_latest("T-1", "N-1")
    assert latest is not None
    assert latest.retry == 2
    assert repo.get_latest("T-1", "missing") is None


def test_update_applies_only_non_none_fields(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(retry=0, run_mode="single_bot", assignee="B-1", output=None))
    changed = repo.update(
        "T-1", "N-1", 0,
        TaskNodeRunInfoUpdate(run_mode="coop_group", output={"k": "v"}),
    )
    assert changed is True
    row = repo.get_by_retry("T-1", "N-1", 0)
    assert row.run_mode == "coop_group"
    assert row.output == {"k": "v"}
    assert row.assignee == "B-1"  # untouched
    assert row.update_time is not None
    # no-op patch does not touch the row.
    assert repo.update("T-1", "N-1", 0, TaskNodeRunInfoUpdate()) is False
    assert repo.update("T-1", "missing", 0, TaskNodeRunInfoUpdate(run_mode="x")) is False


def test_list_by_assignee_and_run_mode(db):
    repo = TaskNodeRunInfoRepository(db)
    repo.insert(_run(node_id="N-1", assignee="B-1", run_mode="single_bot", start_time=10))
    repo.insert(_run(node_id="N-2", assignee="B-2", run_mode="coop_group", start_time=20))
    assert {r.node_id for r in repo.list_by_assignee("B-1")} == {"N-1"}
    assert {r.node_id for r in repo.list_by_run_mode("coop_group")} == {"N-2"}
    assert {r.node_id for r in repo.list_by_run_mode("single_bot", start_time_since=15)} == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_run_info_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: ...task_node_run_info_repository`.

- [ ] **Step 3: Create the implementation**

`core/repository/implementations/task/task_node_run_info_repository.py`:

```python
"""``TaskNodeRunInfoRepositoryProtocol`` implementation for ``task_node_run_info``.

The table is 1:N by ``retry`` per ``(task_id, node_id)``; "latest" = ``max(retry)``.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel
from agentclaw.community.core.task.repository.types import (
    TaskNodeRunInfoRecord,
    TaskNodeRunInfoUpdate,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRunInfoRepository(TaskNodeRunInfoRepositoryProtocol):
    """Unified ORM implementation for ``task_node_run_info`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeRunInfoModel

    @staticmethod
    def _to_row(record: TaskNodeRunInfoRecord) -> TaskNodeRunInfoModel:
        return TaskNodeRunInfoModel(
            node_id=record.node_id,
            task_id=record.task_id,
            run_mode=record.run_mode,
            assignee=record.assignee,
            output=json.dumps(record.output) if record.output is not None else None,
            acceptance_result=(
                json.dumps(record.acceptance_result)
                if record.acceptance_result is not None
                else None
            ),
            retry=record.retry,
            session_id=record.session_id,
            extend_props=(
                json.dumps(record.extend_props)
                if record.extend_props is not None
                else None
            ),
            start_time=record.start_time,
            update_time=record.update_time,
            end_time=record.end_time,
        )

    def insert(self, record: TaskNodeRunInfoRecord) -> TaskNodeRunInfoRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def update(
        self,
        task_id: str,
        node_id: str,
        retry: int,
        patch: TaskNodeRunInfoUpdate,
    ) -> bool:
        values: dict[str, Any] = {}
        if patch.run_mode is not None:
            values["run_mode"] = patch.run_mode
        if patch.assignee is not None:
            values["assignee"] = patch.assignee
        if patch.output is not None:
            values["output"] = json.dumps(patch.output)
        if patch.acceptance_result is not None:
            values["acceptance_result"] = json.dumps(patch.acceptance_result)
        if patch.session_id is not None:
            values["session_id"] = patch.session_id
        if patch.extend_props is not None:
            values["extend_props"] = json.dumps(patch.extend_props)
        if patch.start_time is not None:
            values["start_time"] = patch.start_time
        if patch.end_time is not None:
            values["end_time"] = patch.end_time
        if not values:
            return False
        # update_time: caller-supplied, else now-millis (the column has no DB default).
        values["update_time"] = (
            patch.update_time if patch.update_time is not None else int(time.time() * 1000)
        )
        with self._db.orm_session() as db:
            count = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                    self._model.retry == retry,
                )
                .update(values, synchronize_session=False)
            )
        return count > 0

    def get_latest(self, task_id: str, node_id: str) -> Optional[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id, self._model.node_id == node_id)
                .order_by(self._model.retry.desc())
                .first()
            )
            return row.to_record() if row else None

    def get_by_retry(
        self, task_id: str, node_id: str, retry: int
    ) -> Optional[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(
                    self._model.task_id == task_id,
                    self._model.node_id == node_id,
                    self._model.retry == retry,
                )
                .first()
            )
            return row.to_record() if row else None

    def list_by_task(self, task_id: str) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def list_by_assignee(
        self, assignee: str, *, limit: int = 100
    ) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.assignee == assignee)
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_run_mode(
        self,
        run_mode: str,
        *,
        start_time_since: Optional[int] = None,
        limit: int = 100,
    ) -> list[TaskNodeRunInfoRecord]:
        with self._db.orm_session() as db:
            q = db.query(self._model).filter(self._model.run_mode == run_mode)
            if start_time_since is not None:
                q = q.filter(self._model.start_time >= start_time_since)
            rows = q.limit(limit).all()
            return [r.to_record() for r in rows]
```

- [ ] **Step 4: Add `TaskNodeRunInfoRepository` to README `provides:`**

Append `- TaskNodeRunInfoRepository` under the `# task` implementations group.

- [ ] **Step 5: Run test to verify it passes + contract suite green**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_run_info_repository.py tests/community/architecture/test_repository_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_run_info_repository.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/test_task_node_run_info_repository.py
git commit -m "feat(task): add TaskNodeRunInfoRepository (1:N by retry, partial update)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: TaskNodeRelationRepository

**Files:**
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_relation_repository.py`
- Create: `src/backend/tests/community/repository/task/test_task_node_relation_repository.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add `TaskNodeRelationRepository` to `provides:`)

**Interfaces:**
- Consumes: `TaskNodeRelationRepositoryProtocol` (Task 1), `TaskNodeRelationModel`/`TaskNodeRelationRecord` (Task 1), `DatabasePlugin`, `conftest` `db` fixture.
- Produces: `TaskNodeRelationRepository`. Methods: `add_relations`, `list_relations`, `children`, `parents`.

- [ ] **Step 1: Write the failing test**

`tests/community/repository/task/test_task_node_relation_repository.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.domain.models import RelationType
from agentclaw.community.core.task.repository.types import TaskNodeRelationRecord
from agentclaw.community.core.repository.implementations.task.task_node_relation_repository import (
    TaskNodeRelationRepository,
)


def _rel(src, dst, task_id="T-1", extend_props=None) -> TaskNodeRelationRecord:
    return TaskNodeRelationRecord(
        id=0,
        task_id=task_id,
        src_node_id=src,
        dst_node_id=dst,
        relation_type=RelationType.DEPENDENCY,
        extend_props=extend_props,
    )


def test_add_and_list(db):
    repo = TaskNodeRelationRepository(db)
    n = repo.add_relations([_rel("N-1", "N-2"), _rel("N-1", "N-3", extend_props={"w": 1})])
    assert n == 2
    edges = repo.list_relations("T-1")
    assert {(e.src_node_id, e.dst_node_id) for e in edges} == {("N-1", "N-2"), ("N-1", "N-3")}
    assert repo.list_relations("missing") == []


def test_duplicate_edge_raises(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2")])
    with pytest.raises(IntegrityError):
        repo.add_relations([_rel("N-1", "N-2")])


def test_children_and_parents(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2"), _rel("N-1", "N-3"), _rel("N-2", "N-4")])
    assert {e.dst_node_id for e in repo.children("N-1")} == {"N-2", "N-3"}
    assert {e.src_node_id for e in repo.parents("N-4")} == {"N-2"}
    assert repo.children("N-4") == []


def test_to_relation_projection(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2", extend_props={"w": 1})])
    rec = repo.list_relations("T-1")[0]
    rel = rec.to_relation()
    assert rel.src_id == "N-1"
    assert rel.dst_id == "N-2"
    assert rel.type is RelationType.DEPENDENCY
    assert rel.extend_props == {"w": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_relation_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: ...task_node_relation_repository`.

- [ ] **Step 3: Create the implementation**

`core/repository/implementations/task/task_node_relation_repository.py`:

```python
"""``TaskNodeRelationRepositoryProtocol`` implementation for ``task_node_relation``."""
from __future__ import annotations

import json

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskNodeRelationRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskNodeRelationModel
from agentclaw.community.core.task.repository.types import TaskNodeRelationRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskNodeRelationRepository(TaskNodeRelationRepositoryProtocol):
    """Unified ORM implementation for ``task_node_relation`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskNodeRelationModel

    @staticmethod
    def _to_row(record: TaskNodeRelationRecord) -> TaskNodeRelationModel:
        return TaskNodeRelationModel(
            task_id=record.task_id,
            src_node_id=record.src_node_id,
            dst_node_id=record.dst_node_id,
            relation_type=record.relation_type.value,
            extend_props=(
                json.dumps(record.extend_props)
                if record.extend_props is not None
                else None
            ),
        )

    def add_relations(self, records: list[TaskNodeRelationRecord]) -> int:
        with self._db.orm_session() as db:
            for record in records:
                db.add(self._to_row(record))
            db.flush()
        return len(records)

    def list_relations(self, task_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = db.query(self._model).filter(self._model.task_id == task_id).all()
            return [r.to_record() for r in rows]

    def children(self, src_node_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model).filter(self._model.src_node_id == src_node_id).all()
            )
            return [r.to_record() for r in rows]

    def parents(self, dst_node_id: str) -> list[TaskNodeRelationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model).filter(self._model.dst_node_id == dst_node_id).all()
            )
            return [r.to_record() for r in rows]
```

- [ ] **Step 4: Add `TaskNodeRelationRepository` to README `provides:`**

Append `- TaskNodeRelationRepository` under the `# task` implementations group.

- [ ] **Step 5: Run test to verify it passes + contract suite green**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_node_relation_repository.py tests/community/architecture/test_repository_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/repository/implementations/task/task_node_relation_repository.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/test_task_node_relation_repository.py
git commit -m "feat(task): add TaskNodeRelationRepository for task_node_relation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: TaskCallbackRepository

**Files:**
- Create: `src/backend/src/agentclaw/community/core/repository/implementations/task/task_callback_repository.py`
- Create: `src/backend/tests/community/repository/task/test_task_callback_repository.py`
- Modify: `src/backend/src/agentclaw/community/core/repository/README.md` (add `TaskCallbackRepository` to `provides:`)

**Interfaces:**
- Consumes: `TaskCallbackRepositoryProtocol` (Task 1), `TaskCallbackModel`/`TaskCallbackRecord` (Task 1), `DatabasePlugin`, `conftest` `db` fixture.
- Produces: `TaskCallbackRepository`. Methods: `insert`, `get`, `list_by_session`.

- [ ] **Step 1: Write the failing test**

`tests/community/repository/task/test_task_callback_repository.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)


def _cb(run_id="R-1", node_id="N-1", session="S-1", **kw) -> TaskCallbackRecord:
    base = dict(
        id=0, invoker="bcs", run_id=run_id, node_id=node_id, main_session_id=session,
        status="completed", orig_callback_data='{"raw": 1}', execution_graph=None,
        result={"success": True}, result_success=True, exec_error=None, extend_props=None,
    )
    base.update(kw)
    return TaskCallbackRecord(**base)


def test_insert_get_roundtrip(db):
    repo = TaskCallbackRepository(db)
    stored = repo.insert(_cb())
    assert stored.id > 0
    assert repo.get("R-1", "N-1") == stored
    assert stored.result == {"success": True}
    assert stored.orig_callback_data == '{"raw": 1}'


def test_duplicate_run_node_raises(db):
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1"))
    with pytest.raises(IntegrityError):
        repo.insert(_cb(run_id="R-1", node_id="N-1"))
    # different node_id under same run_id is allowed.
    repo.insert(_cb(run_id="R-1", node_id="N-2"))


def test_list_by_session(db):
    repo = TaskCallbackRepository(db)
    repo.insert(_cb(run_id="R-1", node_id="N-1", session="S-1"))
    repo.insert(_cb(run_id="R-2", node_id="N-2", session="S-1"))
    repo.insert(_cb(run_id="R-3", node_id="N-3", session="S-2"))
    rows = repo.list_by_session("S-1")
    assert {r.run_id for r in rows} == {"R-1", "R-2"}
    assert repo.list_by_session("missing") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_callback_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: ...task_callback_repository`.

- [ ] **Step 3: Create the implementation**

`core/repository/implementations/task/task_callback_repository.py`:

```python
"""``TaskCallbackRepositoryProtocol`` implementation for ``task_callback``."""
from __future__ import annotations

import json
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
)
from agentclaw.community.core.task.repository.models import TaskCallbackModel
from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.plugin_api.database import DatabasePlugin


def _dumps(value) -> Optional[str]:
    return json.dumps(value) if value is not None else None


class TaskCallbackRepository(TaskCallbackRepositoryProtocol):
    """Unified ORM implementation for ``task_callback`` (SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskCallbackModel

    @staticmethod
    def _to_row(record: TaskCallbackRecord) -> TaskCallbackModel:
        return TaskCallbackModel(
            invoker=record.invoker,
            run_id=record.run_id,
            node_id=record.node_id,
            main_session_id=record.main_session_id,
            status=record.status,
            orig_callback_data=record.orig_callback_data,
            execution_graph=_dumps(record.execution_graph),
            result=_dumps(record.result),
            result_success=record.result_success,
            exec_error=record.exec_error,
            extend_props=_dumps(record.extend_props),
        )

    def insert(self, rec: TaskCallbackRecord) -> TaskCallbackRecord:
        with self._db.orm_session() as db:
            row = self._to_row(rec)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get(self, run_id: str, node_id: str) -> Optional[TaskCallbackRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.run_id == run_id, self._model.node_id == node_id)
                .first()
            )
            return row.to_record() if row else None

    def list_by_session(
        self, main_session_id: str, *, limit: int = 100
    ) -> list[TaskCallbackRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._model)
                .filter(self._model.main_session_id == main_session_id)
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]
```

- [ ] **Step 4: Add `TaskCallbackRepository` to README `provides:`**

Append `- TaskCallbackRepository` under the `# task` implementations group.

- [ ] **Step 5: Run test to verify it passes + contract suite green**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_callback_repository.py tests/community/architecture/test_repository_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/repository/implementations/task/task_callback_repository.py \
        src/backend/src/agentclaw/community/core/repository/README.md \
        src/backend/tests/community/repository/task/test_task_callback_repository.py
git commit -m "feat(task): add TaskCallbackRepository for task_callback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: DDL files (operator-provisioned)

**Files:**
- Create: `src/backend/src/agentclaw/community/core/task/sql/2026_08_20_task_info.sql`
- Create: `src/backend/src/agentclaw/community/core/task/sql/2026_08_20_task_node.sql`
- Create: `src/backend/src/agentclaw/community/core/task/sql/2026_08_20_task_node_run_info.sql`
- Create: `src/backend/src/agentclaw/community/core/task/sql/2026_08_20_task_node_relation.sql`
- Create: `src/backend/src/agentclaw/community/core/task/sql/2026_08_20_task_callback.sql`

**Interfaces:** None (artifacts). These are applied by operators; the app never runs them in prod. SQLite tests use `Base.metadata.create_all`. The ORM renders plain unique indexes; `BLOCK_SIZE … LOCAL` is OceanBase-only (mirrors `core/task_queue/README.md`).

- [ ] **Step 1: Create the 5 DDL files**

`core/task/sql/2026_08_20_task_info.sql`:

```sql
-- task_info: task-level source record. Operator-provisioned in prod (OceanBase).
-- The ORM (core/task/repository/models.py) renders a plain unique index; the
-- BLOCK_SIZE/LOCAL modifier below is OceanBase-only and not expressible in SQLAlchemy.
CREATE TABLE IF NOT EXISTS `task_info` (
    `id`                                    bigint(20)         NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `task_id`             varchar(128)  NOT NULL                  COMMENT '任务id',
    `source_type`                 varchar(128)  NOT NULL                  COMMENT '触发渠道类型 bot|coop_group',
    `owner_user_id`           varchar(256)  NOT NULL                  COMMENT 'userId',
    `owner_bot_id`             varchar(256)  NOT NULL                  COMMENT 'botId',
    `execution_config`    text          DEFAULT NULL              COMMENT '用户指定的执行配置',
    `task_spec`                    text          NOT NULL                      COMMENT '任务信息',
    `status`              varchar(64)   NOT NULL                COMMENT '节点状态',
    `gmt_create`          timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`        timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_id` (`task_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_status` (`status`, `gmt_modified`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务来源信息';
```

`core/task/sql/2026_08_20_task_node.sql`:

```sql
-- task_node: node spec + status. Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node` (
    `id`                         bigint(20)         NOT NULL AUTO_INCREMENT         COMMENT '主键ID',
    `task_id`        varchar(128)      NOT NULL                    COMMENT '归属任务ID',
    `node_id`        varchar(128)      NOT NULL                    COMMENT '节点唯一实例ID',
    `task_spec`         text           NOT NULL                            COMMENT '任务信息',
    `status`         varchar(64)       NOT NULL                    COMMENT '节点状态',
    `gmt_create`     timestamp         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`   timestamp         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    KEY `idx_task_status` (`task_id`, `status`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务执行节点';
```

`core/task/sql/2026_08_20_task_node_run_info.sql`:

```sql
-- task_node_run_info: node runtime info, 1:N by retry per (task_id, node_id).
-- Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node_run_info` (
    `id`                                 bigint(20)      NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `node_id`            varchar(128)  NOT NULL                COMMENT '节点 ID(1:1 task_node)',
    `task_id`            varchar(128)  NOT NULL                COMMENT 'task_id',
    `run_mode`           varchar(64)   DEFAULT NULL            COMMENT '执行模态：single_bot|coop_group|bbs',
    `assignee`           varchar(1024) DEFAULT NULL            COMMENT '执行者 bot_id / group_id',
    `output`             text          DEFAULT NULL            COMMENT '执行产出',
    `acceptance_result`  text          DEFAULT NULL            COMMENT '验收结果 JSON: {verdict,acceptances_metric,gaps}',
    `retry`                    int           DEFAULT 0               COMMENT '第几次重试',
    `session_id`            varchar(256)  DEFAULT NULL            COMMENT 'session_id',
    `extend_props`       text          DEFAULT NULL            COMMENT '扩展属性,json格式',
    `start_time`         bigint(20)    unsigned DEFAULT NULL   COMMENT '开始执行时间',
    `update_time`        bigint(20)    unsigned DEFAULT NULL   COMMENT '执行最近更新时间',
    `end_time`           bigint(20)    unsigned DEFAULT NULL   COMMENT '结束执行时间',
    `gmt_create`         timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`       timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_node` (`task_id`, `node_id`, `retry`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_task` (`task_id`),
    KEY `idx_assignee` (`assignee`),
    KEY `idx_run_mode_status_time` (`run_mode`, `start_time`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点运行时执行信息';
```

`core/task/sql/2026_08_20_task_node_relation.sql`:

```sql
-- task_node_relation: decomposition-tree edges. Operator-provisioned in prod (OceanBase).
CREATE TABLE IF NOT EXISTS `task_node_relation` (
    `id`                        bigint(20)         NOT NULL AUTO_INCREMENT                         COMMENT '主键ID',
    `task_id`       varchar(128)     NOT NULL                              COMMENT '归属任务 ID',
    `src_node_id`   varchar(128)     NOT NULL                              COMMENT '父节点',
    `dst_node_id`   varchar(128)     NOT NULL                              COMMENT '子节点',
    `relation_type` varchar(64)      NOT NULL DEFAULT 'DEPENDENCY'         COMMENT '关系类型',
    `extend_props`  text          DEFAULT NULL                                                COMMENT '扩展信息',
    `gmt_create`    timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP     COMMENT '创建时间',
    `gmt_modified`  timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_src_dst` (`src_node_id`, `dst_node_id`) BLOCK_SIZE 16384 LOCAL,
    KEY `idx_src` (`task_id`, `src_node_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='任务节点关系(Relation)';
```

`core/task/sql/2026_08_20_task_callback.sql` (note D5.1/D5.3 fix on `node_id`):

```sql
-- task_callback: received callback audit. Operator-provisioned in prod (OceanBase).
-- D5.1: node_id is NOT NULL (was DEFAULT NULL) so uk_workflow_instance dedups.
-- D5.3: node_id is varchar(128) (was varchar(512)), consistent with task_node.node_id.
CREATE TABLE IF NOT EXISTS `task_callback` (
    `id`                                     bigint(20)         NOT NULL AUTO_INCREMENT                  COMMENT '主键ID',
    `invoker`                    varchar(128)  NOT NULL                         COMMENT '回调服务调用者',
    `run_id`                     varchar(512)     NOT NULL                         COMMENT '运行实例ID',
    `node_id`                 varchar(128)     NOT NULL                     COMMENT '内部',
    `main_session_id`       varchar(256)     NOT NULL                         COMMENT '主session_id',
    `status`                           varchar(64)     DEFAULT NULL                     COMMENT '状态',
    `orig_callback_data`  text          NOT NULL                         COMMENT '原始上报数据',
    `execution_graph`       text             DEFAULT NULL                     COMMENT '解析之后的执行状态图',
    `result`                     text             DEFAULT NULL                     COMMENT '产出结果',
    `result_success`             tinyint(1)       DEFAULT NULL                     COMMENT '是否成功',
    `exec_error`                 text                     DEFAULT NULL                     COMMENT '错误信息',
    `extend_props`                 text             DEFAULT NULL                               COMMENT '扩展属性',
    `gmt_create`                 timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `gmt_modified`               timestamp        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_workflow_instance` (`run_id`, `node_id`),
    KEY `idx_session_id` (`main_session_id`)
) DEFAULT CHARSET = utf8mb4 COMMENT='节点执行回调记录';
```

- [ ] **Step 2: Verify DDL table names match ORM `__tablename__`**

Run (one-shot check; not a committed test — the parity is structural):

```bash
cd src/backend && .venv/bin/python -c "
from agentclaw.community.core.task.repository import models as m
for t in ('task_info','task_node','task_node_run_info','task_node_relation','task_callback'):
    print(t)
import pathlib, re
sql = pathlib.Path('src/agentclaw/community/core/task/sql').glob('*.sql')
names = sorted(re.search(r'CREATE TABLE IF NOT EXISTS \`(\w+)\`', p.read_text()).group(1) for p in sql)
print('sql:', names)
"
```
Expected: the 5 printed table names match the 5 SQL file names exactly (no `ac_` prefix).

- [ ] **Step 3: Commit**

```bash
git add src/backend/src/agentclaw/community/core/task/sql
git commit -m "feat(task): add DDL for the 5 task tables (OceanBase, operator-provisioned)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: DI wiring + bootstrap model registration

**Files:**
- Create: `src/backend/src/agentclaw/community/di/modules/task_persistence_module.py`
- Modify: `src/backend/src/agentclaw/community/di/container.py` (+import, +`TaskPersistenceModule()` in `build_injector()` base list)
- Modify: `src/backend/src/agentclaw/community/plugins/local/database.py` (+side-effect import of task models in `SqliteDB.bootstrap()`)
- Test: `src/backend/tests/community/repository/task/test_task_persistence_module.py`

**Interfaces:**
- Consumes: the 5 `*RepositoryProtocol` + 5 `*Repository` (Tasks 1–6); `DatabasePlugin` (provided per-profile by existing modules).
- Produces: `TaskPersistenceModule` (profile-independent; bound into the injector base list so any service can `@inject` the protocols). After this task the 5 protocols resolve from a built injector.

- [ ] **Step 1: Write the failing test**

`tests/community/repository/task/test_task_persistence_module.py`:

```python
"""DI wiring: TaskPersistenceModule binds the 5 protocols to their impls as
singletons, on top of a TestingDatabaseModule-provided DatabasePlugin."""
from injector import Injector

from agentclaw.community.di.modules.testing_database_module import TestingDatabaseModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)


def _injector() -> Injector:
    return Injector([TestingDatabaseModule(), TaskPersistenceModule()])


def test_each_protocol_resolves_to_its_impl():
    inj = _injector()
    assert isinstance(inj.get(TaskInfoRepositoryProtocol), TaskInfoRepository)
    assert inj.get(TaskNodeRepositoryProtocol) is not None
    assert inj.get(TaskNodeRunInfoRepositoryProtocol) is not None
    assert inj.get(TaskNodeRelationRepositoryProtocol) is not None
    assert inj.get(TaskCallbackRepositoryProtocol) is not None


def test_bindings_are_singletons():
    inj = _injector()
    assert inj.get(TaskInfoRepositoryProtocol) is inj.get(TaskInfoRepositoryProtocol)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/test_task_persistence_module.py -v`
Expected: FAIL — `ModuleNotFoundError: ...task_persistence_module`.

- [ ] **Step 3: Create the DI module**

`di/modules/task_persistence_module.py`:

```python
"""TaskPersistenceModule — binds the 5 task repository protocols to their ORM
implementations as singletons.

Profile-independent: the only per-profile difference is the ``DatabasePlugin``
injected into each constructor, which is bound one layer below by the profile's
infrastructure module (CommunityDatabase / SqliteDB / corp ZdasDB). Mirrors
``TaskQueueModule``.
"""
from injector import Binder, Module, singleton

from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_relation_repository import (
    TaskNodeRelationRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)


class TaskPersistenceModule(Module):
    """Bind the 5 task repository contracts to their unified ORM implementations."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TaskInfoRepositoryProtocol, to=TaskInfoRepository, scope=singleton)
        binder.bind(TaskNodeRepositoryProtocol, to=TaskNodeRepository, scope=singleton)
        binder.bind(
            TaskNodeRunInfoRepositoryProtocol,
            to=TaskNodeRunInfoRepository,
            scope=singleton,
        )
        binder.bind(
            TaskNodeRelationRepositoryProtocol,
            to=TaskNodeRelationRepository,
            scope=singleton,
        )
        binder.bind(
            TaskCallbackRepositoryProtocol,
            to=TaskCallbackRepository,
            scope=singleton,
        )
```

- [ ] **Step 4: Register the module in `di/container.py`**

Add the import alphabetically near the other module imports (after the `task_queue_module` import line):

```python
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
```

In `build_injector()`'s `modules: list[Module] = [...]` base list, add `TaskPersistenceModule(),` immediately after the `TaskQueueModule(),` entry.

- [ ] **Step 5: Register the models in `plugins/local/database.py::SqliteDB.bootstrap()`**

In the side-effect import list inside `bootstrap()` (where `import agentclaw.community.core.task_queue.repository.models  # noqa: F401` already appears), append:

```python
        import agentclaw.community.core.task.repository.models  # noqa: F401  task_info / task_node / task_node_run_info / task_node_relation / task_callback
```

- [ ] **Step 6: Run the DI test + the full task repo suite + contract suite**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community/repository/task/ tests/community/architecture/test_repository_contracts.py -v`
Expected: all PASS.

- [ ] **Step 7: Run an app-boot smoke (the new module must resolve inside the real base list)**

Run: `cd src/backend && .venv/bin/python -m pytest tests/community -k "bootstrap or boot or container or eager" -v`
Expected: PASS (no new critical binding is required by `eager_check_critical_bindings`, and `TaskPersistenceModule` only needs `DatabasePlugin`, which every profile provides). If the chosen `-k` filter selects nothing, run the existing boot test by name instead — search with `grep -rln "build_injector" tests/community | head` and run that file.

- [ ] **Step 8: Commit**

```bash
git add src/backend/src/agentclaw/community/di/modules/task_persistence_module.py \
        src/backend/src/agentclaw/community/di/container.py \
        src/backend/src/agentclaw/community/plugins/local/database.py \
        src/backend/tests/community/repository/task/test_task_persistence_module.py
git commit -m "feat(task): wire task persistence repositories into DI + bootstrap

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Final verification (no commit unless something needs fixing)

**Files:** none (verification only).

- [ ] **Step 1: Run the full task-persistence surface + architecture guards**

```bash
cd src/backend && .venv/bin/python -m pytest \
  tests/community/repository/task/ \
  tests/community/architecture/test_repository_contracts.py \
  -v
```
Expected: all green.

- [ ] **Step 2: Run SAST/lint on the new files (pre-push gate runs lint-only by default)**

```bash
cd src/backend && .venv/bin/python -m flake8 src/agentclaw/community/core/task/repository \
  src/agentclaw/community/core/repository/protocols/task.py \
  src/agentclaw/community/core/repository/implementations/task \
  src/agentclaw/community/di/modules/task_persistence_module.py \
  tests/community/repository/task 2>/dev/null || .venv/bin/python -m antflake ...
```
(Use the project's actual lint entrypoint — confirm via the pre-push hook in `AGENTS.md`. Expected: no errors. `# noqa: F401` covers the side-effect imports.)

- [ ] **Step 3: Confirm no existing task-module tests regressed**

```bash
cd src/backend && .venv/bin/python -m pytest tests/community/core/task -v 2>/dev/null || echo "no task core tests dir — skip"
```
Expected: green (or skipped). The in-memory `TaskModule` (`di/modules/task_module.py`) is untouched, so existing task tests must not change.

- [ ] **Step 4: If Steps 1–3 are all green, report done. If anything regressed, fix in a new commit.**

---

## Self-Review (run before handoff)

**1. Spec coverage (spec §2 decisions, §4 layout, §5–§11):**
- D1 no prefix → all ORM `__tablename__` + DDL use bare names. ✓ (Task 1, 7)
- D2 acceptance_result JSON → stored `json.dumps`, restored `_loads` → dict + `to_acceptance_result()`. ✓ (Task 1, 4)
- D4 one repo per table → 5 protocols + 5 impls, Tasks 2–6. ✓
- D5.1 node_id NOT NULL → model `nullable=False` + DDL; verified by schema test (`test_five_tables_build_with_key_columns`). ✓
- D5.2 1:N by retry, latest=max(retry) → `get_latest` orders `retry.desc()`; test `test_get_latest_is_max_retry`. ✓
- D5.3 node_id varchar(128) → model `_NODE_ID` (128) + DDL. ✓
- §4 layout → File Structure matches exactly. ✓
- §6 records + projections → `Relation`/`AcceptanceResult` projections present; `TaskSpec`/`TaskInfo`/`RuntimeInfo` projections explicitly deferred (documented in `types.py` + spec §3). ✓ (deviation from spec §6's `to_task_info()` — flagged in `types.py` docstring; spec §3 already defers graph-state gaps).
- §7 protocol method sets → match exactly. ✓
- §8 serialization rules → enums `.value`, TEXT→dict/raw-str, `result_success` bool, `retry` int, millis ints. ✓
- §9 DI wiring → `TaskPersistenceModule` + `container.py` base list. ✓
- §10 DDL → 5 files + `BLOCK_SIZE LOCAL`/`GLOBAL` notes. ✓
- §11 tests → 5 repo tests + conftest + schema + DI + architecture lockstep. ✓
- Bootstrap import (spec §4 addendum) → Task 8 Step 5. ✓

**2. Placeholder scan:** none — every code step has full code; every run step has an exact command and expected result. No "TBD"/"similar to"/"add error handling".

**3. Type consistency:** `TaskNodeRunInfoUpdate` defined in Task 1, used in Task 4 protocol + impl + test — same fields. `insert`/`get`/`update_status`/`list_by_status` names match between protocol (Task 1) and impls (Tasks 2–3). `get_latest`/`get_by_retry`/`list_by_run_mode` match. `add_relations`/`children`/`parents` match. Callback `insert`/`get`/`list_by_session` match. README `provides` gains exactly the names the impls define — `test_readme_provides_lists_the_real_public_surface` stays green at every commit.

**4. Scope discipline:** scope A only — no `TaskGraphService`/`TaskService`/`task_module.py` changes; deferred gaps documented, not solved.
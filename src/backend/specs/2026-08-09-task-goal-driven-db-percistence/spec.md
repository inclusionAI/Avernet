# Task Persistence Layer — Design Spec

- **Date:** 2026-08-20
- **Scope:** A — database + persistence layer + DI auto-wiring only. Does **not** rewire the existing in-memory `TaskGraphService`/`TaskService`.
- **Status:** Draft, pending user review → then implementation plan.
- **Template followed:** `core/task_queue` persistence stack (the closest sibling in this repo).

## 1. Goal

Persist the 5 collaboration-task tables supplied as DDL (`task_info`, `task_node`,
`task_node_run_info`, `task_node_relation`, `task_callback`) behind a repository
layer that mirrors the existing `task_queue` contract, and register the
repositories in the DI container so they are auto-wireable by any service that
asks for their Protocol. This step ships the persistence surface; services do
not consume it yet (that is a later, separately-scoped change).

## 2. Locked decisions

| # | Decision | Resolution |
|---|---|---|
| D1 | Table-name prefix | **None.** ORM `__tablename__` and DDL match the supplied SQL verbatim (`task_info`, …). The repo-wide `ac_` convention is intentionally **not** applied here. |
| D2 | `acceptance_result` column | Stored as **JSON** of domain `AcceptanceResult{verdict, acceptances_metric, gaps}` (not the bare `PASS\|FAIL` the DDL comment suggests). |
| D4 | Repository decomposition | **One repository per table, no merging.** 5 protocols + 5 implementations. (`task_node` and `task_node_run_info` remain separate even though 1:1.) |
| D5.1 | `task_callback.uk_workflow_instance` | `node_id` is **`NOT NULL`** (fixes silent dedup skip under NULL). |
| D5.2 | `task_node_run_info` ↔ `task_node` | **1:N by `retry`**; "latest" = `max(retry)` for a `(task_id, node_id)`. |
| D5.3 | `task_callback.node_id` length | **`varchar(128)`** (was `varchar(512)`), consistent with `task_node.node_id`. |

**Assumed (object unless you raise it):**
- `task_callback.run_id` stays `varchar(512)` — it is the workflow engine's run
  instance id (a string), a different identifier space from the domain graph's
  `run_id: int`. Not re-typed.
- DDL `BLOCK_SIZE 16384 LOCAL` modifiers are kept verbatim where the supplied
  SQL had them (`task_info`, `task_node_run_info`, `task_node_relation` UKs).
- No `env` column (none of these tables have one; `env`-scoping is out of scope).
- `task_info.status` is treated as the task-level `Status` enum.
- Separate `TaskPersistenceModule` (not folded into the existing `task_module.py`).

## 3. Known contract gaps (deferred — NOT solved in this scope)

The 5 supplied tables cannot fully persist the in-memory
`TaskExecutionGraph` (see `core/task/domain/models.py`):

- **No home for graph-level `loop_round`, graph `output`, graph `extend_props`.**
  `task_info` carries `status`/`task_spec`/`execution_config` only; it has no
  `loop_round`/`output`/`extend_props` columns.
- **`run_id` appears only in `task_callback`.** The graph's `run_id` is not
  persisted on `task_info`/`task_node`.
- **`RuntimeInfo.action_log` (append-only node-action history) has no column.**
  `task_node_run_info` stores only the latest single-value runtime fields.

This spec persists **exactly** the 5 tables' columns. Closing these gaps (a
6th table, or extending columns) is a follow-up scope-B decision and is out of
scope here. The deferred fields are documented in `types.py` docstrings.

## 4. File layout

Net-new files (all under `src/backend/src/agentclaw/community/`):

```
core/task/repository/
├── __init__.py
├── models.py                       # 5 ORM models, Convention A, mirrors TaskQueueModel
├── types.py                        # 5 table-faithful record dataclasses + projection helpers
└── sql/
    ├── 2026_08_20_task_info.sql
    ├── 2026_08_20_task_node.sql
    ├── 2026_08_20_task_node_run_info.sql
    ├── 2026_08_20_task_node_relation.sql
    └── 2026_08_20_task_callback.sql   # node_id NOT NULL, varchar(128)

core/repository/protocols/
└── task.py                         # 5 Protocol classes (@runtime_checkable, TYPE_CHECKING-only)

core/repository/implementations/
└── task/
    ├── __init__.py
    ├── task_info_repository.py
    ├── task_node_repository.py
    ├── task_node_run_info_repository.py
    ├── task_node_relation_repository.py
    └── task_callback_repository.py

di/modules/task_persistence_module.py   # binds 5 Protocol→Impl, singleton
```

Edits to existing contract surfaces:
- `di/container.py` — import `TaskPersistenceModule` and add `TaskPersistenceModule()`
  to `build_injector()`'s base module list.
- `core/repository/README.md` — context-boundary YAML: add the `task` domain and
  the 5 protocol + 5 impl type names.
- `tests/community/architecture/test_repository_contracts.py` — wherever it
  enumerates protocols/impls, include the new `task` domain so the
  enforceability guard (every member abstract, every impl based, no runtime
  domain import in `protocols/`) covers them.
- `plugins/local/database.py` — `SqliteDB.bootstrap()` side-effect-imports every
  ORM model so `Base.metadata.create_all` builds the tables in local/singlebox
  (see its existing list, e.g. `core.task_queue.repository.models`). Append
  `import agentclaw.community.core.task.repository.models  # noqa: F401` to that
  list, or the 5 new tables are invisible to `create_all` and the first request
  crashes with `no such table: task_info`. (Prod tables are operator-provisioned;
  unit tests side-effect-import the models themselves, so neither path is
  affected — only the app-boot local path needs this line.)

Tests (net-new under `src/backend/tests/community/repository/task/`):

```
test_task_info_repository.py
test_task_node_repository.py
test_task_node_run_info_repository.py
test_task_node_relation_repository.py
test_task_callback_repository.py
```

## 5. ORM models (`core/task/repository/models.py`)

Import `Base` from `core/base.py`. Each model is `class <Name>Model(Base)` with
`__tablename__` = the exact supplied table name (no prefix). Conventions lifted
from `TaskQueueModel` (`core/task_queue/repository/models.py`):

- PK: re-declare locally
  `AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")`
  (SQLite autoincrement needs `INTEGER PRIMARY KEY`; prod stays `BIGINT`).
- Unique-key columns holding caller-supplied identifiers (`task_id`, `node_id`,
  `run_id`, `src_node_id`, `dst_node_id`) use the `_binary_string(length)`
  helper → `with_variant(mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql")`
  so OceanBase PAD-SPACE / case-fold cannot merge two distinct IDs in a unique
  index. Plain `String(length)` on SQLite (which ignores collation/length anyway).
- `gmt_create`/`gmt_modified`: `Column(DateTime, default=func.now(),
  onupdate=func.now(), nullable=False)`; DDL uses
  `DEFAULT CURRENT_TIMESTAMP [ON UPDATE CURRENT_TIMESTAMP]`.
- `__table_args__` = `Index(name, *cols, unique=True)` for UKs and plain
  `Index(name, *cols)` for secondaries.
- **OceanBase-only modifiers (`BLOCK_SIZE 16384 LOCAL`/`GLOBAL`) are
  ORM-unrepresentable** → they appear only in the DDL files + a README note,
  never in the ORM. Same approach as `task_queue` (documented at
  `core/task_queue/README.md:237-240`).

Column → SQLAlchemy type mapping:

| SQL column | ORM type |
|---|---|
| `bigint(20) AUTO_INCREMENT` PK | `AutoIncrementBigInteger` |
| `varchar(n)` (FREE text, not in a UK) | `String(n)` |
| `varchar(n)` in a UK (ID-ish) | `_binary_string(n)` |
| `text` | `Text` |
| `timestamp` (gmt_*) | `DateTime` + `func.now()`/`onupdate` |
| `bigint(20) unsigned` (start/update/end_time) | `BigInteger().with_variant(Integer, "sqlite")`, nullable |
| `int` (retry) | `Integer`, default 0 |
| `tinyint(1)` (result_success) | `Boolean`, nullable |

Each model carries `to_record()` → its record dataclass (§6); row construction
goes through a `from_record()` classmethod or a module-level `_apply(record, model)`.

## 6. Record dataclasses + projections (`core/task/repository/types.py`)

Mirror `core/task_queue/types.py`: frozen, table-faithful dataclasses returned
by repositories (never ORM objects). One per table:

`TaskInfoRecord`, `TaskNodeRecord`, `TaskNodeRunInfoRecord`,
`TaskNodeRelationRecord`, `TaskCallbackRecord`.

Plus one lightweight **update** dataclass for the only table whose mutable
fields are many and partial (mirrors the domain's `TaskNodePatch`/`TaskGraphPatch`
"None = don't touch" idiom):

`TaskNodeRunInfoUpdate` — all-`Optional` fields (`run_mode`, `assignee`,
`output`, `acceptance_result`, `session_id`, `extend_props`, `start_time`,
`update_time`, `end_time`); a `None` field means "leave the row unchanged".
(`task_info` and `task_node` only mutate `status` → no patch dataclass, just
`update_status`; `task_node_relation` and `task_callback` are append-only.)

Projections onto existing domain dataclasses (`core/task/domain/models.py`),
with documented fidelity:

| Record | Domain type | Fidelity |
|---|---|---|
| `TaskNodeRelationRecord` | `Relation` | **Clean 1:1.** `to_relation()` / `from_relation()`. (`src_node_id`↔`src_id`, `dst_node_id`↔`dst_id`, `relation_type`↔`type`, `extend_props`.) |
| `TaskNodeRunInfoRecord` | `RuntimeInfo` | **Partial.** Maps `run_mode`/`assignee`/`start_time`/`end_time`/`output`/`extend_props`; `acceptance_result` JSON↔`AcceptanceResult`. **`action_log` has no column → dropped** (deferred, §3). Table adds `retry`/`session_id`/`update_time` not on the domain type. |
| `TaskInfoRecord` | `TaskInfo` | **Lossy subset.** Table is richer (`task_id`/`owner_user_id`/`owner_bot_id`/`status` not on the domain `TaskInfo`). `to_task_info()` projects the overlap (`task_spec`, `execution_config`, source-channel mapping); the extra columns stay on the record. |
| `TaskNodeRecord` | — (no direct single) | Holds `task_spec` (+ `to_task_spec()`) and `status`. Full `TaskNode` reconstruction needs the graph ref → scope B. |
| `TaskCallbackRecord` | — (none) | Pure table-faithful audit row. (`TaskCallbackData` is the inbound protocol, not this row.) |

## 7. Protocols + repositories

`core/repository/protocols/task.py`: 5
`@runtime_checkable` `Protocol` classes with `@abstractmethod` members,
`from __future__ import annotations` + `if TYPE_CHECKING:` imports of the
record dataclasses (so the architecture guard sees no runtime domain import).
Returns are record dataclasses, `str | None`, `bool`, or `list[...]` — never
ORM objects.

Impls under `core/repository/implementations/task/`, each
`class <Name>Repository(<Name>RepositoryProtocol)` with
`@inject def __init__(self, db: DatabasePlugin)` and
`with self._db.orm_session() as db:` per method. `IntegrityError` → a domain
error (mirror `task_queue`'s conflict classification).

Method sets (aligned to the eventual graph API but **not invoked in scope A**):

### TaskInfoRepository — `task_info`
- `insert(record: TaskInfoRecord) -> TaskInfoRecord`  (UK on `task_id`;
  conflict → `IntegrityError` → domain error. No dialect-specific `ON CONFLICT`/
  `ON DUPLICATE` — mirror `task_queue`'s insert-then-classify-conflict.)
- `get(task_id: str) -> TaskInfoRecord | None`
- `update_status(task_id: str, status: Status) -> bool`  (row-count CAS)
- `list_by_status(status: Status, *, gmt_modified_since: datetime | None = None, limit: int = 100) -> list[TaskInfoRecord]`

### TaskNodeRepository — `task_node`
- `insert(record: TaskNodeRecord) -> TaskNodeRecord`  (UK-free; de-dup is the
  caller's responsibility — a node is inserted once per `(task_id, node_id)`.)
- `get(task_id: str, node_id: str) -> TaskNodeRecord | None`
- `update_status(task_id: str, node_id: str, status: Status) -> bool`
- `list_nodes(task_id: str) -> list[TaskNodeRecord]`
- `list_by_status(task_id: str | None, status: Status, *, limit: int = 100) -> list[TaskNodeRecord]`

### TaskNodeRunInfoRepository — `task_node_run_info`
- `insert(record: TaskNodeRunInfoRecord) -> TaskNodeRunInfoRecord`  (UK
  `(task_id, node_id, retry)`; conflict → domain error.)
- `update(task_id: str, node_id: str, retry: int, patch: TaskNodeRunInfoUpdate) -> bool`
   (applies only the non-`None` fields; writes `update_time` when any field
   changes.)
- `get_latest(task_id: str, node_id: str) -> TaskNodeRunInfoRecord | None`  (`max(retry)`)
- `get_by_retry(task_id: str, node_id: str, retry: int) -> TaskNodeRunInfoRecord | None`
- `list_by_task(task_id: str) -> list[TaskNodeRunInfoRecord]`
- `list_by_assignee(assignee: str, *, limit: int = 100) -> list[TaskNodeRunInfoRecord]`
- `list_by_run_mode(run_mode: str, *, start_time_since: int | None = None, limit: int = 100) -> list[TaskNodeRunInfoRecord]`
   (backs `idx_run_mode_status_time`; note this table has **no `status`**
   column — status lives on `task_node` — so the index is effectively
   `(run_mode, start_time)` despite its name.)

### TaskNodeRelationRepository — `task_node_relation`
- `add_relations(records: list[TaskNodeRelationRecord]) -> int`
- `list_relations(task_id: str) -> list[TaskNodeRelationRecord]`
- `children(src_node_id: str) -> list[TaskNodeRelationRecord]`
- `parents(dst_node_id: str) -> list[TaskNodeRelationRecord]`

### TaskCallbackRepository — `task_callback`
- `record(rec: TaskCallbackRecord) -> TaskCallbackRecord`  (insert; idempotent
  on UK `(run_id, node_id)`; `node_id` is NOT NULL per D5.1.)
- `get(run_id: str, node_id: str) -> TaskCallbackRecord | None`
- `list_by_session(main_session_id: str, *, limit: int = 100) -> list[TaskCallbackRecord]`

## 8. Column serialization rules

- `Status` / `AcceptanceVerdict` / `RelationType` (`StrEnum`) → store `.value`,
  restore via ctor (`Status(row.status)`).
- TEXT-as-JSON (`task_spec`, `execution_config`, `output`, `extend_props`,
  `execution_graph`, `orig_callback_data`, `result`, `exec_error`,
  `acceptance_result`) → `json.dumps`/`json.loads`.
  - `task_spec` ↔ domain `TaskSpec` (projection helper).
  - `acceptance_result` ↔ `AcceptanceResult` (D2).
  - `execution_config`/`output`/`extend_props` ↔ `dict[str, Any]`.
- `result_success` `tinyint(1)` → `bool | None`.
- `retry` `int` default 0.
- `start_time`/`update_time`/`end_time` `bigint unsigned` → `int | None` (ms).

## 9. DI wiring

New `di/modules/task_persistence_module.py`:

```python
class TaskPersistenceModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(TaskInfoRepositoryProtocol,        to=TaskInfoRepository,        scope=singleton)
        binder.bind(TaskNodeRepositoryProtocol,        to=TaskNodeRepository,        scope=singleton)
        binder.bind(TaskNodeRunInfoRepositoryProtocol, to=TaskNodeRunInfoRepository, scope=singleton)
        binder.bind(TaskNodeRelationRepositoryProtocol,to=TaskNodeRelationRepository,scope=singleton)
        binder.bind(TaskCallbackRepositoryProtocol,    to=TaskCallbackRepository,    scope=singleton)
```

Profile-independent — only the injected `DatabasePlugin` differs per profile
(`CommunityDatabase` OceanBase/MySQL vs `SqliteDB` test/singlebox), exactly like
`TaskQueueModule`. Add to `di/container.py::build_injector` base list. The
existing `TaskModule` (in-memory services + transport ports) is **untouched**.

## 10. DDL files (`core/task/sql/`)

OceanBase/MySQL syntax, table names verbatim (no prefix), `DEFAULT CHARSET=utf8mb4`,
`gmt_*` timestamps, operator-provisioned in prod. Changes vs the supplied SQL,
per locked decisions:

- `task_callback`: `node_id` → `varchar(128) NOT NULL` (was
  `varchar(512) DEFAULT NULL`). UK `(run_id, node_id)` now enforces dedup.
- Everything else verbatim, including `BLOCK_SIZE 16384 LOCAL` on the UKs that
  had it.

A short README/heading note in each file records that the ORM renders a plain
unique index and the `BLOCK_SIZE`/`LOCAL` modifier is OceanBase-only (mirrors
`task_queue`).

## 11. Tests

`tests/community/repository/task/test_*.py` — real in-memory SQLite engine
(`sqlite:///:memory:` + `StaticPool`), side-effect-import the models so
`Base.metadata.create_all(engine)` builds the 5 tables, an `orm_session` stub
mirroring `test_task_queue_repository.py`. Per-repo coverage:

- CRUD round-trip via record dataclass (`to_record()` == input).
- UK conflict → `IntegrityError` mapped to a domain error (esp. `task_id`,
  `(run_id, node_id)`, `(task_id, node_id, retry)`, `(src_node_id, dst_node_id)`).
- `TaskNodeRunInfoRepository.get_latest` returns `max(retry)`.
- Status CAS returns row-count (`update_status`).
- `acceptance_result` / `task_spec` / `output` JSON round-trip; enum round-trip.
- `TaskNodeRelationRepository.children`/`parents` traversal.
- `TaskCallbackRepository.list_by_session`.

Also: `tests/community/architecture/test_repository_contracts.py` passes for the
new `task` domain (abstract members, based impls, no runtime domain import in
`protocols/task.py`).

## 12. Verification

Before claiming done (per verification-before-completion):
- `src/backend/.venv/bin/python -m pytest tests/community/repository/task/` green.
- `src/backend/.venv/bin/python -m pytest tests/community/architecture/test_repository_contracts.py` green.
- A boot smoke check that `build_injector(profile=...)` resolves the 5 new
  protocols (or the existing eager-check/app boot test still passes).
- Relevant existing task-module tests still green (nothing in `task_module.py`
  changed, so they should).

## 13. Out of scope (follow-ups)

- Wiring `TaskGraphService`/`TaskService` to load/persist through these repos
  (replaces in-memory SSOT) — scope B.
- Closing the §3 graph-state gaps (`loop_round`, graph `output`/`extend_props`,
  `run_id` on graph, `action_log`) — needs a contract/DDL decision.
# Task `execute` Request Object + `task_info` Persistence — Design Spec

- **Date:** 2026-08-20
- **Scope:** Change `TaskService.execute` to take a new externally-facing
  `TaskInfoRequest` (flattened, per the supplied TS contract) instead of the
  internal `TaskInfo`, and convert+persist it to the `task_info` table
  (via `TaskInfoRepository`) before initializing the in-memory graph.
- **Status:** Draft, pending user review → then implementation plan.
- **Builds on:** `src/backend/specs/2026-08-09-task-goal-driven-db-percistence/spec.md`
  (the persistence layer this wires in — scope B begins here).

## 1. Goal

`POST /openapi/v1/collaboration/tasks/execute` (and its internal mirror)
currently accepts the internal domain `TaskInfo` shape. Replace it with a
purpose-built external request object `TaskInfoRequest`, generate the task id
server-side, and durably insert a `task_info` row **first**, before the existing
in-memory `initialize_graph` flow. Only `task_info` is persisted in this step
(the request maps to the task-level record); nodes/relations/graph-state remain
a later scope.

## 2. Locked decisions

| # | Decision | Resolution |
|---|---|---|
| D1 | `task_id` source | **Server-generated `uuid4`** (the request carries no `task_id`). Used as the `task_info` PK, the root `node_id`, and returned in `TaskOpResult`. **Breaking:** clients no longer send `task_id`; they read it from the response. |
| D2 | Persisted `task_spec` JSON shape | **Domain shape** — serialize via a new `TaskSpec.to_dict()` (`metadata{task_id,title,instruction}`, `goal.acceptances[{id,description}]`). Consistent with the in-memory graph; a future load-from-DB maps cleanly. |
| D3 | Internal `TaskInfo.owner_bot_id` mapping | **`owner_bot_id`** (the owning bot). `source_type = request.source_type`. `TaskInfo` is otherwise unchanged; coop_group's group assignment happens later at the node-run level, not on `task_info`. |
| D4 | Insert failure behavior | **Return `TaskOpResult(success=False, error=...)`** and skip `initialize_graph`. Consistent with `execute`'s existing return contract. |

**Assumed (object unless raised):**
- New contract lives in the **domain layer** (`core/task/domain/`) because
  `core/` is structurally barred from importing `api/` (the service protocol in
  `api/task/` imports the param type at runtime).
- Persist **only `task_info`** in this change (the request → task-level record).
- `execute` stays `async`; the sync `repo.insert` is called inline (mirrors the
  existing inline sync `initialize_graph` call). No `asyncio.to_thread` for now.
- Initial `task_info.status` = `Status.PENDING`.

## 3. The external request contract (TS, verbatim from the request)

```ts
type TaskSourceType = 'bot' | 'coop_group' | 'api';
type TaskType = 'yaml' | 'workflow' | 'dynamic';

interface TaskRequest {
  task_spec: {
    metadata: { title: string; instruction: string };
    context: { background: string; extend_props: { [key: string]: unknown } };
    goal: {
      objective: string;
      acceptances: Array<{ id: string; acceptance: string }>;
    };
  };
  source_type: TaskSourceType;
  owner_user_id: string;
  owner_bot_id: string;
  execution_config: {
    task_type: TaskType;
    yaml?: string | Record<string, unknown>;
    workflow_id?: string;
    [key: string]: unknown;
  };
}
```

Field-mapping notes (request → domain/persistence):
- `acceptances[].acceptance` → domain `AcceptanceCriteria.description`.
- Request `metadata` has **no `task_id`** → generated server-side (D1).
- `source_type` → `task_info.source_type` (column) and `TaskInfo.source_type`.
- `owner_user_id` / `owner_bot_id` → `task_info.owner_user_id` / `owner_bot_id`
  (net-new vs the old single `owner_bot_id`); `owner_bot_id` →
  `TaskInfo.owner_bot_id` (D3).
- `execution_config` (incl. `task_type`, `yaml`, `workflow_id`) → stored as the
  `task_info.execution_config` JSON dict; `TaskType` validates `task_type`.

## 4. File layout

Net-new / edits (all under `src/backend/src/agentclaw/community/` unless noted):

```
core/task/domain/
├── models.py                         (edit) +TaskSourceType, +TaskType StrEnums;
│                                                  +to_dict() on Metadata/Context/Goal/
│                                                  AcceptanceCriteria/TaskSpec
└── requests.py                       (new)  TaskInfoRequest + nested request dataclasses
                                                  + TaskInfoRequest.to_task_info() (uuid4, maps
                                                  acceptance→description, owner_bot_id=owner_bot_id)

core/task/task_center/task_service.py (edit)  execute(request: TaskInfoRequest); inject
                                              TaskInfoRepositoryProtocol; persist-first; run_execute helper

api/task/task_service.py              (edit)  TaskServiceProtocol.execute signature → TaskInfoRequest

di/modules/task_module.py             (edit)  task_service provider @injects TaskInfoRepositoryProtocol

adapters/http/task/schemas.py         (edit)  +TaskInfoRequestDTO + nested DTOs + task_info_request_from_dto;
                                              remove TaskInfoDTO + task_info_from_dto (execute was sole consumer)

adapters/http/openapi_v1/task/router.py (edit)  execute_task → TaskInfoRequestDTO → service.execute
adapters/http/task/router.py          (edit)  execute_task_internal → TaskInfoRequestDTO → service.execute

tests/community/core/task/task_center/test_task_service.py (edit)  build TaskInfoRequest; assert task_info persisted
tests/community/core/task/e2e/test_e2e.py                 (edit)  build TaskInfoRequest
tests/community/core/task/task_center/test_execute_persist.py (new)  execute persists task_info row (status PENDING,
                                                                       task_spec round-trips, generated task_id returned)
```

## 5. New domain contract (`core/task/domain/requests.py`)

Frozen dataclasses mirroring the TS interface (flattened): `RequestMetadata`,
`RequestContext`, `RequestAcceptance`(`id`,`acceptance`), `RequestGoal`,
`RequestTaskSpec`, and `TaskInfoRequest`. Enums `TaskSourceType` / `TaskType`
are `StrEnum` and live in `core/task/domain/models.py` beside the existing
`Status`/`AcceptanceVerdict`/`RelationType` enums (the established enum home).

`TaskInfoRequest.to_task_info() -> TaskInfo`:
- `task_id = str(uuid.uuid4())`
- `Metadata(task_id, title, instruction)`
- `Context(background, extend_props or {})`
- `Goal(objective, [AcceptanceCriteria(id, description=acceptance) for ...])`
- `TaskSpec(metadata, context, goal)`
- `TaskInfo(task_spec, source_type=source_type.value,
  owner_bot_id=owner_bot_id, execution_config=execution_config)`
- returns the `TaskInfo` (used by both the persist step and `initialize_graph`).

`to_task_info()` is pure domain logic (uuid is stdlib); no framework deps,
honoring the domain's zero-transport-dep rule.

## 6. Domain `to_dict()` (for persistence)

Add `to_dict()` to `Metadata`, `Context`, `Goal`, `AcceptanceCriteria`,
`TaskSpec` in `models.py` — the canonical domain shape (NOT the request shape).
Used to serialize `task_info.task_spec` (D2). Mirrors the manual serialization
already done node-by-node in `graph_to_dto`, now centralized.

## 7. `execute` flow change (`task_service.py`)

```python
async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
    task_info = request.to_task_info()          # generates task_id (D1)
    task_id = task_info.task_spec.metadata.task_id
    # Persist first (D4): insert task_info row; on failure, return failure.
    record = TaskInfoRecord(
        id=0,
        task_id=task_id,
        source_type=request.source_type.value,
        owner_user_id=request.owner_user_id,
        owner_bot_id=request.owner_bot_id,
        execution_config=request.execution_config,
        task_spec=task_info.task_spec.to_dict(),   # domain shape (D2)
        status=Status.PENDING,
    )
    try:
        self._task_info_repo.insert(record)
    except IntegrityError as exc:                 # dup task_id (uuid collision — ~never)
        return TaskOpResult(task_id=task_id, success=False, error=f"persist failed: {exc}")
    # Existing flow unchanged.
    graph = self._graph.initialize_graph(task_info)
    if self._harness is not None:
        self._harness.register(task_id)
    bg = asyncio.create_task(self._engine.on_execute(task_id))
    self._bg_tasks.add(bg); bg.add_done_callback(self._on_bg_done)
    return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)
```

`TaskService.__init__` gains `task_info_repo: TaskInfoRepositoryProtocol`,
stored as `self._task_info_repo`. `run_execute(facade, request)` helper updated
to the new param type.

## 8. DI wiring (`di/modules/task_module.py`)

The `task_service` `@provider` already `@inject`s several ports; add
`task_info_repo: TaskInfoRepositoryProtocol` and forward it into
`TaskService(...)`. `TaskInfoRepositoryProtocol` is already bound by
`TaskPersistenceModule` (in the base list). No new module.

## 9. HTTP layer (breaking)

`schemas.py`: new pydantic `TaskInfoRequestDTO` (+ nested
`RequestMetadataDTO`/`RequestContextDTO`/`RequestAcceptanceDTO`/`RequestGoalDTO`/
`RequestTaskSpecDTO`) mirroring the TS interface, plus
`task_info_request_from_dto(dto) -> TaskInfoRequest`. `execution_config` is a
dict with `task_type` validated to `TaskType`; `yaml`/`workflow_id` optional.

Both execute routes switch:
```python
body: TaskInfoRequestDTO, ...
request = task_info_request_from_dto(body)
result = await service.execute(request)
return envelope(op_result_to_dto(result), request)
```

`TaskInfoDTO` and `task_info_from_dto` are removed (recon confirmed execute was
their sole consumer). `TaskSpecDTO`/`MetadataDTO`/`AcceptanceCriteriaDTO`/
`GoalDTO`/`ContextDTO` are reused only if other (non-execute) paths need them;
otherwise also removed. **This is a breaking API change** for the two execute
endpoints (new request shape, no caller-sent `task_id`, `acceptance` field,
`owner_user_id`/`owner_bot_id`, `execution_config.task_type`).

## 10. Tests

- Update `test_task_service.py` and `test_e2e.py` to construct `TaskInfoRequest`
  (via `to_task_info()` or directly) instead of `TaskInfo`.
- New `test_execute_persist.py`: build a `TaskInfoRequest`, call `execute`
  against a `TaskService` wired with a real in-memory SQLite
  `TaskInfoRepository` (reuse the `tests/community/repository/task/conftest.py`
  harness), assert:
  - a `task_info` row exists with `task_id` == the returned `task_id`,
    `status == PENDING`, `source_type`/`owner_user_id`/`owner_bot_id` correct,
    and `task_spec` JSON round-trips to the domain shape (incl. generated
    `task_id` and `description` from `acceptance`);
  - `execute` returns `success=True` with the generated `task_id`;
  - an insert failure path returns `success=False` (e.g. force a duplicate
    `task_id` by pre-inserting).
- Router-level tests (if present for the execute routes) updated to the new DTO.

## 11. Out of scope (later scope-B work)

- Persisting `task_node` / `task_node_run_info` / `task_node_relation` /
  `task_callback` from the graph lifecycle.
- Loading the graph from DB on boot / persisting graph mutations.
- Closing the `TaskExecutionGraph` state gaps (`loop_round`, graph
  `output`/`extend_props`, graph `run_id`).

## 12. Validation

- New + updated tests green.
- `tests/community/core/task` no-regression (the in-memory path is unchanged
  except the `execute` signature + the insert-before-init).
- Full `tests/community` suite green (incl. `test_module_boundaries`,
  `test_repository_contracts`).
- flake8/antflake on touched files clean (pre-push default lint-only).

## 13. Compatibility and risk

- **Breaking** for the two `…/collaboration/tasks/execute` endpoints (request
  shape). Clients must stop sending `task_id` and read it from the response.
- `execute` now does a DB write on every call (was pure in-memory). Failure
  short-circuits before graph init (D4). Sync `insert` inline in async `execute`
  (acceptable; mirrors existing inline sync work).
- `task_id` changes from caller-supplied to server-generated `uuid4` — the
  in-memory `_graphs` key and root `node_id` now come from the generated id.
- No change to other `TaskServiceProtocol` methods or to `TaskInfo`'s shape.
# Task Graph Shared Persistence and Recovery — Implementation Plan

> **For agentic workers:** implement in small, reviewable tasks. Keep the existing
> task HTTP contract stable. Run the repository and task regression suites after
> each persistence milestone.

**Goal:** Make the existing task tables the shared source of truth for complete
`TaskExecutionGraph` recovery across singlebox and OceanBase-backed instances,
while storing high-volume action history in a new independent `task_action_log`
table.

**Spec:** `src/backend/specs/2026-08-24-task-graph-shared-persistence/spec.md`

## Global constraints

- `task_info.status` stores runtime `Status` values only.
- Product status mapping remains in the HTTP adapter and is not persisted.
- Do not add `action_log` to `task_node_run_info`.
- Normal Dashboard queries must not scan `task_action_log`.
- Every graph mutation must update shared state in a transaction.
- Local memory graph is a cache and may only update after database commit.
- Do not use sticky sessions as the correctness mechanism.
- Reuse `DatabasePlugin` for SQLite/singlebox and OceanBase/ZDAS profiles.
- Preserve the existing one-repository-per-table contracts; add aggregate graph
  behavior without bypassing repository boundaries.

## File structure

```text
src/backend/src/agentclaw/community/
├── core/task/repository/
│   ├── models.py                         # extend task ORM models
│   ├── types.py                          # graph/action records and projections
│   └── serializers.py                    # graph/runtime JSON conversion
├── core/repository/protocols/task.py     # graph/action protocols
├── core/repository/implementations/task/
│   ├── task_graph_repository.py
│   └── task_action_log_repository.py
├── core/task/task_graph/task_graph_service.py
├── core/task/task_center/task_service.py
├── core/task/task_runner/callback_adapter.py
├── core/task/task_harness/harness.py
└── di/modules/task_persistence_module.py

src/backend/specs/2026-08-24-task-graph-shared-persistence/
├── spec.md
├── plan.md
└── tasks.md
```

## Phase 1 — schema and table contracts

1. Add additive DDL for `task_info` graph metadata/version/lease fields.
2. Add `task_action_log` DDL with event, ordering, task, and retention indexes.
3. Add callback event idempotency fields and indexes.
4. Verify or migrate task node/relation natural keys.
5. Update ORM models and record dataclasses.
6. Add SQLite bootstrap imports and profile-safe migrations.

## Phase 2 — repositories

1. Add graph aggregate repository protocol and implementation.
2. Add action-log repository protocol and implementation.
3. Implement graph load from the four existing graph state tables.
4. Implement transactional graph save with version checking.
5. Implement action-log append and cursor pagination.
6. Implement lease acquisition, heartbeat, release, and recoverable-task scan.

## Phase 3 — TaskGraphService integration

1. Inject the graph repository into `TaskGraphService`.
2. Add cache version tracking.
3. Add cache-miss hydrate.
4. Route `initialize_graph`, node mutation, graph mutation, BBS mutation, and
   action-event append through the graph repository.
5. Update memory cache only after commit.
6. Retry stale graph versions at most twice.
7. Convert persistence failures into explicit errors and logs.

## Phase 4 — task service and callback integration

1. Make execute create task metadata and initial graph state atomically.
2. Keep `task_info.status` as runtime state and update it with graph transitions.
3. Resolve callbacks by task ID or session ID through shared repositories.
4. Persist callback audit and graph mutation in one transaction.
5. Add event-idempotent callback handling.
6. Make BBS claim/attach/result use shared graph locking and version checks.

## Phase 5 — recovery worker

1. Add instance identity and configurable lease duration.
2. Scan `task_info` for non-terminal runtime states with expired leases.
3. Acquire lease using conditional update.
4. Hydrate graph and inspect the last persisted action/runtime state.
5. Resume only idempotently recoverable work.
6. Heartbeat while running and release/clear lease on terminal state.

## Phase 6 — rollout

1. Deploy additive migrations.
2. Enable dual-write metrics and compare memory graph version with DB version.
3. Enable Dashboard hydrate fallback.
4. Enable version-aware cache refresh.
5. Enable recovery worker for tasks created after the migration marker.
6. Remove temporary same-instance routing requirements after validation.

## Validation commands

```bash
cd src/backend
.venv/bin/python -m pytest -q tests/community/repository/task
.venv/bin/python -m pytest -q tests/community/core/task
.venv/bin/python -m pytest -q tests/community/adapters/http/openapi_v1
.venv/bin/python -m pytest -q tests/community/di
```

For BCS/singlebox integration, run the existing task integration and Singlebox
coverage commands with the profile-specific environment enabled.

## Compatibility and rollback

- Migrations are additive and can be deployed before the application change.
- During rollback, old code can continue using the in-memory graph and ignore the
  new columns/table. New action rows remain harmless historical data.
- Do not remove new columns or indexes until all old application instances are
  drained and the migration has been validated in every environment.

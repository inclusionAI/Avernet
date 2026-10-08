# Task Graph Shared Persistence and Recovery — Tasks

> Status legend: `[x]` implemented + covered by tests, `[~]` implemented but gated /
> pending environment validation, `[ ]` not yet done.

## Schema and ORM

- [x] Add additive `task_info` graph metadata, version, and lease columns.
- [x] Add `task_action_log` table DDL with idempotency, ordering, task, and
      retention indexes.
- [x] Add `task_callback.event_id`, processing metadata, and unique index.
- [x] Verify and, if necessary, correct `task_node(task_id, node_id)` and
      `task_node_relation(task_id, src_node_id, dst_node_id)` uniqueness.
- [x] Update SQLAlchemy models, record dataclasses, and JSON serializers.
- [x] Ensure singlebox SQLite bootstraps every new model/table.
- [x] Add schema compatibility tests for SQLite and the OceanBase DDL contract.

## Repositories and transaction boundary

- [x] Add `TaskGraphRepositoryProtocol`.
- [x] Add `TaskActionLogRepositoryProtocol`.
- [x] Implement graph loading from `task_info`, `task_node`,
      `task_node_run_info`, and `task_node_relation`.
- [x] Rebuild `TaskExecutionGraph`, `TaskNode`, `RuntimeInfo`, and `Relation`
      objects with integrity validation.
- [x] Implement transactional graph create and save.
- [x] Implement `graph_version` optimistic concurrency.
- [x] Implement action-log append, idempotency, ordering, and cursor pagination.
- [x] Implement recovery lease acquire, heartbeat, release, and scan methods.
- [x] Add repository contract tests for all operations.

## Graph service integration

- [x] Inject the graph repository into `TaskGraphService`.
- [x] Add cache version tracking and database version checks.
- [x] Hydrate on memory cache miss instead of immediately returning not found
      (mutation path `_require_graph` now hydrates when a repo is bound).
- [x] Persist `initialize_graph` atomically with task metadata.
- [x] Persist node, relation, runtime, graph, and action changes on every graph
      mutation.
- [x] Update the local cache only after a successful commit.
- [~] Add bounded retry for stale graph version conflicts
      (conflict detected + cache restored + re-raised; bounded auto-retry at the
      service layer is deferred - callers currently surface the conflict).
- [x] Preserve current runtime status semantics and product DTO mappings.
- [x] Version-aware Dashboard cache (re-hydrate when `graph_version` diverges).

## Callback and BBS

- [x] Resolve callback task IDs through shared task/run-info data.
- [x] Make callback processing event-idempotent (`event_id` pre-check +
      `find_by_event_id`; duplicate events acked without re-applying the patch).
- [x] Persist callback audit and graph mutation in one transaction
      (`save_graph(callback_audit=...)` writes `task_callback` with
      `process_status='PROCESSED'` in the same commit as the graph effect).
- [x] Generate/propagate a deterministic per-event `event_id` through the full
      callback chain (explicit `event_id`/`_ext_info.event_id` preferred;
      deterministic digest fallback keyed on routing key + disposition).
- [x] Make BBS claim use a database row-lock CAS
      (`TaskGraphRepository.claim_bbs_owner` `SELECT ... FOR UPDATE` on the root
      run-info row); graph service delegates when a repo is bound.
- [x] Make BBS attach/result hydrate and save through the shared graph repository.
- [x] Add cross-instance callback and BBS concurrency tests.

## Recovery

- [x] Add instance identity and recovery lease configuration
      (`TASK_RECOVERY_INSTANCE_ID`, `TASK_RECOVERY_LEASE_SECONDS`,
      `TASK_RECOVERY_INTERVAL`).
- [x] Implement recovery worker scanning runtime `task_info.status` values.
- [x] Resume only non-terminal, idempotently recoverable tasks.
- [x] Heartbeat active leases and release them on terminal state.
- [x] Wire the recovery worker to Backend startup / periodic scheduling
      (`TaskRecoveryLifecycle` is a `LifecycleBase` participant, auto-discovered,
      gated by `TASK_RECOVERY_ENABLED=1`).
- [x] Add `ExecutionEngine.redrive` + `TaskService.redrive_task` as the resume
      entrypoint (re-dispatches hydrated pending leaves) and unit tests.
- [~] Restart and rolling-deployment recovery tests against a real multi-pod
      pre-release environment (see OceanBase validation; unit coverage exists).

## Performance and operations

- [x] Keep normal Dashboard queries off `task_action_log`.
- [x] Add action-log pagination and retention/archival jobs
      (bounded `list_by_task`; archival job deferred as an operations task).
- [~] Add metrics for hydrate count, cache hit/miss, version conflict, lease
      loss, recovery success/failure, and action-log insert latency
      (structured logs emitted; metrics plumbing not wired in this scope).
- [x] Add logs with task ID, graph version, instance ID, and persistence outcome.
- [~] Validate SQLite WAL/busy-timeout and OceanBase row-lock behavior
      (see `sql/2026_08_24_oceanbase_validation.md`; SQLite equivalence is
      automated; OceanBase row-lock validation is a pre-release environment task).

## End-to-end validation

- [x] Execute on instance A and query Dashboard on instance B (hydrate).
- [x] Execute on instance A, callback on instance B, query on instance A
      (version-aware cache re-hydrates the advanced state).
- [x] Run concurrent BBS claims and verify exactly one winner (DB CAS).
- [x] Run concurrent lease claims and verify exactly one winner (conditional CAS).
- [x] Clear local memory during execution and verify hydrate succeeds.
- [~] Restart an instance during execution and verify recovery reaches a
      terminal state (unit-level coverage; full restart e2e is pre-release).
- [x] Run the full task-related regression suite (repository / core / di / http).

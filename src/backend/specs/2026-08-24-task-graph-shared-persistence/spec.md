# Task Graph Shared Persistence and Recovery — Design Spec

- **Date:** 2026-08-24
- **Scope:** Complete cross-instance persistence and recovery for `TaskExecutionGraph`, reusing the existing task tables and adding the high-volume `task_action_log` table.
- **Status:** Draft, pending implementation review.
- **Environments:** singlebox SQLite/Test database; dev/pre/prod OceanBase/ZDAS through the existing `DatabasePlugin` profile binding.
- **Related specs:** `2026-08-09-task-goal-driven-db-percistence`, `2026-08-20-task-execute-request-persistence`, `2026-08-21-task-execute-task-type-branching`.

## 1. Problem

`TaskGraphService` currently stores the authoritative graph in the process-local
`_graphs` dictionary. `TaskInfoRepository` persists task records, and the other
task repositories exist, but Dashboard does not reconstruct a graph from shared
storage on a cache miss. In a multi-instance pre-release deployment, execute,
Dashboard, callback, and BBS requests can reach different Backend instances.
The same `task_id` then intermittently returns a graph or `TaskNotFoundError`.

The task tables are a persistence foundation, not yet a complete graph store:

- `task_info` lacks graph-level run metadata, output, extension properties,
  version, and recovery lease fields.
- `task_node` and `task_node_relation` are not updated on every graph mutation.
- `task_node_run_info` stores node runtime fields but not the high-volume action
  history.
- `task_callback` stores callback audit and execution graph data, but is not the
  primary graph state.
- `RuntimeInfo.action_log` is currently process-local.

## 2. Goals

1. Make the database the shared source of truth for the current graph state.
2. Allow any Backend instance to hydrate a complete `TaskExecutionGraph`.
3. Keep `task_info.status` as the persisted runtime status. Product-facing
   values (`DEFINED`, `EXECUTING`, `REVIEWING`, `DONE`, `FAILED`, `CANCELLED`)
   remain adapter mappings only.
4. Add an independent, append-only `task_action_log` table so diagnostic history
   does not enlarge or slow normal `task_node_run_info` queries.
5. Make callback and BBS operations safe across instances and retries.
6. Recover in-flight tasks after process restart or rolling deployment.
7. Use the same repository contract for singlebox and OceanBase profiles.

## 3. Non-goals

- Replacing the existing HTTP task contracts.
- Changing product-facing status strings.
- Introducing sticky sessions as the correctness mechanism.
- Adding a separate graph snapshot table in this scope.
- Moving task execution into a new queue system.
- Rewriting the existing task state machine.

## 4. Locked decisions

| ID | Decision | Resolution |
|---|---|---|
| D1 | Graph source of truth | Shared database state reconstructed from `task_info`, `task_node`, `task_node_run_info`, and `task_node_relation`. Memory is a cache only. |
| D2 | Runtime status | `task_info.status` stores runtime `Status` values (`PENDING`, `PLANNING`, `RUNNING`, `DONE`, `FAILED`, `HUNG`, `CANCELLED`). No `graph_status` or product-status column is added. |
| D3 | Product status | HTTP DTO conversion maps runtime status to product status. Database never stores `DEFINED` or `EXECUTING` as a replacement for runtime values. |
| D4 | Action history | Add a separate append-only `task_action_log` table. Do not add `action_log` to `task_node_run_info`. |
| D5 | Graph metadata | Add graph run metadata, output, extension properties, version, and lease fields to `task_info`; do not add a second graph status field. |
| D6 | Concurrency | Use `task_info.graph_version` optimistic concurrency for normal graph updates and row locking/CAS for BBS claim and recovery leases. |
| D7 | Callback idempotency | Add `event_id` to `task_callback`; duplicate callback events are acknowledged without replaying graph mutations. |
| D8 | Recovery | A recovery worker scans runtime `task_info.status` rows with expired leases, hydrates the graph, and resumes eligible work. |
| D9 | Profile parity | Repository behavior is profile-independent; only the injected `DatabasePlugin` differs between SQLite/singlebox and OceanBase/dev/pre/prod. |

## 5. Runtime and product status contract

Runtime values stored in `task_info.status`:

```text
PENDING, PLANNING, RUNNING, DONE, FAILED, HUNG, CANCELLED
```

Product mapping remains in `adapters/http/task/schemas.py`:

```text
PENDING   -> DEFINED
PLANNING  -> EXECUTING
RUNNING   -> EXECUTING
HUNG      -> REVIEWING
DONE      -> DONE
FAILED    -> FAILED
CANCELLED -> CANCELLED
```

The mapping is presentation logic. Recovery and scheduling query runtime values
from the database and must never query product-facing strings.

The implementation must align task-level persistence with graph transitions:
`task_info.status` is updated in the same transaction as the graph mutation.
The root node status and graph metadata are persisted alongside it.

## 6. Existing table extensions

### 6.1 `task_info`

Keep existing columns and add:

```sql
ALTER TABLE task_info
    ADD COLUMN graph_run_id VARCHAR(512) NULL,
    ADD COLUMN graph_loop_round INT NOT NULL DEFAULT 0,
    ADD COLUMN graph_output TEXT NULL,
    ADD COLUMN graph_extend_props TEXT NULL,
    ADD COLUMN graph_version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN lease_owner VARCHAR(256) NULL,
    ADD COLUMN lease_until BIGINT NULL,
    ADD COLUMN heartbeat_at BIGINT NULL;

CREATE INDEX idx_task_info_graph_version
    ON task_info(task_id, graph_version);

CREATE INDEX idx_task_info_recovery
    ON task_info(status, lease_until, gmt_modified);
```

`status` remains the runtime status column. `graph_output` and
`graph_extend_props` are JSON serialized TEXT values.

### 6.2 `task_node`

Add or verify the natural identity constraint:

```sql
CREATE UNIQUE INDEX uk_task_node_identity
    ON task_node(task_id, node_id);
```

Existing node status remains the runtime node status.

### 6.3 `task_node_run_info`

Keep the current node runtime columns. Do not add action history here. Existing
`retry` rows remain 1:N for a `(task_id, node_id)` pair; recovery selects the
highest retry and newest modification time.

### 6.4 `task_node_relation`

Persist every dependency edge created by `add_task_nodes`. Verify the unique
key includes `task_id` so equal node IDs in different tasks cannot conflict.

### 6.5 `task_callback`

Add callback event idempotency metadata:

```sql
ALTER TABLE task_callback
    ADD COLUMN event_id VARCHAR(256) NULL,
    ADD COLUMN process_status VARCHAR(64) NULL,
    ADD COLUMN processed_at DATETIME NULL;

CREATE UNIQUE INDEX uk_task_callback_event
    ON task_callback(event_id);
```

Historical rows may have `NULL` event IDs. New callbacks must provide one.

## 7. New `task_action_log` table

`task_action_log` is intentionally independent from `task_node_run_info` because
it is append-only, high-volume diagnostic data. Normal Dashboard queries do not
read or join this table.

```sql
CREATE TABLE task_action_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_id VARCHAR(256) NOT NULL,
    task_id VARCHAR(128) NOT NULL,
    node_id VARCHAR(128) NOT NULL,
    seq INT NOT NULL,
    action VARCHAR(64) NOT NULL,
    loop_round INT NULL,
    attempt INT NOT NULL DEFAULT 0,
    status_from VARCHAR(64) NULL,
    status_to VARCHAR(64) NULL,
    payload TEXT NOT NULL,
    instance_id VARCHAR(256) NULL,
    gmt_create DATETIME NOT NULL,
    UNIQUE KEY uk_task_action_event (event_id),
    UNIQUE KEY uk_task_node_action_seq (task_id, node_id, seq),
    KEY idx_task_action_task_node (task_id, node_id, seq),
    KEY idx_task_action_created (gmt_create)
);
```

Required behavior:

- `event_id` makes action insertion idempotent.
- `(task_id, node_id, seq)` preserves per-node ordering.
- Normal graph load does not query the table.
- Diagnostic action-log reads are bounded and paginated.
- Retention/archival is based on `gmt_create`; it must not affect current graph
  recovery.

## 8. Repository design

Add a graph aggregate repository while retaining the existing one-repository-per-
table repositories:

```text
core/repository/protocols/task.py
  TaskGraphRepositoryProtocol
  TaskActionLogRepositoryProtocol
  existing task table protocols

core/repository/implementations/task/
  task_graph_repository.py
  task_action_log_repository.py
  existing repositories
```

The graph repository composes the existing repositories under one transaction
boundary. It is not a second persistence model.

Required graph operations:

```python
class TaskGraphRepositoryProtocol(Protocol):
    def create_graph(self, graph: TaskExecutionGraph) -> TaskExecutionGraph: ...
    def load_graph(self, task_id: str) -> TaskExecutionGraph | None: ...
    def get_version(self, task_id: str) -> int | None: ...
    def save_graph(
        self,
        graph: TaskExecutionGraph,
        *,
        expected_version: int,
        action_events: list[NodeActionEvent],
    ) -> TaskExecutionGraph: ...
    def acquire_lease(
        self,
        task_id: str,
        *,
        instance_id: str,
        lease_seconds: int,
    ) -> bool: ...
    def heartbeat(self, task_id: str, *, instance_id: str, lease_seconds: int) -> bool: ...
    def release_lease(self, task_id: str, *, instance_id: str) -> bool: ...
    def list_recoverable(self, *, limit: int = 100) -> list[str]: ...
```

Action log reads are separate:

```python
class TaskActionLogRepositoryProtocol(Protocol):
    def append_many(self, events: list[NodeActionEvent]) -> int: ...
    def list_by_task(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> list[NodeActionEvent]: ...
```

`save_graph()` updates the current graph tables and inserts action events in the
same database transaction. The action log remains physically independent and is
not read as part of the normal graph snapshot.

## 9. Graph reconstruction

`load_graph(task_id)` performs:

1. Read `task_info` for task metadata, runtime status, graph run ID, loop round,
   graph output, graph extensions, and version.
2. Read all `task_node` rows for `task_id`.
3. Read the latest `task_node_run_info` row for every node by highest `retry`,
   then newest `gmt_modified`.
4. Read all `task_node_relation` rows for `task_id`.
5. Rebuild domain `TaskNode`, `RuntimeInfo`, `Relation`, and `TaskExecutionGraph`.
6. Reconnect each node's `node_run_graph` reference to the rebuilt graph.
7. Validate graph integrity before returning it.

A missing `task_info` row is `TaskNotFoundError`. A task row with incomplete
node/runtime/relation data is a persistence-integrity error with structured
logging; it must not silently return an empty graph.

`task_callback.execution_graph` is loaded separately for the root session when
Dashboard needs the callback execution DAG. Callback absence does not make the
main graph unavailable.

## 10. Write transaction

Every graph mutation goes through one transaction:

```text
BEGIN
  SELECT task_info FOR UPDATE
  verify graph_version == expected_version
  apply domain patch
  update task_info.status and graph metadata
  upsert task_node rows
  upsert task_node_run_info rows
  insert task_node_relation rows
  insert task_action_log rows
COMMIT
```

The local memory cache is updated only after commit. If any write fails, the
transaction rolls back and the cache remains unchanged.

The optimistic version update is:

```sql
UPDATE task_info
SET status = :runtime_status,
    graph_loop_round = :loop_round,
    graph_output = :output,
    graph_extend_props = :extend_props,
    graph_version = graph_version + 1,
    gmt_modified = CURRENT_TIMESTAMP
WHERE task_id = :task_id
  AND graph_version = :expected_version;
```

Zero affected rows means a version conflict. The service reloads the latest graph,
reapplies the patch, and retries at most twice before returning a conflict error.

External calls are outside the database transaction:

```text
transaction: mark dispatch intent / persist current state
external call: BCS or bot
transaction: persist result / callback / next state
```

## 11. Dashboard cache and hydrate

```text
GET /dashboard
  -> read task_info.graph_version
  -> local cache exists and version matches?
       yes -> return a projection
       no  -> load_graph(task_id), replace cache, return a projection
```

A memory cache miss is never treated as task absence. Only a shared-store miss is
`TaskNotFoundError`.

`include_action_log=true` must use a bounded action-log query. The default
Dashboard response never scans `task_action_log`.

## 12. Callback and BBS consistency

### Callback

1. Extract `event_id`, `task_id`, or `session_id`.
2. Resolve task through shared storage, using `task_node_run_info.session_id`
   when only a session ID is available.
3. Lock and hydrate the graph.
4. Check whether `event_id` has already been processed.
5. In one transaction, write `task_callback`, apply the graph update, update the
   node/runtime tables, and append action events.
6. Commit and refresh the local cache.

Duplicate events return an idempotent success without reapplying the graph patch.

### BBS claim

BBS claim uses a row lock on `task_info` and atomically updates the claim fields
inside `graph_extend_props`. Two concurrent claimers produce exactly one success
and one conflict. If claim traffic requires a narrower lock later, dedicated
`bbs_owner` and `bbs_claim_until` columns may be added to `task_info` without
changing the graph repository contract.

## 13. Recovery worker

Use the new lease fields on `task_info`:

```text
lease_owner
lease_until
heartbeat_at
```

The worker scans runtime statuses:

```text
PENDING, PLANNING, RUNNING
```

and claims expired rows with a conditional update. The winner hydrates the graph,
checks the current node/action state, and resumes eligible execution. Terminal
statuses (`DONE`, `FAILED`, `HUNG`, `CANCELLED`) are never recovered.

Recovery must be idempotent. A previously issued external dispatch must be
identified from persisted runtime/session data before another dispatch is sent.

## 14. Profile behavior

The graph repository depends only on `DatabasePlugin`:

```text
singlebox -> SQLite/Test DatabasePlugin
 dev/pre/prod -> OceanBase/ZDAS DatabasePlugin
```

No profile-specific graph logic is allowed. SQLite must enable WAL and a busy
 timeout suitable for concurrent local requests. OceanBase deployment must verify
row locking, transaction isolation, TEXT size, and index-length behavior.

## 15. Migration and rollout

### Phase 1 — schema and repositories

Add `task_info` graph metadata/lease fields, `task_action_log`, callback event
metadata, indexes, protocols, implementations, and DI bindings.

### Phase 2 — dual write

Persist graph mutations to the existing tables and action log while keeping the
in-memory path active as a cache.

### Phase 3 — hydrate fallback

On Dashboard or callback cache miss, load the graph from shared storage.

### Phase 4 — version-aware cache

Compare local cache version with `task_info.graph_version`; hydrate when stale.

### Phase 5 — database-authoritative mutation

All graph state changes use the transactional graph repository. Memory updates
happen only after commit.

### Phase 6 — recovery

Enable lease scanning and restart recovery after cross-instance hydrate is proven.

## 16. Validation

Required tests:

- Existing repository contract tests continue to pass.
- Graph create/load round trip across all runtime fields.
- Relation and latest-retry reconstruction.
- Action log append, ordering, idempotency, pagination, and retention query.
- Product status mapping remains unchanged.
- Dashboard cache miss hydrates from shared storage.
- Dashboard on a second independent Injector returns the same graph.
- Callback received by a different instance updates the graph.
- Concurrent graph updates reject stale versions without lost updates.
- Concurrent BBS claim produces one success and one conflict.
- Lease acquisition has one winner.
- Restart recovery resumes an eligible non-terminal task.
- SQLite/singlebox and OceanBase profile contract behavior is equivalent.

## 17. Compatibility and risk

- Existing task HTTP contracts remain unchanged.
- Database migrations are additive except for new uniqueness checks; existing
  duplicate data must be audited before enabling those indexes.
- Product-facing status values do not change.
- Normal Dashboard query cost remains bounded by graph tables and does not include
  the large action-log table.
- Historical rows without callback `event_id` remain readable; only new callback
  writes require the idempotency field.
- During rollout, old in-memory-only tasks may be non-recoverable. The migration
  must record this limitation and enable recovery only for tasks with complete
  persisted state.

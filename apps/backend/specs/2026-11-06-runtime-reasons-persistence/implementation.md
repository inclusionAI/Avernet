# Preserve node runtime reasons across durable graph reads

## Defect

`RuntimeInfo.failure_reason` and `progress_reason` were assigned by the existing
execution / planning flows but omitted from both `TaskGraphRepository` writes and
hydration. A fresh graph read therefore returned null node reasons even when a
HUNG task retained its separate graph-level `hung_reason`. The dashboard already
projects the two node fields; the HTTP projection is not the source of the loss.

## Scope and storage

Persist both existing domain fields in the existing
`task_node_run_info.extend_props` JSON column under the repository-private
`__runtime_reasons` key. Hydration removes the storage key and restores the two
first-class runtime fields. It never modifies the live domain extension bag.
Explicit null values overwrite old reasons, and the existing per-node / retry
record selection continues to isolate execution attempts.

No database columns, HTTP paths, DTO/domain fields, public method signatures,
Relay events, state transitions, or task-loop requests change. Runtime HUNG
continues to project to product REVIEWING. Existing GroupFormation persistence
and unrelated extension properties are preserved.

## Historical records and deployment

Old rows without the private metadata remain readable and yield null node
reasons. Do not copy a graph-level hung reason to a node: it is a different fact
and can refer to a different node/attempt. Historical missing node facts require
separate evidence-backed repair from accepted event/action records or a new,
isolated regression fixture. Idempotent replay of an accepted report is not a
backfill mechanism and must not be changed to rewrite the graph.

This change does not modify pre-release data or claim the historical regression
fixture has recovered. Deploy the updated readers/writers together; an old
writer cannot preserve fields it never persisted.

## Verification

Tests first reproduced null failure reasons on fresh repository reads. Added
SQLite-backed coverage verifies create/save/reload, dashboard DTO projection,
Unicode progress reasons, updates and explicit clears, latest-retry isolation,
unchanged public extension data, and reading pre-fix rows without inventing
reasons from graph metadata.

Task repository and task HTTP suites: 631 passed. No live task reruns, database
writes or deployment were performed.

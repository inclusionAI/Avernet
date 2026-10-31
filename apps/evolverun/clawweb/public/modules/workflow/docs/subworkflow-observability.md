# Subworkflow run observations

`GET /api/runs/:flowId/subworkflows` returns best-effort direct child observations
for a production run. The host must supply a verified whole-workflow view
authorizer. The route independently authorizes the parent and every child;
missing, trial, and unauthorized runs are omitted without revealing identifiers.
Database or authorization errors fail closed. It accepts no query parameters and
sets `Cache-Control: no-store`.

## Relationship storage and trusted writers

Migration 147 adds the independent `flow_run_links` table. Its columns are
`child_flow_id` (primary key), `parent_flow_id`, `parent_node_id`, nullable
`parent_attempt`, `child_workflow_id`, and database-generated `gmt_create`. The
parent is indexed. The SQLite and MySQL/ZDAS definitions use full-column keys;
identifiers supplied to the repository must be nonempty, at most 255 UTF-16 code units,
and contain neither whitespace, control characters nor path separators. The
attempt is null or an integer from 1 through 2,147,483,647. Self-links are
rejected. A duplicate child insertion fails rather than overwriting a link; this
API does not by itself provide idempotent retry or verify run existence.

`FlowRunLinkRepository` in the shared server package exposes `insert(link)`,
`findByChild(childFlowId)` and `listByParent(parentFlowId, limit)`. A trusted
host can construct it with the `IDatabase` passed to `db.transaction`, and
perform its child-run write and link insertion in the same transaction. Both
writes must propagate errors so failure rolls both back. The host is responsible
for authenticating the caller, authorizing the parent/child binding, validating
that runs and workflow identities match, and designing conflict/retry semantics.
The repository is not an authorization boundary; ordinary clients must not be
given table-write access. There is no new HTTP write API. In particular,
`POST /api/internal/runs` has not been changed to create links, and its existing
ordinary and trial registration paths cannot be assumed to atomically do so.
No current trusted runtime writer is connected by this change.

Default `initDatabase` migration mode creates the table at v147 and applies v148
to expand all four ID columns to `VARCHAR(255)` on MySQL/ZDAS. The generic DDL
renderer would otherwise shorten them to 190, so v148 uses explicit MySQL DDL
and verifies the provisioned column lengths and full indexes via
`information_schema`. On SQLite, `VARCHAR(190)` in an existing v147 table does
not limit stored text: v148 preserves that table and its data, while a fresh
SQLite table is declared `VARCHAR(255)`.

MySQL/InnoDB must support a full 255-character index key for the database's
character set. For utf8mb4 this can require 1,020 bytes; deployments with a
767-byte key limit or a 4 KB InnoDB page's 768-byte limit cannot support this
schema as configured. The migration fails rather than replacing the child
uniqueness constraint with a prefix index. MySQL DDL can commit independently;
a failed postcondition does not record v148 and must be repaired before retry.
On MySQL/ZDAS every repository insert also verifies the physical column lengths
and full indexes before writing, including in external or DDL-denied mode; this
requires permission to inspect the table through `information_schema`. This
prevents a non-strict server from silently truncating IDs against an old table.
The schema must not be concurrently modified by another operator during writes.

In externally provisioned mode no migration is run; the operator must provision
the equivalent full-width table and indexes. The existing managed-DB path skips
ordinary migrations if DDL is denied, so it also requires external provisioning.
If the table is missing or unreadable, the read endpoint returns a generic 503.
A schema ledger at or above v148 without the correct table requires operator
repair. SQLite migration and SQL rendering/metadata paths for MySQL/ZDAS are
tested; those tests are not a live MySQL/ZDAS compatibility verification.

## Merge and limitations

The route reads at most 20 independently stored links first, then examines at
most 200 recent historical `subworkflow_linked` / `subworkflow_finished` events
for additional candidates, with at most 20 distinct child candidates in total.
Existing historical events are best-effort evidence, not trusted creation
records. A stored association wins over an event for the same child, including
when the stored association points to another parent. A stored child workflow
must match the child's actual `flow_runs` workflow before the child is returned.
New sensitive relationships must not be written to ordinary events.

Each item includes `parentNodeId`, `parentAttempt`, `childFlowId`,
`childWorkflowId`, status, timestamps, and up to 50 selected child node
summaries. The stored attempt takes precedence; for historical events only, a
newer event lacking an attempt may use an earlier attempt for the same known
parent node. Missing or invalid attempts are `null`, never inferred from child
attempts or current parent node state. Clients must handle `null` and older
servers that omit the field.

The result always declares `completeness: "best_effort"`. Limits, legacy events,
not-yet-connected writers, missing child runs, permissions and races all prevent
a completeness guarantee. A missing link does not prove no child was created.
This endpoint does not guarantee creation idempotency, crash recovery,
pagination, or historical completeness.

The route grants access only to its own limited response. Child-run details,
node, step, log, and archive endpoints must independently enforce resource-level
authorization; a visible child ID alone conveys no access to those endpoints.

## Child static DAG snapshot

`GET /api/runs/:flowId/workflow-snapshot` uses the same verified,
whole-workflow view authorizer as the child observation route. Trial runs and
unauthorized runs are not returned. It only reads a snapshot binding already
persisted on the run at registration, verifies its content digest using
`WorkflowSnapshotRepository.read`, and returns static topology fields only:
node ID, title, dependencies and executor type. It does not return executor
configuration, prompts, parameters or raw snapshots. A missing or mismatched
binding returns 409; storage or authorizer failures fail closed. The response
is `no-store`.

The binding proves that the returned bytes still match the snapshot bound at
registration; **it does not yet prove that the engine executed those bytes**.
The engine may have resolved a different version before registration. The UI
labels the result accordingly and does not include runtime-injected nodes.
A cross-component execution digest contract and integration test are required
before claiming this is the exact executed DAG.

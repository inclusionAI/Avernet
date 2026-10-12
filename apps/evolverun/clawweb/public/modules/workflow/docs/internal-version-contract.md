# Internal workflow version contract

These routes are mounted below `/api/internal` and use the host's internal API authentication. They are consumed by ClawMind; they do not introduce a separate publishing model.

## Deployment snapshots

`GET /deploy-history/:workflowId/active` and `GET /deploy-history/:workflowId/versions/:version/snapshot` return a flat camelCase object, not a nested `record`:

```json
{
  "found": true,
  "workflowId": "example",
  "packId": "example-pack",
  "version": 2,
  "deployNumber": 5,
  "tagName": "deploy/example/#5",
  "action": "deploy",
  "specJson": "{\"id\":\"example\"}",
  "gmtCreate": 1700000000
}
```

The example shows the response envelope only; `specJson` contains the complete workflow spec. `version` is the deployment version, independently of any version declared inside the spec. `packId` identifies the historical snapshot's pack and allows callers to check an explicit pack pin. Both routes return `{ "found": false }` for absent snapshots. Non-success HTTP responses represent request/service failures, not absence.

## Run records

`POST /runs` accepts optional `workflow_version` and `workflow_deploy_number` fields alongside the existing required `flow_id`, `workflow_id`, and `status`. The route preserves both values in `flow_runs` and its response. Values must be positive safe integers within the signed 32-bit range or null, except that `workflow_version` also accepts `-1` (ClawMind's `DRAFT_VERSION`). This sentinel means this run is not bound to a published release, including Git remote snapshot runs; it does not mean the workflow has never been deployed. Do not replace it with the workflow's latest release number. `workflow_deploy_number` does not accept `-1`. Invalid values return HTTP 400 before insertion. Omitted fields remain null for older clients and unversioned runs. No schema migration is needed; deploy this API compatibility change to accept snapshot runs from current plugins.

These changes are additive and require no schema migration: the two run columns already exist. Deploy the Avernet backend before the matching ClawMind client so version metadata is retained and pack-pinned historical lookups receive pack identity.

## Restart recovery retry

`PUT /runs/:flowId/retry-resume` is called only after ClawMind has saved a manual retry intent in its persistent local journal. It requires positive `retry_epoch`, advances `flow_runs.retry_epoch` exactly once from the preceding epoch, changes a `failed`, `blocked`, `waiting`, or already `running` row to `running`, and clears `completed_at`; it preserves the original `started_at` and all `node_executions` history. Replaying the current epoch while the run is still running succeeds without another reset. A missing run returns 404. A skipped/older epoch or terminal run returns 409; ClawMind retains its local intent for reconciliation. A write failure returns 500, so the local intent remains pending for replay. The existing `/runs/:flowId/retry-failed` route retains its legacy failed-node reset behavior for epoch-zero runs and rejects epoch-fenced runs before changing child nodes.

`PUT /runs/:flowId/completion` and `PUT /runs/:flowId/fail-if-running` accept nonnegative `retry_epoch` and update only the matching epoch. An omitted value means zero for older clients; a stored explicit `NULL` also compares as epoch zero. A stale completion receives 409; a stale watchdog claim returns `claimed: false`. ClawMind captures the epoch when execution begins, so a delayed result from the preceding retry cannot finish the current one.

The existing `PUT /runs/:flowId/status` route keeps its legacy ability to move a failed run back to `running`, which API-backed TaskGuard retries still use. A delayed legacy `running` update cannot reopen a run that has already succeeded or been cancelled. Legacy completion may arrive before that asynchronous status update, so completion still accepts a previously failed run. ClawMind's new local-journal retry uses `retry-resume` instead and does not send a second asynchronous `running` status update after that call.

Restart recovery uses the existing run record and retry mailbox; it adds no new table. Migration 140 adds nullable `flow_runs.retry_epoch INTEGER DEFAULT 0`: existing rows and older-client inserts receive zero, while an explicit `NULL` remains allowed and is interpreted as zero. The equivalent manual DDL is `ALTER TABLE flow_runs ADD COLUMN retry_epoch INTEGER DEFAULT 0`. Apply the column before deploying the epoch-aware server, then deploy ClawMind after the server. Where schema migrations have DDL access, migration 140 applies the column automatically; it also tolerates an already-added column and records the migration. Where DDL access is disabled, operations must apply the manual DDL and verify the column exists. Do not manually run it again after migration 140 has applied it. If a deployment already added the earlier non-null or default-NULL column, migration 140 will not change its definition; check that deployment separately before claiming this default and nullability.

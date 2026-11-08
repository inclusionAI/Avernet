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

## Restart recovery retry compatibility

Current ClawMind performs retry bookkeeping in its local journal. It uses the existing `PUT /runs/:flowId/status` with `status: running`, waits for that write, then starts execution. The status write clears the previous `completed_at`, preserving `started_at` and node history. When it reopens a non-running record with a positive epoch, the same atomic update resets that epoch to zero so the preceding explicit-epoch client cannot finish the new attempt. Already-running status updates do not reset the epoch. Schemas without the column use the original status update; other database errors are propagated. It no longer calls `retry-resume` or sends `retry_epoch` on status, completion, or timeout writes.

Requests that omit `retry_epoch` retain the original API contract even when the stored value is `NULL`, zero, or positive. Status updates do not narrow the previously supported retry states. Completion uses the existing run identity; `fail-if-running` retains its atomic nonterminal-state check. The legacy `retry-failed` operation still resets child nodes and the run in one transaction, including records written by an epoch-aware plugin.

Already deployed clients that explicitly send `retry_epoch` retain their matching-epoch completion/status/watchdog checks. `retry-resume` remains available for those clients, advances the preceding epoch once, and preserves original start and node history. Replaying its current running epoch is idempotent; skipped or older epochs return 409. It no longer rejects a fresh retry solely because the current status is succeeded or cancelled.

This compatibility update does not add, drop, or alter any table or column. The existing migration 140 and stored `flow_runs.retry_epoch` values remain intact. Deploy this API compatibility update before upgrading plugins that already own positive-epoch records. A deployment that has never enabled the epoch protocol continues using the original API contract.

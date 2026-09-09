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

`POST /runs` accepts optional `workflow_version` and `workflow_deploy_number` fields alongside the existing required `flow_id`, `workflow_id`, and `status`. The route preserves both values in `flow_runs` and its response. Values must be integers from 1 through 2147483647 or null; invalid values return HTTP 400 before insertion. Omitted fields remain null for older clients and unversioned runs.

These changes are additive and require no schema migration: the two run columns already exist. Deploy the Avernet backend before the matching ClawMind client so version metadata is retained and pack-pinned historical lookups receive pack identity.

## Two-phase saved-snapshot release

The release service API is versioned. Its current contract is **v1** and is exported as
`WORKFLOW_RELEASE_SERVICE_V1` by `@avernet/workflow/server`.

Deploy clients call `POST /api/internal/deploy-history/releases/v1/reserve` before creating a tag.
The request contains `serviceApiVersion: "v1"`, `workflowId`, `packId`, `snapshotCommit` (Git SHA),
`specJson`, `minDeployNumber` (one beyond any known local/remote tag), and optional audit fields
`note`, `botId`, `ownerId`. The response also contains `serviceApiVersion: "v1"` plus
`{workflowId, packId, snapshotCommit, version, deployNumber, tagName, completed}`. A missing or
unknown API version returns HTTP 400; clients must not infer compatibility from an unversioned route.
Allocation locks the existing workflow_specs row and inserts a pending history row and a reservation
in one transaction. A repeated commit returns the same reservation; mismatched pack/spec returns 409.
Pending releases are not listed as published versions and cannot be activated or run.

After pushing and verifying the exact annotated Git tag and preparing its Pack, the client posts
that response to `/api/internal/deploy-history/releases/v1/complete`. Completion validates all identifiers, marks the row deployed,
and activates it atomically unless a later release is active. Retrying completion never reactivates
an old release. Git remains outside the DB transaction; an interrupted push leaves a retryable pending
reservation. The internal caller is responsible for verifying Git before confirming completion.
Legacy inserts now return 409 without silently rewriting the requested version.

Migration 120 stores the idempotent reservation. Migration 121 (MySQL/ZDAS) adds a generated release
number and a unique key on `(workflow_id, release_deploy_number)` for `pending`, `deploy`, and
`rollback` records. Migration 122 is the SQLite counterpart used by tests. This makes an accidentally
duplicated release number fail even when a legacy writer uses another pack. `edit`, `pull`, and
`migration` records remain outside that release-number constraint because they do not create a Git
release tag.

Before applying migration 121 to an existing database, run this preflight query:

```sql
SELECT workflow_id, deploy_number, COUNT(*) AS release_count
FROM workflow_deploy_history
WHERE action IN ('pending', 'deploy', 'rollback')
GROUP BY workflow_id, deploy_number
HAVING COUNT(*) > 1;
```

Any returned row must be reconciled to one canonical release before migration. Do not delete history
or overwrite tags automatically to make the migration pass.

Deploy migrations 120–122 and the server before the new plugin. Managed databases that deny
application DDL must apply:

```sql
CREATE TABLE IF NOT EXISTS workflow_release_reservations (
  workflow_id VARCHAR(255) NOT NULL,
  snapshot_commit VARCHAR(64) NOT NULL,
  pack_id VARCHAR(255) NOT NULL,
  deploy_number INTEGER NOT NULL,
  version INTEGER NOT NULL,
  PRIMARY KEY (workflow_id, snapshot_commit)
);
```

Existing history and tags are preserved. Remote tags from old partial failures count toward the
next allocation, but are not automatically adopted as a new snapshot's reservation. Do not delete
history or overwrite tags to resolve a conflict. Ordinary legacy history inserts remain supported,
but a conflicting release number now requires explicit reconciliation. Rolling back the server
requires rolling back the plugin first; keep the reservation table and pending rows for later recovery.

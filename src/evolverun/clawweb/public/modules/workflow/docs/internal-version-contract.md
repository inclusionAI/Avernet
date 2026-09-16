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

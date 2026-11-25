# Workflow execution record HTTP contract

Owner: `@avernet/workflow`. Contract version: 1 (additive Service API).

`GET /api/runs/:flowId/execution-record` lets authorized clients query the stored
execution record in the same envelope as workflow HTTP callbacks. The path is
relative to the host's runs mount (`/api/runs` in ClawWeb). This is a consumer
read Service API, not the engine's outgoing HTTP callback Plugin API.

## Request

- `flowId` is the nonblank **execution instance ID** (`flow_runs.flow_id`), not
  the workflow definition ID (`workflow_id`) or numeric database row ID. Encode
  it as a URL path segment. Surrounding whitespace is trimmed before lookup.
- Optional query parameter `includeNodeOutput` accepts only `true` or `false`.
  The default is `false`, matching HTTP callback configuration. Repeated,
  structured, empty, or other values return HTTP 400.
- Use the host's existing authentication/session mechanism. Passing
  `botOwnerId`, `botId`, or another query filter does not grant access to a run.

```sh
curl "$CLAWWEB_URL/api/runs/$FLOW_ID/execution-record?includeNodeOutput=true"
```

## Successful response

HTTP 200, `Content-Type: application/json`, `Cache-Control: no-store`.

```json
{
  "workflow_id": "review-pipeline",
  "flow_id": "flow-example",
  "status": "succeeded",
  "ext_info": {
    "flow_runs": {
      "id": 1,
      "flow_id": "flow-example",
      "workflow_id": "review-pipeline",
      "workflow_title": "Review pipeline",
      "status": "succeeded",
      "params_json": null,
      "input_json": "{\"request\":\"example\"}",
      "result_json": "{\"ok\":true}",
      "node_count": 1,
      "succeeded_count": 1,
      "failed_count": 0,
      "total_duration_ms": 1000,
      "total_token_usage": null,
      "triggered_by": "manual",
      "identity_key": null,
      "current_phase": null,
      "started_at": 1700000000,
      "completed_at": 1700000001,
      "credentials_json": null,
      "origin_session_key": null,
      "origin_session_id": null,
      "origin_bot_id": "review-bot:owner-example",
      "user_id": null,
      "plugin_version": null,
      "gmt_create": 1700000000,
      "gmt_modified": 1700000001
    },
    "node_executions": [
      {
        "id": 1,
        "flow_id": "flow-example",
        "workflow_id": "review-pipeline",
        "node_id": "review",
        "executor_type": "agent",
        "status": "succeeded",
        "attempt": 1,
        "input_json": null,
        "output_json": "{\"ok\":true}",
        "error_text": null,
        "duration_ms": 1000,
        "token_usage_json": null,
        "node_title": "Review",
        "triggered_by": null,
        "branch_id": null,
        "progress_message": null,
        "session_key": null,
        "session_id": null,
        "system_context_json": null,
        "embedded_session_key": null,
        "started_at": 1700000000,
        "completed_at": 1700000001,
        "gmt_create": 1700000000,
        "gmt_modified": 1700000001
      }
    ]
  }
}
```

The example enables `includeNodeOutput=true`. With the default or explicit
`false`, every node's `output_json` key is **omitted**, not set to null. When
enabled, it is the stored `string | null`, without parsing or additional
truncation. The same applies to other JSON-text fields: consumers decide
whether to parse them. If the engine truncated a value before saving it, this
API cannot recover the original value.

Both snapshots use the callback's explicit field allowlist. Extra database
columns such as `state_json`, `resolved_prompt`, and `version` are not exposed.
For security, `flow_runs.credentials_json` is **always null** and the stored
credential column is never read by this endpoint.

## Snapshot and callback compatibility

- This endpoint reads the current persisted record; it does not replay a
  previously delivered callback or require any callback configuration.
- The top-level `status` is the stored run status, identical to
  `ext_info.flow_runs.status`, including `running`, `waiting`, and `blocked`.
  It is not a notification event name. A callback for a workflow start uses
  `started` and a node callback uses a `node_*` event; neither is synthesized
  by this GET. Terminal `succeeded`, `failed`, and `cancelled` statuses match
  the corresponding workflow callback bodies.
- All stored node execution rows for this flow are returned, retaining every
  attempt. They are ordered by `started_at DESC, id DESC`. There is no
  default list-page truncation or latest-attempt-only reduction. An existing
  run with no persisted nodes returns `node_executions: []`.
- Flow and node writes can still be in progress. A response is not a frozen
  historical event or a guarantee that asynchronously written data has settled.
- `credentials_json: null` and returning all node attempts are deliberate
  differences from the outgoing callback implementation, which may contain
  credentials or apply its repository's default node page limit.

## Access and errors

When the host supplies a workflow permission repository or a run-subworkflows
reader, an identity is required unless the host has already authorized the caller
as an administrator. The effective reader includes the legacy composition that
passes it in the `facadeRepo` argument. Request validation and flow ID trimming
happen before inspection. Reader inspection authorizes the run before either
execution-record repository reads raw payloads; its identity and scope checks are
not bypassed when the router's permission repository is absent.

When a permission repository is supplied, non-administrators also need a view
grant for that workflow and must satisfy its run scope. An `all` grant allows
all ordinary runs; a Bot-scoped grant matches the full Bot/owner identity in
`origin_bot_id`. An explicit wildcard-owner grant matches the named Bot across
owners. Missing origin ownership does not satisfy a Bot-scoped grant.

On reader-enabled mounts, **managed fanout parents, restricted snapshots, and
sensitive fanout children are denied with a fixed HTTP 403**, including for
administrators. Their raw callback payloads cannot substitute for the existing
permission-filtered subworkflow views. Ordinary records keep the callback
allowlist and output opt-in behavior above. Before reading node payloads, the
selected run's exact `flow_id`, `workflow_id`, and `origin_bot_id` must still
match the inspected identity (and the trimmed requested flow ID); a mismatch
returns a fixed HTTP 409 without node payloads. This pins authorization to the
selected identity; it does not make concurrent flow/node writes transactional.

Caller-supplied query filters do not override these rules. Hosts remain
responsible for verifying request identities before the router. Only isolated
mounts with neither a permission repository nor a reader retain unrestricted
mode.

| HTTP status | Meaning |
| --- | --- |
| 400 | Blank flow ID or invalid `includeNodeOutput` |
| 401 | Identity required on a permission- or reader-enabled mount |
| 403 | Workflow/run scope denies access, or reader inspection identifies a fanout/restricted snapshot |
| 404 | No stored run with this `flowId` |
| 409 | Inspected/selected run identity or reader snapshot changed; retry |
| 503 | Required database repositories are not configured, or the reader is unavailable |
| 500 | Database read or other internal failure; no SQL/error details returned |

Errors keep the runs API's `{ "error": "...", "message": "..." }` shape.
Known reader failures map to fixed safe responses: unauthenticated to 401,
forbidden to 403, not-found to 404, invalid query to 400, identity/snapshot
conflicts to 409, unavailable to 503, and invalid persisted state to 500.
Neither caught messages nor driver details are returned. Unexpected errors
remain fixed HTTP 500 responses. Database errors must propagate as failures,
never as a successful empty node list or a false not-found response.

Every 5xx failure emits fixed privacy-safe ERROR metadata and places a diagnostic
in `res.locals.taskGuardApiDiagnostic` using `captureSensitiveApiError`. Its
stack is explicitly an **observation**, not the caught exception stack. The
caught error's message, stack, objects and extension properties are never
logged; they can contain SQL, credentials or raw workflow payloads. Expected
4xx rejections log only fixed status metadata at WARN.

## Propagation and validation

This is an additive route on the existing `createRunsRouter` mount. The
existing run list, detail, node routes, outgoing callback implementation, and
storage schema are unchanged. Consumers opt in by calling this endpoint;
there is no deployment configuration change, schema migration, or new
cross-package runtime dependency. Removing this route is the rollback path.

`server/routes/__tests__/run-execution-record.test.ts` covers the HTTP contract
with Express and an in-memory SQLite database, including output opt-in,
multiple attempts, large records, access scopes, and database failures.
`server/services/__tests__/execution-record-service.test.ts` independently pins
the transport-free projection, output opt-in, credential redaction, and error
propagation behavior.

`server/routes/__tests__/run-execution-record-authorization.test.ts` exercises
reader-only, reader-plus-permission-repository, and legacy reader compositions
using the actual reader service, permission resolver and migrated SQLite schema.
It covers ordinary authorization and fanout denial including administrators,
identity drift, fixed reader-error mapping, and privacy-safe console/HTTP
logging diagnostics. Fault injection supplements the actual-service paths;
existing run-subworkflows service suites remain the reader contract baseline.

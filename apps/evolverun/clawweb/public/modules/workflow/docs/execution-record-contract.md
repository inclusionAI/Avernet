# Workflow execution record HTTP contract

Owner: `@avernet/workflow`. Contract version: 2 (authenticated Service API with executed-workflow YAML).

`GET /api/runs/:flowId/execution-record` lets clients query the stored
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
- Send `Authorization: Bearer <token>` using the dedicated 64-character lowercase
  hexadecimal service token. The scheme is case-insensitive; token bytes are not
  normalized. Login cookies, identity headers, administrator status and query
  parameters do not replace this credential. A workflow/run view grant is not required.

```sh
curl -H "Authorization: Bearer $EXECUTION_RECORD_TOKEN" \
  "$CLAWWEB_URL/api/runs/$FLOW_ID/execution-record?includeNodeOutput=true"
```

## Successful response

HTTP 200, `Content-Type: application/json`, `Cache-Control: no-store`.

```json
{
  "workflow_id": "review-pipeline",
  "flow_id": "flow-example",
  "status": "succeeded",
  "workflow_yaml": "id: review-pipeline\nnodes: []\n",
  "workflow_snapshot": {
    "status": "resolved",
    "snapshot": {
      "kind": "deployment",
      "workflowId": "review-pipeline",
      "workflowVersion": 1,
      "deployNumber": 7,
      "configDigest": "b03d646b3352ccb661e011c28e25ca90c35856128556e123ca56070c49644220"
    }
  },
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

## Executed workflow YAML

`workflow_yaml` is the YAML representation of the configuration used by this
execution instance. `workflow_snapshot` identifies its provenance using Shared's
`WorkflowSnapshotBinding`; the callback-compatible `ext_info` allowlist is unchanged.

- Deployment runs use the selected run's `workflow_version` and
  `workflow_deploy_number`, requiring an unambiguous matching deployment-history
  entry. An active/latest deployment or mutable `workflow_specs` is never a fallback.
- Saved/remote runs (`workflow_version = -1`) use the same selected run's captured
  `state_json.workflowSnapshot` and verified `workflowPin` identity/commit, without
  Git or network access. The state itself is not included in the response.
- Existing captured snapshot associations, when available, must match the run's
  identity and configuration digest. Older managed schemas without the optional
  association column remain supported; ordinary database failures still propagate.
- A valid historical content-only YAML wrapper preserves its YAML text. Structured
  JSON snapshots are serialized as equivalent YAML; original formatting, comments
  and byte identity cannot be recovered from JSON-only storage.
- Saved-run YAML recovery checks the complete `state_json` against an 8 MiB
  budget and the optional `workflow_snapshot_json` association against a 64 KiB
  budget before parsing either. Oversized state returns `SNAPSHOT_MISSING`; an
  oversized association returns `IDENTITY_MISMATCH`. These are YAML-recovery
  budgets, not limits on the complete callback-compatible execution record.
- Workflow document input/output is limited to 1 MiB. Documents are validated as
  JSON-compatible YAML 1.2 with at most 100,000 visited AST nodes and path depth 100;
  duplicate keys, multiple documents, aliases/anchors, explicit tags, non-string
  mapping keys, non-finite numbers, negative zero and unsafe integers are rejected.
  Decimal fractions retain JavaScript `number` precision, not arbitrary source-text
  decimal precision. For saved state, the AST budget and numeric checks apply to
  identity/pin/snapshot subtrees, not unrelated execution fields. Unsupported
  historical documents are reported as unavailable.
- Historical data without a reliable association returns `workflow_yaml: null`
  together with `workflow_snapshot: { status: "unresolved", reason: ... }`.
  Reasons are `LEGACY_MISSING`, `SNAPSHOT_MISSING`, or `IDENTITY_MISMATCH`. Do not
  silently substitute the current workflow or return a fabricated version.
- An infrastructure/database failure is an HTTP 500, not a successful historical
  unavailability result. YAML resolution happens only after authentication and
  structural payload guards.

The YAML is the complete captured workflow configuration and may contain inline
secrets or business data. The service token therefore authorizes more than public
workflow topology; it is not safe for general browser sharing. This endpoint does
not promise payload-wide secret redaction.

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

Every mount requires the dedicated service token, including isolated mounts and
administrator requests. A valid token authenticates this endpoint without needing
an additional user cookie/header or a workflow view grant. Request validation and
flow ID trimming happen before database reads; token verification completes before
flow, node or workflow-snapshot payloads are read.

### Token configuration

Store exactly one enabled row in **Workflow's `cm_app_config`**, not Clawevolve's
`ce_app_config`:

- `config_key`: `api_token`
- `enabled`: `1`
- `config_yaml`:

```yaml
execution_record_sha256: "<SHA-256 digest of the original token: 64 hex characters>"
```

The token is generated from 32 cryptographically random bytes encoded as lowercase
hexadecimal. Only its SHA-256 digest is stored in this configuration; the caller
keeps the original token. The configuration must be a mapping with precisely the
one expected digest field. Missing/disabled/malformed configuration, a missing
configuration reader, or a configuration database read failure returns a fixed
HTTP 503. Disabling/deleting the row disables access, not authentication.

The reader loads the `api_token` row on each request using the existing parameterized
`AppConfigRepository.findByKey` query. Subsequent requests observe rotation or
revocation without a process restart. The received token is hashed as UTF-8 and
compared to the stored 32-byte digest using `crypto.timingSafeEqual`. Duplicate or
malformed Authorization headers and missing/incorrect credentials return a fixed
HTTP 401. The digest itself is not an accepted bearer credential.

Hosts must pass Workflow's configuration repository through
`createRunsRouter(..., { executionRecordConfigRepo: new AppConfigRepository(db) })`;
the public composition does so. Existing external hosts that omit this dependency
fail closed until updated. Do not introduce an environment-token or identity-header
fallback. Protect the configuration management API with a verified administrator
principal: an unauthorized configuration writer could install their own digest.
Use HTTPS, retain deployment-level rate limits, keep tokens out of URLs and logs,
and use separate credentials for separate environments.

**This endpoint does not check workflow view grants or Bot/owner run scope.** A
valid service credential can query ordinary runs across workflows and owners.
It does not call `resolveRunViewScope` or the authorization-enforcing `inspectRun`
reader method. This dedicated credential is not accepted as authentication by
other run list/detail/node/subworkflow or write APIs, whose ACLs remain unchanged.

On reader-enabled mounts, the endpoint still rejects **fanout parent and sensitive
fanout child payloads with a fixed HTTP 403**, including for administrators. It
classifies the same strict database snapshot used for the response, independently
of actor permissions: any nonempty top-level `subworkflowFanouts` map or own
`fanoutOrigin` key (even null) makes the record unavailable. It does not resolve
child permissions or consume custom reader inspection flags. A malformed root or
fanout map returns a fixed 500; a nonempty map is conservatively denied without
validating its group contents. These raw callback payloads cannot substitute for
the existing permission-filtered subworkflow views.

The strict flow snapshot reads `state_json` internally for this classification;
the response allowlist never exposes it. The selected `flow_id` must match the
trimmed requested flow ID, and a state snapshot's embedded `flowId`, if present,
must match the selected run. A mismatch returns a fixed HTTP 409 before reading
node payloads. Flow and node reads are not transactional.

Caller-supplied query filters do not override these rules. No login, administrator,
loopback, development or legacy-mount exception bypasses token verification.

| HTTP status | Meaning |
| --- | --- |
| 400 | Blank flow ID or invalid `includeNodeOutput` |
| 401 | Missing, malformed, duplicate or incorrect Bearer credential |
| 403 | Reader-enabled mount identifies a fanout parent or sensitive child snapshot |
| 404 | No stored run with this `flowId` |
| 409 | Selected or embedded flow identity differs from the requested flow; retry |
| 503 | Required repositories or enabled/valid token configuration unavailable |
| 500 | Database read, malformed classification state or other internal failure; no SQL/error details returned |

Errors keep the runs API's `{ "error": "...", "message": "..." }` shape.
Neither caught messages nor driver details are returned. Database errors must
propagate as failures, never as a successful empty node list or a false not-found
response. Reader/permission service outages do not affect this endpoint because
it does not invoke either service.

Token/configuration failures never return or log the supplied token, stored digest,
configuration YAML or caught database/parser errors. Every 5xx failure emits fixed privacy-safe ERROR metadata and places a diagnostic
in `res.locals.taskGuardApiDiagnostic` using `captureSensitiveApiError`. Its
stack is explicitly an **observation**, not the caught exception stack. The
caught error's message, stack, objects and extension properties are never
logged; they can contain SQL, credentials or raw workflow payloads. Expected
4xx rejections log only fixed status metadata at WARN.

## Propagation and validation

The response adds `workflow_yaml` and `workflow_snapshot` without changing the
callback-compatible `ext_info` fields. Requiring a service token is a breaking
access-contract change: configure the enabled digest row, wire the configuration
repository in every host, and update callers to send the original Bearer token
before deploying this route version. There is no schema migration, credential
seed, environment fallback or new runtime dependency. No production token/digest
belongs in repository source, fixtures or documentation.

The existing run list, detail, node routes and outgoing callbacks retain their
contracts. Rollback must not accidentally re-expose this sensitive endpoint;
disabling/removing the route is safer than reverting to an unguarded mount.

Verification belongs to the execution-record route, token and workflow snapshot
suites, the transport-free projection suite, and the actual public-host composition
suite `server/__tests__/public-execution-record.test.ts`. These cover Token-only
access, administrator bypass prevention, `cm_app_config` selection and dynamic
revocation, preserved fanout guards, exact historical YAML, null/unresolved legacy
data, error privacy, and unchanged authorization on other read endpoints. The
existing run-subworkflows service suites remain the reader contract baseline.

# Run list HTTP contract

Owner: `@avernet/workflow`. Route: `GET /runs` relative to the host API mount
(normally `GET /api/runs`). This is a read-only Service API, not an engine
Plugin API. The optional `query` parameter is an additive, backward-compatible
extension; the response envelope and existing filters are unchanged.

## Keyword search

`query` accepts a single string. Leading and trailing whitespace is removed.
Omitted, empty, whitespace-only, repeated (array), or structured values do not
activate keyword filtering. Clients should send one URL-encoded string.

An active keyword performs a literal substring match against any of:

- `flow_id` (Run ID);
- `triggered_by` (initiator);
- `origin_bot_id` (stored Bot identity, including the owner suffix when present);
- `input_json` (stored run input, including business keywords in nested JSON).

It does not search node logs or outputs. Input matching is against the stored JSON
text, not decoded JSON values (for example, escaped Unicode remains escaped).
`%`, `_`, and `!` are escaped as
literal characters rather than LIKE patterns. Values are SQL-bound parameters.
Case sensitivity follows the database column collation; portable clients must
not rely on a particular case-folding behavior.

## Composition with existing filters

The keyword's four field matches are grouped with OR. That group is combined
with AND against `workflowId`, `status`/`statuses`, `inputQuery`, `from`/`to`,
the existing origin-Bot scope, and workflow view permissions.

- A non-empty comma-separated `statuses` list takes precedence over `status`.
- `inputQuery` retains its separate existing input-JSON LIKE semantics.
- `from` and `to` retain inclusive `started_at` bounds and accept epoch seconds,
  epoch milliseconds, or ISO timestamps. Omitting them searches all history;
  the run list does not inherit the dashboard's metric window.
- `botOwnerId`/`botId` retain their existing origin-scope behavior. The keyword
  does not grant access or override the host's authorization decision.
- Non-admin workflow visibility is applied before counts, ordering, and
  pagination. An empty allowed-workflow scope yields no rows or counts.
  Administrators retain their existing unrestricted view.

Results are ordered by `started_at DESC`. `limit` (default 30, maximum 2000)
and `offset` (default 0) select a page of matching, authorized records.
Records with equal start times have no additional guaranteed order.

## Response compatibility

The JSON envelope remains `{ runs, total, limit, offset, statusCounts? }`.
`total` is the number of authorized matches **before** pagination; `runs`
contains only the requested page. A no-match result is HTTP 200 with
`runs: []` and `total: 0`. Hidden matches must not affect either field.

`statusCounts` is present only when `status`, non-empty `statuses`, non-empty
`inputQuery`, and active `query` are all absent. When present, it observes the
same workflow, time, origin, and permission scope as the list. Searching
clients must not interpret an omitted `statusCounts` as real zero metrics;
dashboard metrics use their independent unfiltered query.

Examples:

```http
GET /api/runs?workflowId=example&status=failed&query=bot_123&limit=20&offset=0
GET /api/runs?workflowId=example&statuses=cancelled,canceled&query=gateway
GET /api/runs?workflowId=example&query=%20run-123%20
```

## Compatibility tests

`server/repositories/__tests__/run-list-search.test.ts` exercises both the real
SQLite repository and the Express HTTP route. Coverage includes normalization,
non-scalar/omitted parameters, unchanged response shape, filter intersections,
status-list precedence, literal wildcards, permission-scoped counts and rows,
pagination, nested input keywords (including Chinese), literal input wildcards,
empty permissions, and the admin view. Run it with:

```sh
npm test --workspace @avernet/workflow -- server/repositories/__tests__/run-list-search.test.ts
```

## Fanout observation (fanout.v1)

The public bootstrap injects `RunSubworkflowsService`, its read-only
`RunSubworkflowsStore`, and the host's owner-preserving `RunViewPermissions`
implementation (including `resolveRunViewScope`). The module-local Service API
and response types are in `server/contracts/run-subworkflows.ts`. The new
repository is intentionally separate from the engine/CAS write repository;
it issues only SELECTs, does not select credential values, and propagates
storage failures. The HTTP adapters live in `server/services/run-read-http.ts`
and `run-subworkflows-http.ts` because the existing runs route was split at its
read/write boundary. They are delivery adapters, not domain/core services.

### Endpoint and authoritative state

```http
GET /api/runs/:flowId/nodes/:nodeId/subworkflows?groupExecutionId=:id&limit=30
GET /api/runs/:flowId/nodes/:nodeId/subworkflows?groupExecutionId=:id&limit=30&cursor=:cursor
```

`groupExecutionId` is required and must match exactly. `limit` is an integer
from 1 through 100 (default 30); query parameters must be scalar. A read loads
one persisted parent row and reads `state_json.subworkflowFanouts[nodeId]`. It does not use event reconstruction, live engine polling, node output,
or the result projection as a fallback. Parent/group/child execution identity
mismatches are errors, not empty groups. No query starts, rejoins, resumes,
retries, cancels, seals, or writes a runtime state.

Production shape is taskguard's `FanoutState`:

- identity and `revision` are at the group root;
- policies and concurrency are in `group.config`;
- the child workflow ID is `group.childWorkflow.id`;
- `children` contains flat `FanoutChildState` values with parent/group identity,
  `itemIndex`, `itemId`, `attemptNumber`, `childFlowId`, `status`, `result`, and
  `error`. v1 has one attempt per item.

For already persisted design-preview snapshots, the reader also accepts the
approved older shape with root policies/concurrency, `dependencySnapshot`,
and `children[].activeAttemptNumber/attempts`. Only the active attempt is
counted or returned. Unknown/malformed shapes fail closed rather than silently
becoming a zero-child success. Neither version's raw `result`, exports, item
inputs, params, session keys, filesystem references or attempt history is
returned by this endpoint. Error DTOs expose only the authorized category,
code, message, retryability (missing means false), and optional source node.
Runtime writers must supply already-redacted error messages, not full logs.

### Authorization, counts and pagination

The caller identity comes from the host request identity resolver, **never**
`botOwnerId`. Authorize the parent workflow and its full stored origin first.
Then authorize each referenced child using its actual workflow and complete
`origin_bot_id = botId:ownerId`; an explicit `ownerId: '*'` grant retains its
all-owner semantics. Parent access does not grant child access. Missing child
rows, unproven origins, or references to another workflow are not visible.
Each request, including a cursor request, resolves current permissions again.

Response fields include:

- `schemaVersion`, `parentFlowId`, `groupNodeId`, `groupExecutionId`, `revision`,
  opaque `snapshotId`, group `status`, `childWorkflowId`, `successPolicy`,
  `concurrency` and visible `activeCount`;
- `countScope: 'full' | 'visible'` (the implemented field is singular);
- `groupTotal`, `visibleTotal`, `returnedCount`, `counts`, `nextCursor`;
- `children[]`: continuous zero-based `displayIndex`, `itemId`, `attemptNumber`,
  `childFlowId`, `status`, and `error`. `itemIndex` is included only in a full view.

`countScope: full` requires unrestricted grants for both workflows and all
current child origins to be proven. A bot-limited grant is not a full-statistics
grant even if it happens to expose every current row. Otherwise `groupTotal`
is null and every count is calculated **after filtering**, before pagination.
`visibleTotal` covers the entire authorized snapshot, while `returnedCount`
is the number of rows in this page. `counts` contains `successCount`,
`failedCount`, `pendingCount`, `cancelledCount`, `timedOutCount`, and
`rejectedCount`. Only confirmed terminal states leave the pending bucket.
The group status remains the persisted collector status; a succeeded
`all_done` group may contain failed children.

Cursors bind the exact parent/group/execution, revision, actor, current grants
and visible origins/data to an offset. They are not authorization tokens.
They are rejected with 409 when that snapshot cannot continue; callers must
refresh page one instead of merging pages from different snapshots.

### Closing legacy read bypasses

With the new reader injected, the run list and workflow run statistics apply
workflow plus **owner-qualified** origin predicates before counts and paging.
`botOwnerId` no longer supplies identity; `botId` may only narrow the view.
Legacy isolated callers that do not inject this reader retain the previous
list behavior; the fanout route returns 503 there. Production hosts must wire
the reader, as the public bootstrap does.

All per-run GET/HEAD requests, including direct child links, nodes and events, use the same
owner-preserving run guard and no-store policy. HEAD derives its response metadata
from the same authorized, redacted representation as GET, never the raw row.
A parent with persisted fanout groups exposes
`subworkflowGroups: [{ nodeId, groupExecutionId }]` for UI discovery, but not
its raw state/input/params/result or opaque node payloads. All parent nodes
are reduced to execution metadata, since downstream outputs can copy a group
result. Parent events are omitted in this minimal read-only view; use the
authorized group snapshot instead. Restricted parents cannot use the legacy
steps/replay/dynamic/topology endpoints to fetch equivalent unfiltered data.
Input keyword search also excludes fanout parents' opaque input (and corrupt
state), preventing search/counts from becoming a hidden-item oracle.
No existing mutation handler is changed or newly authorized by these reads.

### Errors and client lifecycle

| HTTP | Meaning |
| --- | --- |
| 400 | Invalid/missing/repeated parameter, malformed cursor or limit |
| 401 | Request identity missing |
| 403 | Parent/direct child denied, or restricted raw-parent read |
| 404 | Run or persisted node fanout absent |
| 409 | Identity/generation mismatch, revision or permission-bound cursor stale |
| 500 | Corrupt persisted state or failed storage/permission read; no raw exception text |
| 503 | Observation service not configured |

A genuinely empty group and a nonempty group with no visible children are both
200, distinguished by the authorized count scope/total and persisted status;
absence (404), denial (403), and failure (500) are not rendered as empty data.
Responses use `Cache-Control: private, no-store`.

The existing node detail panel mounts the group component only while expanded.
It uses module-local API/hooks, refreshes every three seconds, cancels requests
on close/identity changes, suppresses lower revisions, and discards stale
pagination replies. A permission/read error removes stale rows rather than
leaving an old authorized snapshot onscreen. Before the first durable snapshot,
a fanout node declared by the workflow spec shows a no-data entry without
inventing an execution ID. Child selection reuses the native `DetailDrawer`
container for a read-only run summary and node list, with a parent-context back
button and a separately authorized full-run link. Child nodes never contribute
to the parent's node counters.

### Observation verification

- `server/services/__tests__/run-subworkflows.test.ts`: real Express routes,
  SQLite persistence, flat/legacy state, exact identities, full owners,
  parent-first authorization, filtered counts/pages, stale cursors, no mutation,
  storage failures, and legacy read/search bypasses.
- `web/components/subworkflows/__tests__/SubworkflowPanel.test.tsx`: the real
  node detail mount, API fetch boundary, dialog/back navigation, states/errors,
  polling, revoked access, execution changes, and stale pagination/revisions.

Run those suites with the workflow workspace's Vitest command, and run
`npm run ci:build` from the ClawWeb workspace to rebuild package exports and the
host application. All tests use synthetic in-memory data, not live services.

The read repository supports old schemas without the optional
`evolution_analysis_status` projection column. It retries only an exact
SQLite/MySQL missing-column error for that column, omitting that column on the
retry. Missing required columns, connection errors and fallback failures still
propagate; they never become empty successful reads.

### ClawMind ordinary child correlation

For a flat fanout item, the reader may correlate the parent's known
`nativeFlowId` with a row whose `state_json.fanoutOrigin` matches the parent
flow, node, execution, item ID and index, attempt, logical `childFlowId`, and
child workflow ID exactly. The row's actual flow ID and workflow ID must also
match. `fanoutOrigin.requestDigest` must be a nonblank string; if the parent
item carries `requestDigest`, it must also match exactly. A parent `inputDigest`
is a different field and is not used as a substitute. Missing, malformed, or
mismatched metadata, missing native IDs or rows, and unauthorized child runs
are hidden. The existing attempts-format path is unchanged. Child and parent
are independently subject to the existing workflow and full owner-qualified run
read permissions. A parent grant never grants access to a child. For a run with
`fanoutOrigin`, run detail projects `state_json` onto validated workflow ID and
version, business status, current phase, execution mode, active node IDs and
node execution metadata (status, phase, executor, retry counts and timestamps).
From this state projection, opaque workflow data, node results/errors, credentials,
sessions, params, snapshots and fanout metadata are omitted. The child run
row also redacts `params_json`, `input_json`, `result_json` and session fields.
For child detail and both node-list modes, node execution records are reduced
to execution metadata; child events are omitted and other raw child reads such
as replay and steps are forbidden. Access remains subject to the child's own
owner-qualified run permission. String labels are bounded and
restricted to identifier-like characters; invalid fields are omitted. These
labels are still engine/workflow-supplied and must not be populated with
secrets. A child that itself owns a fanout remains subject to the existing
managed-parent redaction instead.

The child intervention read response returns only a `sessionConfigured`
boolean for session presence, with `interventionReadOnly: true`,
`interventionReady: false`, no actions, and no session values. The frontend
shows an explicitly read-only state and does not offer session settings or
chat/intervention actions; it also fails closed if the intervention read
fails. This UI restriction is not write authorization: the existing public
mutation endpoints have no independently verified production principal and
must not be considered safe to expose or enable for child interventions. This
change does not modify or authorize those mutation endpoints. Ordinary runs
retain their existing behavior.

This is a **best-effort correlation of metadata written by a trusted engine**,
not an authenticated provenance, integrity, or incarnation proof. It assumes
trusted engine writers and a secured engine transport; it does not resist a
trusted writer forging metadata or deleting and recreating runs. The existing
public bootstrap first applies its global admin-role middleware, which sets role
flags but does not reject the request, then mounts ordinary
`POST /api/internal/runs` under `engineAuth`; the legacy route itself has no
additional per-run authorization. With a valid
configured public key, unsigned writes are rejected. In `dev`, however, an
empty key explicitly permits unsigned socket-loopback clients; `pre` and `prod`
with no key reject requests. A configured shared engine signature identifies a
trusted signer, not a particular run owner; it covers timestamp and body but
not method or path and has no nonce/replay protection. It therefore requires a
trusted transport and must not be treated as proof against forgery or replay.
Deployments without an adequate trusted-writer and transport boundary must not rely on this correlation for security. Ordinary
request association records and legacy managed checkpoint fields alone do not
establish this correlation. The endpoint never returns the raw origin state,
digest, or credentials.

The bootstrap authentication integration test is
`server/services/__tests__/run-subworkflows-engine-auth.test.ts`. Because it
loads the actual public host through its package exports, build the shared,
Clawevolve, ClawInsight, workflow and ClawWeb packages before running the test
in a clean checkout; the test uses migrated in-memory SQLite and a synthetic
Ed25519 key, not a production credential.

# Subworkflow observability read contract

`createSubworkflowObservabilityRouter(db, authorize)` is a read-only Express router.
The host mounts it under its run API; the route is `GET /:flowId/subworkflows`.
The public bootstrap mounts the router with an authorizer requiring a verified identity and whole-workflow (`all`) view scope (or verified admin); its identity resolver is available only for loopback development, so other public deployments fail closed. Internal hosts must explicitly mount it and inject a server-verified principal and server-owned permission store. A Bot-scoped edit grant does not grant whole-workflow run visibility. Do not inject an authorizer based on headers, unverified JWTs,
request flags, or a limited bot/run scope. The stored workflow of the parent and every resolved child is checked against
the authorization decision for that workflow. Decisions (including denials)
are cached only within a single request, so a shared parent/child workflow is
authorized once per request and rechecked on the next request.

A 200 response has exactly this shape (times are stored integer epoch values;
nullable fields are `null` when unavailable). This example illustrates the
response schema, not a guarantee that running children are discoverable:

```json
{"items":[{"parentNodeId":"node-1","childFlowId":"child-1","childWorkflowId":"child-workflow","status":"running","startedAt":123,"completedAt":null,"nodes":[{"nodeId":"task-1","nodeTitle":"Task","status":"running","attempt":1}]}],"completeness":"best_effort"}
```

- Discovery reads at most the 200 most recent `subworkflow_linked` and
  `subworkflow_finished` events for the parent flow and its workflow, ordered by
  event time then database ID descending. It parses only `childFlowId`, dedupes
  by child flow ID, and processes at most the first 20 distinct valid child
  IDs. Missing, trial and denied candidates also consume this budget, bounding
  lookup work. It takes `parentNodeId` from the newest usable event for that
  child. Malformed or missing historical events are ignored; `parentNodeId`
  is null if the event has no valid node ID. Event payload workflow IDs and
  statuses are not trusted. The reader accepts both event types, but current
  ClawMind/TaskGuard does not emit `subworkflow_linked`; its available discovery
  evidence is `subworkflow_finished` only.
- Each child is resolved against `flow_runs`, then checked against the
  per-request authorization decision for its **stored** workflow ID. Node rows
  are read only after that check succeeds. Missing, unauthorized and trial children are
  omitted without an identifier, count, or unavailable placeholder. Parent or
  child runs marked `trial` or `trial_run` in the server-owned repair release
  records are excluded. This is not an existence oracle for child IDs.
- Each authorized child returns at most 50 newest `node_executions` by row ID,
  restricted to that child's flow and workflow. Distinct attempt rows are not
  collapsed. Node title can be null; a missing or invalid attempt is null.
  Unrecognized status values become `unknown`. No raw events, inputs, outputs,
  error text, prompts, sessions, or other execution payloads are returned.
- `best_effort` is **never** a completeness guarantee: event persistence can
  fail, old runs may predate these events, records may be missing, and any
  limit (events, child candidates, or nodes) may truncate evidence. No
  omitted-count or truncation flag is exposed.
  This is a bounded snapshot, not a transactional or recursive tree; concurrent
  changes can be reflected inconsistently.

The flow ID must be 1–255 characters without whitespace, control characters,
or path separators; query parameters are not supported. Invalid input is 400;
missing, trial, or unauthorized parent returns the same 404 body. Database, migration,
or authorization dependency errors return a generic 503, never an empty success
or raw diagnostics. A missing/no-op database or missing authorizer also returns
503. Responses have `Cache-Control: no-store`. Successful reads require the
existing `flow_runs`, `flow_events`, `node_executions` (including `node_title`)
and `workflow_repair_release_records` migrations; absent tables/columns fail
closed. SQL uses bound values and SQLite/MySQL-compatible syntax. Normal event,
flow and node flow-ID indexes are expected for bounded per-flow lookup.

## Runtime and rollout limitations

This PR does not include any TaskGuard code changes or add write-side
observability. Current ClawMind/TaskGuard does not emit `subworkflow_linked`;
only existing `subworkflow_finished` events are available for discovery. Event
persistence is best effort, not an authoritative lineage record. In production,
a child is not guaranteed to be visible while it is running, and even a finished
child is visible only if suitable evidence and the required stored records are
available and authorized.

Ordinary child runs are not guaranteed to be visible: depending on the TaskFlow
adapter, they may lack a `flow_runs` row. In that case the read API intentionally
omits the child rather than disclosing an unverified identifier. Adding or
improving write-side event and run-record production requires a separate change;
it is not delivered by this read-only PR. ClawMind and the internal ClawWeb host
are separate repositories and are not modified by this change; their use requires
separate integration and verification. Do not treat this event-based compatibility
view as complete or production-ready real-time tracing. Durable association, crash
recovery, attempt identity, nested-parent correctness and production host
authorization require follow-up before that claim can be made.

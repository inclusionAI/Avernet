# AIStudio training-node observation and control

Owner: workflow module. Participants: ClawMind executor/runtime and ClawWeb
workflow server/UI. This is an additive Service API and persistence contract;
no existing executor, callback token, or workflow route is repurposed.

## User-visible behavior

The existing RunDetail page and its node-row expansion remain authoritative.
Only `executor_type = aistudio-train` displays the training-task observation
card and manual-query controls. Node waiting status is distinct from external
training status and watch lifecycle. Other node details are unchanged.

The user may observe the recorded task, copy its ID, navigate to a recorded safe
HTTPS task URL, or request one immediate query. Missing task URLs are not guessed.
All displayed timestamps use Asia/Shanghai. Automatic querying starts after ten
minutes and repeats every minute by default; a manual request does not reset it.
Unknown statuses and query errors are not training failures.

## Service API: browser to ClawWeb

- `GET /api/runs/:flowId/nodes/:nodeId/aistudio-train?attempt=N`
- `POST /api/runs/:flowId/nodes/:nodeId/aistudio-train/poll-manual`
  with `{ "attempt": N }`

Both use a server-loaded run/node/watch identity. The POST never accepts a caller-
selected record ID, owner, task URL or external credential. A successful POST
returns 202: it persists a manual-query intent, not a completed AIS query.
The engine observes that intent using its normal durable watcher.

The read DTO contains watch ID, flow/node/attempt, mode, record ID, recorded URL,
watch phase, last AIS status, query schedule, recent query source/time, errors,
manual availability/pending state, optional cooldown deadline, and a positive
monotonic `version` for server snapshot ordering. The browser never submits that
version as an authorization or CAS credential. Business times
are ISO strings in this API; the polling interval is numeric seconds. Internal
leases and submission configuration are not part of the DTO.

Errors distinguish invalid attempts (400), missing watches (404), unavailable or
stale state (409), manual cooldown (429 with Retry-After), and unavailable
configuration/identity/runtime/persistence (503). Authentication and object-level
authorization failures are 401/403. Persistence failures must not return accepted.

## Service API: ClawMind persistence

The central table is `aistudio_train_watches`. The signed internal routes are
mounted below `/api/internal/aistudio-train-watches`:

| Operation | Input | Envelope data |
| --- | --- | --- |
| POST `/create` | `{watch}` | `{watch}` |
| GET `/find` | flowId, nodeId, optional attempt | `{watch: row or null}` |
| GET `/due` | exact ownerBotId, now, limit; optional complete after-cursor | `{watches}` |
| POST `/cas` | `{expectedVersion, watch, leaseOwner?}` | `{updated}` |

All responses use `{success: true, data: ...}` on success. Durable row fields
are camelCase on the wire and snake_case in SQL. Times are integer epoch seconds.
Identity includes watchId, flowId, workflowId, nodeId, attempt, mode, recordId and
exact `ownerBotId = botId:ownerId`; identity cannot be changed by CAS.
`next.version` must equal `expectedVersion + 1`.

Due enumeration uses a stable keyset cursor. Optional query fields
`afterManualPriority`, `afterNextPollAt` and `afterWatchId` must be supplied
together. Order is manual requests first (priority 0), then automatic requests
(priority 1), followed by `nextPollAt` and `watchId`. An engine advances its scan
past temporarily unavailable or stale targets without treating them as canceled;
no more than ten actual CLI queries run in one tick. Missing runs are excluded
before SQL ordering and LIMIT, so orphan watches cannot occupy every page.
Existing targets with mismatched exact ownership remain forbidden.

A leased CAS supplies the exact `leaseOwner` string. Its atomic SQL UPDATE checks
the old row's lease owner and requires `lease_expires_at` to exceed the database's
execution-time clock (`unixepoch()` / `UNIX_TIMESTAMP()`), in addition to immutable
identity and version. A client timestamp is not a valid lease guard. Poll result,
terminal transition and final resolution writes require this guard; ordinary
claim, manual mailbox and release writes retain version CAS. Terminal side
effects recheck current ownership and lease after the transition acknowledgement.

A versioned lease fences query updates. Definite terminal results first enter
`resolving`; the owning ClawMind controller rechecks its local workflow revision,
node state and attempt before continuation. The watch is `resolved` only after
that state has persisted. Manual requests and automatic queries use the same
lease path. Expired or stale writers cannot replace a newer version.

Public clients compare persisted versions, not response arrival time or the number
of completed GETs: an old manual 202 cannot replace a later consumed/terminal
watch, and a pre-submit GET cannot discard the new command's confirmed mailbox.
Equal-version DTOs may still refresh time-derived availability/cooldown fields;
their immutable persisted observation fields cannot represent different states.

## Durable manual-query audit

`POST /api/internal/events/aistudio-train-manual-audit` is a dedicated signed
service endpoint, not the legacy best-effort event writer. It accepts the existing
flat snake_case event representation, with event time in epoch seconds. Raw-body
Ed25519 capture includes this exact path, and host composition mounts this route
before the legacy events router. Unknown fields, missing schema, invalid target,
unsigned traffic and unconfirmed writes never receive a successful ACK.

The engine's event repository exposes a specific atomic audit operation. It uses
a stable event ID `aistudio_manual_<watchId>_<requestedAt>` and a dedicated
`aistudio_train_manual_audits` UNIQUE marker. The marker, exact immutable request
identity, inserted event and actual `event_row_id` linkage commit in one
transaction. An identical retry succeeds only after reading and validating that
persisted linkage; observation time and status may change on retry, but actor,
watch, attempt and request identity cannot. Pre-marker exact events may be adopted
without rewriting historical rows or changing legacy event uniqueness.

The `aistudio_manual_` event-ID namespace and structured training-manual events
are reserved to this operation. Generic SQL/API event writers reject them before
SQL so a competing generic write cannot bypass the marker. Ordinary legacy event
IDs retain their existing duplicate semantics.

Only `{success:true,data:{inserted:true}}` confirms an API audit. The watcher
awaits this confirmation before consuming a manual mailbox or resolving a node.
Rejected/failed persistence retains the actor and request for retry. Timeout also
passes through this audit gate; automatic queries do not create manual events.

## Authentication and composition boundary

Internal requests require configured service authentication. They must not use
an unsigned-development fallback. A service signature is not a user ACL.

Public routes require a verified principal supplied by the host and authorization
against the server-loaded exact Bot/Owner pair. Observation needs read scope;
manual queries additionally require operation/execute permission. Existing
headers, decoded cookies, legacy run-list projection or knowledge of a flow ID
are not proof of authority. Missing providers fail closed. The standalone public
runner has no IAM verifier, so merely adding UI code must not expose an insecure
manual-operation endpoint.

## Compatibility, rollout and rollback

Apply ClawMind migrations **41 and 42** and ClawWeb migrations **155 and 156**
before enabling new nodes. These introduce the watch and atomic audit-marker
tables; no legacy event rows are rewritten.
Managed databases may reject application DDL; explicit schema rollout and a
readiness check remain required. SQL and API persistence use the same business
contract; a NoOpDatabase is never a watch store. Deploy the host's authenticated
route composition with the matching engine version.

Existing workflows and historical `async-callback` waits are unchanged. Rollback
uses the previous workflow version; do not reinterpret existing watch rows as
callback tokens. Version one does not automatically stop external training on
timeout or implement an undefined failure-as-success branch policy.

## Validation boundary

Required evidence includes SQL/API conformance, due/lease/CAS races, exact owner
and unsigned-request denial, manual cooldown and failed writes, controller resume
and stale-attempt safety, conditional node-card rendering, and query-cache
invalidation. Unit tests use fake CLI responses and clocks. They do not establish
production AIStudio connectivity, schema deployment or host IAM integration.

See the module usage guide at
`apps/evolverun/clawweb/public/modules/workflow/docs/aistudio-train.md` and the
ClawMind repository's `specs/aistudio-train/` contract.

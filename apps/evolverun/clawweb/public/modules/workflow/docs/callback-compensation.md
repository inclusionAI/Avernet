# Workflow success callback compensation

Status: implemented API and ClawWeb entry; **delivery is denied by default**.
This is notification recovery, not workflow recovery. It never invokes an engine,
retries a workflow, changes a run/node terminal state, or replays business actions.
It is separate from the normal sender's completion/phase serialization fixes.

## Operator flow

In ClawWeb's workflow workspace (`/workflows/workspace?workflowId=…`), an
independently authenticated administrator can open **成功回调补偿** beside Refresh
on the run overview's Runs tab. The UI belongs to the workflow module under
`apps/evolverun/clawweb/public/modules/workflow/web`, alongside the native run
list; it does not depend on or change `apps/frontend-nextgen`.
Select an explicit workflow-level callback configuration, a completion-time range
(default last 24 hours, with timezone shown) or up to 100 run IDs. Scan is not
limited to the current run-list page. A range containing more than 100 runs must
be narrowed; a partial scan must not masquerade as complete evidence.

A persisted, 15-minute preview separates eligible, already delivered, review and
ineligible records. Only eligible items can be selected. Confirmation identifies
the environment/workflow/receiver, requires a reason and explicitly acknowledges
notification-only semantics. Closing the dialog does not stop or recreate work;
reopening restores the same persisted batch. Stop affects only unsent items.
After a persisted batch is terminal with no queued/sending items or pending
audit, **新建补偿** explicitly starts a new preview without deleting server-side
delivery reservations. An unresolved create cannot use this action to obtain a
new request key.

The frontend cannot supply a URL, signing secret, arbitrary headers, evidence or
an override granting eligibility. Ordinary view/edit access is not compensation
administrator access. Missing identity/role services fail closed.

## Safety and evidence

**No callback log is not proof of no delivery.** Existing audit writes happen
outside the sender transaction and may be disabled, fail or be retained for a
shorter period. An HTTP 2xx record proves HTTP receipt, not receiver business
completion. Failed/skipped attempts can coexist with retries or side effects.

All execution items require:

- authoritative, formal `flow_runs.status = succeeded`, a completion time, valid
  result JSON and matching environment; no inference from successful node counts;
- an independent trusted generation attestation tied to the exact run+node
  fingerprint. `retry_epoch` is a comparison fence, **not independent historical
  generation proof**; timestamps/content hashes alone do not supply that proof;
- reviewed historical routing and all historical config/URL aliases of the same
  logical receiver. Current callback settings alone do not prove historical routing;
- a documented receiver business-deduplication contract covering **legacy
  requests, compensation, late arrivals, generation semantics and the entire
  historical/retention interval**. A new compensation header alone is insufficient;
- receiver approval of nullable phase when the stored value is null. Phase is
  preserved, never guessed as P5/P7;
- a stable host-owned 32-byte payload-protection key and a non-expired trusted
  evidence provider. No provider/key means review-only, not a permissive fallback.

Existing delivered evidence for any alias of the logical receiver is skipped.
Other attempts require review. Configuration versions and secret rotation do not
create new delivery identities. There is no force-resend switch. Administrator
confirmation or an "I accept duplicates" checkbox cannot replace receiver proof.

### Trusted host integration

`createClawWebBootstrap({ callbackCompensation })` accepts a server-only host:

- `principal(request)` supplies a verified actor and independently resolved admin
  role. Never pass raw `X-User-Id`, decoded-but-unverified JWTs or `req.isAdmin`;
- `trustedOrigins` is an exact HTTP(S) origin allowlist. Mutations require an
  approved Origin, JSON and `X-Callback-Compensation-CSRF: 1`;
- `evidence.resolve(EvidenceQuery)` returns `CompensationEvidence` or null;
- `protectionKey` is a stable 32-byte secret, separate from receiver signing keys.

The Service API and provider contracts are in
[`types.ts`](../server/services/callback-compensation/types.ts). The
[`reviewedCompensationEvidence`](../../../app/clawweb/server/callback-compensation-evidence.ts)
composition helper validates a version-1 server-only manifest with an exact
`environment` and `entries: CompensationEvidence[]`. It rejects ambiguous alias
ownership, unknown fields and cross-environment approvals, then copies values so
callers cannot mutate the authorization. References must identify independently
reviewed evidence; this helper does not discover or establish receiver guarantees.
Never generate approval entries merely by copying current database rows.

A host without a verified principal exposes no administrative data. A host with
verified admins but no delivery proof supports scan/review, not execution. The
normal ClawMind sender is unchanged and does not share this worker's DB claim;
receiver deduplication therefore remains an indispensable boundary.

## HTTP Service API

All routes are relative to `/api/workflows/:workflowId/callback-compensations`.
Every route rechecks administrator identity; preview and batch scope includes
server environment, workflow and actor. Responses are `Cache-Control: no-store`.

| Method/path | Input | Result |
| --- | --- | --- |
| GET `/capabilities` | none | safe receiver labels/origins and execution blockers; no secrets |
| POST `/preview` | `configId`, ISO `from`/`to` or `flowIds` | immutable preview and redacted structure |
| POST `/` | `previewId`, selected `itemIds`, `reason`, `requestKey` | 202 and persisted batch |
| GET `/:batchId` | none | current per-item results |
| POST `/:batchId/stop` | `{}` | idempotent stop of queued items |

Time ranges require explicit timezone offsets and are at most 31 days. A reason
is 10–500 trimmed characters. A request key is reused after an uncertain create
response. Reusing it with different selection/reason is a conflict, not permission
to create another delivery. Another workflow, environment or actor cannot use a
preview/batch ID as a bearer token. Database/query failures are errors, not empty
"no prior delivery" results. Exact exception/transport bodies are never returned.

## Payload and protocol compatibility

The body remains `{ workflow_id, flow_id, status: "succeeded", ext_info }`.
`ext_info.flow_runs` and `node_executions` use an explicit projection, not `SELECT
*` forwarding. JSON columns remain JSON **strings**; credentials are null and
`output_json` is omitted when `include_node_output` is false. Stored lineage IDs
are used directly. This reconstructs a compatible notification from a confirmed
snapshot; it is not a byte-for-byte recovery of a historical original POST.

Preview returns only a redacted structural representation, **not the wire body**.
Frozen full bodies and evidence are authenticated-encrypted (AES-256-GCM). A
confirmed body is never silently rebuilt from newer run/config data. Every
actual POST recomputes the existing millisecond timestamp/HMAC-SHA256 signature
of `timestamp + "." + rawBody`, using the current matching server-side secret;
`X-Callback-Config-Id` remains the string config identifier. Changing the config,
secret, state, generation or snapshot before final database authorization
invalidates delivery. The final item/lease CAS also compares the last verified
run/config values, complete node set and trial/history predicates; text checks
are byte-sensitive, not dependent on database collation or second-resolution
modification timestamps. That authorization is not a transaction with the
external receiver or a lock on future engine writes; receiver business
idempotency remains required.

## Persistence, concurrency and restart

Migration **v159** adds preview, batch and item tables. SHA-256 delivery identities
cover environment/workflow/run/trusted generation/event/logical receiver, not
configuration versions. Batch request keys and delivery identities have unique
constraints. Queued items enter `sending` through an atomic compare-and-set;
multiple workers cannot POST the same compensation item. There is no network
retry. Final authority predicates use a balanced conjunction without truncating
the node set; a snapshot exceeding the conservative database bind budget stays
review-only before a batch can reserve its delivery identity. The worker runs
at low concurrency and freezes the confirmed encrypted
body, config fingerprint and reviewed evidence before sending.

- `received`: HTTP 2xx durably recorded; not a business-success assertion.
- `unknown`: timeout, non-2xx, crash or expired sending lease; never auto-resend.
- `skipped`: stopped before sending or a changed/invalid precondition.
- `failed`: a definite local failure before HTTP submission, not proof the
  receiver rejected a previously submitted notification.
- `auditPending`: result was saved but legacy audit append needs repair; repair
  only the audit, never POST again.

Stop cannot cancel an already-sent request. Shutdown drains work before closing
the DB. Queued work survives restarts; interrupted sending becomes unknown, not
newly eligible. Keep the protection key available while any frozen records need
recovery; do not rotate it without a data-key migration. Never purge unresolved
unknown items or their delivery identities to regain eligibility. Retention/key
management and receiver contract review remain deployment responsibilities.

## Rollout and validation

Managed databases must provision
[`callback-compensation-v159.mysql.sql`](../../../../scripts/sql/callback-compensation-v159.mysql.sql)
through normal DDL review before enabling this feature. Runtime database failures
remain visible as unavailable; no production DDL or delivery is performed by
opening the UI. SQLite local migrations use the same canonical schema.

Verify payload/signature compatibility, same-receiver alias suppression, missing
proof, null phase, trials, overflow, configuration/run drift, duplicate requests,
concurrent claims, restart/unknown handling, audit-only repair, stop, CSRF,
unverified identities and cross-scope access. Frontend tests must additionally
cover workflow/identity switches, uncertain create responses and reopening the
same batch. Passing local tests does not establish production receiver guarantees
or prove a deployed version. Public and enterprise host integration must be
reviewed and deployed as the correct pair; no engine release is required for
this compensation service itself.

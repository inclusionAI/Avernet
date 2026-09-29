# TC single-chat resource withdrawal — v1 draft

Status: **TC implemented, default-paused; ECB wire contract NOT yet frozen**
(2026-09-28). This document defines TC's consumer expectation, not evidence that
ECB implements it. Enabling delivery requires the release gates in the runbook.

## Ownership and compatibility

- TC owns one uploaded resource (`res_id`), its terminal `deleted` state and the
  durable notification. ECB owns `res_id -> business_file`, authorization,
  tombstones and shared-content reference accounting.
- Only `personal_bot_chat`, `friend_bot_chat`, `openapi_session` deletions emit an
  event. Group/unknown scopes emit nothing. The existing public/legacy delete
  response and authorization rules are unchanged.
- This is NOT message retraction, file-system removal or shared-content GC.
  Deleting A must never withdraw another upload B that references the same data.
- There is no automatic historical backfill and no new callback API. Existing
  materialization transitions continue to reject a deleted resource.

## Service API: TC → ECB (draft)

`POST /api/v1/knowledge/integrations/tc/files/withdraw-by-resource`

Configured origin, HTTPS except explicit loopback HTTP for local verification.
Authorization is `Bearer <service credential>` resolved through SecretResolver;
never place a credential in configuration YAML or a URL. Redirects are errors,
not alternative authenticated destinations.

```json
{"event_id":"tc.resource.withdrawn:sr_example","res_id":"sr_example"}
```

`res_id` is the immutable upload-resource ID, not a content hash. `event_id` is
stable for its lifetime. The body deliberately has only these two fields; v1 is
the document/plugin version, not an invented wire `schema_version` field.

The credential MUST identify an authorized integration/tenant. ECB MUST resolve
and constrain the resource within that scope; the caller's resource ID alone is
not authority. TC retains the resource's tenant in its outbox and each deployment
worker claims only its explicitly configured tenant. A credential and tenant
mismatch must fail closed, not fall back to a global lookup. Multi-tenant workers
with per-tenant credentials are outside this change.

A valid 2xx receipt is:

```json
{"event_id":"tc.resource.withdrawn:sr_example","accepted":true,"status":"pending"}
```

`event_id` must match exactly; `accepted` must be JSON boolean `true`, not `1` or
a truthy string. `status` must be `pending` or `applied`. Additional response
fields are ignored. Empty, non-JSON or mismatched 2xx responses are failures.

**Accepted means durable intake, not completed withdrawal.** Before responding,
ECB must persist the event or an equivalent tombstone, including when upload
mapping does not yet exist. Duplicate delivery (including lost responses) must
return a valid receipt without repeating business effects. Later READY/upload
notifications must not reactivate the withdrawn reference. TC records receipt
acceptance only; it does not infer when knowledge lists/search/summaries stop
exposing the reference. Real ECB conformance remains a release dependency.

## Plugin API: core → publisher

`ResourceWithdrawalPublisherPlugin.publish(ResourceWithdrawalEvent)` returns
`WithdrawalReceipt`, or raises `WithdrawalDeliveryError(code, retryable=...)`.
The synchronous operation runs off the request/event-loop path. Only stable,
non-sensitive error codes cross into core logging; no credentials, response
bodies, session keys or file contents belong in the event or logs. The real
adapter uses the existing HttpClient with a finite transport timeout (not a
wall-clock guarantee across all response chunks).

Only TEST/CORP_TEST select `LocalResourceWithdrawalPublisher` explicitly.
COMMUNITY/SINGLEBOX/CORP use authenticated HTTP even when deployed locally.
Disabled delivery does not resolve a credential or manufacture a successful ack.
Enabling with absent/invalid configuration or secret fails startup.

## Durable state machine

| State / transition | Meaning |
|---|---|
| resource → deleted + outbox → pending | One real DB transaction; any write/commit failure rolls back both |
| pending → processing | Tenant-scoped CAS claim, DB time, unique lease token; increments total and current-budget attempts |
| expired processing → processing | Crash recovery, new token; stale worker cannot save any outcome |
| processing → accepted | Matching durable intake receipt; accepted is terminal |
| processing → pending | Network/timeout/408/429/5xx; bounded exponential retry delay |
| processing → blocked | Other HTTP statuses (including redirects, 401/403/409), invalid receipt, unexpected plugin error, or exhausted retry budget |
| blocked → pending | Explicit operator replay, correct tenant/event/expected total attempts, actor + reason |

Completion is fenced by tenant, processing state, token **and unexpired lease**.
DB save errors propagate; they cannot be converted to successful delivery. The
lease survives process failure. A successful remote intake followed by a failed
local save causes safe duplicate delivery with the same event ID. Duplicate
user deletion never rewrites accepted/blocked events.

Default retry delay is `min(3600, 10 * 2^(retry_count-1))` seconds and the retry
budget is 20 claims, including crash recovery claims. Replay resets only the
current retry budget, not total attempts or original creation time. The latest
replay actor/reason is retained; operators must additionally retain their change
record for a historical audit trail. No automatic purge is introduced.

## Evidence and rollout

TC unit, consumer-contract and real-router tests are listed in
[validation.md](../specs/2026-09-28-tc-session-resource-withdrawal/validation.md).
Local publisher conformance is not ECB conformance. See
[runbook.md](../specs/2026-09-28-tc-session-resource-withdrawal/runbook.md) for
migration, configuration, recovery and explicit production enablement gates.

# Resolved approval delivery and durable consumption

## Scope and compatibility

This is the ClawWeb internal **Service API** consumed by ClawMind API-mode
pollers. It does not change approval policy, the runtime's callback ownership
checks, workflow execution, or the meaning of a rejected decision (`approved: false`).
The internal OCB archive package re-exports the public workflow repository and
routes, so it must build against this public change.

Existing API pollers can receive safe terminal-run filtering and retain legacy
restart/recovery without changing their requests. This does **not** give current
clients that send `{}` durable-success suppression: their acknowledgements remain
non-consuming. Durable suppression requires a client explicitly implementing the
success-only `consumed-v1` semantics below. This PR provides neither a ClawMind
source change nor a released opt-in client, and establishes no minimum client
version. Frontend approval behavior is unchanged.

This contract does not change ClawMind's standalone direct-database poller.
A process-local suppression set is an optimization, not a durable delivery
acknowledgement or evidence that a continuation succeeded.

## Host authentication

The public host protects **every** `/api/approval-cards` operation, including
resolved reads, card creation, and processed acknowledgements, with the same
`createInternalEngineAuth` guard used by its internal engine run APIs. Browser
identity headers, cookies, admin roles, and forwarded IPs do not authorize these
operations. The separate user-facing `/api/approval` routes retain their existing
approval/session identity policy; an engine signature does not supply a browser
approver identity.

Reuse the existing signature configuration and wire protocol:

- The configured public key is base64 SPKI-DER **Ed25519**. Clients send epoch
  milliseconds in `X-Timestamp` and a base64 signature in `X-Signature` over
  `${timestamp}.${JSON.stringify(body)}`. GET/HEAD/DELETE and bodyless requests use
  an empty body string. The accepted clock window is five minutes in either
  direction. A `{}` body and a `{ "consumed": true }` body have different signed
  payloads: the signature must bind the actual opt-in body, not a legacy body.
- With a configured key, missing, malformed, forged, expired, or body-mismatched
  signatures return HTTP 401 before reaching the approval repository or cache.
  Authentication denial must neither persist an ACK nor invalidate cached polls.
- Missing key configuration fails closed with HTTP 503 in `pre`/`prod`, even on
  loopback. An invalid or non-Ed25519 configured key fails closed with HTTP 503 in
  every environment; it never enables unsigned fallback. Only explicit `dev`
  with no configured key retains the existing unsigned socket-loopback policy.
- The existing signature protocol does not bind the method/path or provide
  one-time nonces. Use a trusted transport and protect the runtime signing key.
  This guard authenticates the trusted engine, not an individual Bot; `botId`
  remains a polling filter, not proof of Bot identity or ACK ownership.

The OCB internal host already mounts `/api/internal` behind its signature
middleware, including `/api/internal/approval-cards`. Existing ClawMind API-mode
requests sign the same timestamp/body protocol. No new token or configuration
format is introduced; the opt-in changes the body that must be signed, not the
signature scheme.

**Internal-host rollout prerequisite:** the OCB legacy signature middleware
allows requests through when no public key is configured. Before enabling durable
ACKs on that host, verify that its engine verification key is configured and that
unsigned approval requests are rejected. Do not deploy without this evidence.
This patch does not change that middleware or verify production configuration.

## Resolved query

`GET /approval-cards/resolved` retains the existing response envelope and optional
`botId`/`limit` parameters. With or without `botId`, a card is eligible only when:

- its decision is `approved` or `rejected`;
- its original delivery mode is `card-web`;
- `processed_at` is null; and
- its associated run is not in a confirmed terminal state: `succeeded`, `failed`,
  `cancelled`, `canceled`, or `completed`.

Missing run metadata, a null run status, and unknown or active statuses (including
`waiting`, `running`, and `blocked`) remain eligible. With `botId`, match the exact
Bot ID or its literal `<botId>:` prefix; `%`, `_`, and `!` in a Bot ID are escaped,
not interpreted as SQL wildcards. Null or empty origin metadata retains historical
compatibility. Omitting `botId` preserves cross-Bot polling, not terminal-history
or already-consumed delivery. A missing local owner, a temporary readiness
failure, or a failed callback is **not** evidence of consumption and must not
set `processed_at`.

Terminal filtering is non-destructive: it does not delete cards, change their
decisions or delivery modes, or mark them consumed. If an unconsumed run is
explicitly reopened and returns to an active state, its card becomes eligible
again. Already-consumed card IDs stay consumed; a new approval uses a new card.

## Acknowledgement

`POST /approval-cards/:id/processed` distinguishes legacy acknowledgement from
explicit durable consumption by the JSON body, not by the response shape:

| Request body | Meaning on the new server | Successful response |
| --- | --- | --- |
| No body | Legacy eligibility check only; no writes or cache invalidation. | HTTP 200 `{ "success": true }` |
| `{}` | Same non-consuming legacy check. | HTTP 200 `{ "success": true }` |
| `{ "legacy": true }` (any object without `consumed`) | Same non-consuming legacy check; extra fields do not opt in. | HTTP 200 `{ "success": true }` |
| `{ "consumed": true }` | Explicit `consumed-v1` durable consumption. Only boolean `true` opts in. | HTTP 200 `{ "success": true }` after successful persistence/verification. |
| `null`, `[]`, `true`, `1`, or `"consumed"` | Malformed non-object body. | HTTP 400 |
| `{ "consumed": false }`, `{ "consumed": null }`, `{ "consumed": 1 }`, `{ "consumed": "true" }`, or another supplied value other than boolean `true` | Invalid opt-in; never downgrade to legacy or coerce truthiness. | HTTP 400 |

For either valid mode:

- The ID must be a complete positive safe integer. Malformed IDs return HTTP 400.
- Only existing `approved` or `rejected` cards with `card-web` delivery are
  eligible, including historical terminal-run cards. Missing, pending,
  unknown-decision, or other-delivery cards return HTTP 409 without writing an
  acknowledgement or invalidating cached polls.
- Authentication denial, malformed requests, and eligibility/read/write or
  verification failures must not invalidate cached polls. Database errors return
  failure, not a success inferred from receiving or attempting an ACK.

### Legacy compatibility is non-consuming

Bodyless requests and objects without `consumed` perform only an eligibility
read. They do not update `processed_at`, `gmt_modified`, or any approval/audit
field, and do not invalidate the local resolved-query cache. The core
`markProcessed(cardId, consumed = false)` likewise defaults to this non-consuming
behavior; only an explicit `true` requests the durable write.

Current legacy API pollers can send `{}` even after three failed callbacks.
Treating that body as proof of success would permanently hide a waiting card
and prevent retry after a restart. The non-consuming response preserves its
availability to a fresh poller when the run is still eligible, even if the old
poller suppressed it locally. A legacy ACK after a prior durable ACK cannot clear
or reset the first `processed_at`, undo consumption, or make the card eligible
again. The unchanged HTTP 200 `{ "success": true }` for legacy requests proves
only eligibility, **not durable consumption**.

### Explicit success-only `consumed-v1`

A client may send and sign `{ "consumed": true }` only after the owning runtime
has successfully consumed the callback, all required hooks have succeeded, and
the durable continuation outcome has been verified. It must not opt in on a
failure/retry limit (including three failed callbacks), a missing owner, a
readiness failure, or an unverified continuation. The server trusts this explicit
assertion; it cannot prove those client-side conditions from the ACK alone.

The first successful explicit ACK persists `processed_at` as Unix seconds.
Repeated explicit ACKs succeed without changing that first timestamp, including
when a MySQL update reports zero changed rows. Approval status, reviewers,
comment, resolution timestamp, and delivery mode remain available for audit and
the approval page. Consumption is visible to fresh repository instances and
restarted pollers through the database, rather than just to the process that
handled the card.

Only successful explicit consumption invalidates all local resolved-query cache
keys. Other server instances can serve an already-cached result for at most the
existing 30-second cache TTL. A stale in-flight read must not repopulate a cache
invalidated by an explicit ACK. Pollers still must check ownership/readiness on
redelivery; this change does not add a distributed callback claim or an
exactly-once side effect guarantee.

## Schema and rollout

Migration **159** adds nullable `approval_cards.processed_at` and a polling index.
Existing cards default to null. Do not backfill consumption from `gmt_modified`,
`resolved_at`, age, or missing-owner logs: none proves that the callback was
consumed. Existing terminal runs are excluded by the query even when their old
cards have no consumption timestamp.

The deployment order is **schema → all serving servers → verified opt-in
client**. Compatibility is not a durable-consumption capability handshake:

| Client | Server/schema deployment | Guarantee |
| --- | --- | --- |
| Legacy bodyless/`{}` client | Old server | Existing behavior only; this change's filtering and durable guarantees are unavailable. |
| Legacy bodyless/`{}` client | New server with migration 159 | Safe terminal-run filtering and legacy recovery without a client upgrade; no durable-success suppression from its legacy ACKs. |
| New `consumed-v1` client | Old server, or a mixed old/new server fleet | An old server may return HTTP 200 `{ "success": true }` but ignore `consumed`. Do not claim durable consumption until the new server implementation is installed on every serving replica. |
| Verified success-only `consumed-v1` client | All servers new, migration 159 verified, host auth verified | Durable consumption and restart suppression after successful explicit ACK persistence; cache/redelivery limits still apply. |
| Any client | New server without the required schema | Unsupported deployment; fail rather than silently fall back to a non-durable ACK. |

Local SQLite and databases permitting normal migrations apply migration 159 at
startup. Managed MySQL/OceanBase runtime accounts that deny DDL do **not** apply
it automatically. For these environments:

1. Inspect the existing column and index definitions. Apply the additive DDL in
   [approval-consumption-ob-ddl.sql](approval-consumption-ob-ddl.sql) through the
   approved schema-change process, before deploying the server code. If partially
   applied, apply only missing definitions. Do not write or advance unrelated
   migration-ledger entries.
2. Build and deploy the public workflow/shared packages with the OCB host. All
   serving replicas must run the new query/ACK implementation; old replicas can
   still return historical cards or acknowledge without the new timestamp. Verify
   the host authentication prerequisites above before enabling durable ACKs.
3. Verify the column/index and check that a known completed historical run is
   absent from resolved responses with and without `botId` after cache expiry.
   Verify that signed legacy `{}` leaves a waiting card unconsumed and available
   to a fresh poller after callback failure/restart.
4. Only then enable a client whose success-only `consumed-v1` behavior has been
   implemented and verified. Using controlled non-production approvals, verify
   approve and reject, callback and required-hook success, durable continuation,
   the first consumption timestamp, suppression after a new poller lifetime,
   persistence-error retries, and continued delivery when callbacks fail or the
   owner is temporarily unavailable. Do not mutate real approvals for a smoke
   test. This PR does not supply or certify that client.

Rollback the application independently and retain the additive nullable column
and index. Dropping them while a new instance is serving would break queries
and acknowledgements. Rolling back to the old query/ACK implementation restores
the original repeat-polling defect and removes the durable opt-in guarantee; it
is not a recovery operation for a run. Do not infer persistence from a successful
HTTP response during or after rollback.

## Validation boundaries

Regression coverage uses real SQLite repositories and HTTP routes, plus
MySQL/OceanBase dialect and managed-migration tests. It covers historical
terminal cards, durable/idempotent explicit consumption across independent
file-backed database connections, closed/reopened repositories, and child-process
reads; Bot isolation; legacy queries and non-consuming ACKs; unknown-owner
retention; audit preservation; cache invalidation; and acknowledgement failures.
Public-bootstrap tests bind signatures to the actual legacy/opt-in body and
retain browser/session authorization behavior. These tests are not proof that a
managed production schema change or deployment has happened, or that a released
ClawMind client implements success-only consumption.

Implementation and regression evidence:

- [Repository query and durable ACK](../server/repositories/approval-card-repository.ts).
- [HTTP validation and cache invalidation](../server/routes/internal/approval-cards.ts).
- [Repository routing/consumption regressions](../server/repositories/__tests__/approval-card-routing.test.ts).
- [File-backed persistence and process-boundary regressions](../server/repositories/__tests__/approval-card-persistence.test.ts).
- [HTTP and cache-race regressions](../server/routes/__tests__/internal-approval-cards.test.ts).
- [Legacy failed-callback/restart integration regressions](../server/routes/__tests__/approval-card-legacy-restart.test.ts).
- [Public-host engine guard](../../../app/clawweb/server/internal-engine-auth.ts) and
  [production-host authentication regressions](../../../app/clawweb/server/__tests__/public-approval-consumption-auth.test.ts).
- [Migration 159](../../../shared/server/schema-approval-consumption.ts).

# Resolved approval delivery and durable consumption

## Scope and compatibility

This is the ClawWeb internal **Service API** consumed by ClawMind API-mode
pollers. It does not change approval policy, the runtime's callback ownership
checks, workflow execution, or the meaning of a rejected decision (`approved: false`). The
internal OCB archive package re-exports the public workflow repository and
routes, so it must build against this public change. No frontend or ClawMind
upgrade is required for existing API-mode clients.

This contract does not change ClawMind's standalone direct-database poller.
A process-local suppression set is an optimization, not the durable delivery
acknowledgement described here.

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
  direction.
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
requests sign the same timestamp/body protocol, so this public-host boundary fix
requires no internal-host, ClawMind, token, or configuration-format change.

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

After safely consuming the callback and durably establishing the continuation
outcome, the owning runtime calls `POST /approval-cards/:id/processed` as before.

- A successful response still has the shape `{ "success": true }`.
- The ID must be a complete positive safe integer. Malformed IDs are rejected.
- Only existing `approved` or `rejected` cards with `card-web` delivery can be
  acknowledged, including historical terminal-run cards. Missing, pending,
  unknown-decision, or other-delivery cards return HTTP 409 without writing an
  acknowledgement or invalidating cached polls. Invalid IDs return HTTP 400.
- The first successful acknowledgement persists `processed_at` as Unix seconds.
  Repeated acknowledgement succeeds without changing that first timestamp,
  including when a MySQL update reports zero changed rows.
- A write or verification error returns a failure response. No successful
  acknowledgement is inferred from a request being received or attempted.
- Approval status, reviewers, comment, resolution timestamp, and delivery mode
  remain available for audit and the approval page.

Consumption is visible to fresh repository instances and restarted pollers
through the database, rather than just to the process that handled the card.
Successful acknowledgement invalidates all local resolved-query cache keys;
failed persistence does not pretend the card has disappeared. Other server
instances can serve an already-cached result for at most the existing 30-second
cache TTL. A stale in-flight read must not repopulate a cache invalidated by an
acknowledgement. Pollers still must check ownership/readiness on redelivery;
this change does not add a distributed callback claim or an exactly-once side
effect guarantee.

## Schema and rollout

Migration **159** adds nullable `approval_cards.processed_at` and a polling index.
Existing cards default to null. Do not backfill consumption from `gmt_modified`,
`resolved_at`, age, or missing-owner logs: none proves that the callback was
consumed. Existing terminal runs are excluded by the query even when their old
cards have no consumption timestamp.

Local SQLite and databases permitting normal migrations apply migration 159 at
startup. Managed MySQL/OceanBase runtime accounts that deny DDL do **not** apply
it automatically. For these environments:

1. Inspect the existing column and index definitions. Apply the additive DDL in
   [approval-consumption-ob-ddl.sql](approval-consumption-ob-ddl.sql) through the approved schema-change process,
   before deploying the server code. If partially applied, apply only missing
   definitions. Do not write or advance unrelated migration-ledger entries.
2. Build and deploy the public workflow/shared packages with the OCB host. All
   serving replicas must run the new query/ACK implementation before claiming
   cross-instance suppression; old replicas in a mixed rollout can still return
   historical cards or acknowledge without the new timestamp. Do not compensate
   for an unmigrated database by silently falling back to the old non-durable
   acknowledgement.
3. Verify the column/index and check that a known completed historical run is
   absent from resolved responses with and without `botId` after cache expiry.
4. Using a controlled non-production approval, verify approve and reject, the
   first consumption timestamp, redelivery after a new poller lifetime,
   persistence-error retries, and continued delivery of a waiting card whose
   owner is temporarily unavailable. Do not mutate real approvals for a smoke
   test.

Rollback the application independently and retain the additive nullable column
and index. Dropping them while a new instance is serving would break queries
and acknowledgements. Rolling back to the old query/ACK implementation restores
the original repeat-polling defect; it is not a recovery operation for a run.

## Validation boundaries

Regression coverage uses real SQLite repositories and HTTP routes, plus
MySQL/OceanBase dialect and managed-migration tests. It covers historical
terminal cards, durable/idempotent consumption across independent file-backed
database connections, closed/reopened repositories, and child-process reads; Bot
isolation; legacy queries; unknown-owner retention; audit preservation; cache
invalidation; and acknowledgement failures. These tests are not proof that a managed
production schema change or deployment has happened.


Implementation and regression evidence:

- [Repository query and durable ACK](../server/repositories/approval-card-repository.ts).
- [HTTP validation and cache invalidation](../server/routes/internal/approval-cards.ts).
- [Repository routing/consumption regressions](../server/repositories/__tests__/approval-card-routing.test.ts).
- [File-backed persistence and process-boundary regressions](../server/repositories/__tests__/approval-card-persistence.test.ts).
- [HTTP and cache-race regressions](../server/routes/__tests__/internal-approval-cards.test.ts).
- [Public-host engine guard](../../../app/clawweb/server/internal-engine-auth.ts) and
  [production-host authentication regressions](../../../app/clawweb/server/__tests__/public-approval-consumption-auth.test.ts).
- [Migration 159](../../../shared/server/schema-approval-consumption.ts).

# bcs-friend Context

## Provides

- Friend relationship and friend-request service implementation for BCS.
- Application-facing orchestration around friend lifecycle operations.
- Business rules for friend state transitions and visibility-related friend interactions.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- `plugin-api/*` contracts when persistence or cache support is needed.
- Pure utility crates for IDs, logging, and serialization.

## Allowed dependencies

- `service-api/*`
- `plugin-api/*`
- Utility crates such as `uuid`, `serde`, and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- `external-clients/*` crates not listed in `Allowed dependencies` above

## Configuration

- Bootstrap injects stores, ports, and policy knobs explicitly.
- This crate must not choose concrete plugins or inspect env directly.

## Runtime ownership

The crate owns friendship rules and request lifecycle decisions. It does not own transport handling or implementation selection.

The HTTP compatibility facade for friend connections has moved to
`bcs-edge-permission::EdgeConnectFriendService`; this crate's legacy
`Friend`/`FriendRequestCore` remain for transitional tests; friendship core
and writeback remain for collaboration consumers pending their retirement.
Normal in-memory and durable bootstraps no longer construct the legacy
request service or use a legacy friend-name list delegate.

## Connect compatibility and failure policy

The connect facade reads both Bot and Human friendship membership only from
active default-profile edge grants. It retains the legacy list's self-only
access policy, Bot-only DTOs, registry names/presence and exclusion of missing
or deleted peers. Request reads/decisions use only `permission_requests`;
old request IDs are intentionally invalid and no old-table fallback is used.

`FriendshipWritebackService` still mirrors Bot↔Bot and Human→Bot decisions,
including revocation, for remaining collaboration consumers, not for listing.
Human relation failures propagate to the connect caller; repeated create/approve/remove
repairs partial mirror writes only while the authoritative friend grant remains
active; replaying a historical approval must not resurrect a revoked relation.
Human cleanup is not skipped merely because
an earlier failed attempt already removed the friendship repo row. Bot↔Bot
retains the existing best-effort relation-mirror policy. Ownership edges are
not changed by friend cleanup.

Friend decisions must resolve the persisted Connect request before authorizing
the receiver; adapter-supplied receiver hints never replace this check.
Permission-profile, Rules and Revoke requests are not legacy friend requests.
See the [no-history cutover prerequisites](../../../migrations/README.md#legacy-friend-request-cutover).

## Tests

- `cargo test --package bcs-friend --manifest-path apps/bcs/Cargo.toml`
- `cargo check --package bcs-friend --all-targets --manifest-path apps/bcs/Cargo.toml`

## Writeback access cost

On the SQL path, a successful Human add/retry performs one idempotent friendship
INSERT and two directed relation INSERTs (at most three rows). Removal performs
one normalized friendship DELETE and one non-owner relation DELETE (at most
three rows). Failures stop at the failed statement and propagate; there is no
background retry loop or cross-store transaction held over notification delivery.
Replaying an already-approved Human request additionally checks the current
friend grant (two bounded default-profile reads and, for the supported Human→Bot
direction, at most one bounded edge read) before repairing the legacy mirror.
Bot↔Bot's existing coexistence policy is unchanged. Real store integration and
retry tests verify visibility and failure behavior; production load/lock duration
is not established by these small fixtures.

## Edge-owned compatibility list access cost

The legacy response is unpaged, but membership is read through the existing
fallible edge-page query in batches of 100. With N Bot peers in the initial
count there are at most max(1, ceil(N/100)) edge queries, each returning at most
100 peer rows (or one empty-count row). The initial count bounds the loop even
when new friends arrive concurrently. Each page recomputes the existing count;
this bounds transferred rows, not DB scan cost or total response size. No
cross-page snapshot is promised during concurrent friendship mutations.

Registry enrichment uses `try_get_many` on at most 100 IDs (warm-cache hits and
chunked cold SQL reads), not one metadata query per peer. Online lifecycle peers
use one strict batch runtime observation per page: the production BotCore reads
the local WS set, bounded Provider-binding chunks (128 IDs), Provider and
credential batches, and A2A availability. There is no per-peer delivery lookup.
Storage failures fail the entire response, including on later pages; there is
no successful partial list, new cache or retry loop. Existing caches/invalidation
remain owned by registry stores. The 103-edge regression asserts two membership
queries returning 100/3 rows, missing-peer filtering, presence and later-page
failure. It is not a production-scale query-plan/load validation.

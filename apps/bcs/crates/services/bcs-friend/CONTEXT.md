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
`Friend`/`FriendRequestCore` remain only for writeback, friend-name listing and
transitional tests, and are deleted together with the legacy retirement.

## Connect compatibility and failure policy

The connect facade keeps legacy friend-name reads: Bots use the friendship
repo and Humans use the relation graph. `FriendshipWritebackService` mirrors
both Bot↔Bot and Human→Bot decisions, including revocation. Human relation
failures propagate to the connect caller; repeated create/approve/remove
repairs partial mirror writes only while the authoritative friend grant remains
active; replaying a historical approval must not resurrect a revoked relation.
Human cleanup is not skipped merely because
an earlier failed attempt already removed the friendship repo row. Bot↔Bot
retains the existing best-effort relation-mirror policy. Ownership edges are
not changed by friend cleanup.

Friend decisions must resolve the persisted Connect request before authorizing
the receiver; adapter-supplied receiver hints never replace this check.
Permission-profile, Rules and Revoke requests are not legacy friend requests.
See the [ID-preserving cutover prerequisites](../../../migrations/README.md#legacy-friend-request-cutover).

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

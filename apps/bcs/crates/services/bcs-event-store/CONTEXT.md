# bcs-event-store Context

## Provides

- Memory and SQL-backed implementations of the Event repository contract.
- Atomic Event append, stream sequence allocation, producer idempotency, and
  Subscription revision snapshotting.
- Fanout/Delivery materialization, fenced leases, strict-lane and causal
  eligibility, immutable Attempt audit, replay/skip, and retention state.
- Store-owned database transaction plan fragments that persistent business
  repositories can compose into their unit of work.
- Group provisioning transaction fragments that activate pending
  Subscriptions and persist ordered creation Events/targets in the owning
  Group Store transaction.
- The application-facing Event Recorder implementation.

## Consumes

- `bcs-service-api` Event recording and repository contracts.
- The injected `bcs-db-api::DbPlugin` SQL extension point.

## Allowed dependencies

- `service-api/*` and `plugin-api/bcs-db-api`
- Pure serialization, hashing, time, async, and error utility crates

## Forbidden dependencies

- `bootstrap/bcs`
- HTTP, WebSocket, or other delivery adapters
- Concrete database plugins in production dependencies
- Business application services and runtime-global configuration

## Configuration

- Bootstrap supplies the selected database plugin, SQL flavor, environment,
  retention policy, and enabled state explicitly.
- This crate does not read environment variables or select providers.

## Runtime ownership

This crate owns Eventing persistence semantics and SQL/table mapping. It does
not own subscription authorization, payload projection, delivery retry policy,
HTTP execution, or business resource transitions.

## Tests

- `cargo test -p bcs-event-store`
- The ignored MySQL conformance test runs when `BCS_TEST_MYSQL_URL` is set.

## Idle claims

The initial fanout/delivery claim UPDATE requests DB transaction early success
when it affects zero rows. Empty claims execute one SQL statement; nonempty
claims retain their full atomic transaction, fencing and recovery semantics.
`src/db.rs` implements the repository contract, with private SQL operations and
row mapping split under `src/db/`, and pure input validation shared with the
memory repository in `src/validation.rs`. No schema changes are required.

## Memory repository layout

`src/memory.rs` owns the in-memory store state, the scope/producer/stream lock
registry, and the runtime constructors and commit helpers. Its private siblings
are split by responsibility rather than mirroring `src/db/`:
`src/memory/repo.rs` holds the single `EventRepoPort` implementation,
`src/memory/fanout.rs` owns state-level append, causation and target creation,
`src/memory/delivery.rs` owns delivery due/eligibility/lane ordering plus the
claim, completion and replay bodies, and `src/memory/retention.rs` owns purge
eligibility and the retention sweep. The port methods whose bodies live in
`memory/delivery.rs` or `memory/retention.rs` are thin delegating shells to
`*_impl` inherent methods; preserve their signature and lock/`.await` order
when editing. Cross-sibling helpers are `pub(super)`; the public surface stays
`MemoryEventStore` and its existing public methods.

## Shared input validation

`src/validation.rs` holds the pure input validators used by both the memory and
SQL repositories. Backend-specific checks stay in their backend: the memory
repository rejects an empty replacement Subscription id/env before the shared
replacement predicate, and parses Event timestamps after the shared empty-field
check. Subscription list limits and retention/skip reasons remain per-backend
inline checks.

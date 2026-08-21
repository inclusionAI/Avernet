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

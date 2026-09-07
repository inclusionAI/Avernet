# bcs-eventing Context

## Provides

- The runtime registry loaded from the authoritative public BCS Event Catalog.
- Transport-neutral validation and lookup for public Event types and registered
  family wildcards.
- Transport-neutral Event Subscription CRUD/test/replay/skip application
  policy, including optimistic revisions and redacted sink endpoints.
- Group scope authorization over injected resource services, including
  server-derived Human/Bot actor attribution and fail-closed unsupported scope
  authority chains.
- Catalog-driven scope/filter matching and immutable metadata/full payload
  projection with sensitive-field removal and UTF-8-safe content limits.
- Lease-based fanout, bounded-concurrency Webhook dispatch, full-jitter retry,
  DLQ, safe retention, and managed worker lifecycle policy.
- Group Event Subscription preparation/recovery and deterministic
  `group.created`/initial `session.created` construction for an owning Group
  repository to finalize atomically.

## Consumes

- The versioned Event Catalog under `src/bcs/api-contracts/events/v1`.
- `bcs-service-api` Event and Subscription contracts.
- Injected Event Repo, Delivery, and instrumentation ports.
- Injected Group, Session, and Bot Registry service contracts used by the
  default scope authorizer; HTTP adapters do not own Subscription policy.
- An injected Group provisioning finalization contract; Eventing builds the
  canonical Event batch but the Group Store owns the cross-resource commit.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `service-api/*`
- Pure parsing, serialization, async, and error utility crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*` and HTTP framework types
- Concrete `plugins/*`, Event Store, or Webhook client implementations
- Direct environment or process-global configuration access

## Configuration

- Bootstrap supplies validated policy and all concrete ports explicitly.
- The Event type registry is compiled from the checked-in public Catalog; this
  crate must not maintain a second hardcoded Event list.

## Runtime ownership

This crate owns Eventing application and resource-authorization policy. It does
not own SQL/table mapping, HTTP protocol behavior, resource persistence,
HTTP endpoint policy, or business resource state transitions.

## Tests

- `cargo test --package bcs-eventing --manifest-path src/bcs/Cargo.toml`
- `cargo test -p bcs-eventing --test conformance_event_subscription_service`
- `cargo test -p bcs-eventing --test subscription_service`
- `cargo test -p bcs-eventing --test dispatcher`
- `cargo test -p bcs-eventing --test lifecycle`
- `cargo test -p bcs-eventing --test conformance_eventing_lifecycle`
- `uv run --with pytest --with pyyaml --with jsonschema pytest src/bcs/tests/event_contract -q`

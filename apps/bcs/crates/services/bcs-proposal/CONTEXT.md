# bcs-proposal Context

## Provides

- In-memory `ProposalCoreService` implementation for temporary group-chat proposal storage.
- `GroupProposalUseCases` application wrapper for proposal creation, preview, and confirmation.
- `ProposalBuilder` helpers used by tests and legacy bootstrap exports.

## Consumes

- `bcs-service-api` proposal, group, registry, friend, and context-injection contracts.
- Explicit proposal URL, bot chat URL, and group limit settings supplied by bootstrap.
- Runtime clock and UUID generation for proposal tokens and expiry behavior.

## Allowed dependencies

- `service-api/*`
- Async runtime, serialization, logging, UUID, and error helper crates
- In-memory synchronization primitives for the current proposal store

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Concrete service stores from `services/*`
- Direct database, cache, config file, or env access
- HTTP request parsing or transport-specific confirmation behavior

## Configuration

- Bootstrap supplies proposal URLs and group limit knobs through `GroupProposalUseCasesConfig`.
- This crate must not infer deployment mode, public hostnames, or persistence backend selection.

## Runtime ownership

The crate owns proposal workflow orchestration and temporary proposal state. It does not own HTTP routing, durable group storage, bot registry persistence, friendship persistence, or context delivery transport.

## Phase 3 status

Promoted from `mix/` in Phase 3. Durable repository abstraction is intentionally deferred to `bcs-proposal-store` in Phase 4.

## Tests

- `cargo test --package bcs-proposal --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-proposal --all-targets --manifest-path src/bcs/Cargo.toml`

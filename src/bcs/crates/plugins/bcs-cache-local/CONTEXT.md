# bcs-cache-local Context

## Provides

- In-memory local implementation of `CachePlugin`.
- Dependency-light cache behavior for local development and contract tests.
- A concrete cache adapter without remote middleware dependencies.

## Consumes

- `bcs-cache-api` contract types.
- In-process memory and async runtime primitives.

## Allowed dependencies

- `plugin-api/bcs-cache-api`
- Utility crates needed for in-memory storage and timing

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Business cache key or invalidation policy

## Configuration

- Bootstrap or tests select this implementation explicitly.
- Any capacity or TTL tuning must arrive through constructors, not env lookups.

## Runtime ownership

The crate owns local cache storage mechanics only. It does not own business cache semantics.

## Tests

- `cargo test --package bcs-cache-local --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-cache-local --all-targets --manifest-path src/bcs/Cargo.toml`

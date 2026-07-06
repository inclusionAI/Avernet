# bcs-cache-api Context

## Provides

- Infrastructure cache plugin contract for BCS services.
- Shared cache error types and async trait boundary.
- A minimal capability surface for local and remote cache implementations.

## Consumes

- Async trait and error helper crates only.

## Allowed dependencies

- Contract helper crates such as `async-trait` and `thiserror`
- Test-only reuse through `bcs-test-support`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugins/*`
- Internal SDKs or external middleware clients

## Configuration

- This crate defines the cache capability contract only.
- Concrete cache config is supplied to implementations by bootstrap.

## Runtime ownership

The crate owns cache capability semantics. It does not own business cache keys, invalidation policy, or implementation selection.

## Tests

- `cargo test --package bcs-cache-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-cache-api --all-targets --manifest-path src/bcs/Cargo.toml`

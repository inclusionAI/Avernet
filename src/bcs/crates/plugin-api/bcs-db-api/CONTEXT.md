# bcs-db-api Context

## Provides

- Infrastructure database plugin contract for BCS services.
- Shared DB error types and async trait boundary.
- A driver-level SQL execution surface for local and remote implementations.

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

- This crate defines the DB capability contract only.
- Concrete datasource config is supplied to implementations by bootstrap.

## Runtime ownership

The crate owns DB capability semantics. It does not own service persistence policy, SQL dialect adaptation above the contract, or implementation selection.

## Tests

- `cargo test --package bcs-db-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-api --all-targets --manifest-path src/bcs/Cargo.toml`

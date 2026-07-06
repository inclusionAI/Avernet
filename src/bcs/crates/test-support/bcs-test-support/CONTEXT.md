# bcs-test-support Context

## Provides

- Shared contract-test harnesses for BCS service and plugin boundaries.
- Reusable fixtures and helpers for local conformance testing.
- A single place to host boundary-level test utilities reused across crates.

## Consumes

- `bcs-service-api`, `bcs-cache-api`, and `bcs-db-api` contract crates.
- Test runtime crates such as `tokio`.

## Allowed dependencies

- `service-api/*`
- `plugin-api/*`
- Test-only helper crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*` concrete runtime crates
- `plugins/*` concrete implementations in shared harness code
- `external-clients/*`

## Configuration

- Test setup is provided by each consuming test crate.
- This crate must not read production env or embed production bootstrap logic.

## Runtime ownership

The crate owns shared test fixtures and contract harnesses only. It does not participate in production runtime wiring.

## Tests

- `cargo test --package bcs-test-support --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-test-support --all-targets --manifest-path src/bcs/Cargo.toml`

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

## Tests

- `cargo test --package bcs-friend --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-friend --all-targets --manifest-path src/bcs/Cargo.toml`

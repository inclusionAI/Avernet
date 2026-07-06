# bcs-group Context

## Provides

- Group session service implementation for BCS.
- Group lifecycle, membership, workspace, and session coordination rules.
- Application-facing orchestration for group creation, updates, and state changes.

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

- Bootstrap injects stores, collaborators, and policy knobs explicitly.
- This crate must not choose concrete plugins or inspect env directly.

## Runtime ownership

The crate owns group session business rules and lifecycle decisions. It does not own HTTP/WS handling or implementation selection.

## Tests

- `cargo test --package bcs-group --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-group --all-targets --manifest-path src/bcs/Cargo.toml`

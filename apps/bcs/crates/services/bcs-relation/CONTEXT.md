# bcs-relation Context

## Provides

- Relation graph service implementation for BCS.
- Ownership, friendship, and other relation-edge facts used by higher-level use cases.
- Application-facing orchestration around relation queries and mutations.

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

The crate owns relation facts and relation-update semantics. It does not own delivery/runtime concerns or implementation selection.

## Tests

- `cargo test --package bcs-relation --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-relation --all-targets --manifest-path src/bcs/Cargo.toml`

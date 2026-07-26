# bcs-bot Context

## Provides

- Bot service implementations for BCS, including the independent Bot
  control-plane Core.
- Bot onboarding, discovery, status, connectivity, and binding metadata behavior.
- Application-facing orchestration around registry reads and writes.
- Shared per-bot scheduling for best-effort visibility synchronization.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- The outbound `VisibilitySyncPort` supplied by bootstrap.
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

The crate owns Bot control-plane persistence orchestration such as Provider hydration.
The crate owns registry business rules, status/connectivity semantics, and visibility-sync scheduling. It does not own socket runtime state, transport handling, or BCSFuse retry policy.

## Tests

- `cargo test --package bcs-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-bot --all-targets --manifest-path src/bcs/Cargo.toml`

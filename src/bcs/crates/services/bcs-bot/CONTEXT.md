# bcs-bot Context

## Provides

- Bot service implementations for BCS, including the independent Bot
  control-plane Core.
- Bot onboarding, discovery, status, connectivity, and binding metadata behavior.
- Application-facing orchestration around registry reads and writes.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- `plugin-api/*` contracts when persistence or cache support is needed.
- Pure utility crates for IDs, logging, and serialization.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

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

The crate owns registry business rules, status/connectivity semantics, and Bot
control-plane persistence orchestration such as Provider hydration. It does not
own socket runtime state or transport handling.

## Tests

- `cargo test --package bcs-bot --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-bot --all-targets --manifest-path src/bcs/Cargo.toml`

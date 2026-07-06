# bcs-config Context

## Provides

- Env resolution helpers (`resolve_env`, `RuntimeEnv`, process env view).
- Leaf-config loaders for typed config contracts such as MySQL/OceanBase and Redis.
- Typed config failure helpers used by bootstrap or services.

## Consumes

- `bcs-config-api` contract types.
- `bcs-config-api` leaf data types.
- Explicit typed inputs supplied by bootstrap.

## Allowed dependencies

- `service-api/bcs-config-api`
- Pure utility crates used for config translation or validation

## Forbidden dependencies

- Bootstrap-owned top-level config structures
- `adapters/*`
- `plugins/*`
- `bootstrap/bcs`
- Transport framework crates

## Configuration

- Bootstrap still owns the top-level `BcsConfig` structure because it embeds bootstrap-only wiring types.
- `bcs-config` is the canonical home for env resolution and leaf loaders. Full `BcsConfig` decomposition is tracked post-D.

## Runtime ownership

The crate owns env and leaf-config loading logic. It does not own process bootstrap, service composition, delivery/runtime wiring, or external client selection.

## Tests

- `cargo test --package bcs-config --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-config --all-targets --manifest-path src/bcs/Cargo.toml`

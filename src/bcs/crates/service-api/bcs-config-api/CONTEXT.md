# bcs-config-api Context

## Provides

- Typed configuration contract objects for BCS runtime assembly.
- Config-facing value objects and secrets wrappers shared across bootstrap and services.
- A stable schema boundary for configuration-related evolution.

## Consumes

- Serialization and secrets primitives only.

## Allowed dependencies

- Value-object, serialization, and secrets crates
- No other workspace role beyond contract-only helpers

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- `external-clients/*`

## Configuration

- This crate defines config schema only.
- It must not load files, inspect env, or choose implementations.

## Runtime ownership

The crate owns configuration contract types and their semantics. It does not own runtime wiring or config source discovery.

## Tests

- `cargo test --package bcs-config-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-config-api --all-targets --manifest-path src/bcs/Cargo.toml`

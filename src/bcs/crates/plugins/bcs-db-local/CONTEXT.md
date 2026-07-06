# bcs-db-local Context

## Provides

- Local database implementation of `DbPlugin`.
- Dependency-light SQL execution for local development and contract tests.
- A concrete DB adapter without remote middleware dependencies.

## Consumes

- `bcs-db-api` contract types.
- Local database driver crates and async runtime primitives.

## Allowed dependencies

- `plugin-api/bcs-db-api`
- Utility crates needed for local database access

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Service-owned persistence policy or business repositories

## Configuration

- Bootstrap or tests select this implementation explicitly.
- Local database path and driver options must arrive through constructors or test setup.

## Runtime ownership

The crate owns local DB driver mechanics only. It does not own service-level SQL policy or schema semantics above the plugin contract.

## Tests

- `cargo test --package bcs-db-local --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-local --all-targets --manifest-path src/bcs/Cargo.toml`

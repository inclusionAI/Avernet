# bcs-db-mysql Context

## Provides

- Remote MySQL/OceanBase-backed implementation of `DbPlugin`.
- Concrete database adapter for SQL execution in production-like environments.
- Translation between BCS DB contract semantics and MySQL/OceanBase transport semantics.

## Consumes

- `bcs-db-api` contract types.
- Standard MySQL-compatible driver crates.
- Bootstrap-supplied datasource and credential settings.

## Allowed dependencies

- `plugin-api/bcs-db-api`
- `service-api/bcs-config-api`
- Implementation-specific MySQL driver crates
- Async runtime, driver, and transport helper crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Service-owned persistence policy or repository logic

## Configuration

- Bootstrap selects this plugin when remote DB is enabled.
- Datasource and credential selection must not leak into services.

## Runtime ownership

The crate owns remote DB transport and driver integration. It does not own service-level SQL or business persistence semantics.

## Tests

- `cargo test --package bcs-db-mysql --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-mysql --all-targets --manifest-path src/bcs/Cargo.toml`

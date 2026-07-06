# bcs-cache-redis Context

## Provides

- Remote Redis-compatible implementation of `CachePlugin`.
- Concrete cache adapter for production-like environments.
- Translation between BCS cache contract semantics and Redis transport semantics.

## Consumes

- `bcs-cache-api` contract types.
- Redis-compatible client access through `redis-rs`.
- Bootstrap-supplied cache endpoint and credential settings.

## Allowed dependencies

- `plugin-api/bcs-cache-api`
- `redis-rs` and small transport helper crates
- Serialization, async runtime, and network helper crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Business cache key or invalidation policy

## Configuration

- Bootstrap selects this plugin when remote cache is enabled.
- Endpoint, namespace, and credential selection must not leak into services.

## Runtime ownership

The crate owns remote cache transport and serialization. It does not own BCS business cache semantics.

## Tests

- `cargo test --package bcs-cache-redis --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-cache-redis --all-targets --manifest-path src/bcs/Cargo.toml`

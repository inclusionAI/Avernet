# bcs-fuse-client Context

## Provides

- Pure HTTP wrapper for the remote BCSFuse service.
- Request/response DTOs for worker sync, worker recommendation, batch lookup, and fusion APIs.
- `FuseClientError` for transport and response parsing failures.

## Consumes

- `bcs-config-api` for `BcsFuseConfig`.
- HTTP endpoint, timeout, and profile settings supplied by bootstrap or service wiring.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `bcs-config-api`
- HTTP, serialization, logging, and error helper crates

## Forbidden dependencies

- `bcs-service-api`
- `services/*`
- `adapters/*`
- `bootstrap/bcs`
- direct config file or env discovery

## Runtime ownership

This crate owns BCSFuse transport concerns only. Business implementations that consume this client live in `services/bcs-fusion`.

## Tests

- `cargo test --package bcs-fuse-client --manifest-path Cargo.toml`
- `cargo check --package bcs-fuse-client --all-targets --manifest-path Cargo.toml`

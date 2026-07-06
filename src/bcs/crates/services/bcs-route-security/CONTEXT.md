# bcs-route-security Context

## Provides

- Route-time security service implementation for BCS.
- Security intercept, pass, block, and degraded decision logic.
- A transport-agnostic policy boundary for outbound security checks.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- No runtime dependencies yet; implementation currently lives inline in
  `services/bcs-routing`.
- Pure utility crates for logging and policy evaluation.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `serde` and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- `external-clients/*` crates not listed in `Allowed dependencies` above
- HTTP/WS framework types in policy code

## Configuration

- Bootstrap injects gateway clients, thresholds, and degrade policy explicitly.
- This crate must not read process env or extract request principals itself.

## Runtime ownership

The crate owns route-time security decisions. It does not own request authentication or HTTP/WS connection management.

## Tests

- `cargo test --package bcs-route-security --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-route-security --all-targets --manifest-path src/bcs/Cargo.toml`

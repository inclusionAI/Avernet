# bcs-http Context

## Provides

- HTTP delivery adapter for BCS.
- Single resource-oriented router and route modules under `src/routes/`.
- Request/response parsing, HTTP auth extraction, and HTTP error mapping.

## Consumes

- `bcs-service-api` traits and DTOs.
- `bcs-http-auth` extractors.
- `bcs-protocol` wire DTOs when an HTTP endpoint exposes protocol-shaped payloads.

## Allowed dependencies

- `service-api/*`
- `adapters/auth/bcs-http-auth`
- HTTP framework crates such as `axum`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/ws/bcs-ws`
- Legacy DingTalk runtime modules
- Auxiliary Ding logger crate
- `services/*` concrete crates except temporary compile shims recorded in this document

## Configuration

- Route registration, auth adapter wiring, and service handles are injected by bootstrap.
- Handlers in this crate must not read env or choose concrete service implementations.

## Runtime ownership

The adapter owns HTTP routing and extraction. It does not own request-time
business rules. Handlers call service-api traits. Physical directories do not
encode caller identity; route policy declares allowed principals explicitly.

## Tests

- `cargo test --package bcs-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml`

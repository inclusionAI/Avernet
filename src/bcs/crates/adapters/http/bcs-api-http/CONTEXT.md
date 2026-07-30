# bcs-api-http Context

## Provides

- Versioned `/openapi/v1/**` and `/internal/v1/**` HTTP delivery boundaries.
- Request/response DTO translation and the common response envelope.
- An injectable Gateway Principal verification boundary.

## Consumes

- `bcs-service-api::application::v1` contracts.
- HTTP framework crates such as `axum`.

## Allowed dependencies

- `service-api/*`
- HTTP and serialization utility crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/http/bcs-http`
- `adapters/ws/*`
- `contracts/bcs-protocol`
- concrete `services/*` and `plugins/*`

## Configuration

- Bootstrap injects V1 Application services and a Principal verifier.
- The adapter must not read environment variables or select a production
  Principal trust mechanism.

## Runtime ownership

This crate owns HTTP parsing, versioned wire DTOs, request IDs, envelopes, and
HTTP error mapping. Resource authorization and business policy remain in V1
Application services.

## Tests

- `cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-api-http --all-targets --manifest-path src/bcs/Cargo.toml`

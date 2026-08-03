# bcs-api-http Context

## Provides

- Versioned `/openapi/v1/**` and `/internal/v1/**` HTTP delivery boundaries.
- Request/response DTO translation and the common response envelope.
- An injectable Gateway Principal verification boundary.
- The focused authenticated
  `POST /openapi/v1/collaboration/sessions/{sid}/token` delivery slice.
- A preparatory V1 Gateway wire projection and HS256 token verifier that
  returns a complete, secret-free authenticated caller.

## Consumes

- `bcs-service-api::application::v1` contracts.
- HTTP framework crates such as `axum`.
- JWT, time, and serialization utilities used only by the V1 delivery adapter.

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

- Production bootstrap does not mount this crate yet.
- The adapter must not read environment variables or select a production
  Principal trust mechanism.

## Runtime ownership

This crate owns HTTP parsing, versioned wire DTOs, Gateway token verification,
request IDs, envelopes, no-store token responses, and HTTP error mapping.
Header extraction, production trust selection, router mounting, resource
authorization, Actor selection, and business policy remain outside this
delivery boundary.

## Tests

- `cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-api-http --all-targets --manifest-path src/bcs/Cargo.toml`

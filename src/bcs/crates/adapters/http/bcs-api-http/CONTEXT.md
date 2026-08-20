# bcs-api-http Context

## Provides

- Versioned `/openapi/v1/collaboration/**` and `/api/v1/collaboration/**` HTTP delivery
  boundaries.
- Request/response DTO translation and the common response envelope.
- An injectable Gateway Principal verification boundary.
- The focused authenticated
  `POST /openapi/v1/collaboration/sessions/{sid}/token` delivery slice.
- The nine-operation Session File surface, including protected
  `HumanOrOwnedBot` routes and the token-only public shared-content route.
- Streaming upload/download adaptation and configured public URL projection;
  direct storage presigned targets are preserved unchanged.
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

- Production bootstrap mounts this Router directly at its contract-owned
  `/openapi/v1/collaboration/**` paths and injects completed V1 application
  services behind the Gateway Principal boundary.
- Bootstrap injects the Session File application facade and a validated public
  collaboration base URL. The adapter does not infer public URLs from request
  headers.
- The focused session-token Router remains a separate delivery slice; production
  bootstrap composes it explicitly with the shared V1 Session facade and the
  same Principal verifier.
- The adapter must not read environment variables, select concrete V1
  implementations, or select a production Principal trust mechanism.

## Runtime ownership

This crate owns HTTP parsing, versioned wire DTOs, Gateway token verification,
request IDs, envelopes, no-store token responses, and HTTP error mapping.
It also owns the public/protected Router split, raw file-body streaming, and
OpenAPI upload/share URL projection. Identity-policy selection happens before
dispatch, while resource authorization remains in the application facade.
Bootstrap owns concrete service selection, production trust selection, and
Router mounting; application facades own resource authorization, Actor
selection, and business policy.

## Tests

- `cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-api-http --all-targets --manifest-path src/bcs/Cargo.toml`

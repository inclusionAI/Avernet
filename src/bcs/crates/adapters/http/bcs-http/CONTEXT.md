# bcs-http Context

## Provides

GET/PUT /admin/message-delivery/policy allow authenticated Human identities from the configured auth boundary to manage environment-wide policy. Explicit Bot/Provider/service credentials never fall back to local mock Human identity. The old version-prefixed route has no alias; ServiceKey permissions for other APIs are unchanged.

- HTTP delivery adapter for BCS.
- Legacy session history and the V1 Session facade use the same pure domain
  StateMachine merge: durable identity wins, results are newest first, and
  workflow Run IDs remain in metadata instead of chat-round grouping fields.
- State Machine start/get/rerun, node, graph and pending-Human routes preserve
  the runtime's saved Loop metadata/context. Responses keep the legacy shape
  with optional additive fields; node path IDs remain opaque execution IDs.
- Single resource-oriented router and route modules under `src/routes/`.
- Request/response parsing, HTTP auth extraction, and HTTP error mapping.
- Delivery query, cancel, and manual resolution routes share the
  `/openapi/v1/collaboration` prefix; no unprefixed write aliases are mounted.
- Authenticated Bot endpoints for querying current-session state-machine
  permission and submitting one-shot YAML, transient role bindings, and input.

## Consumes

- `bcs-service-api` traits and DTOs.
- `bcs-http-auth` extractors.
- `bcs-protocol` wire DTOs when an HTTP endpoint exposes protocol-shaped payloads.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

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

- Bootstrap injects `state_machine_history.persistence_enabled` and
  `message_history.state_machine_cutoff_timestamp` into both history entry points.
  Enabled Sessions with original `created_at >= cutoff` read frozen content from
  MessageRepo only; older Sessions and disabled persistence use the runtime path.
  The cutoff defaults to 0. Session/Group authorization still applies; selected
  message reads never fall back or backfill. One-shot StateMachine history uses
  this same cutoff; ordinary mixed chat keeps its separate cutoff policy.
  Configuration changes require restart; the removed `read_source` is rejected.

- Route registration, auth adapter wiring, and service handles are injected by bootstrap.
- Handlers in this crate must not read env or choose concrete service implementations.
- `GET`/`PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes` require the
  Provider Admin Bearer token, a Provider ID listed in
  `allowed_switch_provider_ids`, and an active binding between that Provider and
  Bot. The PATCH body is a strict partial update of `user_visibility`,
  `friend_ext`, and `friend_check_in_strategy`.

## Runtime ownership

The adapter owns HTTP routing and extraction. It does not own request-time
business rules. Handlers call service-api traits. Physical directories do not
encode caller identity; route policy declares allowed principals explicitly.

## Tests

- `cargo test --package bcs-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml`

Provider Bot registration and list responses expose the saved optional webhook_url. PATCH maps omitted/null/string to the application endpoint change contract; the application rejects mixing URL and capability writes. URL inheritance is decided in BotCore, not the HTTP adapter.

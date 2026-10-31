# bcs-http Context

## Provides

`POST /providers/{provider_id}/bots` accepts optional `agent_card_url` and
dispatches that branch to A2aBotManagementService. Missing/null preserves the
legacy Provider management path. Provider/Bot registration handlers live under
routes/providers.rs; HTTP does not fetch Cards or write stores.
The request/atomicity/duplicate contract is documented in
docs/specs/a2a/provider-admin-registration-v1.md at the repository root.

`GET /api/v1/collaboration/providers/by-slug/{slug}` is unauthenticated and exposes only Provider
identity, name, auth mode, enabled status, protocol version and audit timestamps.
The legacy `/providers/by-slug/{slug}` remains an alias with identical responses
and errors. The new frontend uses the collaboration prefix through the existing
gateway passthrough; BCS must be deployed before that path is available. No new
persistence calls or authorization rules are introduced. It consumes the
application service's credential-free projection. Existing
registration/PATCH accepts optional slug with unchanged authentication; admin
info responses add nullable slug and effective protocol_version. Core validates
slugs, stores enforce uniqueness, and the adapter maps absence to 404.

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
- Legacy `GET /register/token` and anonymous `POST /register` retain v1 behavior
  and bare HTTP 200 responses, while accepting Provider-scoped v2 tokens through
  the shared injected `RegisterService`. Scoped parameters are parsed only for
  v2 redemption; the facade owns authorization, scope and persistence. Successful
  issuance/redemption responses use `Cache-Control: no-store`.

## Consumes

- `bcs-service-api` traits and DTOs.
- `application::v1::RegisterService` for scoped issuance and registration;
  adapter construction without it retains v1 and fails scoped calls closed.
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
  MessageRepo only; the StateMachine facade keeps its runtime source for older
  Sessions or disabled persistence. The cutoff defaults to 0; selected message
  reads never fall back or backfill. In Chat/ManagerWorker, ordinary history keeps
  its own cutoff and owner filters. Full/Bot views never gain a runtime merge:
  an already-selected ordinary MessageRepo path remains unchanged. Participant
  views use the StateMachine facade for their existing scoped snapshot merge.
  If ordinary history uses native fallback, the StateMachine cutoff controls
  replacing its workflow entries with durable rows. Authorization is unchanged.
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

## Service Session policy retirement

Service Sessions retain creation, authorization, completion, completion events,
activation fencing and reactivation. The Group `service_spec` policy, Service
Session timeout scanner, external completion callbacks and callback recovery are
retired. Reactivation requires a Completed Service Session; it has no callback
status prerequisite. State Machine Node deadlines, progression/IM recovery,
Provider callbacks and Event subscriptions retain their existing contracts.
Legacy Group creation accepts and discards `service_spec` for Backend compatibility, and the legacy settings
endpoint returns 410. Service invocation needs no Group policy marker. Existing
Group policy and Session callback columns, indexes and values remain unchanged;
runtime stores no longer read or write those fields. This retirement adds no
schema migration. Stop old binaries to prevent legacy scanners and callbacks
from continuing to run. See `apps/bcs/specs/2026-10-27-retire-service-session-policies/spec.md`.

## A2A Card administration

`PUT /providers/{provider_id}/bots/{bot_uuid}/a2a` accepts exactly one of source Card URL or
inline JSON Card plus the expected configuration revision. `POST .../a2a/card:refresh` re-fetches only the
saved source. Both extract a Provider-admin Bearer and call
`A2aBotManagementService`; core affiliation/authorization rules remain in the
service. Memory-only test servers may omit this durable capability and return
503 explicitly. Neither route submits a probe task or exposes credential values.

`GET /bots/{id}/agent-card` calls the same authorized Bot detail use case and
returns the registered source Card, or 404 for a legacy Bot. It does not advertise
BCS as an A2A server. Detail, discovery, search and batched query views carry the
full optional `agent_card` alongside existing fields; legacy responses omit it.
Legacy Bot JSON projections remain in routes/bots.rs.

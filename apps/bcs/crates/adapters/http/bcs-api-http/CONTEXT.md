# bcs-api-http Context

## Provides

- Immutable Draft GET/validate at `/api/v1/collaboration/drafts/**`, outside
  Gateway middleware, using only the fixed revision read capability. The native
  and V1 adapters share one injected Draft service; V1 adds an envelope, not
  management routes, identity fallback or persistence.
- `GET /openapi/v1/collaboration/me` projects the verified Human only. The native
  `/collaboration/user` browser slice reuses V1 me/Bot mine/candidates/Group create
  handlers with a bootstrap-selected Human verifier, no-store responses and a
  cross-site/custom-header write boundary. `X-BCS-Expected-User` compares a
  prepared configuration's user with verified identity before dispatch; it is
  never an authentication source. Ownership and eligibility stay in application.

- `GET /openapi/v1/collaboration/channels/conversations/by-session` reuses the
  Session V1 application facade's Human/owned-Bot read authorization before
  querying Channel mappings. It returns a V1 envelope with all mappings and
  no IM user identity or provider configuration. Historical fallback remains
  owned by ChannelService; app-only callers cannot read the mappings.

- Shared V1 normal-Group responses include `driver_bot_name` on both `/groups`
  and `/public-groups` summaries, and `driver_bot_owner` / `driver_bot_owner_name`
  on detail responses (create/get/update). Missing values serialize as JSON
  null, not omitted keys; old payloads without the additive fields remain
  deserializable. Owner is the driver's Human actor ID, not the requesting
  User. DM response shapes and Gateway/resource authorization are unchanged.

- Internal-only `GET /api/v1/collaboration/bots/me`, outside Gateway Principal
  and invite-code middleware. It passes a parsed Bearer token and optional Actor to
  `BotSelfService`, which verifies Agent identity or a concrete Bot Static Token. Success and error
  responses use V1 envelopes and `Cache-Control: no-store`; DTOs explicitly
  include only verified agent_code and safe persisted Bot metadata.

- State Machine Run/Node/Graph/PendingHuman responses and Session creation retain
  the runtime's saved Loop execution mapping/context. The adapter preserves
  opaque node IDs and static loop routes without reconstructing metadata.
  V1 payloads omit absent additive fields; rerun's flattened envelope retains
  the complete node_execution_metadata map.

- Versioned `/openapi/v1/collaboration/**` and `/api/v1/collaboration/**` HTTP delivery
  boundaries.
- Request/response DTO translation and the common response envelope.
- The ten-operation Event Subscription and Delivery management surface,
  including write-only Webhook credentials, cursor pagination, optimistic
  revisions, synchronous test, DLQ replay, and audited skip adaptation.
- An injectable Gateway Principal verification boundary.
- The focused authenticated
  `POST /openapi/v1/collaboration/sessions/{sid}/token` delivery slice.
- The nine-operation Session File surface, including protected
  `HumanOrOwnedBot` routes and the token-only public shared-content route.
- Streaming upload/download adaptation and configured public URL projection;
  direct storage presigned targets are preserved unchanged.
- A preparatory V1 Gateway wire projection and HS256 token verifier that
  returns a complete, secret-free authenticated caller.
- The caller-agnostic public Group catalog at
  `GET /openapi/v1/collaboration/public-groups`, which lists all
  `visibility=public` Groups without participant scoping. The Gateway
  Principal boundary still requires an authenticated User; the catalog
  emits `membership=none` because no per-caller participation is
  evaluated at list time.

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
OpenAPI upload/share URL projection. Event Subscription routes only translate
verified callers and wire inputs into the injected application service; they
do not import Event Store or Webhook implementations. Identity-policy selection happens before
dispatch, while resource authorization remains in the application facade.
Bootstrap owns concrete service selection, production trust selection, and
Router mounting; application facades own resource authorization, Actor
selection, and business policy.

## Tests

Register endpoints accept additive Provider/mode/ref/webhook queries. GET remains
Human-authenticated and POST remains token-only; responses with credentials use
no-store. The application selects v1/v2 tokens and authorizes scope; this adapter
only parses query DTOs and maps application errors. Legacy response shape and
bot-name/bot_name compatibility are covered by route and facade contract tests.

- `cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-api-http --all-targets --manifest-path src/bcs/Cargo.toml`

## AgentPass subbot boundaries

The internal `agents/me` route maps parent verification and bounded pagination
through `BotSelfService`; `bots/me` accepts optional Actor and concrete Static
Tokens. Both remain outside Gateway Principal middleware and return no-store
responses. Scoped registration forwards the complete `provider_bot_ref`; the core
derives authentication mode from the Provider configuration and validates the
parent/slug split. No separate request slug or expected auth mode is consumed.
Gateway Principal checks retain the signed Bot UUID and reject a mismatching
Actor; bootstrap additionally injects current persisted-state validation.

# bcs-api-http Context

## Provides

- A2A registration and Card management at `/api/v1/collaboration/a2a/**`,
  owned by `v1/a2a`, call the injected `application::v1::a2a::A2aService`.
  Provider administration uses raw Provider-admin Bearer credentials, outside
  Gateway Principal and Human invite-code middleware. The existing management
  application enforces Provider/resource authorization. Responses use the V1
  `code/message/data/request_id` envelope and no-store headers; creation Location
  stays under this prefix. Framework/path/body/method errors are also enveloped.
  AgentCard protocol/authentication errors explain the required method, JSON format,
  size limit or Provider-admin credential. Internal failures return safe support
  guidance with request_id, not raw exception details; shared non-A2A errors are unchanged.
  Card payloads preserve unknown fields. Chat and run controls remain exclusively
  on the legacy `/a2a/...` routes.

- Immutable Draft GET/validate at `/api/v1/collaboration/drafts/**`, outside
  Gateway middleware, using public fixed-revision reads without credentials. The native
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

- `tests/a2a_routes.rs`: V1 routing, envelopes, strict parsing, request IDs,
  Provider-admin credential forwarding, Card views and absent chat/run routes.
- `tests/boundary_contract.rs`: new A2A namespace belongs to this crate and is
  injected from production bootstrap, with no legacy HTTP dependency.

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

Scoped A2A registration also accepts an optional JSON body containing inline
agent_card or agent_card_url. Query/body duplicates and mixed sources are rejected.
An omitted A2A display name is passed to the application for Card.name defaults;
legacy registrations still require names. See minimal-card-registration-v1.md.


## Authentication display data

`VerifiedRequestIdentity` carries an optional avatar alongside the unchanged
business caller and pinned authentication context. It is verified-source
display data only, never authorization input. OAuth bootstrap copies it from
the existing strict-session snapshot, and `/openapi/v1/auth/user` forwards it
without another credential-chain run or database query. Sources constructed
with `pin` keep a null avatar unless they explicitly supply one.

## Compatibility current-user selection

Bootstrap may inject `ApiState.auth_user_verifier` for the legacy-configured
OAuth facade when `[api.auth]` is absent. Only `GET /openapi/v1/auth/user`
uses it; protected V1 routes retain `principal_verifier`. The adapter selects
one verifier before authentication, never retries another source on failure.
Explicit chains do not install this override.

## Invite-code issuance

The internal init route forwards the verified caller in `InitInviteCodes` rather
than discarding it. Application enforces the configured issuer-user allowlist;
being under `/api/v1` or passing the Cookie Origin check is not authorization.
The request body remains count-only, and authorization denial maps to 403 with
no authentication-source fallback.

## A2A business-failure diagnostics

A2A's transport boundary retains a request-local body snapshot under its existing
2 MiB limit. Only failures produce `bcs_business_warning` records, through the
neutral `bcs-observability` auxiliary API. Original field values (including unknown
Card fields) are retained after credential redaction, not reconstructed from
normalized application commands. Invalid JSON is explicitly omitted; successful
requests emit no payload. The existing response envelope and admission behavior
are unchanged. See `docs/observability/request-operation-logging.md` in BCS.


## AgentInvoke management and governance delivery

`v1::agent_invoke` owns twelve method/path operations under both
`/api/v1/collaboration/agent-invoke` and its OpenAPI mirror. Both are mounted by
the existing V1 router and share one handler/application, not legacy friend or
raw store methods. `ApiState::with_agent_invoke` injects the independent
management/query Service APIs, exact scope/tenant and optional explicit numeric
App-to-Governance mapping. Default is None: authenticated requests return 503,
never an empty page or a silently enabled writer. Production host assembly and
eligibility/dispatch remain separate unfinished work.

The existing V1 Principal verifier and actual-source CSRF gate authenticate once.
The pinned tenant must equal the injected tenant (including tenantless mode).
Bot identity takes precedence over owner display metadata and remains an Agent,
which Core denies governance access. A verified Human maps to its canonical
Human actor. Only explicitly configured verified Apps map to Governance, which
still needs current pair/action delegation; no public app/agent/system=true
shortcut, unsigned identity/header trust, anonymous fallback or auto-claim.
Authentication failures, framework errors and business outcomes are enveloped
and no-store, with request ID echo. Credentials never appear in response DTOs.

Strict JSON bodies (16 KiB) reject unknown/duplicate fields; query parameters
reject unknown names/duplicates and are bounded to 8 KiB. Required
Idempotency-Key is singular, visible ASCII, 1..128 bytes. Grant/request bodies
specify only pair, reason and grant expected_version; decisions/revoke accept
only expected_version/reason. Exact reads and Human decisions use bot_id + direction and optional peer
to authorize a scoped exact query BEFORE deriving the pair. Governance decisions
require explicit peer and use the action-authorized command directly: Query
delegation is not an extra prerequisite for a delegated write action.
Command authorization/fences remain rechecked by the management application and
atomic store. A failed pre-read never calls the mutation service.

Read pages default 50/max100. Opaque versioned keysets bind scope, principal,
anchor/peer, resource/direction/status/action through canonical structured
context hashing. This is not an authorization token/signature: even fabricated
positions are validated and reauthorized by Core/store. Limit can change without
changing the selected relation. All page/degree/audit fields are safe projection;
StoredApproved is not executable permission, optional name/code are decoration,
and raw audit before/after JSON is not exposed.

Write acknowledgements return immutable action receipt + distinct original and
current state references, not a fabricated registration record or timestamps.
GET records carry actual persisted creation/update times. Creation and replay
retain 201/Location in the originating prefix. No follow-up read is issued after
a committed write; this avoids turning a successful commit into a partial
projection failure. Human decision/revoke adds one independently authorized exact-read operation
to existing command cost (6 SQL statements), then the unchanged atomic command.
Governance exact-pair decisions add no query use case and still revalidate
resource ID + pair + delegated action inside the atomic command. Collections and reads add no external inventory
calls, graph scan, positive cache or HTTP retry. Acknowledge uncertainty remains
503 with a distinct stable code, recovered only by the same canonical key.

The strict wire schemas and both path inventories are in the versioned OpenAPI
contracts, with no old SystemInvoke spelling or legacy route alias. Thirteen new
HTTP tests exercise real application/SQLite lifecycle and query paths, role and
tenant boundaries, stale versions, replay after revoke, cursor context, quotas,
closed gates, strict parsing, missing injection, cookie writes and every
endpoint's authentication. Separate recording fault wrappers verify closed
error mappings/actual application-port hits for all six writes and six reads;
these are transport tests, not DB atomicity/unknown-commit proof. Full adapter
regressions retain legacy/A2A/authority behavior and the application-only import
gate. SDK, production host capabilities, complete invocation Run lifecycle and real
HTTP->DB->A2A Singlebox acceptance remain distinct obligations.


## Verified AgentInvoke ingress and physical handoff

POST /api/v1/collaboration/agent-invoke/invocations and its OpenAPI mirror
use existing principal verification/actual-source CSRF, no-store and request ID
handling. Only a verified Bot with exact configured tenant is accepted; general
App credentials or owner display identity cannot substitute. Scope and verified
UUID/AgentCode go to the application, never through request-body assertions.

Strict JSON (16 KiB) accepts target/message and optional session/timeout only;
unknown/duplicate body/query fields, originator, qualification, authentication
mode and Provider-header assertions are rejected. Independent composition
capability defaults to None (503). 202/20200 means runtime acceptance, not
completion or exactly-once; automatic retries are forbidden. New invocation
Run GET/cancel and SDK contracts are not implied by this POST.

OpenAPI contracts describe both prefixes. Real signed-token HTTP -> SQLite ->
A2A transport tests live in message-flow's test composition; the adapter keeps
its application-only boundary and no forbidden legacy protocol dependency.
Physical source/epoch pinning is application/store/runtime responsibility.
Production inventory/capability injection and Singlebox/rollout remain separate.

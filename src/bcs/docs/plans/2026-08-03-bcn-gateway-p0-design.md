# BCN-to-Gateway P0 Integration Design

- **Date:** 2026-08-03
- **Status:** Approved through the preceding design discussion
- **Tracking for deferred work:** inclusionAI/Avernet#700

## Goal

Expose the existing contract-first BCN V1 HTTP and session-bound WebSocket APIs
through Gateway at identical `/openapi/v1/collaboration/**` paths, publish their
OpenAPI description through Gateway's existing compatibility-gated schema
catalog, and enforce each route's approved authentication boundary in BCS.

## Scope boundary

P0 contains only the work required for a usable, fail-closed integration:

1. Export the authoritative BCS YAML contract as deterministic, self-contained
   `bcn.openapi.json`.
2. Publish that artifact through Gateway's existing dump, compatibility-gate,
   and file-schema-catalog flow.
3. Compose the existing V1 Application facades and mount the existing
   `bcs-api-http` Router in the BCS production bootstrap.
4. Resolve the shared Gateway Principal signing key once in the BCS composition
   root and inject `GatewayPrincipalTokenVerifier` with `iss=gateway`,
   `aud=bcs`, and `kid=bare`.
5. Complete and mount PR #697's session-bound connection flow:
   `POST /openapi/v1/collaboration/sessions/{session_id}/token` and
   `GET /openapi/v1/collaboration/messages/ws?token=...`.
6. Resolve the dedicated group-session WebSocket JWT key once in bootstrap and
   inject the shared connection service into the HTTP and WebSocket adapters.
7. Configure Gateway domain `collaboration` for both HTTP and WebSocket relay,
   with no rewrite. Require a User Principal for normal collaboration routes
   while leaving the WebSocket handshake anonymous at Gateway because BCN
   verifies its query credential.
8. Add focused contract, mount, forwarding, authentication, Upgrade, and
   served-OpenAPI tests. WebSocket message-protocol parity is not duplicated.

The public contract has 34 operations: the original 32 collaboration
operations plus the session-token POST and the WebSocket Upgrade GET.

## Approaches considered

### Selected: complete contract-first HTTP and WebSocket integration

Keep `src/bcs/api-contracts/v1/openapi.yaml` and its fragments authoritative.
The exporter resolves references and emits deterministic JSON. Bootstrap
constructs the existing V1 Application implementations and mounts the HTTP and
WebSocket delivery routers. Gateway relays both planes without rewriting. This
keeps the public contract, runtime mount, and deployment route aligned.

### Rejected: publish the two operations without runtime integration

Adding the operations only to OpenAPI would advertise routes that production
BCS does not mount and would recreate the contract/runtime drift this P0 is
intended to remove.

### Rejected: duplicate the full Workbench message-protocol suite

The new WebSocket route reuses the existing Workbench handler and dispatcher.
This integration verifies Upgrade authentication, immutable session binding,
and connect-time authorization; chat, streaming, attachments, abort, and event
delivery remain owned by the existing `/ws` and PR #697 tests.

### Deferred: generate Axum routes and route inventory from one Rust manifest

This gives stronger runtime/contract drift prevention, but changes the API
development model and is not required to forward today's API. It remains in
issue #700.

### Rejected: derive the public document from runtime introspection

BCS is contract-first, unlike the FastAPI services. Runtime introspection would
create a second authority and would still require schema and error-envelope
conformance work.

## Architecture and data flow

```text
BCS YAML contract
  -> deterministic JSON exporter
  -> Gateway compatibility gate
  -> configs/schemas/bcn.openapi.json
  -> FileSchemaCatalog
  -> Gateway /openapi.json

Client
  -> Gateway /openapi/v1/collaboration/**
  -> authenticate User for normal HTTP routes
  -> sign X-Avernet-Principal (aud=bcs) for the token POST
  -> forward path verbatim to BCS
  -> BCS verifies JWT and creates AuthenticatedCaller
  -> existing bcs-api-http Router
  -> existing V1 Application facade
  -> existing core/store services

Browser
  -> POST /openapi/v1/collaboration/sessions/{session_id}/token
  -> receive five-minute, single-session BCN JWT
  -> GET /openapi/v1/collaboration/messages/ws?token=...
  -> Gateway relays anonymously and preserves path/query
  -> BCS verifies the BCN JWT before Upgrade
  -> shared Workbench handler revalidates the bound session on connect
```

Gateway selects the upstream from the first segment after `/openapi/v1`.
Therefore one `collaboration -> bcs` domain entry covers bots, groups, sessions,
friend requests, invitations, and the two session-bound connection routes.
Gateway and BCS paths remain identical; no proxy rewrite or handwritten Gateway
operation is added.

## OpenAPI publication

The BCS exporter:

- loads the existing multi-file YAML contract;
- resolves all file and local references;
- rewrites discriminator mappings to self-contained JSON pointers;
- rejects any exported operation outside
  `/openapi/v1/collaboration/**`;
- emits UTF-8 JSON with sorted keys, stable separators, and one trailing
  newline;
- never includes unresolved `$ref` values to external files.

The WebSocket endpoint is represented as its HTTP Upgrade handshake rather than
as a message protocol. Its OpenAPI operation is a `GET` with a required,
sensitive `token` query parameter, `x-avernet-protocol: websocket`, an explicit
empty Gateway security requirement, and a `101` response. The contract does not
attempt to describe Workbench frames.

The token POST returns the existing standard success envelope containing only
`token: string` and `expires_at: integer/int64` (Unix timestamp), and documents
the `Cache-Control: no-store` and `Pragma: no-cache` response headers.

Gateway's existing compatibility gate remains the publication authority. The
initial committed `bcn.openapi.json` establishes the single-box published
artifact. Collision detection in the multi-domain OpenAPI merger is deferred
to #700; the dedicated collaboration path prefix prevents path collisions in
this P0.

## BCS runtime composition and authentication

`bcs-api-http` remains a delivery adapter and continues to depend only on V1
Application contracts. Concrete `bcs-app-*` implementations are selected in
`bootstrap/bcs`, which already owns service and store construction.

BCS resolves the same community HMAC key as Gateway:

```text
AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE
```

Local/dev single-box execution may use Gateway's documented fixed development
fallback key. Pre, gray, and prod fail startup when the configured secret is
missing or empty. Request-time missing, duplicate, malformed, expired,
wrong-issuer, wrong-audience, wrong-kid, or incorrectly signed Principal values
produce the existing uniform 401 envelope.

The V1 Router is merged directly into the existing Axum application. Legacy
routes remain mounted unchanged. The mount must not add another
`/openapi/v1/collaboration` prefix.

PR #697 provides the session-token application service, dedicated JWT port and
implementation, HTTP delivery slice, and session-bound Workbench dispatcher
foundation. Bootstrap completes the composition and mounts both public routes.
The WebSocket adapter verifies the query JWT before Upgrade, creates one
immutable tenant/User/Group/Session binding, and delegates the upgraded socket
to the existing Workbench handler.

The group-session JWT uses a dedicated secret:

```text
BCS_SECRET_BCN_GROUP_SESSION_WS_JWT
```

It is not the Gateway Principal or OAuth signing key. Missing or empty material
fails startup; adapters never read environment variables or select concrete
token implementations.

The documented synchronous `BcsServer::new` constructor must resolve its
`SecretAccessPort` from the configured provider; it must never install a
repository-known signing key. The fixed signing key is confined to the
explicit `new_allowing_private_outbound_for_tests` constructor. Because the
configured provider builder is asynchronous, the synchronous constructor uses
the same dedicated-thread Tokio bridge already used by other synchronous
bootstrap dependencies.

Shipped local launchers set an overridable, local-only development value for
`BCS_SECRET_BCN_GROUP_SESSION_WS_JWT` when `SERVER_ENV=local`. They do not
provide this fallback for dev, pre, gray, or prod. External-process tests
select the env secret provider and inject their own test value explicitly, so
production startup remains fail-closed while the documented local and CI
stacks remain reproducible.

## Gateway configuration

Add:

```yaml
upstream_vars:
  bcs_server_url: https://bcs.sample.com

route_security:
  /openapi/v1/collaboration/**:
    user: required
  "GET /openapi/v1/collaboration/messages/ws": {}

upstreams:
  domains:
    collaboration:
      server: bcs
      protocols: [http, websocket]
      schema:
        source: file
        path: schemas/bcn.openapi.json
  servers:
    bcs:
      base_url: ${bcs_server_url}
```

The server name is deliberately `bcs`: Gateway uses it as the signed Principal
audience, matching the BCS verifier contract. Gateway forwards the WebSocket
token query unchanged but must redact its value from logs. It does not parse or
verify the BCN session JWT.

## Error handling

- Export validation errors fail before publication and leave the current
  artifact untouched.
- Backward-incompatible schema changes are rejected by the existing Gateway
  compatibility gate unless an explicit coordinated override is supplied.
- Invalid BCS trust configuration fails startup; it never installs a permissive
  verifier.
- Invalid request Principals fail with 401 before any V1 Application service is
  invoked.
- Missing, malformed, forged, expired, or wrong-purpose session JWTs fail the
  WebSocket handshake with 401 before Upgrade. An unavailable verifier fails
  with 503.
- A valid token whose session scope no longer matches current authorization is
  rejected during the Workbench connect phase and the socket is closed.
- Unknown Gateway domains remain 404 and are never forwarded.

## Verification

Focused automated evidence must cover:

- deterministic JSON bytes, 34 operations, collaboration-only paths, and no
  external references;
- dump/gate/publish of the BCN artifact;
- `collaboration` domain resolution to server `bcs` without rewrite;
- Gateway signing with audience `bcs` and stripping forged inbound Principal
  headers;
- BCS production Router reachability plus missing/invalid Principal 401;
- token issuance success, no-store headers, Human-only authorization, and the
  documented 401/403/404/500 envelopes;
- WebSocket pre-Upgrade rejection for missing, forged, expired, and
  wrong-purpose tokens;
- valid WebSocket Upgrade with the exact immutable User/Group/Session binding;
- a coverage-gated external-process story that creates a real Human-owned
  session, signs a local Gateway Principal with the documented development
  trust, obtains a real session JWT from the token endpoint, and completes a
  `101 Switching Protocols` handshake on the public message WebSocket. The
  story stops at connection/authentication and does not duplicate Workbench
  message-frame tests;
- local `env` secret-provider behavior: a missing named secret returns
  `404/not_found`; the distinct `noop` provider's `503/unavailable` behavior
  remains covered by a focused provider test rather than by the shared
  environment story;
- connect-time scope mismatch and revoked-access rejection, without duplicating
  chat, streaming, attachment, abort, or event-delivery cases;
- Gateway WebSocket domain resolution, anonymous handshake rule, path/query
  preservation, and token redaction;
- one representative GET and one body-carrying POST/PATCH through Gateway;
- Gateway `/openapi.json` contains BCN paths while Backend/BaaS paths remain;
- old `/openapi/v1/bots/collaboration/**` and
  `/openapi/v1/group-sessions/**` paths remain absent.

Full route inventory, representative schema/error serialization conformance,
immutable compatibility baselines, component collision detection, and full
Workbench message-protocol parity through the new entrypoint remain tracked by
#700 or their existing protocol suites.

# BCN Group Session WebSocket Token Design

- Date: 2026-08-03
- Status: Approved
- Scope: BCN Workbench session WebSocket authentication and session scoping

## Problem

The existing Workbench WebSocket endpoint, `/ws`, authenticates a Human during
the HTTP Upgrade and then lets the client select a group and optional session in
the `connect` frame. The frontend currently connects directly to BCS. This does
not provide a portable, session-scoped credential that a browser can use when
the WebSocket is exposed through the application Gateway.

The new public connection flow needs to:

1. let an authenticated Human obtain a short-lived credential for one group
   session;
2. carry that credential in a browser-compatible WebSocket URL;
3. bind the authenticated user, group, and session before Upgrade;
4. re-check current authorization when the Workbench `connect` frame is
   processed; and
5. preserve full feature parity with the existing `/ws` Workbench protocol.

## Goals

1. Add `POST /openapi/v1/collaboration/sessions/{sid}/token` in BCN for
   issuing a five-minute JWT.
2. Add `GET /openapi/v1/collaboration/group/ws?token={token}` in BCN for
   session-scoped Workbench WebSocket connections.
3. Bind one JWT to exactly one tenant, Human, group, and group session.
4. Keep JWT verification stateless. A valid JWT may open multiple connections
   to its one session during its five-minute lifetime.
5. Re-run current session authorization when the `connect` frame arrives.
6. Reuse the existing Workbench connection handler, dispatcher, connection
   registry, frontend delivery, frame protocol, and event stream.
7. Keep `/ws` and `/ws/bot` behavior unchanged.
8. Keep HTTP, WebSocket, application, token, and composition responsibilities
   within the existing architecture boundaries.

## Non-goals

- Do not create a user-wide token that can connect to every session the user
  may access.
- Do not create a group-wide token that can select among multiple sessions.
- Do not make the JWT single-use or persist a `jti` consumption record.
- Do not close an established WebSocket merely because its opening JWT later
  expires.
- Do not replace or remove the existing `/ws` endpoint in this change.
- Do not change the bot runtime `/ws/bot` protocol.
- Do not implement a second, reduced Workbench protocol for the new endpoint.

## Chosen Approach

Use a single-session bearer JWT. The token carries immutable connection scope,
while current resource authorization remains server-side and is checked again
when the Workbench `connect` frame is processed.

This separates two decisions:

- **Upgrade authentication:** Is this a genuine, unexpired BCN token carrying
  one complete session binding?
- **Connect authorization:** Does this Human still have permission to access
  this group session now?

The token is not a cache of an authorization decision. It is a signed binding
of identity and requested scope.

### Rejected alternatives

#### User-wide token

A token that can connect to all sessions accessible to one Human has a larger
leak blast radius, permits session probing, and turns the new credential into a
second login token. It also makes the token-issuance `{sid}` and the `gid`/`sid`
claims non-authoritative.

#### Upgrade-only validation

Verifying the JWT and then trusting arbitrary `connect` frame parameters lets a
client escape the scope in the token. It also fails to detect permissions that
were revoked after issuance.

#### Server-side replacement of frame scope

Silently ignoring `connect.group_id` and `connect.session_id` and replacing them
with claims would be safe but confusing: the server would act on values other
than those visible in the frame. Exact matching is explicit and preserves the
existing Workbench protocol.

#### Single-use JWT

Strict single-use requires a persistent or distributed `jti` store and atomic
consumption at Upgrade. The accepted requirement is a stateless five-minute
opening credential, so that state and operational dependency are unnecessary.

## External Contract

### Issue a connection token

```http
POST /openapi/v1/collaboration/sessions/{sid}/token
```

The request must carry an authenticated Gateway Human Principal. The caller
cannot provide `uid`, `gid`, token lifetime, or any JWT claim.

BCN performs these steps:

1. Require a Human identity in the authenticated caller.
2. Load the session named by `sid`.
3. Derive the authoritative `gid` from that session.
4. Check that the Human currently has permission to read/connect to it.
5. Sign a token whose expiration is exactly 300 seconds after issuance.

Success response:

```json
{
  "data": {
    "token": "<compact-jwt>",
    "expires_at": "2026-08-03T12:05:00Z"
  }
}
```

The response includes:

```http
Cache-Control: no-store
Pragma: no-cache
```

The response does not repeat `uid` or `gid`. They are server-owned values and
must not become caller inputs on the next request.

### Open the session WebSocket

```http
GET /openapi/v1/collaboration/group/ws?token=<compact-jwt>
```

The browser opens this URL as given. The JWT is the authentication credential
for this WebSocket handshake. A Gateway login cookie is not required on this
route.

After Upgrade, the endpoint exposes the same Workbench protocol and feature set
as `/ws`. Its only differences are its authentication source and immutable
session scope.

## JWT Contract

The token uses HS256 with a dedicated BCN group-session WebSocket signing key.
It must not reuse the OAuth login JWT claims or signing key.

Example payload:

```json
{
  "iss": "bcn",
  "aud": "bcn-group-session-ws",
  "purpose": "group_session_ws",
  "sub": "user-123",
  "tenant": "tenant-a",
  "uid": "user-123",
  "gid": "group-456",
  "sid": "session-789",
  "iat": 1785744000,
  "exp": 1785744300
}
```

Claim rules:

- `iss` must be `bcn`.
- `aud` must be `bcn-group-session-ws`.
- `purpose` must be `group_session_ws`, preventing token-type confusion.
- `sub` and `uid` must both equal the authenticated Human's stable user ID.
- `tenant`, `uid`, `gid`, and `sid` must be non-blank and length-bounded.
- `gid` is derived from the stored session, never from the caller.
- `sid` comes from the token-issuance path.
- `iat` is the server issuance time.
- `exp` equals `iat + 300` seconds.
- Tokens with an unsupported algorithm, invalid signature, future issuance
  time, invalid lifetime, or expired `exp` are rejected.
- The verifier rejects excessively large compact tokens before decoding.

Expiration limits opening or reopening a connection. Once a socket has passed
Upgrade and `connect`, expiration alone does not terminate it. Reconnection
after expiration requires a newly issued token.

One token may establish multiple concurrent WebSockets during its lifetime,
but every WebSocket is restricted to the token's one `sid` and independently
performs connect-time authorization.

## Signing Key Source

The first release reuses the existing `SecretAccessPort` contract and its
environment-backed `EnvSecretAccess` implementation. Bootstrap constructs the
port with the fixed prefix `BCS_SECRET_` and requests the fixed secret name
`bcn-group-session-ws-jwt`. The resulting environment variable is:

```text
BCS_SECRET_BCN_GROUP_SESSION_WS_JWT
```

Bootstrap reads the value once during startup and passes it directly to the
dedicated group-session JWT implementation. Adapters, application services,
and the JWT implementation do not read process environment variables.

Missing or empty key material fails BCN startup. There is no fallback key and
the value is not written to merged configuration logs. This key remains
independent from both the OAuth session JWT key and the Gateway Principal key.
Mist, KMS, and online key rotation are later implementations of the same
`SecretAccessPort`, not part of the first release.

## Connection Binding

Successful JWT verification produces a transport-neutral immutable binding:

```rust
pub struct SessionConnectionBinding {
    pub tenant: String,
    pub group_id: String,
    pub session_id: String,
    pub user_id: String,
}
```

The Workbench connection handler receives one of two authentication contexts:

```rust
pub enum WorkbenchConnectionAuth {
    UserBound {
        actor_id: Option<String>,
    },
    SessionBound {
        tenant: String,
        actor_id: String,
        group_id: String,
        session_id: String,
    },
}
```

The existing `/ws` route supplies `UserBound`. The new route supplies
`SessionBound`, with `actor_id = "human_{uid}"`.

Both routes enter the same `handle_client_connection`, dispatcher, connection
registry, frontend delivery path, run-channel integration, and disconnect
cleanup.

## Connection State Machine

```text
HTTP Upgrade
    | JWT is valid and yields one complete binding
    v
AwaitingConnect(binding)
    | connect scope matches and current authorization succeeds
    v
Connected(binding, subscription)
    | close, protocol failure, idle timeout, or authorization failure
    v
Closed
```

### Upgrade phase

Before accepting the socket, BCN verifies:

1. the token is present and non-empty;
2. its header, signature, issuer, audience, purpose, times, and claim shape;
3. the required binding fields are non-empty and valid.

The WebSocket route has no `sid` path or query parameter. The verified JWT is
the sole Upgrade-time source of `tenant`, `uid`, `gid`, and `sid`.

Upgrade performs no session database query. Current authorization is checked
when the protocol establishes its subscription, avoiding an authorization
decision at Upgrade that can become stale before `connect`.

### AwaitingConnect phase

Ping/pong remains available. Business frames other than `connect` are rejected
with `connect_required`.

For a session-bound connection:

```text
frame.group_id   must equal binding.group_id
frame.session_id must equal binding.session_id
```

The handler then calls the existing `WorkbenchSessionService.connect` with:

```text
bound_actor_id = human_{binding.user_id}
group_id       = binding.group_id
session_id     = binding.session_id
```

That service reloads current group/session state and checks the existing
Workbench access policy, including ownership or participation and participant
mode. It also confirms that the session still belongs to the bound group.

Only one `connect` may succeed on one socket. Repeated connects are rejected
with `already_connected` so they cannot create duplicate subscriptions.

### Connected phase

Every scoped request remains constrained by the immutable binding:

- `chat.send.group_id` and `chat.send.session_id` must match it;
- `chat.abort.group_id` must match it and the run must belong to the bound
  session;
- frame fields such as `bot_id` and `bot_uuid` cannot replace the bound Human
  identity; and
- every future Workbench method must declare and test its scope behavior for a
  `SessionBound` connection.

The two endpoints remain functionally equivalent. New Workbench methods must
flow through their shared dispatcher so `/ws` and the session-bound endpoint do
not drift into separate feature sets.

## Component Boundaries

### `bcs-service-api`

Add an inbound application contract such as
`GroupSessionConnectionService`:

```rust
#[async_trait]
pub trait GroupSessionConnectionService: Send + Sync {
    async fn issue_token(
        &self,
        command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, ApplicationError>;

    async fn verify_token(
        &self,
        command: VerifyGroupSessionConnectionToken,
    ) -> Result<SessionConnectionBinding, ApplicationError>;
}
```

Add a non-repository outbound port for signing and verifying the dedicated
token claims. Delivery adapters depend only on the application contract and do
not call a JWT implementation directly.

### `bcs-app-session`

Implement the application use cases. Reuse the existing session lookup and
read/connect authorization policy rather than duplicating membership rules in
an adapter. The application service derives `gid`, maps the authenticated Human
to `uid`, fixes the 300-second lifetime, and calls the token port.

### `bcs-jwt`

Implement the token port with dedicated group-session claims. OAuth login JWTs
and group-session WebSocket JWTs remain separately typed and separately keyed.

### `bcs-api-http`

Own `POST /openapi/v1/collaboration/sessions/{sid}/token`, path extraction,
response DTO, no-store headers, and HTTP error mapping. It calls only the
application service.

### `bcs-ws`

Own `GET /openapi/v1/collaboration/group/ws`, query extraction, pre-Upgrade
error mapping, the `SessionBound` authentication context, and scope
enforcement around the shared Workbench dispatcher. It calls only application
services.

### Bootstrap

The composition root creates `EnvSecretAccess` with the `BCS_SECRET_` prefix,
resolves `bcn-group-session-ws-jwt`, constructs the token implementation and
application service, injects them into both delivery adapters, and mounts both
routes. No adapter reads environment variables or constructs a concrete JWT
service.

Configuration is validated at startup. A missing or empty signing key fails
closed; it must not select a default production credential or anonymous mode.

## Gateway Behavior

The Gateway continues to perform application-level WebSocket relay in Python.
An outer Nginx or ingress may terminate TLS and pass Upgrade traffic, but it
does not replace the Gateway authentication and routing plane.

The two routes have intentionally different Gateway security requirements:

```text
POST /openapi/v1/collaboration/sessions/{sid}/token
    authenticated Gateway Human required

GET /openapi/v1/collaboration/group/ws?token=...
    anonymous at Gateway; authenticated by the BCN session JWT
```

The `collaboration` upstream domain therefore serves both HTTP and WebSocket
planes. Gateway configuration maps both requests to the same BCN upstream and
forwards each path unchanged. The Gateway does not parse or validate the BCN
JWT.

All Gateway, ingress, application-server, and BCN access logs must redact the
`token` query value. Existing `x-proxypass-token` redaction is not sufficient
for this new credential name.

Every L7 hop must pass WebSocket Upgrade and avoid a read timeout shorter than
the Workbench heartbeat and connection policy.

## Error Semantics

### Token issuance

- Missing or invalid Gateway Human Principal: HTTP 401.
- Existing session access policy rejects the Human: the existing application
  authorization error mapping is preserved.
- Session does not exist: the existing session-read not-found behavior is
  preserved.
- Signing infrastructure failure: HTTP 500 without credential or claim data.

### Upgrade

| Failure | Result |
| --- | --- |
| Missing, malformed, forged, expired, or wrong-purpose token | HTTP 401; no Upgrade |
| Token verification service unavailable | HTTP 503; no Upgrade |

The browser-facing Gateway relay may expose only a generic handshake failure.
BCN logs and low-cardinality metrics retain the internal reason classification
without recording the token.

### Workbench frames

The shared protocol adds these closed error codes where needed:

- `invalid_connection_token`
- `token_scope_mismatch`
- `connect_required`
- `already_connected`
- `session_access_revoked`

A scope mismatch or failed connect-time authorization produces an error
response and closes the session-bound socket. Retrying different scope on the
same immutable connection cannot succeed.

## Security and Observability

- Never log the compact JWT, signature, query value, or complete claims.
- Never put `uid`, `gid`, `sid`, or token values into metric labels.
- Metric labels remain low-cardinality, for example:
  `endpoint`, `phase`, `result`, and `reason`.
- If correlation is necessary, use an existing request/connection ID. Do not
  introduce a persisted raw-token identifier.
- Do not accept caller-provided `uid`, `gid`, lifetime, issuer, audience, or
  purpose.
- Keep the JWT signing secret separate from Gateway Principal and OAuth login
  secrets.
- Validate compact-token and individual claim lengths before allocating or
  using them as identifiers.
- Preserve constant-time signature verification through the JWT implementation.

## Compatibility

- `/ws` keeps its current authentication and behavior.
- `/ws/bot` is unchanged.
- The new endpoint uses the same Workbench frames and events as `/ws`.
- Existing frontend code can migrate one session at a time.
- The current frontend already maintains providers by `groupId:sessionId`, so
  one Token per session fits the existing connection model.
- Both endpoints run in parallel until a separate, explicit deprecation design
  is approved.

## Test Strategy

### JWT tests

- Exact required claims and 300-second lifetime.
- Wrong key, algorithm, issuer, audience, purpose, and signature.
- Expired token, future `iat`, invalid lifetime, blank claims, oversized token,
  and oversized claims.
- Deterministic clock injection; no tests sleep for expiration.
- Login JWTs cannot be accepted as group-session tokens and vice versa.

### Application tests

- Human-only issuance.
- `uid` comes only from the authenticated caller.
- `gid` comes only from the stored session.
- Missing or unreadable session does not yield a token.
- Participant and ownership access follows existing policy.
- TTL, claims, and scope cannot be overridden by request data.

### HTTP adapter tests

- Principal middleware failures do not call the issuance service.
- Success returns only `token` and `expires_at` in the standard envelope.
- `Cache-Control: no-store` and `Pragma: no-cache` are present.
- Error responses and logs contain no token or claims.

### WebSocket boundary tests

- Every pre-Upgrade rejection class.
- Binding is derived only from the verified token because the WS path has no
  session selector.
- Correct immutable binding.
- `connect` scope mismatch, connect-before-business requirement, and duplicate
  connect rejection.
- Permission revoked, session deleted, group changed, or participant absent
  between token issuance and `connect`.
- Cross-session `chat.send` and `chat.abort` rejection.
- Same-token multiple connections to the same session.
- Token expiration does not close an established connection.

### Workbench parity tests

Run the existing `/ws` Workbench protocol cases through both entrypoints after
authentication setup. Assert identical response/event behavior for:

- connect and participant response;
- chat send, mentions, attachments, thinking, and idempotency;
- run streaming, final events, error events, and abort;
- frontend broadcast and multi-observer delivery;
- heartbeat, idle timeout, close, and cleanup.

Only authentication and scope-mismatch cases intentionally differ.

### Gateway and end-to-end tests

- Human authentication is required on token issuance.
- The WS route is anonymously relayed and BCN enforces its JWT.
- HTTP and WebSocket planes resolve to the BCN upstream.
- Query-token redaction covers Gateway and BCN access logs.
- Text, binary, subprotocol, and close frames remain transparent.
- Full flow: Gateway Human authentication, token issuance, Gateway WS relay,
  BCN Upgrade verification, dynamic `connect` authorization, `chat.send`, and
  session event delivery.
- Existing `/ws` and `/ws/bot` regression suites stay green.

## Rollout

1. Add contracts, token implementation, configuration, and composition wiring.
2. Add HTTP issuance and session-bound WS routes behind the existing BCN
   deployment boundary.
3. Add Gateway routing, route security, and credential redaction.
4. Exercise the complete flow in singlebox and a deployed pre-production
   environment.
5. Migrate the frontend session Provider flow to request one token per active
   session.
6. Run both Workbench endpoints in parallel and monitor issuance, Upgrade,
   connect, scope, and disconnect metrics.
7. Treat removal of `/ws` as a separate compatibility decision.

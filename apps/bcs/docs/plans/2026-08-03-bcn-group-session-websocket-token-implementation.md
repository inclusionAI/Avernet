# BCN Group Session WebSocket Token Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose a five-minute, single-session JWT flow through the `/openapi/v1/collaboration` Gateway domain so a browser can open `/openapi/v1/collaboration/messages/ws?token=...` with behavior equivalent to the existing Workbench `/ws` endpoint.

**Architecture:** `bcs-service-api` defines transport-neutral issuance and verification contracts plus an outbound token port. `bcs-app-session` authorizes the Human and derives `gid` from `sid`; `bcs-jwt` signs and verifies the dedicated JWT. Bootstrap obtains the signing key through the existing environment-backed `SecretAccessPort`. `bcs-api-http` exposes token issuance, while `bcs-ws` authenticates before Upgrade and feeds an immutable session-bound identity into the existing Workbench handler and dispatcher. The Python Gateway transparently relays both planes to identical BCN paths under `/openapi/v1/collaboration`.

**Tech Stack:** Rust, Axum, Tokio, HMAC-SHA256 JWT, Python/FastAPI Gateway, pytest, Cargo workspace tests.

---

## Preconditions and invariants

- Read `docs/arch/arch.rules.md`, `docs/arch/ci.enforce.md`, `docs/arch/context-boundary-format.md`, `docs/arch/protocol-contract-tests.md`, `src/bcs/AGENTS.md`, and `src/bcs/CLAUDE.md` before editing production code.
- Expose `POST /openapi/v1/collaboration/sessions/{sid}/token` and `GET /openapi/v1/collaboration/messages/ws?token=...` at both Gateway and BCN. Gateway forwards both paths unchanged.
- Never accept `uid`, `gid`, tenant, lifetime, or JWT purpose from the request body or query string.
- The token lifetime is exactly 300 seconds. It scopes one tenant, one Human, one group, and one session. It is stateless and may open more than one socket to that same session before expiration.
- Token expiration only gates a new Upgrade. It does not terminate an established WebSocket.
- `/ws` and `/ws/bot` remain backward-compatible.
- The new route uses the same Workbench handler, dispatcher, registry, frontend delivery, frame envelopes, ping behavior, idle timeout, chat/run behavior, and error delivery as `/ws`.
- Redact the `token` query value everywhere. Do not use tokens, `uid`, `gid`, or `sid` as metric labels.

### Task 1: Define the application and token-port contracts

**Files:**

- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group_session_connection.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/port/group_session_token.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/group_session_connection_contracts.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`

**Step 1: Write the failing contract test**

Add compile-time and value tests that require these transport-neutral types:

```rust
use bcs_service_api::application::v1::{
    GroupSessionConnectionBinding, GroupSessionConnectionError,
    GroupSessionConnectionService, IssueGroupSessionConnectionToken,
    IssuedGroupSessionConnectionToken, VerifyGroupSessionConnectionToken,
};
use bcs_service_api::port::{
    GroupSessionTokenClaims, GroupSessionTokenError, GroupSessionTokenPort,
};

#[test]
fn connection_binding_keeps_one_exact_session_scope() {
    let binding = GroupSessionConnectionBinding {
        tenant: "tenant-a".into(),
        user_id: "user-a".into(),
        group_id: "group-a".into(),
        session_id: "session-a".into(),
    };
    assert_eq!(binding.session_id, "session-a");
    assert_eq!(binding.group_id, "group-a");
}
```

Also require a `GROUP_SESSION_WS_TOKEN_TTL_SECONDS: u64 = 300` constant and closed error variants that distinguish unauthenticated/invalid token, forbidden scope, unavailable verifier, and internal failure.

**Step 2: Run the test to verify it fails**

Run:

```bash
cargo test --package bcs-service-api --test group_session_connection_contracts --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the modules and types do not exist.

**Step 3: Add the minimal contracts**

Define:

- `IssueGroupSessionConnectionToken { caller: AuthenticatedCaller, sid: String }`
- `VerifyGroupSessionConnectionToken { token: String }`
- `IssuedGroupSessionConnectionToken { token: String, expires_at: OffsetDateTime }`
- `GroupSessionConnectionBinding { tenant, user_id, group_id, session_id }`
- async `GroupSessionConnectionService::{issue_token, verify_token}`
- `GroupSessionTokenClaims { tenant, uid, gid, sid, purpose, iat, exp }`
- async or synchronous `GroupSessionTokenPort::{issue, verify}`, with no HTTP/Axum types

Keep validation and error projection at this boundary so adapters do not know JWT internals.

**Step 4: Run the test to verify it passes**

Run the command from Step 2, then:

```bash
cargo check --package bcs-service-api --all-targets --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api
git commit -m "feat(bcs): define group session connection contracts"
```

### Task 2: Implement the dedicated five-minute JWT

**Files:**

- Modify: `src/bcs/crates/services/bcs-jwt/Cargo.toml`
- Create: `src/bcs/crates/services/bcs-jwt/src/group_session.rs`
- Modify: `src/bcs/crates/services/bcs-jwt/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-jwt/tests/group_session_jwt.rs`
- Create: `src/bcs/crates/services/bcs-jwt/CONTEXT.md`

**Step 1: Write failing deterministic tests**

Test with a fixed `now` and a dedicated test key. Cover:

- emitted `exp == iat + 300`;
- exact round-trip of tenant/uid/gid/sid and `purpose == "group_session_ws"`;
- rejection at `now >= exp`;
- rejection of future `iat`, non-300-second lifetime, blank/oversized claims, oversized compact token, invalid signature, wrong algorithm/type, and wrong purpose;
- rejection when an existing OAuth/login JWT is passed to this verifier;
- rejection of empty signing keys at construction.

Expose `issue_at` and `verify_at` only as deterministic seams; production `GroupSessionTokenPort` methods obtain current Unix time internally.

**Step 2: Run the tests to verify they fail**

```bash
cargo test --package bcs-jwt --test group_session_jwt --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because `GroupSessionJwtService` is missing.

**Step 3: Implement the minimal signer/verifier**

Add a separate claims struct and service; do not widen or reinterpret the current generic login `Claims`. Implement the `GroupSessionTokenPort` from Task 1. Require HS256 and JWT type, use constant-time HMAC verification, bound compact-token and claim lengths before expensive decoding, and return token-port errors without embedding the raw credential.

The group-session key must be supplied to `GroupSessionJwtService::new`; never reuse or default to the OAuth key.

**Step 4: Run focused and regression tests**

```bash
cargo test --package bcs-jwt --test group_session_jwt --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-jwt --manifest-path src/bcs/Cargo.toml
```

Expected: PASS, including existing login JWT tests.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-jwt
git commit -m "feat(bcs): sign scoped group session websocket tokens"
```

### Task 3: Authorize token issuance and derive scope in `bcs-app-session`

**Files:**

- Modify: `src/bcs/crates/application/v1/bcs-app-session/Cargo.toml`
- Create: `src/bcs/crates/application/v1/bcs-app-session/src/connection.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Create: `src/bcs/crates/application/v1/bcs-app-session/tests/group_session_connection.rs`
- Create or modify: `src/bcs/crates/application/v1/bcs-app-session/CONTEXT.md`

**Step 1: Write failing application tests**

Build fakes for `SessionService` and `GroupSessionTokenPort`. Verify:

- an authenticated Human with session read access receives a 300-second token;
- `uid` comes from `AuthenticatedCaller.user.id`, tenant comes from the caller, `sid` comes from the command path, and `gid` comes from `SessionService::get`;
- request-controlled values cannot override any claim;
- Bot-only, App-only, access-key-only, or missing-Human callers are rejected;
- missing session and forbidden session access do not call the signer;
- token-port unavailable/internal errors remain distinguishable;
- verification returns the complete binding from verified claims; the WS route has no path or query session selector.

**Step 2: Run the tests to verify they fail**

```bash
cargo test --package bcs-app-session --test group_session_connection --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the connection application service is missing.

**Step 3: Implement the service**

Create `GroupSessionConnectionServiceImpl` with injected `Arc<dyn SessionService>` and `Arc<dyn GroupSessionTokenPort>`. Reuse `SessionService::get` so the existing V1 session-read authorization remains authoritative. Do not duplicate repository or group membership rules. Pass the fixed TTL constant to the port.

On verification, validate the JWT through the port and return its immutable
binding. This initial token task does not yet add dynamic connect authorization;
Task 6A adds the dedicated V1 `authorize_connect` operation and invokes it from
the session-bound Workbench `connect` path.

**Step 4: Run focused and package tests**

```bash
cargo test --package bcs-app-session --test group_session_connection --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-app-session --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/application/v1/bcs-app-session
git commit -m "feat(bcs): issue authorized group session tokens"
```

### Task 4: Expose the token issuance HTTP endpoint

**Files:**

- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/group_session_connection.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/lib.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_session_connection_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`

**Step 1: Write failing HTTP contract tests**

Build a focused route fixture with an injected fake `GroupSessionConnectionService` and `PrincipalVerifier`. Verify:

- `POST /openapi/v1/collaboration/sessions/session-a/token` returns the standard envelope containing only `data.token` and `data.expires_at`;
- response headers include `Cache-Control: no-store` and `Pragma: no-cache`;
- missing/invalid Gateway Principal is 401;
- a valid signed Gateway Principal without a Human is rejected;
- session not found, forbidden access, and signer unavailable map to the approved status/code without leaking claims or raw token;
- query/body `uid`, `gid`, `sid`, or TTL fields are ignored or rejected rather than trusted.

**Step 2: Run the test to verify it fails**

```bash
cargo test --package bcs-api-http --test group_session_connection_routes --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the route is absent.

**Step 3: Implement the handler and response DTO**

Add a focused `group_session_connection_router` with a minimal state containing only `Arc<dyn GroupSessionConnectionService>` and `Arc<dyn PrincipalVerifier>`. Mount `POST /openapi/v1/collaboration/sessions/{session_id}/token` and apply the trusted Principal middleware. Extract `AuthenticatedCaller` from that middleware, and pass only caller plus path `session_id` to the application service.

Do not mount the whole preparatory V1 router in production as a side effect of this feature. Reuse the existing envelope, request-id, and error response helpers instead of duplicating the wire format.

Do not add the token to structured logs or tracing fields.

**Step 4: Run route and package tests**

```bash
cargo test --package bcs-api-http --test group_session_connection_routes --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat(bcs): expose group session token endpoint"
```

### Task 5: Refactor Workbench authentication context without changing `/ws`

**Files:**

- Create: `src/bcs/crates/adapters/ws/bcs-ws/src/web/auth.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/mod.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/handler.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/dispatcher.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`

**Step 1: Lock current `/ws` behavior with failing characterization tests**

Introduce tests expecting a `WorkbenchConnectionAuth::UserBound { actor_id }` input while preserving all existing frame results. Cover connect, chat.send, chat.abort, subscription registration, ping/pong, unknown method, and cleanup.

**Step 2: Run the tests to verify they fail**

```bash
cargo test --package bcs-ws --test web_frame_compat --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the new auth context does not exist.

**Step 3: Add the minimal auth-context refactor**

Define:

```rust
pub enum WorkbenchConnectionAuth {
    UserBound { actor_id: Option<String> },
    SessionBound {
        tenant: String,
        actor_id: String,
        group_id: String,
        session_id: String,
    },
}
```

Change the shared handler and dispatcher to accept this enum instead of a loose optional actor string. For `UserBound`, retain the current `/ws` behavior byte-for-byte. Do not mount the new route yet.

**Step 4: Run the entire WebSocket adapter suite**

```bash
cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml
```

Expected: PASS with no existing `/ws` or `/ws/bot` regression.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/ws/bcs-ws
git commit -m "refactor(bcs): carry explicit workbench connection auth"
```

### Task 6: Enforce session scope across Workbench commands

**Files:**

- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/dispatcher.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/message_flow.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/group_flow.rs`
- Modify: all `ChatAbortCommand` constructors reported by `rg -n "ChatAbortCommand" src/bcs/crates`
- Modify: `src/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs`

**Step 1: Write failing scope tests**

For `SessionBound`, verify:

- before successful `connect`, business frames return `connect_required`;
- `connect.group_id` and `connect.session_id` must exactly equal bound `gid`/`sid`, otherwise return `token_scope_mismatch` and close;
- the dynamic V1 group-session authorization still runs using the immutable binding;
- a second successful connect returns `already_connected`;
- chat.send cannot name another group/session;
- chat.abort cannot abort a run belonging to another session, including the form without a client-provided run/session selector;
- revoked access at connect returns `session_access_revoked` and closes;
- UserBound behavior remains unchanged.

**Step 2: Run tests to verify they fail**

```bash
cargo test --package bcs-ws --test web_frame_compat session_bound --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-message-flow --test contract_message_flow abort --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because session scope is not enforced for all commands.

**Step 3: Implement the state machine and abort scope**

Use explicit `AwaitingConnect -> Connected -> Closed` state. Before connect, allow only ping/pong and connect. On connect, compare exact scope first, then call the dynamic V1 authorization path. Keep the accepted binding immutable for the life of the connection.

Extend `ChatAbortCommand` with `session_id: Option<String>` rather than creating a second abort path. Existing `/ws`, HTTP, provider, and test callers pass `None`; session-bound WS passes `Some(bound_sid)`. In message flow, constrain candidate runs by session when present. Propagate constructor changes mechanically without changing unrelated behavior.

**Step 4: Run affected packages**

```bash
cargo test --package bcs-message-flow --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-http --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-provider-http --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api src/bcs/crates/services/bcs-message-flow src/bcs/crates/adapters/ws/bcs-ws src/bcs/crates/adapters/http/bcs-http src/bcs/crates/adapters/http/bcs-provider-http src/bcs/crates/services/bcs-channel src/bcs/crates/bootstrap/bcs/tests
git commit -m "feat(bcs): confine workbench commands to one session"
```

### Task 6A: Revalidate the exact session through V1 without changing legacy `/ws`

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group_session_connection.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/group_session_connection_contracts.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/connection.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/group_session_connection.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/dispatcher.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`
- Verify unchanged: `src/bcs/crates/services/bcs-group/src/application/management.rs`
- Verify unchanged behavior: `src/bcs/crates/services/bcs-group/tests/management.rs`

**Step 1: Write failing V1 application tests**

Extend `GroupSessionConnectionService` with an `authorize_connect` operation
whose input is the already-verified `GroupSessionConnectionBinding`. Its output
contains the exact V1 `SessionParticipant` list used by the Workbench connect
response.

Add tests proving that connect authorization:

- reloads the session instead of trusting the token's issuance-time decision;
- rejects a deleted session;
- rejects a session whose current `group_id` differs from the binding;
- rejects a caller who no longer has V1 session read access;
- rejects a matching Human participant whose mode is `Absent`;
- accepts a current Human participant or a Human who still owns a Bot in the
  exact session.

**Step 2: Run the focused V1 test and verify RED**

```bash
cargo test --package bcs-app-session --test group_session_connection authorize_connect --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the V1 connect authorization operation does not exist.

**Step 3: Implement minimal V1 revalidation**

In `GroupSessionConnectionServiceImpl::authorize_connect`:

1. reconstruct `AuthenticatedCaller` only from the signed binding's `tenant`
   and `user_id`;
2. call `SessionService::get` for the bound `session_id` so the existing V1
   session-read policy remains the single authority;
3. require the loaded session's `group_id` to equal the bound `group_id`;
4. reject a matching Human participant whose mode is `Absent`; and
5. return the loaded V1 session participants.

Do not call or modify `GroupManagement::connect` and do not add a V1 flag to
`WorkbenchConnectCommand`.

**Step 4: Run the V1 application test and verify GREEN**

```bash
cargo test --package bcs-app-session --test group_session_connection authorize_connect --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Write failing WebSocket routing tests**

Add a recording V1 connection service to `WebDispatchState`. Verify that:

- `SessionBound` connect calls `authorize_connect` with the exact immutable
  tenant/user/group/session binding and does not call the legacy
  `WorkbenchSessionService::connect`;
- a V1 authorization error produces `session_access_revoked` and closes;
- `UserBound` connect still calls only `WorkbenchSessionService::connect` and
  never calls the V1 service; and
- the session-bound success response projects the authorized V1 participant
  list into the existing Workbench connect frame shape.

**Step 6: Run the focused WebSocket tests and verify RED**

```bash
cargo test --package bcs-ws --test web_frame_compat session_bound --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --test web_frame_compat user_bound_connect --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the dispatcher still routes session-bound connect
through the legacy service.

**Step 7: Route only session-bound connect through V1**

Add the V1 application service dependency to `WebDispatchState`. In
`handle_connect`, dispatch by authentication context:

- `UserBound` keeps the existing legacy `WorkbenchSessionService::connect`
  path byte-for-byte;
- `SessionBound` calls only `GroupSessionConnectionService::authorize_connect`,
  maps application failures to `session_access_revoked`, and projects the V1
  participants into the existing Workbench response.

Until the new V1 Upgrade route is composed, the optional V1 dependency is
absent in the legacy bootstrap state and must fail closed if a `SessionBound`
context is ever constructed without it. Task 8 makes this dependency required
when mounting the V1 route.

**Step 8: Run focused and compatibility tests**

```bash
cargo test --package bcs-service-api --test group_session_connection_contracts --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-app-session --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-group --test management workbench --manifest-path src/bcs/Cargo.toml
```

Expected: PASS. `src/bcs/crates/services/bcs-group/src/application/management.rs`
has no diff, and all legacy Workbench management tests remain green.

**Step 9: Commit**

```bash
git add src/bcs/docs/plans \
  src/bcs/crates/service-api/bcs-service-api \
  src/bcs/crates/application/v1/bcs-app-session \
  src/bcs/crates/adapters/ws/bcs-ws
git commit -m "fix(bcs): revalidate v1 websocket session access"
```

### Task 7: Add the session-bound WebSocket Upgrade route

**Files:**

- Create: `src/bcs/crates/adapters/ws/bcs-ws/src/web/group_session.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/mod.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/Cargo.toml`
- Create: `src/bcs/crates/adapters/ws/bcs-ws/tests/group_session_ws.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/CONTEXT.md`

**Step 1: Write failing Upgrade tests**

Build an adapter router with a fake `GroupSessionConnectionService` and shared Workbench state. Verify:

- missing, malformed, forged, expired, or wrong-purpose token returns HTTP 401 before Upgrade;
- unavailable verifier returns HTTP 503;
- the verified token is the only source of `tenant`, `uid`, `gid`, and `sid` because the route carries no session selector;
- valid binding upgrades and enters the same `handle_client_connection` path used by `/ws`;
- token values never appear in response bodies, close reasons, logs, or metrics;
- an already-upgraded socket remains alive after the token's `exp`.

**Step 2: Run the tests to verify they fail**

```bash
cargo test --package bcs-ws --test group_session_ws --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the route builder is missing.

**Step 3: Implement pre-Upgrade verification and shared handling**

Add a handler for `GET /openapi/v1/collaboration/messages/ws?token=...`. Extract and validate the query credential before calling `on_upgrade`. Map only the approved status classes and use sanitized messages. Convert the application binding to `WorkbenchConnectionAuth::SessionBound`, with `actor_id = format!("human_{}", binding.user_id)`, then call the existing shared Workbench connection handler.

Do not add a second dispatcher, registry, idle timer, or frontend delivery path.

**Step 4: Run focused and regression tests**

```bash
cargo test --package bcs-ws --test group_session_ws --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/ws/bcs-ws
git commit -m "feat(bcs): add session-bound workbench websocket"
```

### Task 8: Resolve the signing key from the environment and compose Bootstrap

**Files:**

- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/plugins/bcs-secret-local/src/lib.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/integration_http_api.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/e2e_workbench.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`

**Step 1: Write failing secret-resolution and composition tests**

Extend the existing `EnvSecretAccess` tests with the exact production mapping:

```rust
#[tokio::test]
async fn resolves_group_session_ws_key_from_fixed_environment_name() {
    let env = HashMap::from([(
        "BCS_SECRET_BCN_GROUP_SESSION_WS_JWT".to_string(),
        "test-only-key".to_string(),
    )]);
    let access = EnvSecretAccess::from_map("BCS_SECRET_", env);
    let result = access.get_secret("bcn-group-session-ws-jwt").await;
    assert!(matches!(result, Ok(secret) if secret.value == "test-only-key"));
}
```

Add Bootstrap tests proving that missing or empty `BCS_SECRET_BCN_GROUP_SESSION_WS_JWT` fails startup, a non-empty value constructs the group-session JWT service, both BCN routes are mounted, and existing `/ws` still resolves. Assert that the key is never included in formatted errors or configuration logs.

**Step 2: Run tests to verify they fail**

```bash
cargo test --package bcs-secret-local env_plugin --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test integration_http_api group_session --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test e2e_workbench group_session --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL because the exact environment mapping and Bootstrap wiring are absent.

**Step 3: Compose the approved services**

At bootstrap only:

1. resolve the Gateway Principal verification key and build `GatewayPrincipalTokenVerifier` with issuer `gateway`, audience `bcs`, and configured `kid`;
2. construct `EnvSecretAccess::new("BCS_SECRET_")`, request `bcn-group-session-ws-jwt`, reject missing or empty material, and build `GroupSessionJwtService`;
3. build `GroupSessionConnectionServiceImpl` using the V1 session service and token port;
4. inject the connection service into the HTTP and WS adapters;
5. mount the focused V1 token route and session WebSocket route without unintentionally exposing unrelated preparatory V1 routes;
6. retain current `/ws` and `/ws/bot` mounting and auth behavior.

The 300-second TTL remains a code-level protocol constant, not an environment-controlled value. Do not read environment variables in adapters, application services, or `bcs-jwt`. Do not provide a fallback secret. A test fixture may inject `InMemorySecretAccess` or `EnvSecretAccess::from_map` with an explicit test-only value.

**Step 4: Run bootstrap tests**

```bash
cargo test --package bcs-secret-local --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test integration_http_api --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test e2e_workbench --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test e2e_ws_messaging --manifest-path src/bcs/Cargo.toml
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs src/bcs/crates/plugins/bcs-secret-local/src/lib.rs
git commit -m "feat(bcs): wire group session websocket authentication"
```

### Task 9: Route both planes through the Python Gateway

**Files:**

- Modify: `src/gateway/configs/application.yaml`
- Modify: `src/gateway/tests/test_domain_map.py`
- Modify: `src/gateway/tests/test_route_security.py`
- Modify: `src/gateway/tests/test_log_redaction.py`
- Modify: `src/gateway/tests/integration/test_relay_ws_route.py`
- Modify: `src/gateway/tests/integration/test_forward_signs_principal.py`

**Step 1: Write failing Gateway tests**

Require:

- a `bcs_server_url` upstream variable and `bcs` server;
- `collaboration` domain with `protocols: [http, websocket]`;
- `POST /openapi/v1/collaboration/sessions/{sid}/token` to require a Gateway Human, forward a signed Principal with audience `bcs`, and arrive at the identical BCN path;
- `GET /openapi/v1/collaboration/messages/ws` to have an explicit empty Gateway requirement, work without browser-supplied headers, and arrive at the identical BCN path;
- the more-specific WS rule to beat the general authenticated collaboration rule;
- `token=...` to be redacted in request/relay/error logs, including percent-encoded query cases;
- no path or query rewriting beyond changing the upstream origin.

**Step 2: Run tests to verify they fail**

```bash
cd src/gateway
uv run pytest tests/test_domain_map.py tests/test_route_security.py tests/test_log_redaction.py tests/integration/test_relay_ws_route.py tests/integration/test_forward_signs_principal.py -q
```

Expected: FAIL because the collaboration domain and security exception are absent.

**Step 3: Add the minimal Gateway configuration**

Add:

```yaml
upstream_vars:
  bcs_server_url: https://bcs.sample.com

route_security:
  "/openapi/v1/collaboration/**":
    user: required
  "POST /openapi/v1/collaboration/sessions/{sid}/token":
    user: required
  "GET /openapi/v1/collaboration/messages/ws": {}

upstreams:
  domains:
    collaboration:
      server: bcs
      protocols: [http, websocket]
  servers:
    bcs:
      base_url: "${bcs_server_url}"
```

Keep Nginx/L7 responsible only for TLS and passing Upgrade headers. The WebSocket relay remains in the Python Gateway application.

The current redactor already treats query keys containing `token` as sensitive; add explicit regression coverage rather than a second redaction mechanism unless the test exposes a real gap.

**Step 4: Run Gateway tests**

Run the command from Step 2, then the full Gateway suite required by its module instructions.

Expected: PASS.

**Step 5: Commit**

```bash
git add src/gateway/configs/application.yaml src/gateway/tests
git commit -m "feat(gateway): relay group session websocket"
```

### Task 10: Add parity, security, and end-to-end contract coverage

**Files:**

- Create: `src/bcs/crates/bootstrap/bcs/tests/e2e_group_session_ws.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/metrics_cardinality.rs`
- Modify: `src/bcs/docs/BCS.md`
- Create: `src/bcs/docs/protocols/group-session-workbench-websocket.md`
- Modify: relevant `CONTEXT.md` files touched above

**Step 1: Write the failing parity matrix**

Run the same Workbench frame cases through `/ws` and the new session-bound route, then compare successful response/event envelopes for:

- connect and registry subscription;
- chat.send and streamed bot/frontend events;
- chat.abort;
- run lifecycle events;
- ping/pong and idle timeout;
- malformed/unknown frames;
- disconnect cleanup and reconnect;
- concurrent sockets using one still-valid token for the same session.

Add negative cases for cross-session replay, permission revoked between issuance and connect, token expiration before reconnect, and an established connection surviving token expiration.

Add metrics assertions proving token/uid/gid/sid are absent from labels and cardinality remains bounded.

**Step 2: Run the tests to verify any missing behavior fails**

```bash
cargo test --package bcs --test e2e_group_session_ws --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --test web_frame_compat --manifest-path src/bcs/Cargo.toml
```

Expected: FAIL for any parity or security case not yet implemented.

**Step 3: Fix only the exposed gaps and document the contract**

Do not create new protocol behavior. Route all fixes through the existing Workbench implementation. Document:

- issuance and connection sequence;
- exact JWT claims and 300-second semantics;
- single-session scope and multi-connection reuse;
- Upgrade and connect-time authorization split;
- protocol error codes and close behavior;
- Gateway `/openapi/v1` prefix versus logical BCN suffix;
- credential redaction and key separation;
- compatibility guarantee for `/ws` and `/ws/bot`.

**Step 4: Run the affected workspace gates**

```bash
cargo fmt --all --manifest-path src/bcs/Cargo.toml -- --check
cargo test --package bcs-service-api --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-secret-local --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-jwt --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-app-session --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-api-http --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-message-flow --manifest-path src/bcs/Cargo.toml
cargo test --package bcs-ws --manifest-path src/bcs/Cargo.toml
cargo test --package bcs --test e2e_group_session_ws --manifest-path src/bcs/Cargo.toml
cargo check --workspace --all-targets --manifest-path src/bcs/Cargo.toml
cd src/gateway && uv run pytest -q
```

If time permits and the focused suites pass, run:

```bash
cargo test --workspace --manifest-path src/bcs/Cargo.toml
```

Expected: PASS. Record any suite that cannot run and the exact reason.

**Step 5: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs/tests src/bcs/crates/adapters/ws/bcs-ws/tests src/bcs/docs src/bcs/crates/bootstrap/bcs/CONTEXT.md src/bcs/crates/adapters/ws/bcs-ws/CONTEXT.md src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md src/bcs/crates/application/v1/bcs-app-session/CONTEXT.md src/bcs/crates/service-api/bcs-service-api/CONTEXT.md src/bcs/crates/services/bcs-jwt/CONTEXT.md
git commit -m "test(bcs): verify group session websocket parity"
```

### Task 11: Rename the public collaboration message WebSocket path

**Files:**

- Modify: `src/bcs/tests/openapi/test_group_session_connection_contract.py`
- Modify: `src/bcs/tests/openapi/test_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/README.md`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/group_session.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/group_session_ws.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`
- Modify: `src/gateway/configs/application.yaml`
- Regenerate: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: relevant BCS and Gateway tests, `CONTEXT.md` files, and current P0 plans containing the old path

**Step 1: Point tests at the approved path**

Change contract, runtime, Gateway security, log-redaction, relay, live-forwarding,
and served-OpenAPI expectations to
`/openapi/v1/collaboration/messages/ws`. Assert that the old
`/openapi/v1/collaboration/group/ws` path is absent from the public contract and
does not mount in BCN.

**Step 2: Run focused tests to verify they fail**

```bash
uv run --with pytest --with pyyaml pytest \
  src/bcs/tests/openapi/test_group_session_connection_contract.py \
  src/bcs/tests/openapi/test_contract.py -q
CARGO_SHIM_SKIP_CLEAN=1 cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs-ws --test group_session_ws
CARGO_SHIM_SKIP_CLEAN=1 cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs --test openapi_v1_mount
cd src/gateway && uv run pytest \
  tests/test_domain_map.py tests/test_route_security.py \
  tests/test_log_redaction.py tests/integration/test_relay_ws_route.py -q
```

Expected: FAIL because the authoritative YAML, Axum route, Gateway exception,
and published schema still use the old path.

**Step 3: Rename the contract and runtime path without an alias**

Update the authoritative OpenAPI path, the single Axum route constant, and the
Gateway anonymous WebSocket exception. Keep token issuance at
`/openapi/v1/collaboration/sessions/{session_id}/token`, keep the session-bound
claims and message protocol unchanged, and do not add a rewrite or compatibility
alias. Regenerate the deterministic schema with:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  src/gateway/configs/schemas/bcn.openapi.json
```

Update current documentation to explain that `/messages/ws` is the WebSocket
message channel and reserves `/messages/sse` for a future SSE sibling.

**Step 4: Run focused and cross-module verification**

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
CARGO_SHIM_SKIP_CLEAN=1 cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs-ws --test group_session_ws
CARGO_SHIM_SKIP_CLEAN=1 cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs --test openapi_v1_mount
cd src/gateway && uv run pytest -q
bash src/gateway/scripts/test_live_bcs_forwarding.sh
git diff --check
```

Expected: PASS. A repository search across current BCS/Gateway source, tests,
configuration, generated schema, and active plans finds no old public path.

**Step 5: Commit**

```bash
git add src/bcs src/gateway
git commit -m "fix(gateway): rename the BCN message websocket path"
```

## Frontend adoption follow-up

The BCN and Gateway contract is complete without changing the current frontend in the same patch. Migrate the frontend separately after deployment routing is available:

1. request a fresh token for the active `sid`;
2. open the returned session-scoped WebSocket URL;
3. send the existing Workbench `connect` frame with the exact `gid` and `sid`;
4. on reconnect after token expiry, request a new token;
5. keep one provider/socket cache entry per `gid:sid`;
6. retain a rollout fallback to `/ws` until parity telemetry is stable.

This separate rollout avoids coupling the server security contract to frontend release timing while preserving the requirement that a successful new connection is functionally equivalent to `/ws`.

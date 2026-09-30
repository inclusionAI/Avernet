# V1 Authentication HTTP Contract

- 日期：2026-09-18
- 状态：Implemented（this branch, security upgrade introduced by
  the 2026-09-18 v1-api-auth-plugin-chain design）
- Owner：BCS
- 范围：`/openapi/v1/auth/*` 路由及其 V1 OpenAPI 装配；不含旧 `/auth/*`
  与内部 SDK 适配，旧入口在本期一并安全收紧
- 绑定语义：[spec
  §4](../../../docs/superpowers/specs/2026-09-18-v1-api-auth-plugin-chain-design.md)
  (配置契约)、[spec
  §8.2–§8.6](../../../docs/superpowers/specs/2026-09-18-v1-api-auth-plugin-chain-design.md)
  (HTTP 行为与 OAuth 生命周期)、[spec
  §10](../../../docs/superpowers/specs/2026-09-18-v1-api-auth-plugin-chain-design.md)
  (验收标准)
- HTTP 契约文件：[../openapi/auth.yaml](../openapi/auth.yaml) +
  [../openapi.yaml](../openapi.yaml) 主索引
- 上游 Gateway Principal 契约：[../gateway-principal/contract.md](../gateway-principal/contract.md)

This document is the binding HTTP behavior contract for the V1 auth
facade. The OpenAPI YAML is the wire schema; this document is the
security变迁 narrative — what changed, what must not regress, and how
the deployment is allowed to roll out. When the YAML and this narrative
disagree, the YAML is the truth for wire shape; this narrative is the
truth for security behavior and rollout constraints.

## 1. Source precedence and chain semantics (spec §7.1)

`[api.auth].chain` carries an ordered list of authentication sources.
The chain executes strictly in configuration order; the legacy
`AuthPlugin::priority()` is NOT consulted. Per-source outcomes:

| Source result | HTTP behavior |
| --- | --- |
| NotApplicable: no credential for this source | continue next source |
| Authenticated: valid complete identity | use the result; STOP |
| InvalidCredential: bad/expired/revoked credential | `401`, no fallback |
| Forbidden: valid identity explicitly denied by source policy | `403`, no fallback |
| Unavailable: storage/secret/external dependency fault | `503`, no fallback |
| Internal conversion or contract violation | `500`, sanitized log, no fallback |
| All sources NotApplicable | `401` |

The first success stops evaluation. Spec §7.1 explicit warning: a
chain with `gateway` first will not check OAuth cookies after a valid
Gateway header; a chain with OAuth first will not fall back to Gateway
after a valid cookie. The inverse of "any source passes" is NOT the
contract — earlier failures cannot be retried against a later
source.

Disabled sources do not participate; their credentials do not
trigger implicit fallback. Duplicate/blank/malformed credentials of
an enabled source are NOT "credential missing"; they are rejected.
Once an OAuth source is reached, any `Cookie` header that cannot be read as
ASCII is `InvalidCredential` (401), even if another Cookie header is readable.
The verifier cannot safely conclude that the unreadable header lacks a
session carrier. V1 and legacy refresh/logout use the same fail-closed rule;
malformed carriers never enter the no-cookie logout path and cause no cookie
changes or session writes. A successful earlier non-cookie source still
stops the chain before OAuth is consulted.

## 2. Per-credential-kind provenance (spec §7.4)

The delivery adapter exposes `VerifiedRequestIdentity` to the HTTP
layer (NOT directly to the application). It carries:

- `caller: AuthenticatedCaller` — the business identity (unchanged).
- `authentication_context.source` — the configured source name that
  succeeded.
- `authentication_context.credential_kind` — the concrete carrier:
  `GatewayPrincipalHeader`, `OAuthSessionCookie`, or `SsoCookie` for
  extension sources.

Multi-carrier sources MUST report the ACTUAL carrier used on this
request; not a static name. The HTTP layer decides CSRF/Origin policy
against this concrete `credential_kind`. Application/core never
consume `source` or `credential_kind`.

## 3. Cookie protocol

### 3.1 Browser-binding challenge cookie (`__Host-bcs_oauth_login`)

`GET /openapi/v1/auth/url` issues a temporary
`__Host-bcs_oauth_login` cookie via `Set-Cookie` on every response (spec
§8.4):

- HttpOnly, Secure, SameSite=Lax, Path=/, Max-Age=300.
- Host-only. The `__Host-` prefix requires `Secure`, `Path=/`, no
  `Domain` — these constraints are honored.
- Carries a CSPRNG browser nonce (not the OAuth `state`; the two are
  independent draws).
- Local HTTP development is the ONLY environment permitted to use a
  non-Secure variant; production MUST use the `__Host-` prefix.
- The nonce never appears in the JSON `providers` list, the provider
  authorize URLs, logs, or query parameters.

`GET /openapi/v1/auth/url` carries `Cache-Control: no-store`, `Pragma:
no-cache` because the challenge cookie is unique per response and must
not be cached or replayed.

### 3.2 Session cookie (`bcs_session`)

`bcs_session` carries the signed session JWT. Issued by:

- `GET /openapi/v1/auth/callback/{provider}` on a fully-matching
  callback, alongside a cleared challenge cookie (multiple Set-Cookie
  headers — one fresh `bcs_session`, one cleared
  `__Host-bcs_oauth_login` `Max-Age=0`).
- `POST /openapi/v1/auth/refresh` on a successful atomic rotate.

Cleared on `POST /openapi/v1/auth/logout` (only when a session was
presented; the idempotent no-cookie logout does not emit a clear
header).

HttpOnly, Secure, SameSite=Lax. The signed JWT contains `sid`
(session_id), `revision` (session_revision), provider, env, user_id,
exp, iat — see §4 below.

### 3.3 Multiple Set-Cookie handling

OpenAPI 3.1 only allows a single `Set-Cookie` header declaration in the
schema; the OpenAPI document describes the multiple-header behavior in
the response `Set-Cookie` description. The HTTP adapter emits
distinct `Set-Cookie` headers; clients MUST treat each as a separate
cookie change, never merge them.

## 4. Session model: `sid` / `revision` JWT schema (spec §8.5)

Each `(user_id, provider, env)` row in the identity store carries:

- `session_id`: a fresh random value per new login.
- `session_revision`: monotonically increasing; refresh keeps
  `session_id` and bumps `revision`.
- `token_hash`: SHA-256 over the signed JWT (NOT the JWT itself).
- `expires_at`.

The signed JWT in the `bcs_session` cookie contains `sid`, `revision`,
`provider`, `env`, `user_id`, `iat`, `exp`. The JWT is valid only when
`sid`/`revision`/`env` agree with the identity record. A JWT whose
`(sid, revision)` is older than the live identity record IS rejected
as `InvalidCredential` (401).

### 4.1 Single active session

A new login (`install_login_session`) replaces the previous active
instance for the scope: old `sid` becomes unrecoverable regardless of
JWT presentation. No "second active session" coexistence is permitted
under the same scope.

## 5. Error contract

Status mapping aligned with `bcs-api-http/src/v1/common/error.rs`:

| Status | When | Error codes |
| --- | --- | --- |
| 200 | URL discovery / login URL list | n/a |
| 302 | Successful callback | n/a (Location header) |
| 400 | Bad callback query / state mismatch / missing code | `invalid_request`, `invalid_state`, `provider_mismatch`, `missing_code` |
| 401 | Missing/malformed/expired/unbound/replaced session, no cookie on `/user`, all sources NotApplicable | `unauthenticated`, `session_conflict` (refresh CAS) |
| 403 | Verified identity without a Human on `/user`; disallowed provider; Origin/CSRF rejection on a cookie-backed unsafe request (spec §7.4 / S1, S2) | `forbidden` |
| 404 | OAuth not configured; unknown provider | `auth_not_configured`, `provider_not_found` |
| 500 | Internal contract violation; identity-corruption projection error | `internal_error`; specific corrupt-record codes for projection errors (per spec §8.6) |
| 503 | Storage / secret / external dependency fault during session verification, install, rotate, revoke, refresh, or logout | `unavailable` |

### 5.1 Refresh CAS-conflict → 401

A `POST /auth/refresh` whose atomic CAS `(scope, session_id,
expected_revision, expected_hash)` does not match returns `401` with
`session_conflict` — the session was already rotated, revoked, or
replaced by a newer login. The HTTP adapter clears the `bcs_session`
cookie on this path; the client MUST re-authenticate (no retry against
another source).

### 5.2 DB/dependency faults → 503

Spec §8.6 requires that a `DbPlugin` query/execute failure during
identity/session resolution maps through the strict identity port →
strict auth port → delivery `Unavailable` → HTTP `503 unavailable`.
This MUST NOT be folded into `401 unauthenticated` or `200` silence.
The conformance evidence (Task 13) covers `get_session_by_hash`,
`install_login_session`, `rotate_session`, `revoke_session`, and
`ensure_identity`.

### 5.3 Corrupt records → 500

A row whose projection contract is broken (unparseable `token_hash`,
missing required columns, etc.) is NOT the same as "no row" (which is
`401`) or "DB fault" (which is `503`). It is a typed corrupt-record
error that surfaces as `500` with a sanitized internal error code; no
SQL/credential detail is exposed.

### 5.4 Logout persistence failure → 503 (not 200)

Spec §8.5 explicitly forbids best-effort logout: a persistence failure
during the atomic revoke MUST NOT be reported as a successful
logout. The response is `503 unavailable`; no success envelope, no
promise the session is gone.

### 5.5 Current-user identity and terminal errors

`GET /openapi/v1/auth/user` returns `403 forbidden` when the verifier succeeds
but the caller has no Human identity, or when source policy disallows the
verified provider. This explicitly replaces the earlier OpenAPI description
that classified a verified non-Human caller as `401`. Invalid credentials
remain `401 unauthenticated`; a verifier dependency failure remains
`503 unavailable`; an internal verification failure remains `500 internal_error`.
None of these terminal outcomes may invoke the legacy compatibility projection.
Only `Missing` may do so when bootstrap has explicitly wired that projection.

This HTTP contract correction affects consumers of the V1 user-info endpoint:
clients must distinguish forbidden identity (403) from missing/invalid login
(401) and temporary dependency failure (503). `AuthUserPath` declares each
response with the unchanged V1 ErrorEnvelope. No Service API, Plugin API,
storage schema, or configuration change is needed for this correction.

## 6. Shared-writer rollout constraints (spec §9)

The atomic session model is a shared-contract upgrade; mixed old/new
binaries cannot coexist while the migration is in progress:

- Old unconditional `update_token(user_id, ...)` write entry points
  have been removed from every shared production path (callback,
  refresh, logout, AuthService, identity_wiring, Memory/SQLite/MySQL
  stores). The conformance evidence is in §7 below.
- A binary from before this branch still writes `update_token` without
  CAS — running it against a post-migration store re-introduces the
  "refresh(A→B) then logout(A) revokes C" race. DO NOT mix old and new
  binaries against the same database.
- Pre-migration JWTs without `sid`/`revision` are invalidated by this
  upgrade; users must re-authenticate. The spec's compatibility
  envelope (spec §8.5) explicitly permits this fail-closed invalidation.
- Rollout: deploy new binaries, drain in-flight requests, THEN cut
  over. Rollback must also block shared OAuth entrypoints if the
  downgrade target predates the fix.

A config-only switch (`[api.auth]` absent) is supported: the V1 chain
falls back to Gateway-only protected routes while STILL using the
fixed browser binding + atomic session model. Returning to an old
binary is the only state where the constraints above MUST be enforced
manually; the storage schema does not require migration.

### 6.1 Entry-point coexistence (review 2026-09-20 #4 — deployment constraint)

The legacy `/auth/*` entrypoint and the explicit `[api.auth]` chain each
assemble their own strict session engine: the legacy engine signs with
`[auth.oauth].jwt_secret`, the V1 chain with
`api.auth.session_signing_key_secret`. Both write the SAME `bcs_session`
cookie name against the SAME identity/session rows.

When both entrypoints are mounted AND the two signing secrets differ,
logins from one entrypoint overwrite the browser cookie with a token the
other engine cannot verify: the user experiences a spurious 401 and a
broken session after switching surfaces. Deployments MUST pick one:

- run only one entrypoint per cookie scope (browser origin), or
- configure the two secrets to resolve to the SAME signing material, or
- explicitly accept mutually-exclusive login states between the surfaces
  as a known product limitation.

Cross-entrypoint session interop with DIFFERENT secrets is NOT a supported
state of this iteration; a future revision may add a startup
conflict check or shared signing material if product requires interop.

### 6.2 Login-flow pinning (review 2026-09-20 #5 — deployment constraint)

The pending-login state issued by `GET .../auth/url` (browser nonce +
per-provider OAuth `state`) lives in PROCESS-LOCAL memory in this
iteration. A callback hitting a different instance finds no pending state
and the login fails closed (`invalid_state`). Deployments MUST therefore
either run the auth entrypoint single-instance, or guarantee sticky
routing that pins the whole login flow — URL issue, provider redirect,
callback — to the same instance. A shared `PendingOAuthLoginStore` is a
follow-up if horizontal scaling of the login path is required.

## 7. Conformance evidence cross-reference

The strict HTTP behaviors above are asserted by:

- `scripts/test_api_auth_contract.py` — structural assertions on the
  OpenAPI YAML for Set-Cookie, no-store, multiple-Set-Cookie on
  callback, CAS-conflict 401, 503 on refresh/logout, `invalid_state`
  on callback 400.
- `crates/bootstrap/bcs/tests/conformance_auth_session_identity.rs` —
  the bootstrap `RepoAuthSessionIdentityPort` bridge run against a
  real `LocalSqliteDbPlugin` + `DbUserIdentityStore`, with fault
  injection asserting every store operation surfaces as `Unavailable`
  rather than `None`/`Ok`.
- `crates/services/bcs-user-identity/tests/conformance_auth_session_*.rs`
  — Memory/SQLite/MySQL `AuthSessionRepoPort` conformance for the
  atomic session operations (install/rotate/revoke, including
  `Ok(0)`-for-missing-scope and `Conflict`/`NotCurrent` success-typed
  outcomes).
- `crates/test-support/bcs-test-support/src/contract/plugin/pending_oauth_login.rs`
  — `PendingOAuthLoginStore` cross-browser binding, batch atomic
  consume, expiry, provider/callback/flow mismatch, and nonce ≠ state
  behaviors (spec §8.4 matrix).

## 8. Differences from the legacy `/auth/*` facade

The legacy `/auth/*` OAuth entrypoint shares the same `bcs_session`
cookie and the same atomic session store under this upgrade. The
browser-binding challenge cookie and pending-login batch consume
semantics are shared with V1; only the `flow` namespace differs
(`legacy` vs `v1`). Cross-flow replay is rejected by design (callback
consumes only for its flow namespace).

See also [2026-08-28-openapi-auth-facade.md](../../../docs/superpowers/specs/2026-08-28-openapi-auth-facade.md)
for the prior facade design — its earlier "Gateway 缺失时回退 cookie" and
"best-effort logout" descriptions have been corrected this branch; the
security behavior described by THIS contract is the truth.

## 9. What this contract deliberately does NOT promise

- Account binding / merge across providers (spec §7.3).
- A `require_authentication = false` mode for protected routes; the
  chain is always required.
- Hot reload of `[api.auth]` configuration. Restart required.
- A transparent upgrade for pre-migration JWTs without `sid`/`revision`.
  They are rejected, requiring re-login.
- Cross-tenant session coexistence. The single-active-session model is
  keyed by `(user_id, provider, env)`; multi-tenant isolation is not
  added by this contract.

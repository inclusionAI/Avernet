# Password Authentication (Register + Login) — Design

- Date: 2026-08-10
- Status: Approved (pending spec review)
- Scope: BCS (`src/bcs`)
- Original requirement: 新增账密登录和账密用户注册功能;用户注册后落存储,之后可通过账密登录;登录后通过 cookie / Authorization 存入登录 token,后续操作可从 token 验证用户是否登录并提取用户信息。

## 1. Problem

BCS already has a full human-login system for OAuth providers (Google, GitHub, WeChat,
Alipay): it issues a `bcs_session` JWT, binds its SHA-256 hash in `bcs_user_identities`,
and the `OAuthSessionPlugin` verifies it on every request. There is **no** username/password
registration or login, and **no** password storage.

We need self-service username/password auth that:
1. Persists a registered user to storage.
2. Issues a login token on `POST /auth/login`.
3. Delivers the token via **both** cookie (`Set-Cookie: bcs_session`) and JSON body (so
   non-browser clients can use `Authorization: Bearer <jwt>`).
4. Lets every subsequent request verify the token — from **either** the cookie **or** the
   `Authorization` header — to confirm the user is logged in and extract their info.

## 2. Goals & Non-Goals

### Goals
- `POST /auth/register { username, password }` → create user, persist, issue session token.
- `POST /auth/login { username, password }` → verify credentials, issue session token.
- Token delivered in cookie **and** JSON body.
- Read path accepts `bcs_session` cookie **or** `Authorization: Bearer <jwt>`.
- Reuse the existing identity / JWT / cookie / revocation machinery — no parallel session system.
- Works in local dev (SQLite) **without** any OAuth provider configured.

### Non-Goals (YAGNI — out of scope, layer later)
- Email verification, password reset / "forgot password".
- Invite / admin-gated registration (registration is openly self-service).
- Account lockout, rate limiting, brute-force protection beyond argon2's cost.
- Multi-session (single active session per user, same as OAuth today).
- "Remember me" / long-lived tokens; refresh-on-header.
- Username rename (usernames are immutable).
- A new `[auth.session]` config section — we reuse `[auth.oauth]` session fields (see §10).

## 3. Existing Infrastructure (reused)

| Concern | Existing asset | Location |
|---|---|---|
| Identity row | `bcs_user_identities(user_id, auth_source, external_user_id, user_name, external_user_name, avatar, token, token_expire_at, env)` keyed by `(auth_source, external_user_id, env)` | MySQL `migrations/mysql/001_init_schema.sql:657`; SQLite DDL `bootstrap/bcs/src/migrations.rs:239` |
| Auth-layer identity port | `bcs_auth_api::UserIdentityPort` (`ensure_identity`, `get_identity_by_token`, `update_token`, …) | `plugin-api/bcs-auth-api/src/port.rs:51` |
| Persistence port | `bcs_service_api::UserIdentityRepoPort` | `bcs-service-api` |
| Identity store | `DbUserIdentityStore` (MySQL + SQLite) + `MemoryUserIdentityRepo` | `services/bcs-user-identity/src/lib.rs` |
| Identity wiring (port adapter) | `RepoUserIdentityPort` | `bootstrap/bcs/src/identity_wiring.rs` |
| Session JWT (HS256) | `JwtService::sign/verify/verify_no_exp`, `Claims { sub, src, iat, exp }`, `token_hash` (SHA-256 hex) | `services/bcs-jwt/src/lib.rs` |
| Session cookie | `bcs_session`; `extract_session_cookie`; `session_cookie()` / `clear_session_cookie()` helpers | `plugin-api/bcs-auth-api/src/cookie.rs`; `adapters/http/bcs-http/src/oauth/mod.rs:259` |
| Read-path plugin | `OAuthSessionPlugin` (chain name `oauth_session`, priority 25) → `verify_oauth_session` (cookie → verify → hash-bind → `AuthPrincipal`) | `plugins/bcs-auth-oauth/src/{plugin,verify}.rs` |
| Auth chain | `AuthPluginChain` (priority sorted, first-writer-wins) | `plugin-api/bcs-auth-api/src/chain.rs` |
| Single-point logged-in verdict on business routes | `ChainUserIdentityPort::extract` runs the chain and maps `principal.user_id` → `HttpUserIdentity.staff_no` | `adapters/http/bcs-http/src/state.rs:104` |
| `/auth/*` delivery state | `OAuthRouteState { jwt_service, user_port, providers, config, auth_chain }` | `adapters/http/bcs-http/src/oauth/mod.rs:29` |
| Auth chain wiring | `build_builtin_auth_plugin` (`session`, `local`, `oauth_session`) | `bootstrap/bcs/src/auth_wiring.rs:140` |

Key implication: because password login will issue a `bcs_session` JWT of **identical wire
shape** to OAuth (only `Claims.src = "password"` differs), the existing `OAuthSessionPlugin`
verifies password sessions with **no per-source logic**. Password users are detected as
logged-in on business routes through `ChainUserIdentityPort::extract`, which delegates to the
same chain — so **no new read-path code is required for the cookie case**. The only read-path
change is making the plugin also accept `Authorization: Bearer` (§8).

## 4. Architecture & Layering

Follows the BCS canonical call direction. New pieces, by layer:

```
POST /auth/register, POST /auth/login        adapters/http/bcs-http (oauth module)
        │  delivery adapter — owns JSON/cookie proto; maps app errors → HTTP
        ▼
PasswordAuthService (application use case)    bcs_service_api::application (impl in application/v1)
        │  orchestrates: validate → ensure_identity → store/verify password → sign JWT → bind hash
        ▼
UserIdentityRepoPort (existing)  +  UserCredentialRepoPort (new, port::repo)
        │  persistence contracts
        ▼
bcs-user-identity store (extended)            services/*-store — owns SQL for both tables
        ▼
plugin-api/bcs-db-api, bcs-cache-api         infra (unchanged)
```

Layer rules respected:
- Delivery adapter owns HTTP/cookie protocol; calls the application service only; maps
  application errors to `HttpAdapterError`; never exposes core errors.
- `PasswordAuthService` is application use-case orchestration — not a re-export of a core
  service. It depends on `UserIdentityPort` + `UserCredentialRepoPort` + `JwtService`.
- `UserCredentialRepoPort` is a persistence port under `port::repo`, implemented by the store
  crate; core impls hold `Arc<dyn *Repo>`, no direct DB-plugin dependency.
- No `cargo fmt`. No `unwrap`/`expect`/`unsafe`. UTF-8-safe string handling.

## 5. Data Model

New table `bcs_user_credentials`, keyed by `user_id` (the 12-char base62 id from
`bcs_user_identities`), **carrying `username`** so login is a single indexed lookup:

```sql
-- migrations/mysql/009_add_user_credentials.sql
CREATE TABLE IF NOT EXISTS `bcs_user_credentials` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` varchar(32) NOT NULL COMMENT '关联 bcs_user_identities.user_id',
  `username` varchar(64) NOT NULL COMMENT '登录用户名(不可变)',
  `password_hash` varchar(256) NOT NULL COMMENT 'argon2 PHC 串(含 salt+params)',
  `env` varchar(64) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_creds_user` (`user_id`, `env`),
  UNIQUE KEY `uk_user_creds_username` (`username`, `env`),
  KEY `idx_user_creds_env` (`env`)
) DEFAULT CHARSET = utf8mb4;
```

SQLite mirror (TEXT types, two `CREATE UNIQUE INDEX`) added to `SQLITE_DDL_STATEMENTS` in
`bootstrap/bcs/src/migrations.rs`, plus a version-9 entry in
`SQLITE_VERSIONED_MIGRATIONS` with a no-op body (table created by DDL, mirroring how
migration 8 is recorded). Forward-only, DDL only, no seed data.

### Why `username` lives in two places
- `bcs_user_identities.external_user_id = <username>` (`auth_source = "password"`) — needed
  because the read path (`get_identity_by_token`) and `GET /auth/user` resolve display info
  from the identity row, generically across all auth sources.
- `bcs_user_credentials.username` — so `POST /auth/login` is a **single** indexed query
  (`username, env → user_id, password_hash`) yielding everything needed to verify and to
  sign the JWT (`sub = user_id`).

This duplication is benign: usernames are immutable, so the two never diverge. Uniqueness is
enforced by both `uk_external(auth_source, external_user_id, env)` on the identity table and
`uk_user_creds_username(username, env)` on the credential table — they are consistent because
they describe the same `(password, username, env)` tuple.

### Why a separate table (vs a `password_hash` column on `bcs_user_identities`)
Credentials are isolated from display rows; OAuth users never carry a null password column;
`password_hash` can be rotated / locked independently of identity display fields.

## 6. Password Hashing

- Add `argon2 = "0.5"` to `[workspace.dependencies]` in `src/bcs/Cargo.toml`; depend on it
  from the crate that implements `PasswordAuthService` (hashing is a service-layer concern,
  not the store's — the store only persists the opaque PHC string).
- Hash with **Argon2id** (default recommended params). Store the PHC string (self-describing:
  salt + params + hash) in `password_hash`; no separate salt column.
- Verify via argon2's PHC-parsed compare (constant-time).
- `sha2` (already in the workspace) is **not** appropriate for passwords; argon2 is the
  modern standard.

## 7. New Ports & Service

### `UserCredentialRepoPort` (new — `bcs_service_api::port::repo`)
```rust
#[async_trait]
pub trait UserCredentialRepoPort: Send + Sync {
    /// Insert a credential. Error on duplicate (username, env) or (user_id, env).
    async fn create_credential(
        &self, user_id: &str, username: &str, password_hash: &str, env: &str,
    ) -> Result<(), String>;

    /// Single indexed lookup used by login. Returns (user_id, password_hash).
    async fn find_for_login(
        &self, username: &str, env: &str,
    ) -> Result<Option<(String, String)>, String>;
}
```
- Implemented by `DbUserIdentityStore` (MySQL + SQLite flavors) alongside the existing
  `UserIdentityRepoPort` impl, plus a `MemoryUserCredentialRepo` for tests (mirroring
  `MemoryUserIdentityRepo`).
- Lives in `services/bcs-user-identity/src/lib.rs` (extended) — same crate, same SQL ownership.

### `PasswordAuthService` (new — application layer)
Trait in `bcs_service_api::application`; implemented in a new
`crates/application/v1/bcs-app-auth` crate (registered in the workspace).

```rust
pub struct PasswordLoginResult {
    pub user_id: String,
    pub username: String,
    pub token: String,       // raw JWT, also placed in Set-Cookie by the adapter
    pub expires_at: u64,     // unix epoch seconds (same unit as JWT `exp`)
}

#[async_trait]
pub trait PasswordAuthService: Send + Sync {
    async fn register(
        &self, username: &str, password: &str, env: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError>;

    async fn login(
        &self, username: &str, password: &str, env: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError>;
}

pub enum PasswordAuthError {
    ValidationFailed(String),   // weak password / invalid username
    UsernameTaken,              // 409
    InvalidCredentials,         // 401 (unknown user OR wrong password — same message)
    Storage(String),            // 500
}
```

Internal flow:
- **register** (implicitly logs the user in — returns a usable token + the adapter sets the
  cookie, same as login): validate username/password strength → `ensure_identity("password", username, Some(username), None, env)` → argon2 hash → `create_credential(user_id, username, hash, env)` → `JwtService::sign(Claims { sub: user_id, src: "password", iat, exp })` → `update_token(user_id, token_hash(jwt), exp)`.
- **login**: `find_for_login(username, env)` → argon2 verify → `JwtService::sign(...)` → `update_token(...)`.
- `env`, `jwt_secret`, `idle_timeout_secs`, `cookie_secure` come from the existing
  `OAuthConfig` (resolved from `[auth.oauth]`), passed into the service / adapter.

### Security note on register/login error mapping
- `register` returns `UsernameTaken` (409) only when the username is already taken — needed
  so the user can pick another. (An attacker can enumerate usernames via 409; acceptable per
  non-goals — lockout/rate-limiting is out of scope.)
- `login` returns `InvalidCredentials` (401) for **both** unknown-user and wrong-password,
  with the same generic message, to avoid user enumeration on the login path.

## 8. HTTP Endpoints (delivery adapter)

Extend `OAuthRouteState` (`adapters/http/bcs-http/src/oauth/mod.rs`) with the
`PasswordAuthService` (and the credential port it needs). Add two routes via the existing
`routes()` builder (mounted per §10):

### `POST /auth/register`
- Request: `{ "username": string, "password": string }`
- Success `200`: `Set-Cookie: bcs_session=<jwt>; HttpOnly; SameSite=Lax; Path=/` (+ `; Secure`
  when `cookie_secure`) and JSON `{ "user_id", "username", "token", "expires_at" }`.
- Errors: `400` ValidationFailed, `409` UsernameTaken, `500` Storage.
- Validation constants (not config — YAGNI): username `^[A-Za-z0-9_-]{3,32}$`; password ≥ 8 chars.

### `POST /auth/login`
- Request: `{ "username": string, "password": string }`
- Success `200`: same cookie + JSON `{ "user_id", "username", "token", "expires_at" }`.
- Errors: `401` InvalidCredentials, `400` malformed body, `500` Storage.
- The raw token is returned in the body so non-browser clients can set
  `Authorization: Bearer <jwt>`.

Both reuse the existing `session_cookie(jwt, secure)` helper. Cookie path is `/` (same as
OAuth), so the same cookie is sent to every BCS route and the chain verifies it.

## 9. Read Path — "How we judge that a password user is logged in"

The verdict is **uniform across all auth sources**:

```
auth_chain.authenticate(headers) returns Some(principal) with non-empty principal.user_id
  ⇒ logged in
otherwise ⇒ not logged in (401)
```

The auth source (`"password"` vs OAuth provider) is carried in `claims.src` /
`AuthPrincipal.source_name` but does **not** affect the logged-in verdict.

### Login token delivery
`POST /auth/login` success places the same `<jwt>` in two places:
- `Set-Cookie: bcs_session=<jwt>` (browser)
- JSON body `{ token: <jwt> }` → API/CLI clients put it in `Authorization: Bearer <jwt>`

### Path A — `GET /auth/user` ("who am I?")  (`oauth/mod.rs:396`)
```
current_user_handler → state.auth_chain.authenticate(headers)
  → OAuthSessionPlugin (priority 25; first-writer-wins)
     verify_oauth_session:
       1. extract JWT: cookie first, else Authorization Bearer   ← the one read-path change (§9.1)
       2. JwtService::verify(jwt)            HMAC sig + exp
       3. token_hash(jwt) → get_identity_by_token(hash)
            match against bcs_user_identities.token              ← single-session + revocation
       4. confirm info.user_id == claims.sub
       5. build AuthPrincipal { source_name = "password", user_id, user_name, avatar }
  → user_id non-empty ⇒ 200 + user info; else 401
```

### Path B — any protected business route (e.g. `POST /groups`, `GET /groups/my`)  (`routes/caller.rs`)
```
handler → require_caller_actor_id_from_headers(state, headers, uri)
  → caller_actor_id_from_headers:
       1. state.bot_uuid_from_headers(headers)        ← bot token? no (human JWT) → skip
       2. state.user_identity.extract(headers, uri)
            → ChainUserIdentityPort::extract (state.rs:110)
                 self.chain.authenticate(headers)     ← the SAME auth chain
                 principal → HttpUserIdentity { staff_no: principal.user_id,
                                                nick_name: principal.user_name }
       3. staff_no non-empty ⇒ return "human_{user_id}"
  → actor_id present ⇒ logged in, proceed; else 401
```

### 9.1 The one read-path change: accept `Authorization: Bearer`
Today `OAuthSessionPlugin::can_authenticate` only checks `extract_session_cookie`. To support
non-browser clients, extend `verify_oauth_session` + `can_authenticate` to fall back to a
bearer token when no cookie is present:
- Add a small `extract_bearer_token(headers)` to `plugin-api/bcs-auth-api` (next to
  `extract_session_cookie`). The existing helper in `adapters/http/bcs-http/src/headers.rs`
  is reused/deduplicated to call this.
- Token-extraction order in `verify_oauth_session`: **cookie first, then Bearer**. Same
  `JwtService::verify` + `get_identity_by_token` hash-bind path afterwards. No behavior change
  for existing OAuth cookie clients.
- `POST /auth/logout` is similarly extended to read the token from either source before
  revoking (so a header-only client can log out).

### 9.2 Single-session + revocation (already provided by the hash-bind)
Step 3 of `verify_oauth_session` compares the presented JWT's SHA-256 against the stored
`bcs_user_identities.token`:
- Login elsewhere → `update_token` overwrites with the new hash → old JWT no longer matches →
  judged not logged in.
- `POST /auth/logout` → `update_token(user_id, "", 0)` clears the hash → all of that user's
  JWTs immediately fail the match.

### 9.3 Extracting user info (after logged-in)
- `AuthPrincipal.user_id` (= JWT `sub`, 12-char internal id).
- `AuthPrincipal.user_name` (from `bcs_user_identities.user_name`, initialized to the username
  at registration).
- `AuthPrincipal.avatar` (from the identity row; `None` for password users unless later set).
- Business routes consume these as `human_{user_id}` (the actor id); `GET /auth/user` returns
  them directly.

## 10. Wiring & Config

### Mounting
`build_auth_router()` in `bootstrap/bcs/src/server.rs` currently branches on whether OAuth
**providers** are configured (full OAuth routes vs identity-only). Change the condition so
that `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/user` are
mounted **whenever a session JWT secret is configured** (`config.oauth` present with a
non-empty `jwt_secret`), regardless of whether any OAuth provider is present. `base_url` is
only used for OAuth redirect URIs and may stay empty for password-only deployments.

### Config
Reuse `[auth.oauth]` session fields as the session-JWT config for password login too (they
already hold `jwt_secret`, `idle_timeout_minutes`, `cookie_secure`, `env`):

- `configs/bcs-config-local.toml`: add an `[auth.oauth]` section with a dev `jwt_secret`,
  `idle_timeout_minutes`, `cookie_secure = false`, `env = "dev"`; add `"oauth_session"` to
  `chain` (so password-issued JWTs are verified on subsequent requests). Local secret is a
  clearly-marked placeholder (e.g. `"local-development-only-token-secret"`).
- **Crucially, unset `mock_user_id`** in the local `[auth]` section (and leave
  `allow_mock_headers = true`). `LocalAuthPlugin` (priority 5) with a config `mock_user_id`
  set has `can_authenticate == true` on every request and would shadow `oauth_session`
  (priority 25), so password sessions could never resolve. With `mock_user_id` unset, `local`
  is header-gated (only fires when `X-Mock-User-Id` is present) and stays out of the way of
  normal cookie/bearer requests.
- `configs/bcs-config-example.toml`: document the same fields; note that `jwt_secret` may be
  set without any `[auth.oauth.providers.*]` to enable password-only login; reference the
  secret by env-var name per repo convention, never inline real secrets.

### Chain
To verify password sessions, the chain must include `oauth_session`. `oauth_session` is built
only when `config.oauth` (jwt_secret) + `user_identity_port` are present
(`auth_wiring.rs:157`) — both satisfied once `[auth.oauth].jwt_secret` is set. No change to
`auth_wiring.rs` logic; only the config values change.

## 11. Error Handling

- Adapter maps `PasswordAuthError` → `HttpAdapterError` (existing in `bcs-http/src/error.rs`):
  `ValidationFailed → BadRequest(400)`, `UsernameTaken → Conflict(409)`,
  `InvalidCredentials → Unauthorized(401)`, `Storage → Service(500)`. Core errors are never
  exposed.
- Persistence write failures propagate as errors (per AGENTS.md: never swallow a failed write
  and return success).
- Username-uniqueness race: insert identity first (catches `uk_external` duplicate →
  `UsernameTaken`), then credential (catches `uk_user_creds_username`). A genuine username
  clash surfaces as a duplicate-key error; `ensure_identity`'s existing `uk_user_id` retry
  path is unaffected (it retries on the random `user_id` collision, not on username clash).

## 12. Testing

- **Unit** (`PasswordAuthService`, in-memory repos):
  - argon2 hash/verify round-trip.
  - register: success; duplicate username → `UsernameTaken`; weak password/invalid username →
    `ValidationFailed`.
  - login: success; wrong password → `InvalidCredentials`; unknown user → `InvalidCredentials`.
  - Registered user can log in; JWT `sub` matches returned `user_id`; `src == "password"`.
- **Store conformance** (`services/bcs-user-identity`): `create_credential` +
  `find_for_login` against in-memory SQLite; duplicate `(username, env)` rejected;
  `(user_id, env)` uniqueness rejected. Mirror `tests/conformance_user_identity.rs`.
- **HTTP** (`bcs-http`): `POST /auth/register` + `POST /auth/login` via axum test util:
  - success → `Set-Cookie` present, body contains `token` + `user_id` + `username`.
  - 409 on duplicate; 401 on bad credentials; 400 on weak password/malformed.
  - Subsequent request with `Authorization: Bearer <jwt>` resolves via the chain to the right
    `user_id` (new — exercises the §9.1 bearer extension).
  - Subsequent request with the cookie resolves identically (existing path).
  - `POST /auth/logout` with bearer header revokes (token hash cleared); next request 401.
- **Migration** (`bootstrap/bcs/src/migrations.rs`): extend the SQLite migration test to
  assert `bcs_user_credentials` exists on a fresh DB and that version 9 is recorded in
  `bcs_schema_migrations` (mirror the existing assertion list).
- **Chain**: a test that `OAuthSessionPlugin` resolves a password-issued JWT from both cookie
  and Bearer header (extends existing plugin tests in `bcs-auth-oauth`).
- Repo coding rules: no `cargo fmt`; no `unwrap`/`expect`/`unsafe`; usernames are
  ASCII-validated (regex), but any user-facing string truncation uses `char_indices()`.

## 13. Decisions Resolved (from brainstorming)

| Question | Decision |
|---|---|
| Login identifier | **username** (self-chosen, unique per `env`). `external_user_id = username`. |
| Token delivery | **Both** cookie and Authorization Bearer; read path accepts either. |
| Registration policy | **Open self-service** (no invite / email verification). |
| Password hashing | **argon2id** (new workspace dep); PHC string stored. |
| Credential storage | **Separate `bcs_user_credentials` table**, carrying `username` for single-query login. |
| Session config | **Reuse `[auth.oauth]`** session fields (jwt_secret, idle_timeout, cookie_secure, env) for password JWT too. |
| Read-path plugin | **Extend existing `OAuthSessionPlugin`** to also read `Authorization: Bearer` (no new plugin). |

## 14. Affected Files (summary — full list at plan time)

- New: `migrations/mysql/009_add_user_credentials.sql`.
- Edit: `bootstrap/bcs/src/migrations.rs` (SQLite DDL + version 9).
- New: `UserCredentialRepoPort` in `bcs-service-api/src/port/repo*`; impl in
  `services/bcs-user-identity/src/lib.rs` (+ memory repo).
- New: `PasswordAuthService` trait in `bcs-service-api/src/application*`; impl in
  `application/v1/bcs-app-auth` (or `bcs-app-session`).
- Edit: `adapters/http/bcs-http/src/oauth/mod.rs` (extend `OAuthRouteState`, add
  register/login handlers + routes).
- Edit: `plugin-api/bcs-auth-api/src/` (add `extract_bearer_token`).
- Edit: `plugins/bcs-auth-oauth/src/{verify,plugin}.rs` (bearer fallback in
  `verify_oauth_session` + `can_authenticate`).
- Edit: `bootstrap/bcs/src/server.rs` (`build_auth_router` mounting condition).
- Edit: `bootstrap/bcs/src/http_adapter.rs` (wire `PasswordAuthService` into `OAuthRouteState`).
- Edit: `Cargo.toml` (add `argon2` workspace dep; register any new crate).
- Edit: `configs/bcs-config-local.toml` + `configs/bcs-config-example.toml`.
- Tests: per §12.

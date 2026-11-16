# OpenAPI V1 Auth Facade

> **2026-11 security-upgrade note.** This 2026-08-28 design predates the
> configurable V1 authentication chain (the V1 API auth plugin chain). Its
> narrative of a "cookie fallback" for business routes and a "best-effort"
> logout described INTENDED behavior that the runtime never actually
> implemented as written. The superseding wire contract is
> `api-contracts/v1/authentication/contract.md` (with the machine-checked
> YAML). The sections below are reconciled in place: the removed claims are
> marked, not silently retained.

## Problem

BCS already has server-side OAuth login plugins (Google, Alipay, GitHub, and compatible providers) exposed through legacy `/auth/*` routes. Other modules need a versioned OpenAPI surface so they can discover login URLs, complete callbacks, read the current human identity, refresh sessions, and logout without depending on legacy route response shapes or concrete OAuth provider plugins.

## Decision

Expose a transport-independent `AuthService` contract in `bcs_service_api::application::v1` and mount a public OpenAPI HTTP facade at `/openapi/v1/auth/*`.

The HTTP adapter depends only on the `AuthService` trait. Concrete provider construction remains in the bootstrap composition root. The current implementation reuses the shared secure application orchestration so token exchange, browser-bound CSRF state, identity persistence, strict session-instance JWT signing, token hash binding, refresh, and logout semantics stay consistent with the legacy routes on the SAME hardened lifecycle.

For OpenAPI protected business routes, the request boundary is the
configurable source chain of
[`api.auth`](../../../api-contracts/v1/authentication/contract.md) (or,
with `[api.auth]` absent, the compatibility assembly): the chain verifies
sources strictly in configured order — an `x-avernet-principal` accepted by
a chained gateway source resolves the caller; a `bcs_session` cookie
resolves ONLY through a chained OAuth source's strict session verification.
There is no "unbound-browser cookie fallback": a request that carries only
an out-of-chain credential is unauthenticated. *(Removed 2026-11: the
original "when the request carries a valid `bcs_session` cookie ... the
HTTP layer resolves the cookie through `AuthService::current_user`"
fallback description — cookie resolution is a named, chained source with
provider-allowlist and environment binding, not a residual path that
trusts any valid-looking cookie.)*

## OpenAPI HTTP contract

All JSON responses use the OpenAPI V1 envelope:

```json
{
  "code": 20000,
  "message": "OK",
  "data": {},
  "request_id": "..."
}
```

Errors use the standard V1 error envelope and application error mapping.

### `GET /openapi/v1/auth/url`

Returns configured OAuth provider login URLs.

- Success: `200`, `data.providers[]` with `name` and `url` — plus, since the
  2026-11 security upgrade, exactly ONE `Set-Cookie` header issuing the
  browser login challenge (`__Host-bcs_oauth_login` on HTTPS production,
  a distinct non-`__Host-` name over plain-HTTP local) and
  `Cache-Control: no-store`. The challenge is REQUIRED by the callback.
- The generated provider URL uses `/openapi/v1/auth/callback/{provider}` as its `redirect_uri`.
- If OAuth providers are not configured: `404`, `data.error_code = "auth_not_configured"`.
- A pending-login dependency outage is `503`, `data.error_code = "auth_dependency_unavailable"`.

### `GET /openapi/v1/auth/callback/{provider}`

Completes OAuth login using `code` or `auth_code` plus `state`, bound to
the initiating browser.

- Success: `302`, `Location: <success_redirect_path>`, and (2026-11) TWO
  separate `Set-Cookie` headers: install the fresh `bcs_session` cookie
  AND clear the temporary browser challenge; the response carries
  `Cache-Control: no-store`.
- Invalid or expired CSRF state, or a callback whose browser cannot present
  the transport's challenge cookie, a state bound to another browser, or a
  state already consumed: `400`, `data.error_code = "invalid_state"` or
  `"invalid_login_state"`.
- Missing authorization code: `400`, `data.error_code = "missing_code"`.
- Unknown or not-enabled provider: `404`, `data.error_code = "provider_not_found"` or `"provider_not_enabled"`.
- A concurrent login replaced this identity's session instance first:
  `409`, `data.error_code = "login_session_conflict"` (the browser
  restarts the login batch).
- A pending/identity/session dependency outage is `503`,
  `data.error_code = "auth_dependency_unavailable"`; a third-party
  exchange or userinfo failure is `502` (`"token_exchange_failed"` /
  `"userinfo_request_failed"`).

### `GET /openapi/v1/auth/user`

Returns the current authenticated human user resolved by the configured auth chain.

- Success: `200`, `data = { user_id, name, provider, avatar }`, `Cache-Control: no-store`.
- Anonymous or non-human principal: `401`, `data.error_code = "unauthenticated"`.
- A verified session rejected by source policy: `403`, `data.error_code = "forbidden"` (2026-11).
- An identity/session dependency outage: `503`, `data.error_code = "auth_dependency_unavailable"` (2026-11).

### `POST /openapi/v1/auth/refresh`

Renews a bound `bcs_session` cookie.

- Success: `200`, success envelope, and a fresh `Set-Cookie` header
  (the atomically rotated next revision of the same session instance;
  `Cache-Control: no-store`).
- Missing, malformed, expired, revoked, or no-longer-current cookie —
  including a lost rotation race against another refresh or a newer
  login: `401`, `data.error_code = "unauthenticated"` (never a
  rewritten cookie and never a 500).
- A cookie-authenticated unsafe write without exactly one trusted
  browser Origin: `403`, `data.error_code = "browser_origin_untrusted"`.
- An identity/session dependency outage: `503`,
  `data.error_code = "auth_dependency_unavailable"` — never folded into
  a denial.


### Protected OpenAPI business routes

Protected `/openapi/v1/collaboration/*` routes authenticate through the
configured source chain (see
`api-contracts/v1/authentication/contract.md`): in the compatibility
assembly the signed internal principal header is the boundary, exactly as
before the 2026-11 upgrade; with `[api.auth]` configured the ordered chain
(short-circuit on first success, no fallback on any failure class)
resolves the caller. *(Removed 2026-11: the original "the adapter may fall
back to the configured OpenAPI auth service and resolve the caller from
`bcs_session`" description — a `bcs_session` cookie is accepted only via
a chained OAuth source under strict, provider-allowlisted session
verification, never as a blanket fallback behind a missing gateway
header.)*

### `POST /openapi/v1/auth/logout`

Revoke the bound session instance atomically, then clear the cookie.
*(Removed 2026-11: the original "best-effort revokes" and "Always returns
`200` when auth service is configured" — reconciliation: logout is
authoritative, not best-effort.)*

- Success: `200` and `Set-Cookie` clearing `bcs_session` with `Max-Age=0`.
- No cookie presented at all: idempotent `200` (the cookie-clearing
  boundary); an EMPTY cookie value is a malformed credential (`401`).
- A persistence failure on the revoke: `503`,
  `data.error_code = "auth_dependency_unavailable"` — never a success
  envelope with a cleared cookie.
- A cookie-bearing logout without exactly one trusted browser Origin:
  `403`, `data.error_code = "browser_origin_untrusted"`.

## Tests

The contract is covered by bootstrap integration tests in `crates/bootstrap/bcs/tests/integration_oauth_routes.rs`:

- OpenAPI URL discovery returns a V1 envelope and OpenAPI callback redirect URI.
- OpenAPI URL discovery fails closed when OAuth is absent.
- `/user` returns V1 unauthenticated errors without a session and returns user info for a bound OAuth cookie.
- `/refresh` returns V1 unauthenticated errors without a session and issues a new cookie for a bound OAuth cookie.
- `/logout` returns a success envelope and clears the cookie.
- `/callback/{provider}` returns V1 errors for invalid state and provider/state mismatch.
- Protected business routes resolve the caller through the configured
  source chain; in the compatibility assembly that is the signed
  `x-avernet-principal` boundary. *(Removed 2026-11: the original
  "fall back to the configured OpenAPI auth service + `bcs_session` when
  the gateway header is absent" — see the Protected OpenAPI business
  routes section above for the reconciled chain semantics.)*

Additional 2026-11 coverage: `crates/bootstrap/bcs/tests/api_auth_http.rs`
(DB→HTTP dependency propagation, 401/503 taxonomy),
`crates/bootstrap/bcs/tests/api_auth_races.rs` (barrier-controlled
logout/refresh/refresh-login races over both entries),
`crates/bootstrap/bcs/tests/api_auth_compatibility.rs` (V1/legacy
compatibility matrix), and
`crates/adapters/http/bcs-api-http/tests/oauth_browser_binding.rs`
(two-jar browser binding over the V1 router).

## 2026-11 Security-compatible reproduction (V1 API auth plugin chain, Task 11)

Both HTTP login entries now run on the SAME secured OAuth primitives
(the shared strict session engine, the browser-bound pending-login
store, and the shared application orchestration). The legacy
`/auth/*` entry keeps its response SHAPES but inherits the following
security tightening — deployments relying on the old behavior must
adjust:

- **Browser-bound login (spec §8.4).** `GET /auth/url` now issues a
  temporary, host-only `__Host-bcs_oauth_login` challenge cookie
  (`Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=300` in
  production; plain-HTTP local development uses the separate
  non-`__Host` name `bcs_oauth_login`). The callback requires this
  cookie's nonce: a browser that did not receive the challenge cannot
  redeem a valid state, and the state is bound to the exact
  `{callback_base}/{provider}` URL. Historical bookmarks into the
  callback flow stop working; users must restart login from URL
  discovery. Flow responses carry `Cache-Control: no-store`.
- **Cookie CSRF on refresh/logout (spec §8.3 compatibility rule).**
  `POST /auth/refresh` and cookie-bearing `POST /auth/logout` now
  require exactly one `Origin` header that is an EXACT member of the
  trusted set bootstrap derives from `cors.allowed_origins` —
  wildcards (`*`, `*.domain`), the literal `null`, and empty entries
  are EXCLUDED. With no exact origin configured, cookie-authenticated
  unsafe writes are REJECTED. Origins are never inferred from
  `Host`/`Referer`, and a Gateway identity never substitutes for the
  cookie. `/auth/logout` without any cookie remains the idempotent
  success boundary; an EMPTY `bcs_session=` cookie value is an error.
- **Strict sessions only.** The unconditional `update_token`
  backdoor is gone: sessions install/refresh/revoke through the
  strict engine's atomic lifecycle (session instance + revision +
  hash). Legacy sid-less JWTs are rejected by the hot path; existing
  browser sessions must log in again after this change.
- **Logout persistence.** `POST /auth/logout` (and the V1
  counterpart) revoke the session BEFORE clearing the cookie; a
  persistent revoke failure is an error response, never a success
  with a cleared cookie.

Tests: `crates/bootstrap/bcs/tests/integration_oauth_routes.rs`
(mounting, challenge cookie, origin gating, strict seeding),
`crates/adapters/http/bcs-api-http/tests/oauth_browser_binding.rs`
(two-jar browser binding over the V1 router), and
`crates/bootstrap/bcs/tests/auth_log_redaction.rs` (credential-log
red line over both real routers).

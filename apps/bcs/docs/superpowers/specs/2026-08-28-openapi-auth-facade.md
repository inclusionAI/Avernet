# OpenAPI V1 Auth Facade

## Problem

BCS already has server-side OAuth login plugins (Google, Alipay, GitHub, and compatible providers) exposed through legacy `/auth/*` routes. Other modules need a versioned OpenAPI surface so they can discover login URLs, complete callbacks, read the current human identity, refresh sessions, and logout without depending on legacy route response shapes or concrete OAuth provider plugins.

## Decision

Expose a transport-independent `AuthService` contract in `bcs_service_api::application::v1` and mount a public OpenAPI HTTP facade at `/openapi/v1/auth/*`.

The HTTP adapter depends only on the `AuthService` trait. Concrete provider construction remains in the bootstrap composition root. The current implementation reuses the existing OAuth route state as the service implementation so token exchange, CSRF state, identity persistence, JWT signing, token hash binding, refresh, and logout semantics stay consistent with the legacy routes.

For OpenAPI protected business routes, the request boundary is: `x-avernet-principal` if the gateway has already signed the caller; otherwise, when the request carries a valid `bcs_session` cookie and OpenAPI auth is configured, the HTTP layer resolves the cookie through `AuthService::current_user`, projects that identity into `AuthenticatedCaller`, and lets the route proceed.

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

- Success: `200`, `data.providers[]` with `name` and `url`.
- The generated provider URL uses `/openapi/v1/auth/callback/{provider}` as its `redirect_uri`.
- If OAuth providers are not configured: `404`, `data.error_code = "auth_not_configured"`.

### `GET /openapi/v1/auth/callback/{provider}`

Completes OAuth login using `code` or `auth_code` plus `state`.

- Success: `302`, `Location: /?login=success`, `Set-Cookie: bcs_session=...`.
- Invalid or expired CSRF state: `400`, `data.error_code = "invalid_state"`.
- Provider in path does not match provider bound to state: `400`, `data.error_code = "provider_mismatch"`.
- Missing authorization code: `400`, `data.error_code = "missing_code"`.
- Unknown provider: `404`, `data.error_code = "provider_not_found"`.

### `GET /openapi/v1/auth/user`

Returns the current authenticated human user resolved by the configured auth chain.

- Success: `200`, `data = { user_id, name, provider, avatar }`.
- Anonymous or non-human principal: `401`, `data.error_code = "unauthenticated"`.

### `POST /openapi/v1/auth/refresh`

Renews a bound `bcs_session` cookie.

- Success: `200`, success envelope, and a fresh `Set-Cookie` header.
- Missing, malformed, expired beyond server-side binding, or unbound cookie: `401`, `data.error_code = "unauthenticated"`.


### Protected OpenAPI business routes

Protected `/openapi/v1/collaboration/*` routes are gateway-compatible first: they accept the signed internal principal header when present. If that header is absent, the adapter may fall back to the configured OpenAPI auth service and resolve the caller from `bcs_session`. This keeps browser-driven OpenAPI calls usable while still allowing backend callers to use the gateway trust boundary.

### `POST /openapi/v1/auth/logout`

Clears the session cookie and best-effort revokes the bound server-side token hash.

- Always returns `200` when auth service is configured.
- Response includes `Set-Cookie` clearing `bcs_session` with `Max-Age=0`.

## Tests

The contract is covered by bootstrap integration tests in `crates/bootstrap/bcs/tests/integration_oauth_routes.rs`:

- OpenAPI URL discovery returns a V1 envelope and OpenAPI callback redirect URI.
- OpenAPI URL discovery fails closed when OAuth is absent.
- `/user` returns V1 unauthenticated errors without a session and returns user info for a bound OAuth cookie.
- `/refresh` returns V1 unauthenticated errors without a session and issues a new cookie for a bound OAuth cookie.
- `/logout` returns a success envelope and clears the cookie.
- `/callback/{provider}` returns V1 errors for invalid state and provider/state mismatch.
- Protected business routes use `x-avernet-principal` when present and fall back to the configured OpenAPI auth service + `bcs_session` when the gateway header is absent.

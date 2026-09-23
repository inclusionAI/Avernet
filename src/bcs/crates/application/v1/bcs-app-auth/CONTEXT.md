# bcs-app-auth Context

## Provides

- `AuthApplicationService`, the transport-agnostic BCN V1 secure-auth
  facade implementing `bcs_service_api::application::v1::auth::SecureAuthService`.
- `AuthApplicationServiceConfig`, the plain config-injected inputs
  (enabled provider chain subsequence, configured pending-login flow
  namespace, configured post-login redirect URL).

## Consumes

- `bcs-service-api` application contracts: the `SecureAuthService` trait,
  `BoundOAuthLogin` / `BrowserLoginBinding` / `BuildLoginUrls` /
  `AuthenticatedUserQuery` / `PresentedSession` commands, the
  `AuthProviderUrl` / `AuthRedirect` / `AuthUserInfo` / `SessionRenewal`
  reply payloads, the `BrowserCookieChange` / `AuthFlowReply` cookie
  contract, and the `ApplicationError` taxonomy.
- `bcs-service-api` port contracts: the `OAuthProviderPort`,
  `OAuthSessionPort`, and `PendingOAuthLoginPort` traits plus the
  `ExternalLoginIdentity`, `BrowserSession`, and `PendingLoginBatch`
  payload types from `bcs_service_api::port::oauth`.
- Pure utility crates for asynchronous traits.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait` and `tracing`.

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*` (incl. `bcs-auth-oauth`, `bcs-auth-google`,
  `bcs-auth-github`, `bcs-auth-wechat`, `bcs-auth-alipay`, `bcs-auth-local`).
- Plugin-API contract crates (`bcs-auth-api`, `bcs-jwt`, `bcs-user-identity`)
  in production code.
- Direct environment, cookie, or HTTP access. The application layer
  never parses cookies — it consumes a `PresentedSession { token }` and
  emits explicit `BrowserCookieChange` instructions.

## Configuration

- The composition root injects the three ports plus the
  `AuthApplicationServiceConfig` plain-value bundle.
- This crate MUST NOT resolve secrets, select provider implementations,
  check request origins, or perform any DB/cache access. The bootstrap
  bridges translate plugin-API types field-by-field; this crate sees only
  the service-api ports.
- `enabled_providers` configures the chain's provider subsequence order;
  `login_urls` preserves that exact order in its reply.

## Runtime ownership

This crate owns the OAuth login orchestration:

- `login_urls` issues a pending-login batch, builds provider URLs in the
  configured chain order, and replies the URL list plus a
  `SetLoginChallenge` cookie change. A failed pending store surfaces an
  error reply with NO cookie changes.
- `complete_login` validates the enabled provider and required `code`,
  atomically consumes the pending-login batch, exchanges the code for an
  external identity, installs the identity, and replies the redirect
  with `SetSession` + `ClearLoginChallenge`. A binding failure burns
  nothing and clears nothing; a post-consume failure (exchange/install)
  replies the error AND `ClearLoginChallenge` per spec §8.4.
- `refresh_session` rotates a presented token via the session port and
  replies `SetSession`; an error reply carries NO cookie changes.
- `logout` revokes a presented token (none presented = idempotent
  success) and replies `ClearSession`.
- `current_user` only projects the authenticated Human principal from
  the caller; `provider_label` and `avatar` are display-only, never
  authorization.

Delivery adapters may translate HTTP requests into these contracts but
must not reimplement pending-login atomicity, session install/refresh,
or the cookie-change sequencing.

## Tests

- `cargo test --package bcs-app-auth --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-auth --all-targets --manifest-path src/bcs/Cargo.toml`

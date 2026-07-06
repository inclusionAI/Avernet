# bcs-auth-api

Authentication plugin **contracts** for BCS: the traits and value types that the
delivery layer and the auth plugins agree on. This crate holds no IO and no
provider logic — only definitions.

## Context Boundary

```yaml
purpose: Auth plugin/port contracts and shared auth value types (no IO, no impls).
provides:
  - AuthPlugin            # Plugin API: core calls a plugin to authenticate a request
  - AuthPluginChain       # priority-ordered runner over AuthPlugin
  - OAuthProvider         # Plugin API: login-flow provider (code->token->userinfo)
  - OAuthToken
  - ProviderUserInfo
  - OAuthError
  - UserIdentityPort      # Outbound port: core->identity directory + session token store
  - BotLookupPort         # Outbound port: resolve bot session token -> BotInfo
  - AuthPrincipal
  - AuthSource            # incl. OAuth(String) provider tag
  - AuthConfig            # resolved chain config (neutral Default; bootstrap fills chain)
  - OAuthConfig
  - LocalAuthConfig
  - extract_session_cookie
  - BCS_SESSION_COOKIE
consumes:
  - axum::http::HeaderMap   # transport type currently in the AuthPlugin signature (see note)
internal_dependencies: []   # leaf contract crate; depends only on external crates
```

### Change impact

Editing a trait here ripples to **every** auth plugin (`bcs-auth-*`), the
delivery adapter (`bcs-http` OAuth routes + `ChainUserIdentityPort`), the
bootstrap composition root (`auth_wiring`), and the test doubles in
`bcs-test-support`. Adding a method to `UserIdentityPort` forces edits in all of
its implementations (`bcs-user-identity` memory/db, `NoopUserIdentityPort`,
route-test mocks) — prefer additive, struct-param changes. Contract changes must
follow propagation analysis (Rule 16).

> Note (Rule 7): `AuthPlugin` currently takes `&axum::http::HeaderMap`, leaking a
> transport type into the contract. A transport-neutral header accessor is the
> intended follow-up; until then this crate carries an `axum` dependency.

# bcs-auth-google

Google OAuth **provider**: implements the login flow (authorize URL, code
exchange, userinfo) behind the `OAuthProvider` contract. Session verification is
handled generically by `bcs-auth-oauth`, not here.

## Context Boundary

```yaml
purpose: Google-specific OAuth login flow (auth_url / exchange_code / get_user_info).
provides:
  - GoogleOAuthProvider     # OAuthProvider impl
  - GoogleOAuthConfig
consumes:
  - OAuthProvider           # implements the bcs-auth-api Plugin API
internal_dependencies:
  - bcs-auth-api
```

### Change impact

Self-contained: adding/altering Google endpoints or field mapping affects only
this crate. It is registered in the composition root (`server.rs`
`build_oauth_router`) only when `[auth.oauth.google]` is configured. Must pass the
shared offline `OAuthProvider` conformance suite in `bcs-test-support`
(`run_oauth_provider_offline_contract`); the IO half calls real Google endpoints
and is covered by integration tests, not CI conformance.

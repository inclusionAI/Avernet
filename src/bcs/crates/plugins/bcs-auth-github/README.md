# bcs-auth-github

GitHub OAuth **provider**: implements the login flow (authorize URL, code
exchange, userinfo) behind the `OAuthProvider` contract. Session verification is
handled generically by `bcs-auth-oauth`, not here.

## Context Boundary

```yaml
purpose: GitHub-specific OAuth login flow (auth_url / exchange_code / get_user_info).
provides:
  - GitHubOAuthProvider     # OAuthProvider impl
  - GitHubOAuthConfig
consumes:
  - OAuthProvider           # implements the bcs-auth-api Plugin API
internal_dependencies:
  - bcs-auth-api
```

### Change impact

Self-contained: adding/altering GitHub endpoints or field mapping affects only
this crate. It is registered in the composition root (`server.rs`
`build_oauth_router`) only when `[auth.oauth.github]` is configured. The HTTP
client sets a `User-Agent` because `api.github.com` rejects requests without one.
Must pass the shared offline `OAuthProvider` conformance suite in
`bcs-test-support`; the IO half calls real GitHub endpoints and is covered by
integration tests, not CI conformance.

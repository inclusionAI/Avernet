# bcs-auth-oauth

Provider-agnostic OAuth **session** plugin. One instance authenticates requests
carrying a `bcs_session` JWT cookie regardless of which provider issued it (the
issuer is recorded in the JWT's `src` claim). Provider-specific login logic lives
in the per-provider crates, not here.

## Context Boundary

```yaml
purpose: Verify the bcs_session JWT cookie on the request hot path (read-only).
provides:
  - OAuthSessionPlugin      # AuthPlugin impl (priority 25, between cookie and session)
  - verify_oauth_session    # shared verify-then-bind helper
consumes:
  - AuthPlugin              # implements the bcs-auth-api Plugin API
  - UserIdentityPort        # binds the presented JWT to the stored session hash
  - JwtService              # HS256 verify + token_hash (bcs-jwt)
internal_dependencies:
  - bcs-auth-api
  - bcs-jwt
```

### Change impact

This crate runs on every authenticated request, so behavior changes here affect
all session-cookie auth. The hot path is intentionally **read-only**: it verifies
signature + expiry, then confirms the JWT fingerprint matches the stored session
hash (single-session). It must not re-sign or write to the DB — session renewal
is owned by `POST /auth/refresh` in `bcs-http`. Loosening the hash bind weakens
single-session and logout revocation.

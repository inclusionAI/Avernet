# bcs-auth-oauth

Provider-agnostic OAuth **session** plugin. One instance authenticates requests
carrying a `bcs_session` JWT cookie regardless of which provider issued it (the
issuer is recorded in the JWT's `src` claim). Provider-specific login logic lives
in the per-provider crates, not here. Since the V1 API auth plugin chain
(2026-11) the crate also owns the strict session lifecycle engine and the
browser-bound pending-login state — behind ports, still provider-agnostic.

## Context Boundary

```yaml
purpose: Strict OAuth session verify + atomic lifecycle engine; browser-bound pending login state.
provides:
  - OAuthSessionPlugin      # AuthPlugin impl (legacy read-only hot path)
  - verify_oauth_session    # shared verify-then-bind helper (legacy)
  - OAuthSessionEngine      # strict engine: install/refresh/revoke (V1 chain, 2026-11)
  - MemoryPendingOAuthLoginStore  # browser-bound one-time OAuth state (plan §8.4)
consumes:
  - AuthPlugin              # implements the bcs-auth-api Plugin API
  - AuthSessionIdentityPort # strict install/rotate/revoke + ensure_identity (bcs-auth-api)
  - PendingOAuthLoginStore  # pending-login port this crate defines the memory impl for
  - JwtService              # HS256 verify + token_hash (bcs-jwt); strict sid/revision claims via bcs-jwt OAuthSessionJwt
internal_dependencies:
  - bcs-auth-api
  - bcs-jwt
```

### Change impact

This crate runs on every authenticated request, so behavior changes here affect
all session-cookie auth. The LEGACY hot path stays **read-only**: it verifies
signature + expiry, then confirms the JWT fingerprint matches the stored
session hash. Loosening the hash bind weakens single-session and logout
revocation.

The STRICT side (2026-11, plan §8.5) is the only writer: the
`OAuthSessionEngine` compares-and-swaps by `(scope, session_id, revision,
token_hash)` — install only for a fully browser-bound new login, refresh
rotates to `revision + 1` under the same session instance, revoke kills the
CURRENT instance (including rotated descendants) and is idempotent for a
superseded one. There is no unconditional `update_token` write anywhere: every
write is a CAS whose expected version matches the presented credential, and
a lost race is surfaced (`Conflict` at install, `Invalid` at refresh) —
never resolved by writing unconditionally. The browser-bound pending-login
store is likewise consumed atomically: the first consume of any provider
state invalidates the whole batch, and a cross-browser consume never burns
the initiating browser's record.

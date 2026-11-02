# bcs-user-identity

`UserIdentityRepoPort` implementations: the user-identity directory plus the
session-token fingerprint store backing OAuth login. Owns the
`bcs_user_identities` SQL; depends only on `bcs-db-api`.

## Context Boundary

```yaml
purpose: Persist user identities and the current session token hash (memory + DB).
provides:
  - MemoryUserIdentityRepo  # in-memory impl (local dev / tests)
  - DbUserIdentityStore     # DbPlugin-backed impl (mysql + sqlite flavors)
  - MysqlUserIdentityRepo    # alias of DbUserIdentityStore
  - SqliteUserIdentityRepo   # alias of DbUserIdentityStore
  - generate_user_id        # 12-char base62 CSPRNG internal id
consumes:
  - DbPlugin                # bcs-db-api: query/execute against the backing DB
internal_dependencies:
  - bcs-service-api         # UserIdentityRepoPort + UserIdentity model
  - bcs-db-api
```

### Change impact

Backs identity creation on OAuth callback and the single-session bind on every
authenticated request (via the bootstrap adapter to `UserIdentityPort`). The
stored `token` column holds only the **SHA-256 fingerprint** of the session JWT
(never the raw token); migration `015_*.sql` narrows it to `VARCHAR(64)` — and
that width is exact: MySQL deployments must run a strict SQL mode, because a
permissive mode silently truncates a hash write instead of failing it.

Since the V1 API auth plugin chain (2026-11) this crate also implements the
strict `AuthSessionRepoPort` (`session_memory.rs` / `session_db.rs`) behind the
`UserIdentityRepoPort` rows it already owns: `install_login_session`,
`rotate_session` (compare-and-swap by scope + session id + expected revision +
expected hash), and `revoke_session` by session instance — added by migration
`040_auth_session_version.sql` (`session_id`, `session_revision`,
`session_expires_at`; the historical opaque tokens are cleared exactly once).
There is no unconditional `update_token` writer anymore; every session write
is a CAS whose row-count is checked. The memory and DB impls are kept
behavior-identical by the conformance suite (`conformance_user_identity.rs`
plus the strict-session `conformance_auth_session_{memory,sqlite,mysql}.rs`
drivers over the shared central harness); mysql and sqlite share one code
path apart from the timestamp SQL flavor. Schema changes require a migration
+ the SQLite test DDL update.

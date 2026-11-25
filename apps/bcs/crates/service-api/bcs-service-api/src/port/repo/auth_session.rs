//! Strict atomic auth-session repository contract (V1 API auth plugin chain).
//!
//! This port is deliberately independent of [`UserIdentityRepoPort`]: an
//! implementation may implement both traits, but this trait declares no
//! default methods and never delegates to the legacy Option-returning
//! identity lookups. A missing identity row is never auto-created by a
//! session write; identity creation/confirmation is a separate operation.
//!
//! Pinned semantics:
//! - `AuthSessionScope::env` is part of the scope. Scope values come from
//!   trusted configuration plus an already-verified identity, never from
//!   unverified JWT claims.
//! - `AuthSessionVersion::revision` advances by `checked_add(1)` and must
//!   stay within SQL signed BIGINT (`i64::MAX`) bounds.
//! - Writes are compare-and-swap against the expected revision/version.
//!   A conflict is a distinct `Conflict` outcome, never an error and never
//!   resolved by an unconditional retry write.
//! - Revocation also bumps the revision and clears the token hash, so a
//!   revoked session no longer resolves by hash. Repeating a revoke reports
//!   `NotCurrent`, not an error.
//! - Store failures carry closed category text only — no SQL fragments,
//!   endpoint identifiers, or credential material.

use async_trait::async_trait;

/// Trusted identity scope of one login session. `env` participates in
/// equality and row addressing, so the same user/provider pair in different
/// environments has independent sessions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionScope {
    pub user_id: String,
    pub provider: String,
    pub env: String,
}

/// One atomic session version: the session id, its CAS revision, and the hash
/// of the currently valid token. Revisions advance by `checked_add(1)` and
/// are bounded by SQL signed BIGINT (`i64::MAX`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionVersion {
    pub session_id: String,
    pub revision: u64,
    pub token_hash: String,
}

/// Read model of the current session version joined with the identity row's
/// display fields. Absence is `None`; persistence failures are errors.
#[derive(Clone, Debug)]
pub struct AuthSessionSnapshot {
    pub scope: AuthSessionScope,
    pub version: AuthSessionVersion,
    pub expires_at: u64,
    pub username: String,
    pub display_name: Option<String>,
    pub avatar: Option<String>,
}

/// Compare-and-swap install of a new login session. The caller first reads
/// the current revision, pre-signs the JWT, then installs `next` only when
/// the stored revision is still `expected_revision`.
#[derive(Clone, Debug)]
pub struct InstallAuthSession {
    pub scope: AuthSessionScope,
    pub expected_revision: u64,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
}

/// Compare-and-swap refresh rotation: `next` replaces `expected` only when
/// `expected` is still the current version. A conflict is reported to the
/// caller as an authentication failure, never retried with an unconditional
/// write.
#[derive(Clone, Debug)]
pub struct RotateAuthSession {
    pub scope: AuthSessionScope,
    pub expected: AuthSessionVersion,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
    pub now: u64,
}

/// Outcome of a CAS write (install or rotate) against the expected revision.
/// `Conflict` is a lost race, not a persistence error.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionWrite {
    Applied,
    Conflict,
}

/// Outcome of a revoke by session id. A repeated revoke of an already
/// revoked (no longer current) session is `NotCurrent`, not an error.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionRevoke {
    Revoked,
    NotCurrent,
}

/// Closed persistence failure categories. The display text carries no SQL,
/// endpoint identifiers, or credential material, so it is safe to surface
/// in logs and translated errors alike.
#[derive(Debug, thiserror::Error)]
pub enum AuthSessionStoreError {
    #[error("identity store unavailable")]
    Unavailable,
    #[error("invalid persisted identity")]
    CorruptRecord,
}

/// Persistence contract for strict, atomic, environment-scoped login
/// sessions backing the V1 API auth plugin chain.
///
/// An implementation may also implement [`UserIdentityRepoPort`], but this
/// trait owns no default methods and must not delegate to the legacy
/// Option-returning lookups. Session writes never auto-create a missing
/// identity row.
#[async_trait]
pub trait AuthSessionRepoPort: Send + Sync {
    /// Read the current session revision for a scope. `0` means no session is
    /// installed yet (install expects revision 0 for its CAS).
    async fn read_session_revision(
        &self,
        scope: &AuthSessionScope,
    ) -> Result<u64, AuthSessionStoreError>;

    /// Resolve the current session snapshot by token hash under its scope.
    /// Expired, rotated-out, and revoked hashes return `None`; the snapshot
    /// joins the identity row's display fields.
    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError>;

    /// Atomically install a new login session when the stored revision still
    /// equals `expected_revision`. A concurrent login race yields `Conflict`;
    /// the caller restarts the login batch instead of writing
    /// unconditionally.
    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError>;

    /// Atomically rotate the session from `expected` to `next`. A lost race
    /// yields `Conflict`, which the caller maps to an authentication
    /// failure — never to an unconditional rewrite.
    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError>;

    /// Revoke the current session by session id. Revoke bumps the revision
    /// and clears the token hash; repeating the revoke on the now
    /// non-current session returns `NotCurrent` instead of an error.
    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError>;
}

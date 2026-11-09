//! Strict atomic OAuth session identity port (V1 API auth plugin chain).
//!
//! Plugin-API mirror of the repository contract
//! `bcs_service_api::port::repo::auth_session`: the five session methods
//! carry the same parameter/result semantics, and [`ensure_identity`]
//! creates/confirms the identity a NEW LOGIN needs (it never delegates to
//! an error-swallowing Option lookup — identity failures are errors).
//!
//! The two crates never depend on each other; the bootstrap composition
//! root translates field-by-field between the two vocabularies (see
//! bootstrap `identity_session_wiring.rs`), so this port invents no
//! semantics of its own.
//!
//! Pinned semantics (mirroring the repository side):
//! - `SessionScope::env` participates in scope equality/addressing; scope
//!   values come from trusted configuration plus an already-verified
//!   identity, never from unverified JWT claims.
//! - `SessionVersion::revision` advances by `checked_add(1)` within SQL
//!   signed BIGINT (`i64::MAX`) bounds; revocation bumps it too.
//! - Writes are compare-and-swap; a lost race is a distinct `Conflict`
//!   outcome, never an error and never resolved by an unconditional write.
//! - `token_hash` is the lowercase-hex SHA-256 fingerprint of the JWT
//!   (exactly 64 chars; storage `VARCHAR(64)` has zero headroom) and
//!   `session_id` values must fit 64 chars (e.g. 128-bit random hex or a
//!   dash-less UUID).

use async_trait::async_trait;

/// Trusted identity scope of one login session. `env` is part of the
/// address: the same user/provider pair in different environments has
/// independent sessions, and a scope mismatch (wrong env) never sees or
/// touches another environment's session.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionScope {
    pub user_id: String,
    pub provider: String,
    pub env: String,
}

/// One atomic session version: the session id, its CAS revision, and the
/// SHA-256 (64-hex-char) token fingerprint. Revisions advance by
/// `checked_add(1)` and stay within SQL signed BIGINT bounds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionVersion {
    pub session_id: String,
    pub revision: u64,
    pub token_hash: String,
}

/// Read model of the current session version joined with the identity
/// row's display fields. `username` may be empty for a nameless identity;
/// filling it with an internal-ID fallback is a downstream (consumer)
/// concern, not a store or bridge concern. Absence is `None`; persistence
/// failures are errors.
#[derive(Clone, Debug)]
pub struct SessionSnapshot {
    pub scope: SessionScope,
    pub version: SessionVersion,
    pub expires_at: u64,
    pub username: String,
    pub display_name: Option<String>,
    pub avatar: Option<String>,
}

/// Compare-and-swap install of a new login session. The caller first reads
/// the current revision, pre-signs the JWT, then installs `next` only when
/// the stored revision is still `expected_revision`.
#[derive(Clone, Debug)]
pub struct InstallSession {
    pub scope: SessionScope,
    pub expected_revision: u64,
    pub next: SessionVersion,
    pub expires_at: u64,
}

/// Compare-and-swap refresh rotation: `next` replaces `expected` only when
/// `expected` is still the current version. A conflict is reported as an
/// authentication failure, never retried with an unconditional write.
#[derive(Clone, Debug)]
pub struct RotateSession {
    pub scope: SessionScope,
    pub expected: SessionVersion,
    pub next: SessionVersion,
    pub expires_at: u64,
    pub now: u64,
}

/// Outcome of a CAS write (install or rotate) against the expected
/// revision. `Conflict` is a lost race, not a persistence error.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SessionWrite {
    Applied,
    Conflict,
}

/// Outcome of a revoke by session id. A repeated revoke of an already
/// revoked (no longer current) session is `NotCurrent`, not an error.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SessionRevoke {
    Revoked,
    NotCurrent,
}

/// Closed failure categories for identity/session operations, mirrored
/// from the repository side. The display text carries no SQL fragments,
/// endpoint identifiers, or credential material, so it is safe to surface
/// in logs and translated errors alike.
#[derive(Debug, thiserror::Error)]
pub enum SessionStoreError {
    #[error("identity store unavailable")]
    Unavailable,
    #[error("invalid persisted identity")]
    CorruptRecord,
}

/// Issued session artifact returned by the strict engine after a session
/// install or refresh has been ATOMICALLY APPLIED. `token` is bearer
/// credential material (plan §8.4 redaction): the hand-written `Debug`
/// below prints only the expiry, never the token.
#[derive(Clone)]
pub struct IssuedSession {
    /// The signed session JWT. Bearer material — never logged, never
    /// persisted in clear; only its SHA-256 fingerprint (64 hex chars)
    /// is stored.
    pub token: String,
    /// Trusted session-window expiry (unix seconds) of this exact version.
    pub expires_at: u64,
}

impl std::fmt::Debug for IssuedSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("IssuedSession")
            .field("expires_at", &self.expires_at)
            .finish_non_exhaustive()
    }
}

/// Closed failure categories for strict OAuth session-engine operations.
/// Distinct variants keep the delivery error mapping honest: `Invalid` is
/// the 401-side denial (re-authenticate), `Conflict` is the 409-side lost
/// LOGIN race (restart the browser login batch), and `Unavailable` is the
/// dependency failure that must surface as 503, never as a quiet denial.
/// Display text is a fixed stable category — no SQL fragments, endpoints,
/// claim values, or credential material.
#[derive(Debug, thiserror::Error)]
pub enum SessionAuthError {
    #[error("invalid session credential")]
    Invalid,
    /// The deployment environment or the entry's provider policy refuses it.
    #[error("session credential not accepted by this authentication policy")]
    Forbidden,
    #[error("session identity store unavailable")]
    Unavailable,
    #[error("session engine internal failure")]
    Internal,
    #[error("session write conflict")]
    Conflict,
}

/// Plugin-API port for strict, atomic, environment-scoped OAuth login
/// sessions backing the V1 API auth plugin chain.
///
/// This port declares no default methods and never delegates to legacy
/// Option-returning identity lookups. [`AuthSessionIdentityPort::ensure_identity`]
/// is the NEW-LOGIN identity creation/confirmation entry point: it must
/// create-or-confirm the identity and PROPAGATE failures as
/// [`SessionStoreError`] instead of swallowing them.
#[async_trait]
pub trait AuthSessionIdentityPort: Send + Sync {
    /// Ensure `(provider, external_user_id, env)` has an identity row and
    /// return its internal `user_id`. Idempotent; called by the OAuth
    /// callback on a NEW LOGIN before pre-signing and installing the
    /// session JWT. Failures are errors — never a silent Option miss.
    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError>;

    /// Read the current session revision for a scope. `0` means no session
    /// is installed yet (an install expects revision 0 for its CAS).
    async fn read_session_revision(&self, scope: &SessionScope) -> Result<u64, SessionStoreError>;

    /// Resolve the current session snapshot by token hash under its scope.
    /// Expired, rotated-out, revoked, cross-env, and unknown hashes return
    /// `None`; the snapshot joins the identity row's display fields.
    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError>;

    /// Atomically install a new login session when the stored revision
    /// still equals `expected_revision`. A concurrent login race yields
    /// `Conflict`; the caller restarts the login batch instead of writing
    /// unconditionally.
    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError>;

    /// Atomically rotate the session from `expected` to `next`. A lost
    /// race yields `Conflict`, which the caller maps to an authentication
    /// failure — never to an unconditional rewrite.
    async fn rotate_session(&self, command: RotateSession) -> Result<SessionWrite, SessionStoreError>;

    /// Revoke the current session by session id. Revoke bumps the
    /// revision and clears the token hash; repeating the revoke on the
    /// now-non-current session returns `NotCurrent` instead of an error.
    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError>;
}

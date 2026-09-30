//! Strict atomic auth-session repository contract.
//!
//! This port is the persistence boundary for the V1 auth-session vertical
//! slice: it stores the binding between a user identity scope and the
//! currently issued JWT hash, with compare-and-set (CAS) semantics so that
//! rotate/revoke never overwrite a newer session silently.
//!
//! # Independence from `UserIdentityRepoPort`
//!
//! This trait is fully independent of [`super::user_identity::UserIdentityRepoPort`].
//! The legacy port exposes Option-returning CRUD with idempotent `ensure_identity`
//! semantics and a token-lookup path that predates the strict CAS protocol.
//! Implementation objects MAY implement both traits against the same backing
//! store, but this trait's contract has no default methods and no path that
//! calls the legacy Option-returning methods; the two contracts are kept
//! disjoint at the type level so stores can migrate one without touching the
//! other.
//!
//! # Storage-failure vs missing-row vs conflict semantics
//!
//! [`AuthSessionStoreError`] carries no SQL, no endpoint, and no secret. It
//! has exactly two variants:
//!
//! - [`AuthSessionStoreError::Unavailable`] — storage failure (DB down,
//!   timeout, serialization failure that the implementation cannot retry
//!   internally). Anything where the caller cannot conclude the row state.
//! - [`AuthSessionStoreError::CorruptRecord`] — invalid persisted identity.
//!   Decoding the stored row produced a structurally valid record whose
//!   fields are unusable (bad revision, mismatched scope, torn write). The
//!   implementation MUST not silently repair such rows through this trait.
//!
//! Conflict, no-match, and not-current are *success-typed enums*, not errors:
//!
//! - [`AuthSessionWrite::Conflict`] — CAS failed because the stored revision
//!   advanced past `expected_revision` / `expected`. The caller MUST observe
//!   the new revision and decide whether to retry the read → pre-sign → CAS
//!   loop; an unconditional write retry is forbidden (it would overwrite a
//!   newer session).
//! - [`AuthSessionRevoke::NotCurrent`] — the named `session_id` is no longer
//!   the live binding for the scope. The scope's session may have rotated or
//!   been revoked already; the harness treats this as success, not an error.
//!
//! `get_session_by_hash` returns `Ok(None)` when the query succeeded but no
//! live binding matches (expired, revoked, or unknown hash); a DB error is
//! `Err(Unavailable)`. Implementations MUST never collapse these two cases.
//!
//! # Concurrency semantics
//!
//! Implementations enforce the following invariants under concurrent access:
//!
//! - `read_session_revision` on a scope with no identity row returns `Ok(0)`.
//!   A missing identity row is thus not auto-created by a blind read; the
//!   caller drives install explicitly via [`AuthSessionRepoPort::install_login_session`].
//! - `install_login_session` and `rotate_session` use compare-and-set against
//!   the supplied `expected_revision` / `expected.revision`. On success the
//!   stored revision MUST be `expected.checked_add(1)`; an overflow is
//!   reported as [`AuthSessionStoreError::CorruptRecord`] (the persisted
//!   counter is unusable), never silently wrapped.
//! - `revoke_session` also bumps the revision: a subsequent install of a
//!   fresh login observes the bump and CAS-fails if it races an in-flight
//!   rotate. After revoke, the cleared hash MUST NOT be queryable by
//!   `get_session_by_hash` (returns `Ok(None)`).
//! - The login flow is read-revision → pre-sign JWT → CAS install; a CAS
//!   conflict is the returned [`AuthSessionWrite::Conflict`] and is never
//!   retried by an unconditional write at this layer.
//!
//! Implementations are responsible for atomicity; the trait makes no
//! threading guarantees and assumes a single-store sequencing point per
//! scope.

use async_trait::async_trait;

/// The identity scope of an auth session.
///
/// The triple `(user_id, provider, env)` is the partition key for session
/// state. `env` is part of the scope so the same user identity in different
/// environments holds independent sessions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionScope {
    pub user_id: String,
    pub provider: String,
    pub env: String,
}

/// The version of an auth session. Used for compare-and-set.
///
/// `revision` strictly increases on every successful write (install, rotate,
/// revoke). `token_hash` is the opaque hash (storage-side, e.g. SHA-256 of
/// the JWT) of the currently live credential; the trait never receives the
/// raw JWT.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthSessionVersion {
    pub session_id: String,
    pub revision: u64,
    pub token_hash: String,
}

/// A point-in-time read of the live session for a scope. Returned by
/// `get_session_by_hash` when the hash matches the current binding.
///
/// `display_name` and `avatar` are optional display fields persisted with
/// the session for caller convenience; they carry no security semantics.
#[derive(Clone, Debug)]
pub struct AuthSessionSnapshot {
    pub scope: AuthSessionScope,
    pub version: AuthSessionVersion,
    pub expires_at: u64,
    pub username: String,
    pub display_name: Option<String>,
    pub avatar: Option<String>,
}

/// Command for the initial install of a login session.
///
/// The caller first reads the current revision via
/// [`AuthSessionRepoPort::read_session_revision`], pre-signs the JWT, then
/// installs with `expected_revision` set to the read value. A CAS conflict
/// is returned as [`AuthSessionWrite::Conflict`].
#[derive(Clone, Debug)]
pub struct InstallAuthSession {
    pub scope: AuthSessionScope,
    pub expected_revision: u64,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
}

/// Command for rotating an existing session to a new token hash.
///
/// `expected` is the live version the caller believes it is rotating from;
/// `next` is the freshly signed replacement. On success the stored revision
/// is `expected.revision.checked_add(1)`. `now` is the caller's clock at
/// rotate time and is the same `now` used to bound `expires_at`.
#[derive(Clone, Debug)]
pub struct RotateAuthSession {
    pub scope: AuthSessionScope,
    pub expected: AuthSessionVersion,
    pub next: AuthSessionVersion,
    pub expires_at: u64,
    pub now: u64,
}

/// Outcome of an install or rotate write.
///
/// `Applied` indicates the CAS succeeded and the stored revision advanced.
/// `Conflict` indicates the stored revision did not match the expected value;
/// the caller MUST re-read and decide on a bounded retry. `Conflict` is a
/// success-typed enum, not an [`AuthSessionStoreError`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionWrite {
    Applied,
    Conflict,
}

/// Outcome of `revoke_session`.
///
/// `Revoked` indicates the named `session_id` was the live binding and is now
/// cleared (revision bumped). `NotCurrent` indicates the named session is no
/// longer live (already rotated or revoked); the harness treats this as
/// success.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthSessionRevoke {
    Revoked,
    NotCurrent,
}

/// Storage-level error for the auth-session repository.
///
/// Carries no SQL, no endpoint, and no secret. Only two variants: a missing
/// row, a conflict, or a no-match outcome are NOT errors — they are
/// success-typed enums ([`AuthSessionWrite`] / [`AuthSessionRevoke`] or
/// `Ok(None)` from `get_session_by_hash`).
#[derive(Debug, thiserror::Error)]
pub enum AuthSessionStoreError {
    #[error("identity store unavailable")]
    Unavailable,
    #[error("invalid persisted identity")]
    CorruptRecord,
}

/// Strict atomic auth-session repository contract.
///
/// Concrete implementations live in `services/*-store` crates and call the
/// central conformance harness in `bcs-test-support::contract::repo::auth_session`
/// from their `conformance_*.rs` integration tests. The trait has no default
/// methods: every method is contract-bearing and must be implemented
/// explicitly. A `Noop*` placeholder is intentionally not provided here — the
/// enforced empty-state contract (missing scope → `Ok(0)`) makes a noop that
/// returns errors semantically wrong; tests that need a double should
/// implement the trait.
///
/// See the module docs for the full semantics.
#[async_trait]
pub trait AuthSessionRepoPort: Send + Sync {
    /// Read the current stored revision for `scope`.
    ///
    /// `Ok(0)` for a scope with no identity row — a missing identity row is
    /// NOT auto-created by this read and is NOT an error. The caller drives
    /// the initial install explicitly.
    async fn read_session_revision(&self, scope: &AuthSessionScope)
        -> Result<u64, AuthSessionStoreError>;

    /// Look up the live session for `scope` by `hash` at instant `now`.
    ///
    /// `Ok(None)` means the query succeeded but no live binding matches: the
    /// hash is expired, revoked, or unknown. `Err(Unavailable)` is a storage
    /// failure; implementations MUST NOT collapse `Ok(None)` and an error.
    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError>;

    /// Install the initial login session for `scope` (or rotate the very
    /// first revision). See [`InstallAuthSession`] and the module docs for
    /// the read-revision → pre-sign → CAS sequence.
    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError>;

    /// Rotate an existing session to a new token hash. See [`RotateAuthSession`].
    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError>;

    /// Revoke the session identified by `session_id` for `scope`. Also
    /// bumps the revision; a subsequent install CAS-fails if it races an
    /// in-flight rotate. See [`AuthSessionRevoke`].
    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError>;
}

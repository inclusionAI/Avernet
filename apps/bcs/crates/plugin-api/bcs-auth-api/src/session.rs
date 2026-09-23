//! Strict auth-session identity port for the V1 auth vertical slice.
//!
//! This trait is the auth-plugin layer's view of the strict CAS session
//! surface. It mirrors the shape of the persistence-layer
//! [`AuthSessionRepoPort`](bcs_service_api::port::repo::auth_session::AuthSessionRepoPort)
//! but lives in `bcs-auth-api` so BCS's auth plugins (Google/GitHub/agentpass
//! etc.) can depend on a session port without depending on
//! `bcs-service-api` — contract crates on both sides stay independent, and
//! [`bootstrap`](bcs)::identity_session_wiring glues a concrete repo
//! implementation into this port by translating every field.
//!
//! # Why a structurally-duplicated type?
//!
//! `bcs-auth-api` MUST NOT depend on `bcs-service-api` (the persistence
//! contract lives there, the auth-plugin contract lives here; circular
//! layering would force both crates to release together and would pull the
//! whole persistence layer into the auth plugins' dep graph). So this module
//! defines its own `SessionScope` / `SessionVersion` / `SessionSnapshot` /
//! `InstallSession` / `RotateSession` / `SessionWrite` / `SessionRevoke` /
//! `SessionStoreError` with byte-for-byte equivalent semantics to the repo
//! port's `AuthSession*` types. The bootstrap bridge translates every field;
//! no `From`/`Into` impls are exposed across the crate boundary (the two
//! types intentionally do NOT share an interface).
//!
//! # Method parity with `AuthSessionRepoPort`
//!
//! Five of the six methods match [`AuthSessionRepoPort`] method-by-method,
//! substituting only the parameter/result types. The sixth —
//! [`AuthSessionIdentityPort::ensure_identity`] — is new: it delegates to
//! the legacy
//! [`UserIdentityRepoPort::ensure_identity`](bcs_service_api::UserIdentityRepoPort)
//! that uses `Result<String, String>`, mapping its `Err(String)` to
//! [`SessionStoreError::Unavailable`] (a repository-level failure; the
//! legacy method surfaces no missing-row signal — `ensure_identity` is
//! idempotent create-or-confirm — so the only failure mode is a
//! storage-level fault). The strict port never swallows that error: every
//! `Err` propagates as `Err(SessionStoreError::Unavailable)`.
//!
//! # Storage-failure vs missing-row vs conflict semantics
//!
//! [`SessionStoreError`] carries no SQL, no endpoint, no secret. Two
//! variants:
//!
//! - [`SessionStoreError::Unavailable`] — storage failure (DB down,
//!   timeout, idempotent `ensure_identity` error). The caller cannot
//!   conclude the row state.
//! - [`SessionStoreError::CorruptRecord`] — invalid persisted identity. A
//!   structurally valid record whose fields are unusable (bad revision,
//!   torn write). The implementation MUST NOT silently repair such rows
//!   through this trait.
//!
//! Conflict, no-match, and not-current are *success-typed enums*, not
//! errors:
//!
//! - [`SessionWrite::Conflict`] — CAS failed because the stored revision
//!   advanced past `expected_revision` / `expected`. The caller MUST re-read
//!   and decide whether to retry the read → pre-sign → CAS loop.
//! - [`SessionRevoke::NotCurrent`] — the named `session_id` is no longer
//!   the live binding; the harness treats this as success.
//!
//! `get_session_by_hash` returns `Ok(None)` when the query succeeded but no
//! live binding matches; a DB error is `Err(SessionStoreError::Unavailable)`.
//! The port does NOT collapse these two cases.

use async_trait::async_trait;

/// The identity scope of an auth session. Mirrors
/// `AuthSessionScope` field-for-field.
///
/// The triple `(user_id, provider, env)` is the partition key for session
/// state; `env` is part of the scope so the same user identity in different
/// environments holds independent sessions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionScope {
    pub user_id: String,
    pub provider: String,
    pub env: String,
}

/// The version of an auth session; used for compare-and-set. Mirrors
/// `AuthSessionVersion` field-for-field.
///
/// `revision` strictly increases on every successful write (install,
/// rotate, revoke). `token_hash` is the opaque hash (storage-side; e.g.
/// SHA-256 of the JWT) of the currently live credential; the port never
/// receives the raw JWT.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionVersion {
    pub session_id: String,
    pub revision: u64,
    pub token_hash: String,
}

/// A point-in-time read of the live session for a scope. Mirrors
/// `AuthSessionSnapshot` field-for-field.
///
/// `display_name` and `avatar` are optional display fields persisted with
/// the session for caller convenience; they carry no security semantics.
#[derive(Clone, Debug)]
pub struct SessionSnapshot {
    pub scope: SessionScope,
    pub version: SessionVersion,
    pub expires_at: u64,
    pub username: String,
    pub display_name: Option<String>,
    pub avatar: Option<String>,
}

/// Command for the initial install of a login session. Mirrors
/// `InstallAuthSession` field-for-field.
///
/// The caller first reads the current revision via
/// [`AuthSessionIdentityPort::read_session_revision`], pre-signs the JWT,
/// then installs with `expected_revision` set to the read value. A CAS
/// conflict is returned as [`SessionWrite::Conflict`].
#[derive(Clone, Debug)]
pub struct InstallSession {
    pub scope: SessionScope,
    pub expected_revision: u64,
    pub next: SessionVersion,
    pub expires_at: u64,
}

/// Command for rotating an existing session to a new token hash. Mirrors
/// `RotateAuthSession` field-for-field.
///
/// `expected` is the live version the caller believes it is rotating from;
/// `next` is the freshly signed replacement. On success the stored
/// revision is `expected.revision.checked_add(1)`. `now` is the caller's
/// clock at rotate time and is the same `now` used to bound `expires_at`.
#[derive(Clone, Debug)]
pub struct RotateSession {
    pub scope: SessionScope,
    pub expected: SessionVersion,
    pub next: SessionVersion,
    pub expires_at: u64,
    pub now: u64,
}

/// Outcome of an install or rotate write. Mirrors `AuthSessionWrite`
/// field-for-field.
///
/// `Applied` indicates the CAS succeeded; `Conflict` indicates the stored
/// revision did not match. `Conflict` is a success-typed enum, NOT a
/// [`SessionStoreError`]: the caller re-reads and decides on a bounded
/// retry; an unconditional write retry at this layer is forbidden.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SessionWrite {
    Applied,
    Conflict,
}

/// Outcome of `revoke_session`. Mirrors `AuthSessionRevoke`
/// field-for-field.
///
/// `Revoked` — the named `session_id` was the live binding and is now
/// cleared. `NotCurrent` — the named session is no longer live (already
/// rotated or revoked); the harness treats this as success.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SessionRevoke {
    Revoked,
    NotCurrent,
}

/// Storage-level error for the auth-session identity port. Carries no SQL,
/// no endpoint, no secret. Mirrors `AuthSessionStoreError` field-for-field.
///
/// Only two variants: `Unavailable` (storage fault, the caller cannot
/// conclude the row state) and `CorruptRecord` (structurally valid
/// persisted row whose fields are unusable).
#[derive(Debug, thiserror::Error)]
pub enum SessionStoreError {
    #[error("identity store unavailable")]
    Unavailable,
    #[error("invalid persisted identity")]
    CorruptRecord,
}

/// Outcome of a successful session-issuing operation (install or refresh).
///
/// Carries only the freshly signed bearer token plus its `expires_at` clock
/// instant; the caller (HTTP cookie layer) is responsible for writing the
/// cookie and for any audit logging. `expires_at` is `now + idle_secs` at the
/// time of issue and is duplicated inside the signed JWT (`exp` claim).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IssuedSession {
    /// The freshly signed compact JWT string (`<header>.<claims>.<sig>`).
    pub token: String,
    /// Unix-seconds expiry. Equal to `exp` inside the signed JWT and to the
    /// `expires_at` written to the identity row via
    /// [`AuthSessionIdentityPort::install_login_session`] /
    /// [`AuthSessionIdentityPort::rotate_session`].
    pub expires_at: u64,
}

/// Strict OAuth session-engine error taxonomy.
///
/// Payload-free by design: callers translate it into HTTP statuses (or
/// internal metrics) without leaking storage details, JWT reasons, or user
/// identifiers. The plugin engine and its tests match on these variants
/// directly; the variants MUST stay exhaustive and free of strings so that
/// adding a new failure mode does not stabilize a payload shape.
///
/// Mapping (engine → HTTP, applied by the delivery adapter — never by the
/// engine):
/// - [`SessionAuthError::Invalid`]       → 401 unauthorized (bad/expired/revoked token, env mismatch, sid/revision mismatch).
/// - [`SessionAuthError::Forbidden`]     → 403 forbidden (reserved for the HTTP origin/provider-allowlist layer; the engine never returns it).
/// - [`SessionAuthError::Unavailable`]   → 503 service unavailable (storage layer fault).
/// - [`SessionAuthError::Internal`]      → 500 internal server error (overflow, sign failure).
/// - [`SessionAuthError::Conflict`]      → 409 conflict (install CAS conflict; refresh conflicts surface as 401/`Invalid` per spec).
///
/// `Forbidden` is part of the taxonomy so the engine-to-HTTP translation
/// table is complete; the engine itself never emits `Forbidden` (origin and
/// provider-allowlist checks live in the HTTP/source adapter, never in the
/// plugin engine).
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum SessionAuthError {
    #[error("invalid session")]
    Invalid,
    #[error("forbidden")]
    Forbidden,
    #[error("session store unavailable")]
    Unavailable,
    #[error("internal session error")]
    Internal,
    #[error("session cas conflict")]
    Conflict,
}

/// Strict atomic auth-session identity port.
///
/// Concrete implementations bridge a persistence-layer
/// `AuthSessionRepoPort` (and `UserIdentityRepoPort` for
/// [`Self::ensure_identity`]) into this trait; the production bridge lives
/// in `bootstrap::identity_session_wiring` (`RepoAuthSessionIdentityPort`).
/// The trait has no default methods: every method is contract-bearing and
/// must be implemented explicitly.
///
/// The semantics are the same as `AuthSessionRepoPort`; see that trait's
/// docs and the module docs above for the read-revision → pre-sign → CAS
/// protocol, the `Ok(0)`-for-missing-scope contract, and the classification
/// of conflict / not-current / `Ok(None)` as success-typed enums.
#[async_trait]
pub trait AuthSessionIdentityPort: Send + Sync {
    /// Read the current stored revision for `scope`. `Ok(0)` for a scope
    /// with no identity row — not auto-created, not an error.
    async fn read_session_revision(
        &self,
        scope: &SessionScope,
    ) -> Result<u64, SessionStoreError>;

    /// Look up the live session for `scope` by `hash` at instant `now`.
    /// `Ok(None)` means the query succeeded but no live binding matches:
    /// expired, revoked, or unknown hash. An `Err(Unavailable)` is a
    /// storage failure; implementations MUST NOT collapse `Ok(None)` and
    /// an error.
    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError>;

    /// Install the initial login session for `scope` (or rotate the very
    /// first revision). See [`InstallSession`] and the module docs.
    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError>;

    /// Rotate an existing session to a new token hash. See [`RotateSession`].
    async fn rotate_session(
        &self,
        command: RotateSession,
    ) -> Result<SessionWrite, SessionStoreError>;

    /// Revoke the session identified by `session_id` for `scope`. Also
    /// bumps the revision; a subsequent install CAS-fails if it races an
    /// in-flight rotate. See [`SessionRevoke`].
    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError>;

    /// Ensure `(provider, external_user_id)` has an internal identity row
    /// and return its `user_id`. Delegates to the legacy
    /// `UserIdentityRepoPort::ensure_identity` (which is idempotent
    /// create-or-confirm), propagating every `Err` as
    /// [`SessionStoreError::Unavailable`]. The legacy method surfaces no
    /// missing-row signal — `ensure_identity` is create-or-confirm — so
    /// the only failure mode is a storage-level fault, mapped to
    /// `Unavailable`. The bridge MUST NOT swallow: every `Err` propagates.
    ///
    /// `name` and `avatar` are write-through: on first create they seed the
    /// internal display name; on subsequent confirms they refresh the
    /// external display fields. The returned `user_id` is the internal
    /// subject of the JWT (`OAuthSessionClaims::sub`).
    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError>;
}

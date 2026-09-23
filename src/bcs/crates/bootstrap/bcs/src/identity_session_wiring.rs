//! Composition root for the strict `AuthSessionIdentityPort`.
//!
//! Bridges the persistence-layer [`AuthSessionRepoPort`] (the strict CAS
//! session surface in [`bcs_service_api::port::repo::auth_session`]) and
//! the legacy [`UserIdentityRepoPort`] (whose `ensure_identity` the strict
//! port delegates to, since it is the create-or-confirm path for the JWT
//! subject) into the auth-plugin-layer
//! [`bcs_auth_api::AuthSessionIdentityPort`].
//!
//! This bridge is field-by-field translation ONLY. The two contracts live
//! on opposite sides of the architecture split: `bcs-auth-api` is the
//! auth-plugin contract (no `bcs-service-api` dependency, structurally
//! duplicated `Session*` types), and `bcs-service-api::port::repo` is the
//! persistence contract (the `AuthSession*` types the stores implement).
//! Every type is translated by hand; no `From`/`Into` impls cross the
//! crate boundary, since the two types intentionally do NOT share an
//! interface and either side may evolve across releases.
//!
//! # Error mapping
//!
//! The strict port's [`bcs_auth_api::SessionStoreError`] and the repo
//! port's [`bcs_service_api::port::repo::auth_session::AuthSessionStoreError`]
//! have identical variant shape (`Unavailable` / `CorruptRecord`) and
//! identical semantics; the bridge keeps the variant name through the
//! translation. [`UserIdentityRepoPort::ensure_identity`] returns
//! `Result<String, String>` — its only failure mode is a storage-level
//! fault (the method is idempotent create-or-confirm, so it surfaces no
//! missing-row signal as `Err`). The bridge maps that `Err(String)` to
//! [`SessionStoreError::Unavailable`] and never swallows it.
//!
//! # Cross-layer independence
//!
//! The bridge holds `Arc<dyn AuthSessionRepoPort>` and
//! `Arc<dyn UserIdentityRepoPort>` and depends on neither concrete store
//! type. Production wires `DbUserIdentityStore` (which implements both
//! traits against the same `bcs_user_identities` row); tests wire either
//! real SQLite stores or fault-injection decorators equally well.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionSnapshot, SessionStoreError, SessionVersion, SessionWrite,
};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionSnapshot as RepoSnapshot,
    AuthSessionStoreError as RepoError, AuthSessionVersion as RepoVersion,
    AuthSessionWrite as RepoWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;

/// Map the persistence-layer `AuthSessionStoreError` to the auth-plugin
/// `SessionStoreError`. The two enums have identical variant shape and
/// identical semantics; this helper centralizes the variant mapping so
/// the impl reads cleanly call-by-call.
fn map_err(err: RepoError) -> SessionStoreError {
    match err {
        RepoError::Unavailable => SessionStoreError::Unavailable,
        RepoError::CorruptRecord => SessionStoreError::CorruptRecord,
    }
}

/// Translate an auth-session `Scope` from the plugin layer to the repo
/// layer. Field-by-field, since the two types intentionally share no
/// interface.
fn to_repo_scope(scope: &SessionScope) -> AuthSessionScope {
    AuthSessionScope {
        user_id: scope.user_id.clone(),
        provider: scope.provider.clone(),
        env: scope.env.clone(),
    }
}

/// Translate an auth-session `Scope` from the repo layer to the plugin
/// layer.
fn from_repo_scope(scope: AuthSessionScope) -> SessionScope {
    SessionScope {
        user_id: scope.user_id,
        provider: scope.provider,
        env: scope.env,
    }
}

/// Translate an auth-session `Version` from the plugin layer to the repo
/// layer.
fn to_repo_version(version: &SessionVersion) -> RepoVersion {
    RepoVersion {
        session_id: version.session_id.clone(),
        revision: version.revision,
        token_hash: version.token_hash.clone(),
    }
}

/// Translate an auth-session `Version` from the repo layer to the plugin
/// layer.
fn from_repo_version(version: RepoVersion) -> SessionVersion {
    SessionVersion {
        session_id: version.session_id,
        revision: version.revision,
        token_hash: version.token_hash,
    }
}

/// Translate an auth-session `Snapshot` from the repo layer to the plugin
/// layer.
fn from_repo_snapshot(snapshot: RepoSnapshot) -> SessionSnapshot {
    SessionSnapshot {
        scope: from_repo_scope(snapshot.scope),
        version: from_repo_version(snapshot.version),
        expires_at: snapshot.expires_at,
        username: snapshot.username,
        display_name: snapshot.display_name,
        avatar: snapshot.avatar,
    }
}

/// Bridge: a persistence-layer [`AuthSessionRepoPort`] (plus a
/// [`UserIdentityRepoPort`] for `ensure_identity`) wrapped as an
/// [`AuthSessionIdentityPort`].
///
/// Constructed by bootstrap's identity wiring in production (the future
/// wiring will provide a `db_session_identity_port` helper alongside the
/// existing `db_user_identity_port`); the bridge itself is the only
/// translation surface and lives here so test suites can build it from any
/// `Arc<dyn AuthSessionRepoPort>` + `Arc<dyn UserIdentityRepoPort>`.
pub struct RepoAuthSessionIdentityPort {
    repo: Arc<dyn AuthSessionRepoPort>,
    identities: Arc<dyn UserIdentityRepoPort>,
}

impl RepoAuthSessionIdentityPort {
    /// Construct the bridge. Both ports MUST come from the same backing
    /// store in production (the strict CAS port and the legacy
    /// `ensure_identity` path share the `bcs_user_identities` row), but the
    /// bridge enforces no structural constraint — tests may mix-and-match
    /// stand-ins.
    pub fn new(
        repo: Arc<dyn AuthSessionRepoPort>,
        identities: Arc<dyn UserIdentityRepoPort>,
    ) -> Self {
        Self { repo, identities }
    }
}

#[async_trait]
impl AuthSessionIdentityPort for RepoAuthSessionIdentityPort {
    async fn read_session_revision(&self, scope: &SessionScope) -> Result<u64, SessionStoreError> {
        self.repo
            .read_session_revision(&to_repo_scope(scope))
            .await
            .map_err(map_err)
    }

    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
        self.repo
            .get_session_by_hash(&to_repo_scope(scope), hash, now)
            .await
            .map_err(map_err)
            .map(|opt| opt.map(from_repo_snapshot))
    }

    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        let repo_command = InstallAuthSession {
            scope: to_repo_scope(&command.scope),
            expected_revision: command.expected_revision,
            next: to_repo_version(&command.next),
            expires_at: command.expires_at,
        };
        self.repo
            .install_login_session(repo_command)
            .await
            .map_err(map_err)
            .map(|w| match w {
                RepoWrite::Applied => SessionWrite::Applied,
                RepoWrite::Conflict => SessionWrite::Conflict,
            })
    }

    async fn rotate_session(
        &self,
        command: RotateSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        let repo_command = RotateAuthSession {
            scope: to_repo_scope(&command.scope),
            expected: to_repo_version(&command.expected),
            next: to_repo_version(&command.next),
            expires_at: command.expires_at,
            now: command.now,
        };
        self.repo
            .rotate_session(repo_command)
            .await
            .map_err(map_err)
            .map(|w| match w {
                RepoWrite::Applied => SessionWrite::Applied,
                RepoWrite::Conflict => SessionWrite::Conflict,
            })
    }

    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError> {
        self.repo
            .revoke_session(&to_repo_scope(scope), session_id)
            .await
            .map_err(map_err)
            .map(|r| match r {
                AuthSessionRevoke::Revoked => SessionRevoke::Revoked,
                AuthSessionRevoke::NotCurrent => SessionRevoke::NotCurrent,
            })
    }

    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError> {
        self.identities
            .ensure_identity(provider, external_user_id, name, avatar, env)
            .await
            // The legacy `ensure_identity` is idempotent create-or-confirm;
            // its only failure mode is a storage-level fault (the method
            // surfaces no missing-row `Err`). Per the task contract we
            // MUST NOT invent a new variant: `Err(String)` → `Unavailable`,
            // documented here and in the trait's rustdoc. Every Err
            // propagates — the bridge does not swallow or log-and-default.
            .map_err(|_| SessionStoreError::Unavailable)
    }
}

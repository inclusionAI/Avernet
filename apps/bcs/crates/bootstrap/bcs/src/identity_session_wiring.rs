//! Composition root for the strict V1 API `AuthSessionIdentityPort`.
//!
//! [`RepoAuthSessionIdentityPort`] bridges the persistence-layer
//! `bcs_service_api::port::repo::auth_session::AuthSessionRepoPort` (+
//! `UserIdentityRepoPort` for identity creation) to the plugin-API
//! [`bcs_auth_api::AuthSessionIdentityPort`] consumed by the V1 API auth
//! plugin chain. The two contract crates do not depend on each other, and
//! this adapter is a PURE field-by-field translation: it adds no semantics,
//! no fallbacks, and no retry logic. In particular `snapshot.username` may
//! be empty for a nameless identity — any internal-ID display fallback is
//! a downstream consumer concern (application/plugin layer), never baked
//! here. `ensure_identity` failures propagate to the strict port as
//! [`SessionStoreError::Unavailable`]; the error-swallowing Option
//! lookups are never used on that path.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::session::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionSnapshot, SessionStoreError, SessionVersion, SessionWrite,
};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke as RepoSessionRevoke, AuthSessionRepoPort, AuthSessionScope,
    AuthSessionSnapshot, AuthSessionStoreError as RepoSessionStoreError, AuthSessionVersion,
    AuthSessionWrite as RepoSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;

/// Translate a persistence store error into the plugin-API error category
/// (1:1 blunt translation; no context is invented or dropped).
fn store_error(error: RepoSessionStoreError) -> SessionStoreError {
    match error {
        RepoSessionStoreError::Unavailable => SessionStoreError::Unavailable,
        RepoSessionStoreError::CorruptRecord => SessionStoreError::CorruptRecord,
    }
}

/// Translate a CAS write outcome, 1:1.
fn port_write(outcome: RepoSessionWrite) -> SessionWrite {
    match outcome {
        RepoSessionWrite::Applied => SessionWrite::Applied,
        RepoSessionWrite::Conflict => SessionWrite::Conflict,
    }
}

/// Translate a revoke outcome, 1:1.
fn port_revoke(outcome: RepoSessionRevoke) -> SessionRevoke {
    match outcome {
        RepoSessionRevoke::Revoked => SessionRevoke::Revoked,
        RepoSessionRevoke::NotCurrent => SessionRevoke::NotCurrent,
    }
}

/// Translate the trusted scope, field by field.
fn repo_scope(scope: &SessionScope) -> AuthSessionScope {
    AuthSessionScope {
        user_id: scope.user_id.clone(),
        provider: scope.provider.clone(),
        env: scope.env.clone(),
    }
}

/// Translate one atomic session version, field by field.
fn repo_version(version: &SessionVersion) -> AuthSessionVersion {
    AuthSessionVersion {
        session_id: version.session_id.clone(),
        revision: version.revision,
        token_hash: version.token_hash.clone(),
    }
}

/// Translate the joined read model (scope + version + display fields),
/// field by field. `username` is passed through as-is and may be empty.
fn port_snapshot(snapshot: AuthSessionSnapshot) -> SessionSnapshot {
    SessionSnapshot {
        scope: SessionScope {
            user_id: snapshot.scope.user_id,
            provider: snapshot.scope.provider,
            env: snapshot.scope.env,
        },
        version: SessionVersion {
            session_id: snapshot.version.session_id,
            revision: snapshot.version.revision,
            token_hash: snapshot.version.token_hash,
        },
        expires_at: snapshot.expires_at,
        username: snapshot.username,
        display_name: snapshot.display_name,
        avatar: snapshot.avatar,
    }
}

/// Bridge the persistence contracts to the strict plugin-API port.
///
/// Only contract translation happens here: the five session methods map
/// 1:1 onto `AuthSessionRepoPort`, and `ensure_identity` maps onto
/// `UserIdentityRepoPort::ensure_identity` (a Result-returning creation,
/// never a swallowing Option lookup) with errors classified as
/// `SessionStoreError::Unavailable`.
pub struct RepoAuthSessionIdentityPort {
    repo: Arc<dyn AuthSessionRepoPort>,
    identities: Arc<dyn UserIdentityRepoPort>,
}

impl RepoAuthSessionIdentityPort {
    pub fn new(repo: Arc<dyn AuthSessionRepoPort>, identities: Arc<dyn UserIdentityRepoPort>) -> Self {
        Self { repo, identities }
    }
}

#[async_trait]
impl AuthSessionIdentityPort for RepoAuthSessionIdentityPort {
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
            .map_err(|_| SessionStoreError::Unavailable)
    }

    async fn read_session_revision(
        &self,
        scope: &SessionScope,
    ) -> Result<u64, SessionStoreError> {
        self.repo
            .read_session_revision(&repo_scope(scope))
            .await
            .map_err(store_error)
    }

    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
        self.repo
            .get_session_by_hash(&repo_scope(scope), hash, now)
            .await
            .map(|found| found.map(port_snapshot))
            .map_err(store_error)
    }

    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        self.repo
            .install_login_session(InstallAuthSession {
                scope: repo_scope(&command.scope),
                expected_revision: command.expected_revision,
                next: repo_version(&command.next),
                expires_at: command.expires_at,
            })
            .await
            .map(port_write)
            .map_err(store_error)
    }

    async fn rotate_session(
        &self,
        command: RotateSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        self.repo
            .rotate_session(RotateAuthSession {
                scope: repo_scope(&command.scope),
                expected: repo_version(&command.expected),
                next: repo_version(&command.next),
                expires_at: command.expires_at,
                now: command.now,
            })
            .await
            .map(port_write)
            .map_err(store_error)
    }

    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError> {
        self.repo
            .revoke_session(&repo_scope(scope), session_id)
            .await
            .map(port_revoke)
            .map_err(store_error)
    }
}

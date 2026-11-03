//! Composition root for the OAuth `UserIdentityPort`.
//!
//! Bridges the persistence-layer `UserIdentityRepoPort` (implemented by
//! `bcs-user-identity` stores) to the auth-plugin `bcs_auth_api::UserIdentityPort`
//! consumed by `GoogleAuthPlugin`, GitHub auth plugin, and the `/auth/*` routes.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::session::AuthSessionIdentityPort;
use bcs_auth_api::{AuthError, UserIdentityInfo, UserIdentityPort};
use bcs_db_api::DbPlugin;
use bcs_service_api::port::repo::auth_session::AuthSessionRepoPort;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::{DbUserIdentityStore, MemoryUserIdentityRepo};

use crate::identity_session_wiring::RepoAuthSessionIdentityPort;
use crate::plugins::DbPluginKind;

/// Convert a persistence-layer `UserIdentity` into an auth-layer display struct.
fn to_display_info(row: &bcs_service_api::UserIdentity) -> UserIdentityInfo {
    UserIdentityInfo {
        user_id: row.user_id.clone(),
        auth_source: row.auth_source.clone(),
        user_name: row.user_name.clone(),
        external_user_name: row.external_user_name.clone(),
        avatar: row.avatar.clone(),
    }
}

/// Adapts a persistence `UserIdentityRepoPort` into the auth-plugin
/// `bcs_auth_api::UserIdentityPort`. The two traits share method shapes but
/// live in different architecture layers, so the adapter translates
/// error types, return shapes, and adds the `avatar` passthrough.
pub struct RepoUserIdentityPort {
    repo: Arc<dyn UserIdentityRepoPort>,
}

impl RepoUserIdentityPort {
    pub fn new(repo: Arc<dyn UserIdentityRepoPort>) -> Self {
        Self { repo }
    }
}

#[async_trait]
impl UserIdentityPort for RepoUserIdentityPort {
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError> {
        self.repo
            .ensure_identity(
                auth_source,
                external_user_id,
                external_user_name,
                avatar,
                env,
            )
            .await
            .map_err(AuthError::LookupFailed)
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(self.repo.lookup_by_user_id(user_id, auth_source).await)
    }

    async fn get_identity_by_token(
        &self,
        token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(self
            .repo
            .get_by_token(token)
            .await
            .map(|r| to_display_info(&r)))
    }

    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(self
            .repo
            .get_by_user_id_display(user_id)
            .await
            .map(|r| to_display_info(&r)))
    }
}

/// The two identity bridges over one store: the display-oriented legacy
/// `UserIdentityPort` and the strict V1 API `AuthSessionIdentityPort`.
///
/// Both bridges are built over the SAME store instance: for the in-memory
/// store that single instance is what keeps display-path identity writes
/// (legacy `/auth/*`) and V1 strict session operations in one state; the
/// DB bridges share the database naturally.
pub struct IdentityPorts {
    pub auth_session_identity: Arc<dyn AuthSessionIdentityPort>,
    pub user_identity: Arc<dyn UserIdentityPort>,
}

/// Build both identity bridges from the selected DB plugin's store. The
/// strict session bridge is constructed alongside the legacy display
/// bridge; the display bridge's construction and behavior are unchanged.
pub fn db_identity_ports(db_kind: DbPluginKind, db: Arc<dyn DbPlugin>) -> IdentityPorts {
    let (identity_repo, session_repo): (Arc<dyn UserIdentityRepoPort>, Arc<dyn AuthSessionRepoPort>) =
        match db_kind {
            DbPluginKind::LocalSqlite => {
                let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(db));
                (store.clone(), store)
            }
            DbPluginKind::Mysql => {
                let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::mysql(db));
                (store.clone(), store)
            }
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no user identity store wiring",
                    provider
                )
            }
        };
    IdentityPorts {
        user_identity: Arc::new(RepoUserIdentityPort::new(identity_repo.clone())),
        auth_session_identity: Arc::new(RepoAuthSessionIdentityPort::new(
            session_repo,
            identity_repo,
        )),
    }
}

/// Build both identity bridges over one fresh in-memory store, for
/// standalone / test paths that have no DB plugin. Identities do not
/// survive a restart.
pub fn memory_identity_ports() -> IdentityPorts {
    let store: Arc<MemoryUserIdentityRepo> = Arc::new(MemoryUserIdentityRepo::new());
    IdentityPorts {
        user_identity: Arc::new(RepoUserIdentityPort::new(store.clone())),
        auth_session_identity: Arc::new(RepoAuthSessionIdentityPort::new(
            store.clone(),
            store,
        )),
    }
}

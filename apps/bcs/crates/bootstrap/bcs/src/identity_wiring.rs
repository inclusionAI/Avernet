//! Composition root for the OAuth `UserIdentityPort`.
//!
//! Bridges the persistence-layer `UserIdentityRepoPort` (implemented by
//! `bcs-user-identity` stores) to the auth-plugin `bcs_auth_api::UserIdentityPort`
//! consumed by `GoogleAuthPlugin`, GitHub auth plugin, and the `/auth/*` routes.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{AuthError, UserIdentityInfo, UserIdentityPort};
use bcs_db_api::DbPlugin;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::{DbUserIdentityStore, MemoryUserIdentityRepo};

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

    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), AuthError> {
        self.repo
            .update_token(user_id, token, expire_at)
            .await
            .map_err(AuthError::LookupFailed)
    }
}

/// Build a DB-backed identity port from the selected DB plugin.
///
/// Public builds wire the identity store selected by the unified database kind.
pub fn db_user_identity_port(
    db_kind: DbPluginKind,
    db: Arc<dyn DbPlugin>,
) -> Arc<dyn UserIdentityPort> {
    let repo: Arc<dyn UserIdentityRepoPort> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbUserIdentityStore::sqlite(db)),
        DbPluginKind::Mysql => Arc::new(DbUserIdentityStore::mysql(db)),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no user identity store wiring",
                provider
            )
        }
    };
    Arc::new(RepoUserIdentityPort::new(repo))
}

/// Build an in-memory identity port for standalone / test paths that have no
/// DB plugin. Identities do not survive a restart.
pub fn memory_user_identity_port() -> Arc<dyn UserIdentityPort> {
    Arc::new(RepoUserIdentityPort::new(Arc::new(
        MemoryUserIdentityRepo::new(),
    )))
}

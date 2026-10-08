//! Composition root for the OAuth `UserIdentityPort`.
//!
//! Bridges the persistence-layer `UserIdentityRepoPort` (implemented by
//! `bcs-user-identity` stores) to the auth-plugin `bcs_auth_api::UserIdentityPort`
//! consumed by `GoogleAuthPlugin`, GitHub auth plugin, and the `/auth/*` routes.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{AuthError, UserIdentityInfo, UserIdentityPort};
use bcs_db_api::DbPlugin;
use bcs_service_api::port::repo::auth_session::AuthSessionRepoPort;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::{DbUserIdentityStore, MemoryUserIdentityRepo};

use crate::identity_session_wiring::RepoAuthSessionIdentityPort;
use crate::plugins::DbPluginKind;

/// The identity-repo pair backing the OAuth login/subscriber surface. Both
/// ports share ONE store instance so the legacy display lookups and the
/// strict CAS session writes stay consistent (spec §8.5: a shared record
/// must not have two independent writers).
pub struct UserIdentityStores {
    pub identity: Arc<dyn UserIdentityRepoPort>,
    pub auth_session: Arc<dyn AuthSessionRepoPort>,
}

/// Build one shared store instance pair from the selected DB plugin.
/// Task 11 (spec §8.6): the strict session surface and the legacy
/// `ensure_identity` surface MUST observe the same rows.
pub fn db_identity_stores(db_kind: DbPluginKind, db: Arc<dyn DbPlugin>) -> UserIdentityStores {
    let store: Arc<DbUserIdentityStore> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbUserIdentityStore::sqlite(db)),
        DbPluginKind::Mysql => Arc::new(DbUserIdentityStore::mysql(db)),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{provider}' has no user identity store wiring"
            )
        }
    };
    UserIdentityStores {
        identity: store.clone(),
        auth_session: store,
    }
}

/// Shared in-memory store pair for standalone / test paths without a DB
/// plugin. Identities do not survive a restart.
pub fn memory_identity_stores() -> UserIdentityStores {
    let store = Arc::new(MemoryUserIdentityRepo::new());
    UserIdentityStores {
        identity: store.clone(),
        auth_session: store,
    }
}

/// Build the strict `AuthSessionIdentityPort` over the shared store pair
/// (the CAS install/rotate/revoke surface the OAuth session engine drives).
pub fn auth_session_identity_port(stores: &UserIdentityStores) -> Arc<dyn bcs_auth_api::AuthSessionIdentityPort> {
    Arc::new(RepoAuthSessionIdentityPort::new(
        stores.auth_session.clone(),
        stores.identity.clone(),
    ))
}

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

/// Build the legacy display identity port from the shared store pair.
pub fn user_identity_port(stores: &UserIdentityStores) -> Arc<dyn UserIdentityPort> {
    Arc::new(RepoUserIdentityPort::new(stores.identity.clone()))
}

/// Build a DB-backed identity port from the selected DB plugin.
///
/// Public builds wire the identity store selected by the unified database kind.
pub fn db_user_identity_port(
    db_kind: DbPluginKind,
    db: Arc<dyn DbPlugin>,
) -> Arc<dyn UserIdentityPort> {
    Arc::new(RepoUserIdentityPort::new(
        db_identity_stores(db_kind, db).identity,
    ))
}

/// Build an in-memory identity port for standalone / test paths that have no
/// DB plugin. Identities do not survive a restart.
pub fn memory_user_identity_port() -> Arc<dyn UserIdentityPort> {
    Arc::new(RepoUserIdentityPort::new(
        memory_identity_stores().identity,
    ))
}

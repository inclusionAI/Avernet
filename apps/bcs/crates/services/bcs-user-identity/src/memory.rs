//! In-memory `UserIdentityRepoPort` + `AuthSessionRepoPort` implementation.
//!
//! ONE lock guards the whole store: the identity rows, the internal id set,
//! and the scope-keyed auth-session index all live behind
//! `RwLock<IdentityStore>`, so every session compare-and-set reads and
//! writes under a single lock domain (never a read-then-write across two
//! locks).

use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionSnapshot,
    AuthSessionStoreError, AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::{UserIdentity, UserIdentityRepoPort};

use crate::session_memory::{SessionIndex, SessionScopeKey};

/// Generate an internal user id: 12 base62 chars (no prefix), drawn from the
/// OS CSPRNG via `uuid` v4 so ids are unpredictable, not just unique.
pub fn generate_user_id() -> String {
    const ALPHABET: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let bytes = uuid::Uuid::new_v4().into_bytes();
    let mut s = String::with_capacity(12);
    for b in bytes.iter().take(12) {
        s.push(ALPHABET[(*b as usize) % ALPHABET.len()] as char);
    }
    s
}

pub(crate) type ExternalKey = (String, String, String); // (auth_source, external_user_id, env)

/// All mutable state, held behind ONE lock so identity updates and session
/// compare-and-sets share a single lock domain.
#[derive(Default)]
pub(crate) struct IdentityStore {
    by_external: HashMap<ExternalKey, UserIdentity>,
    user_ids: HashSet<String>,
    sessions: SessionIndex,
}

impl IdentityStore {
    fn read_session_revision(&self, scope: &AuthSessionScope) -> u64 {
        self.sessions.current_revision(scope)
    }

    fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        self.sessions.get_by_hash(scope, hash, now, &self.by_external)
    }

    fn install_login_session(
        &mut self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        self.sessions.install(command, &self.by_external)
    }

    fn rotate_session(
        &mut self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        self.sessions.rotate(command, &self.by_external)
    }

    fn revoke_session(
        &mut self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        self.sessions.revoke(scope, session_id, &self.by_external)
    }
}

#[derive(Default)]
pub struct MemoryUserIdentityRepo {
    store: RwLock<IdentityStore>,
}

impl MemoryUserIdentityRepo {
    pub fn new() -> Self {
        Self::default()
    }

    fn key(auth_source: &str, external_user_id: &str, env: &str) -> ExternalKey {
        (
            auth_source.to_string(),
            external_user_id.to_string(),
            env.to_string(),
        )
    }

    /// Scope address of one identity row for the session index:
    /// `(user_id, provider, env)`.
    fn scope_key(user_id: &str, provider: &str, env: &str) -> SessionScopeKey {
        (user_id.to_string(), provider.to_string(), env.to_string())
    }
}

#[async_trait]
impl UserIdentityRepoPort for MemoryUserIdentityRepo {
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, String> {
        let key = Self::key(auth_source, external_user_id, env);
        let mut store = self.store.write().await;
        if let Some(existing) = store.by_external.get_mut(&key) {
            existing.external_user_name = external_user_name.map(|s| s.to_string());
            existing.avatar = avatar.map(|s| s.to_string());
            return Ok(existing.user_id.clone());
        }
        // Allocate a unique user_id (retry on the rare collision).
        let mut user_id = generate_user_id();
        let mut attempts = 0;
        while store.user_ids.contains(&user_id) {
            attempts += 1;
            if attempts >= 5 {
                return Err("user_id collision retry exhausted".to_string());
            }
            user_id = generate_user_id();
        }
        store.user_ids.insert(user_id.clone());
        store.sessions.register_identity(
            Self::scope_key(&user_id, auth_source, env),
            key.clone(),
        );
        store.by_external.insert(
            key,
            UserIdentity {
                user_id: user_id.clone(),
                auth_source: auth_source.to_string(),
                external_user_id: external_user_id.to_string(),
                // Initialize the internal display name from the external one on
                // first creation; the hit branch above leaves it untouched.
                user_name: external_user_name.map(|s| s.to_string()),
                external_user_name: external_user_name.map(|s| s.to_string()),
                avatar: avatar.map(|s| s.to_string()),
                token: None,
                token_expire_at: None,
                env: env.to_string(),
            },
        );
        Ok(user_id)
    }

    async fn lookup_user_id(
        &self,
        auth_source: &str,
        external_user_id: &str,
        env: &str,
    ) -> Option<String> {
        let key = Self::key(auth_source, external_user_id, env);
        self.store
            .read()
            .await
            .by_external
            .get(&key)
            .map(|u| u.user_id.clone())
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Option<String> {
        let store = self.store.read().await;
        store
            .by_external
            .values()
            .find(|u| u.user_id == user_id && u.auth_source == auth_source)
            .map(|u| u.external_user_id.clone())
    }

    async fn get_by_token(&self, token: &str) -> Option<UserIdentity> {
        let store = self.store.read().await;
        store
            .by_external
            .values()
            .find(|u| u.token.as_deref() == Some(token))
            .cloned()
    }

    async fn get_by_user_id_display(&self, user_id: &str) -> Option<UserIdentity> {
        let store = self.store.read().await;
        store
            .by_external
            .values()
            .find(|u| u.user_id == user_id)
            .cloned()
    }

}

/// Strict atomic session operations. Every call takes the ONE store lock and
/// delegates the compare-and-set to [`crate::session_memory`], which joins the
/// identity display fields from the same shared record.
#[async_trait]
impl AuthSessionRepoPort for MemoryUserIdentityRepo {
    async fn read_session_revision(
        &self,
        scope: &AuthSessionScope,
    ) -> Result<u64, AuthSessionStoreError> {
        Ok(self.store.read().await.read_session_revision(scope))
    }

    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        self.store.read().await.get_session_by_hash(scope, hash, now)
    }

    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        self.store.write().await.install_login_session(command)
    }

    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        self.store.write().await.rotate_session(command)
    }

    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        self.store
            .write()
            .await
            .revoke_session(scope, session_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_id_format() {
        let id = generate_user_id();
        assert_eq!(id.len(), 12, "id={id}");
        assert!(id.chars().all(|c| c.is_ascii_alphanumeric()), "id={id}");
    }

    #[tokio::test]
    async fn ensure_idempotent_on_external_key() {
        let repo = MemoryUserIdentityRepo::new();
        let a = repo.ensure_identity("cookie", "12345", Some("name1"), None, "dev").await.unwrap();
        let b = repo.ensure_identity("cookie", "12345", Some("name2"), None, "dev").await.unwrap();
        assert_eq!(a, b, "same external identity must map to same user_id");
        assert_eq!(repo.lookup_user_id("cookie", "12345", "dev").await.as_deref(), Some(a.as_str()));
    }

    #[tokio::test]
    async fn different_external_distinct_ids() {
        let repo = MemoryUserIdentityRepo::new();
        let a = repo.ensure_identity("cookie", "111", None, None, "dev").await.unwrap();
        let b = repo.ensure_identity("cookie", "222", None, None, "dev").await.unwrap();
        assert_ne!(a, b);
    }

    // NOTE (V1 API auth plan, Task 11): the legacy `update_token`
    // single-session cases that lived here (bind/overwrite the identity
    // row's token column) are REPLACED by the strict session lifecycle
    // coverage: crate-level `conformance_auth_session_memory.rs` /
    // `conformance_auth_session_sqlite.rs` drive the central
    // `auth_session_repo_port_contract_tests` harness over THIS SAME store
    // (install → rotate → revoke atomicity, single-active-session via
    // revision CAS), and `bcs-test-support`'s `oauth_session_engine_
    // contract_tests` pins the lifecycle end-to-end. No legacy
    // unconditional writer exists anymore.

    #[tokio::test]
    async fn get_by_token_returns_none_without_a_legacy_binding() {
        // Session credentials are now bound ONLY through the strict session
        // columns (`get_session_by_hash`); the identity row's legacy token
        // column has no writer, so a raw token lookup must find nothing.
        let repo = MemoryUserIdentityRepo::new();
        repo.ensure_identity("google", "sub-1", Some("Alice"), Some("https://img.url"), "dev").await.unwrap();
        assert!(repo.get_by_token("nonexistent").await.is_none());
    }

    #[tokio::test]
    async fn get_by_user_id_display_finds_identity() {
        let repo = MemoryUserIdentityRepo::new();
        let uid = repo.ensure_identity("github", "gh-42", Some("Bob"), Some("https://gh.img"), "dev").await.unwrap();

        let found = repo.get_by_user_id_display(&uid).await.unwrap();
        assert_eq!(found.user_id, uid);
        assert_eq!(found.auth_source, "github");
        assert_eq!(found.external_user_name.as_deref(), Some("Bob"));
        assert_eq!(found.avatar.as_deref(), Some("https://gh.img"));
    }
}
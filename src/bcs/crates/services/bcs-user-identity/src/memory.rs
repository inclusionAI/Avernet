//! In-memory `UserIdentityRepoPort` + internal `user_id` generation, and the
//! storage layer shared with `AuthSessionRepoPort` (impl in
//! [`crate::session_memory`]).
//!
//! Both ports share a single identity record per `(auth_source,
//! external_user_id, env)` row, stored in `by_external`. The record is a
//! [`MemoryIdentityRecord`] wrapper that bundles the legacy
//! [`UserIdentity`] fields with the new session state
//! ([`MemorySessionState`]). The wrapper exists so the legacy port and the
//! new strict-CAS session port operate against the same physical row under the
//! same `by_external` lock — there is no second user table and no parallel
//! session map keyed by session-id; the [`AuthSessionScope`] `(user_id,
//! provider, env)` triple maps to the same record via a `by_external` values
//! scan (the `user_id` is globally_UNIQUE inside the repo).
//!
//! Legacy field semantics are unchanged: `ensure_identity` is idempotent on
//! `(auth_source, external_user_id, env)`; the strict CAS session writes
//! single row's `token`/`token_expire_at` (single-session legacy model). The
//! session-state half defaults to revision `0` / no hash, the contract's
//! "missing-install" state, so a row created via legacy `ensure_identity`
//! presents `read_session_revision == 0` until the first `install_login_session`
//! CAS bumps the counter to 1.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_service_api::{UserIdentity, UserIdentityRepoPort};

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

/// One per-scope identity record: legacy identity fields plus the new
/// auth-session state. Both ports operate on the same record under the same
/// `by_external` lock; see the module docs.
pub(crate) struct MemoryIdentityRecord {
    /// The legacy identity row. `UserIdentityRepoPort` reads and writes only
    /// this part.
    pub identity: UserIdentity,
    /// The new strict-CAS auth-session state. `AuthSessionRepoPort` reads and
    /// writes only this part.
    pub session: MemorySessionState,
}

/// Session state attached to the existing identity record.
///
/// `revision == 0` means no session has been installed yet (the
/// contract's "missing-install" state: `read_session_revision` returns `0`).
/// `token_hash == None` means the session has been revoked (or never
/// installed); `get_session_by_hash` returns `Ok(None)` for this row. After a
/// successful `install_login_session` or `rotate_session` the hash and
/// `expires_at` are populated; after a `revoke_session` the hash and
/// `expires_at` are cleared and the revision is bumped.
#[derive(Default)]
pub(crate) struct MemorySessionState {
    /// CAS counter — bumped on every successful install/rotate/revoke.
    pub revision: u64,
    /// The live session id; `None` before the first install or after revoke.
    pub session_id: Option<String>,
    /// The opaque hash of the currently live credential; `None` before the
    /// first install or after revoke (cleared, not rotated-to-replacement).
    pub token_hash: Option<String>,
    /// Unix-seconds expiry of the live token; `None` before install or after
    /// revoke.
    pub expires_at: Option<u64>,
}

#[derive(Default)]
pub struct MemoryUserIdentityRepo {
    pub(crate) by_external: RwLock<HashMap<ExternalKey, MemoryIdentityRecord>>,
    user_ids: RwLock<std::collections::HashSet<String>>,
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
        let mut map = self.by_external.write().await;
        if let Some(existing) = map.get_mut(&key) {
            existing.identity.external_user_name = external_user_name.map(|s| s.to_string());
            existing.identity.avatar = avatar.map(|s| s.to_string());
            return Ok(existing.identity.user_id.clone());
        }
        // Allocate a unique user_id (retry on the rare collision).
        let mut ids = self.user_ids.write().await;
        let mut user_id = generate_user_id();
        let mut attempts = 0;
        while ids.contains(&user_id) {
            attempts += 1;
            if attempts >= 5 {
                return Err("user_id collision retry exhausted".to_string());
            }
            user_id = generate_user_id();
        }
        ids.insert(user_id.clone());
        map.insert(
            key,
            MemoryIdentityRecord {
                identity: UserIdentity {
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
                session: MemorySessionState::default(),
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
        self.by_external
            .read()
            .await
            .get(&key)
            .map(|rec| rec.identity.user_id.clone())
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Option<String> {
        self.by_external
            .read()
            .await
            .values()
            .find(|rec| rec.identity.user_id == user_id && rec.identity.auth_source == auth_source)
            .map(|rec| rec.identity.external_user_id.clone())
    }

    async fn get_by_token(&self, token: &str) -> Option<UserIdentity> {
        self.by_external
            .read()
            .await
            .values()
            .find(|rec| rec.identity.token.as_deref() == Some(token))
            .map(|rec| rec.identity.clone())
    }

    async fn get_by_user_id_display(&self, user_id: &str) -> Option<UserIdentity> {
        self.by_external
            .read()
            .await
            .values()
            .find(|rec| rec.identity.user_id == user_id)
            .map(|rec| rec.identity.clone())
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

    /// Task 11 coverage transfer: the removed unconditional `update_token`
    /// write path is superseded by the strict `install_login_session` CAS
    /// write; the identity row it writes stays readable via the legacy
    /// `get_by_token` lookup (session cases live in the shared session
    /// harness — see `session_memory.rs` and the conformance suite).
    #[tokio::test]
    async fn get_by_token_finds_identity() {
        use bcs_service_api::port::repo::auth_session::{
            AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion, InstallAuthSession,
        };

        let repo = MemoryUserIdentityRepo::new();
        let uid = repo.ensure_identity("google", "sub-1", Some("Alice"), Some("https://img.url"), "dev").await.unwrap();
        repo.install_login_session(InstallAuthSession {
            scope: AuthSessionScope { user_id: uid.clone(), provider: "google".into(), env: "dev".into() },
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: "sid-1".into(),
                revision: 1,
                token_hash: "jwt-token-abc".into(),
            },
            expires_at: 9999,
        }).await.unwrap();

        let found = repo.get_by_token("jwt-token-abc").await.unwrap();
        assert_eq!(found.user_id, uid);
        assert_eq!(found.auth_source, "google");
        assert_eq!(found.external_user_name.as_deref(), Some("Alice"));
        assert_eq!(found.avatar.as_deref(), Some("https://img.url"));
    }

    #[tokio::test]
    async fn get_by_token_returns_none_for_unknown() {
        let repo = MemoryUserIdentityRepo::new();
        assert!(repo.get_by_token("nonexistent").await.is_none());
    }

    /// Task 11 coverage transfer: single-session overwrite semantics are
    /// now expressed as install(new session) — the new hash is live and the
    /// previous binding is gone.
    #[tokio::test]
    async fn session_install_overwrites_previous_binding() {
        use bcs_service_api::port::repo::auth_session::{
            AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion, InstallAuthSession,
        };

        let repo = MemoryUserIdentityRepo::new();
        let uid = repo.ensure_identity("google", "sub-2", None, None, "dev").await.unwrap();
        for (sid, hash) in [("sid-old", "old-jwt"), ("sid-new", "new-jwt")] {
            let expected = repo.read_session_revision(&AuthSessionScope {
                user_id: uid.clone(), provider: "google".into(), env: "dev".into(),
            }).await.unwrap();
            repo.install_login_session(InstallAuthSession {
                scope: AuthSessionScope { user_id: uid.clone(), provider: "google".into(), env: "dev".into() },
                expected_revision: expected,
                next: AuthSessionVersion {
                    session_id: sid.into(), revision: expected + 1, token_hash: hash.into(),
                },
                expires_at: 2000,
            }).await.unwrap();
        }

        assert!(repo.get_by_token("old-jwt").await.is_none());
        let found = repo.get_by_token("new-jwt").await.unwrap();
        assert_eq!(found.user_id, uid);
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

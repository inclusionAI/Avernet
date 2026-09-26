//! In-memory `UserIdentityRepoPort` + internal `user_id` generation.

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

type ExternalKey = (String, String, String); // (auth_source, external_user_id, env)

#[derive(Default)]
pub struct MemoryUserIdentityRepo {
    by_external: RwLock<HashMap<ExternalKey, UserIdentity>>,
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
            existing.external_user_name = external_user_name.map(|s| s.to_string());
            existing.avatar = avatar.map(|s| s.to_string());
            return Ok(existing.user_id.clone());
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
        self.by_external
            .read()
            .await
            .get(&key)
            .map(|u| u.user_id.clone())
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
            .find(|u| u.user_id == user_id && u.auth_source == auth_source)
            .map(|u| u.external_user_id.clone())
    }

    async fn get_by_token(&self, token: &str) -> Option<UserIdentity> {
        self.by_external
            .read()
            .await
            .values()
            .find(|u| u.token.as_deref() == Some(token))
            .cloned()
    }

    async fn get_by_user_id_display(&self, user_id: &str) -> Option<UserIdentity> {
        self.by_external
            .read()
            .await
            .values()
            .find(|u| u.user_id == user_id)
            .cloned()
    }

    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), String> {
        let mut map = self.by_external.write().await;
        for identity in map.values_mut() {
            if identity.user_id == user_id {
                identity.token = Some(token.to_string());
                identity.token_expire_at = Some(expire_at);
                return Ok(());
            }
        }
        Err(format!("update_token: user_id {user_id} not found"))
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

    #[tokio::test]
    async fn get_by_token_finds_identity() {
        let repo = MemoryUserIdentityRepo::new();
        let uid = repo.ensure_identity("google", "sub-1", Some("Alice"), Some("https://img.url"), "dev").await.unwrap();
        repo.update_token(&uid, "jwt-token-abc", 9999).await.unwrap();

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

    #[tokio::test]
    async fn update_token_overwrites_previous() {
        let repo = MemoryUserIdentityRepo::new();
        let uid = repo.ensure_identity("google", "sub-2", None, None, "dev").await.unwrap();
        repo.update_token(&uid, "old-jwt", 1000).await.unwrap();
        repo.update_token(&uid, "new-jwt", 2000).await.unwrap();

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
//! In-memory OAuth state store for CSRF protection.
//!
//! Each state string is one-time-use and auto-expires after 5 minutes.
//! Suitable for single-instance deployment. For multi-instance,
//! replace with a Redis-backed implementation.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use base64::Engine;
use bcs_auth_api::OAuthError;
use tokio::sync::RwLock;

/// A pending OAuth state entry.
struct StateEntry {
    provider: String,
    expires_at: Instant,
}

/// In-memory state store with TTL-based expiration.
pub struct OAuthStateStore {
    store: RwLock<HashMap<String, StateEntry>>,
    ttl: Duration,
}

impl OAuthStateStore {
    pub fn new(ttl: Duration) -> Self {
        Self {
            store: RwLock::new(HashMap::new()),
            ttl,
        }
    }

    /// Generate a random state string, store it, and return it.
    pub async fn generate(&self, provider: &str) -> String {
        let state = Self::random_state();
        let entry = StateEntry {
            provider: provider.to_string(),
            expires_at: Instant::now() + self.ttl,
        };
        self.store.write().await.insert(state.clone(), entry);
        state
    }

    /// Consume a state string (one-time use). Returns the provider name.
    /// Fails if state not found or expired.
    pub async fn consume(&self, state: &str) -> Result<String, OAuthError> {
        let mut store = self.store.write().await;
        // Also purge expired entries
        let now = Instant::now();
        store.retain(|_, v| v.expires_at > now);
        // Look up and remove
        match store.remove(state) {
            Some(entry) => Ok(entry.provider),
            None => Err(OAuthError::InvalidState("state not found or expired".into())),
        }
    }

    /// Generate a URL-safe random CSRF state string.
    ///
    /// Uses `uuid` v4, which draws from the OS CSPRNG (`getrandom`). The state
    /// is the sole CSRF defense for the OAuth callback (no PKCE), so it must be
    /// cryptographically unpredictable. Two v4 UUIDs (32 random bytes, minus 6
    /// version/variant bits ≈ 122 bits of entropy each) are concatenated and
    /// base64url-encoded.
    fn random_state() -> String {
        let mut bytes = Vec::with_capacity(32);
        bytes.extend_from_slice(uuid::Uuid::new_v4().as_bytes());
        bytes.extend_from_slice(uuid::Uuid::new_v4().as_bytes());
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&bytes)
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn store_and_consume_state() {
        let store = OAuthStateStore::new(Duration::from_secs(300));
        let state = store.generate("google").await;
        assert!(!state.is_empty());
        // Consume valid state
        assert!(store.consume(&state).await.is_ok());
        // Double consume fails (one-time use)
        assert!(store.consume(&state).await.is_err());
    }

    #[tokio::test]
    async fn expired_state_rejected() {
        let store = OAuthStateStore::new(Duration::from_millis(1));
        let state = store.generate("google").await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(store.consume(&state).await.is_err());
    }

    #[tokio::test]
    async fn consume_returns_provider() {
        let store = OAuthStateStore::new(Duration::from_secs(300));
        let state = store.generate("github").await;
        let provider = store.consume(&state).await.unwrap();
        assert_eq!(provider, "github");
    }

    #[tokio::test]
    async fn forged_state_rejected() {
        // A state never issued by the store (forged CSRF token) must fail.
        let store = OAuthStateStore::new(Duration::from_secs(300));
        store.generate("google").await; // unrelated live entry
        assert!(store.consume("attacker-controlled-state").await.is_err());
    }

    #[tokio::test]
    async fn generated_states_are_unique_and_unguessable() {
        // CSPRNG-backed states must not collide and must be long/opaque.
        let store = OAuthStateStore::new(Duration::from_secs(300));
        let mut seen = std::collections::HashSet::new();
        for _ in 0..1000 {
            let s = store.generate("google").await;
            assert!(s.len() >= 32, "state too short: {} chars", s.len());
            assert!(seen.insert(s), "duplicate state generated");
        }
    }
}

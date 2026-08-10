//! Password-credential persistence port for username/password auth.
//!
//! Stores only an argon2 PHC password hash keyed by `(username, env)`; the
//! raw password is never persisted. The internal `user_id` is the link to
//! `bcs_user_identities` (auth_source = "password").

use async_trait::async_trait;

/// A stored password credential. `find_for_login` returns this so login is a
/// single indexed lookup yielding everything needed to verify and to sign the
/// JWT (`user_id` becomes `Claims::sub`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserCredential {
    pub user_id: String,
    pub username: String,
    pub password_hash: String,
    pub env: String,
}

#[async_trait]
pub trait UserCredentialRepoPort: Send + Sync {
    /// Insert a credential. Returns `Err("duplicate")` when `(username, env)`
    /// or `(user_id, env)` already exists; other errors carry a descriptive
    /// string. The caller is expected to have pre-checked absence via
    /// `find_for_login` so a duplicate signals a registration race.
    async fn create_credential(
        &self,
        user_id: &str,
        username: &str,
        password_hash: &str,
        env: &str,
    ) -> Result<(), String>;

    /// Single indexed login lookup: `(username, env) -> credential`.
    async fn find_for_login(
        &self,
        username: &str,
        env: &str,
    ) -> Result<Option<UserCredential>, String>;
}

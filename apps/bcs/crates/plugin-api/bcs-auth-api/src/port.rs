//! Outbound ports the plugins depend on, injected by bootstrap.

use async_trait::async_trait;

use crate::types::AuthError;

/// What a bot session token resolves to.
#[derive(Debug, Clone)]
pub struct BotInfo {
    pub bot_uuid: String,
    pub owner_id: Option<String>,
}

/// Display-oriented user identity info returned by lookup endpoints.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserIdentityInfo {
    pub user_id: String,
    pub auth_source: String,
    /// Internal display name (initialized from the external name at first
    /// login, then independently editable). Prefer this over
    /// `external_user_name` when presenting the user.
    pub user_name: Option<String>,
    pub external_user_name: Option<String>,
    pub avatar: Option<String>,
}

/// Resolve a non-JWT bot session token to bot info. Implemented by bootstrap
/// over `BotRegistryCoreService`.
#[async_trait]
pub trait BotLookupPort: Send + Sync {
    async fn find_bot_by_token(&self, token: &str) -> Result<Option<BotInfo>, AuthError>;

    /// Resolve a provider-registered bot by its `agent_code` (set to the bot's
    /// `provider_bot_ref` at registration). Auth plugins can use this as a
    /// fallback when token-native bot identity resolution does not produce a
    /// local BCS bot.
    ///
    /// Default impl returns `Ok(None)` so existing lookups need not implement it.
    async fn find_bot_by_agent_code(
        &self,
        agent_code: &str,
    ) -> Result<Option<BotInfo>, AuthError> {
        let _ = agent_code;
        Ok(None)
    }
}

/// Login-side identity table writer + reader. Used by OAuth callback,
/// session verification, and user-info endpoints.
#[async_trait]
pub trait UserIdentityPort: Send + Sync {
    /// Ensure `(auth_source, external_user_id, env)` has a row; return its
    /// internal `user_id` (12-char base62, no prefix). Idempotent.
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError>;

    /// Reverse lookup: confirm a `user_id` exists for a given `auth_source`.
    /// Used by `verify_oauth_session` to confirm JWT subject hasn't been deleted.
    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Result<Option<String>, AuthError>;

    /// Look up user identity by session token fingerprint (`SHA-256` hex of
    /// the JWT, via `bcs_jwt::token_hash`). The raw JWT is never stored, so
    /// callers must pass the hash, not the JWT. Used by `GET /auth/user` to
    /// find the user behind a cookie and to enforce single-session validity.
    async fn get_identity_by_token(
        &self,
        token_hash: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError>;

    /// Look up user identity by internal `user_id`.
    /// Used by `GET /auth/user/{user_id}` to return display info.
    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError>;

    /// Write or overwrite the stored session token fingerprint for a user
    /// (`SHA-256` hex of the JWT, via `bcs_jwt::token_hash`). Pass an empty
    /// string to revoke (logout). Called after OAuth callback JWT signing and
    /// after `POST /auth/refresh` re-sign.
    async fn update_token(
        &self,
        user_id: &str,
        token_hash: &str,
        expire_at: u64,
    ) -> Result<(), AuthError>;
}

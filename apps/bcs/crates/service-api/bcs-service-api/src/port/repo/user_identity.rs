use async_trait::async_trait;

/// One row of the user identity table: an external identity and the BCS-assigned
/// internal `user_id` (12-char base62, no prefix).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserIdentity {
    pub user_id: String,
    pub auth_source: String,
    pub external_user_id: String,
    pub user_name: Option<String>,
    pub external_user_name: Option<String>,
    pub avatar: Option<String>,
    pub token: Option<String>,
    /// Unix seconds; stored as `token_expire_at` TIMESTAMP in MySQL.
    pub token_expire_at: Option<u64>,
    pub env: String,
}

/// Persistence contract for the user identity table.
///
/// Idempotent on `(auth_source, external_user_id, env)`. NOT on the auth hot
/// path this phase; built as infrastructure for future external-login plugins.
#[async_trait]
pub trait UserIdentityRepoPort: Send + Sync {
    /// Ensure a row exists; allocate a unique `user_id` if absent, else refresh
    /// `external_user_name`/`avatar`/`gmt_modified`. Returns the internal `user_id`.
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, String>;

    /// Forward lookup: external -> internal `user_id`. Tool/future use only.
    async fn lookup_user_id(
        &self,
        auth_source: &str,
        external_user_id: &str,
        env: &str,
    ) -> Option<String>;

    /// Reverse lookup: internal `user_id` + `auth_source` -> `external_user_id`.
    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Option<String>;

    /// Look up a user identity by session token (the full JWT string).
    /// Returns the full row so callers can read display fields.
    async fn get_by_token(&self, token: &str) -> Option<UserIdentity>;

    /// Look up a user identity by internal `user_id` for display purposes.
    /// Returns the first matching row (there should be at most one per source).
    async fn get_by_user_id_display(&self, user_id: &str) -> Option<UserIdentity>;

    /// Write or overwrite the session token for a user.
    /// Called after OAuth callback JWT signing and after sliding-expiry re-sign.
    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), String>;
}
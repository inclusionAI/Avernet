//! Username/password registration + login application use case.
//!
//! The delivery adapter (`bcs-http` `/auth/*`) calls this service; it
//! orchestrates: validate credentials → ensure identity → store/verify
//! password hash (argon2) → sign `bcs_session` JWT → bind its SHA-256 via
//! `UserIdentityPort::update_token`. The returned token is the raw JWT; the
//! adapter sets the `bcs_session` cookie and also returns it in the JSON body
//! so non-browser clients can use `Authorization: Bearer`.

use async_trait::async_trait;

/// Result of a successful register or login. `expires_at` is unix seconds
/// (same unit as JWT `exp`).
#[derive(Debug, Clone)]
pub struct PasswordLoginResult {
    pub user_id: String,
    pub username: String,
    pub token: String,
    pub expires_at: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum PasswordAuthError {
    #[error("validation failed: {0}")]
    ValidationFailed(String),
    #[error("username already taken")]
    UsernameTaken,
    #[error("invalid credentials")]
    InvalidCredentials,
    #[error("storage error: {0}")]
    Storage(String),
}

#[async_trait]
pub trait PasswordAuthService: Send + Sync {
    /// Register a new user and immediately issue a session token (register
    /// implicitly logs the user in). `ValidationFailed` for weak
    /// password/invalid username; `UsernameTaken` if the username exists.
    async fn register(
        &self,
        username: &str,
        password: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError>;

    /// Verify credentials and issue a session token. `InvalidCredentials` for
    /// unknown user OR wrong password (same message to avoid enumeration).
    async fn login(
        &self,
        username: &str,
        password: &str,
    ) -> Result<PasswordLoginResult, PasswordAuthError>;
}

//! Core auth types shared by the plugin chain and all plugins.

/// Which authentication source produced a principal.
///
/// `OAuth` carries an owned, runtime provider name (e.g. "google", "github")
/// so new providers can be added without editing this crate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthSource {
    AgentPass,
    Cookie,
    SessionToken,
    Local,
    OAuth(String),
}

impl AuthSource {
    /// Wire-compatible name. `Cookie => "Sso"` preserves the legacy
    /// `PreUpgradeAuth` naming so WS downstream consumers do not change.
    pub fn name(&self) -> String {
        match self {
            AuthSource::AgentPass => "AgentPass".to_string(),
            AuthSource::Cookie => "Sso".to_string(),
            AuthSource::SessionToken => "SessionToken".to_string(),
            AuthSource::Local => "Local".to_string(),
            AuthSource::OAuth(provider) => provider.clone(),
        }
    }
}

/// A resolved authentication principal.
///
/// `user_id` is the internal user id. This phase: cookie/agentpass take the
/// external identity directly (cookie -> staff_no). A future external-login
/// plugin will set it to the identity table's internal id (12-char base62, no
/// prefix). NO `human_` prefix here.
#[derive(Debug, Clone, Default)]
pub struct AuthPrincipal {
    pub source_name: Option<String>,
    pub user_id: Option<String>,
    pub user_name: Option<String>,
    pub bot_uuid: Option<String>,
    pub external_bot_id: Option<String>,
    pub owner_id: Option<String>,
    pub owner_name: Option<String>,
    pub token: Option<String>,
}

impl AuthPrincipal {
    pub fn new(source: AuthSource) -> Self {
        Self {
            source_name: Some(source.name()),
            ..Default::default()
        }
    }
}

/// Outcome of running the chain. `None` means anonymous (not an error).
#[derive(Debug, Clone, Default)]
pub struct AuthResult {
    pub principal: Option<AuthPrincipal>,
}

/// Errors a plugin may return. A returned `Err` short-circuits the chain.
#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("invalid token: {0}")]
    InvalidToken(String),
    #[error("expired token: {0}")]
    ExpiredToken(String),
    #[error("sdk error: {0}")]
    SdkError(String),
    #[error("lookup failed: {0}")]
    LookupFailed(String),
    #[error("forbidden: {0}")]
    Forbidden(String),
}

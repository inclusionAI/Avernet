//! Shared OAuth data types.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// OAuth token returned by the provider's token endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OAuthToken {
    pub access_token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_in: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub refresh_token: Option<String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub extra: HashMap<String, String>,
}

/// Normalized user info from any OAuth provider.
#[derive(Debug, Clone)]
pub struct ProviderUserInfo {
    pub id: String,
    pub name: Option<String>,
    pub email: Option<String>,
    pub avatar: Option<String>,
}

/// OAuth-specific errors.
#[derive(Debug, thiserror::Error)]
pub enum OAuthError {
    #[error("token exchange failed: {0}")]
    TokenExchangeFailed(String),
    #[error("user info request failed: {0}")]
    UserInfoFailed(String),
    #[error("invalid state (CSRF): {0}")]
    InvalidState(String),
    #[error("provider not found: {0}")]
    ProviderNotFound(String),
    #[error("config error: {0}")]
    ConfigError(String),
}

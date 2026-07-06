//! Public OAuth placeholder for the CLI.
//!
//! The internal office-network OAuth SDK is intentionally not part of the
//! public workspace. Public deployments should use service API keys or the
//! server-side OAuth providers.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

/// OAuth error that carries the auth_url when available,
/// so callers can include it in structured output.
#[derive(Debug)]
pub struct OAuthError {
    pub message: String,
    pub auth_url: Option<String>,
}

impl std::fmt::Display for OAuthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for OAuthError {}

static STRUCTURED_MODE: AtomicBool = AtomicBool::new(false);

const DEFAULT_OAUTH_CLIENT_ID: &str = "";
const DEFAULT_OAUTH_CLIENT_SECRET: &str = "";

pub fn set_structured_mode(enabled: bool) {
    STRUCTURED_MODE.store(enabled, Ordering::Relaxed);
}

pub fn default_oauth_client_id() -> &'static str {
    DEFAULT_OAUTH_CLIENT_ID
}

pub fn default_oauth_client_secret() -> &'static str {
    DEFAULT_OAUTH_CLIENT_SECRET
}

pub type AuthRequiredCallback = Box<dyn FnOnce(&str) + Send>;

pub async fn get_oauth_headers(
    client_id: String,
    client_secret: String,
    on_auth_required: Option<AuthRequiredCallback>,
) -> Result<HashMap<String, String>, OAuthError> {
    let _ = (client_id, client_secret, on_auth_required);
    let message =
        "CLI OAuth via the internal office-network SDK is not available in the public build"
            .to_string();
    Err(OAuthError {
        message,
        auth_url: None,
    })
}

pub async fn try_get_oauth_headers(
    client_id: String,
    client_secret: String,
    on_auth_required: Option<AuthRequiredCallback>,
) -> Result<HashMap<String, String>, OAuthError> {
    get_oauth_headers(client_id, client_secret, on_auth_required).await
}

//! Google OAuth configuration.
//!
//! `GoogleOAuthConfig` is defined here (not in bcs-auth-api) to keep
//! the plugin API provider-agnostic.

/// Google OAuth endpoints.
pub const GOOGLE_AUTH_URL: &str = "https://accounts.google.com/o/oauth2/v2/auth";
pub const GOOGLE_TOKEN_URL: &str = "https://oauth2.googleapis.com/token";
pub const GOOGLE_USERINFO_URL: &str = "https://openidconnect.googleapis.com/v1/userinfo";
pub const GOOGLE_SCOPES: &str = "openid profile email";

#[derive(Debug, Clone)]
pub struct GoogleOAuthConfig {
    pub client_id: String,
    pub client_secret: String,
}

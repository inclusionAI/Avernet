//! Auth chain configuration.

#[derive(Debug, Clone, Default)]
pub struct OAuthConfig {
    /// HMAC secret for signing/verifying JWT session tokens.
    pub jwt_secret: String,
    /// Idle timeout in minutes. Default: 30.
    pub idle_timeout_minutes: u64,
    /// Base URL for constructing redirect_uri: {base_url}/auth/callback/{provider}
    pub base_url: String,
    /// Whether to set the `Secure` attribute on the session cookie. When the
    /// server is reached over plain HTTP (local dev), browsers drop `Secure`
    /// cookies, so this must be `false` there. Defaults to whether `base_url`
    /// uses an `https` scheme; can be overridden via config.
    pub cookie_secure: bool,
    /// Runtime environment tag used to partition user identities
    /// (`(auth_source, external_user_id, env)`). Must be consistent between
    /// identity creation and session verification. Default: "default".
    pub env: String,
}

impl OAuthConfig {
    pub fn idle_timeout_secs(&self) -> u64 {
        self.idle_timeout_minutes * 60
    }

    /// Default `cookie_secure` derived from the `base_url` scheme: HTTPS
    /// deployments get `Secure`, plain-HTTP (local dev) does not.
    pub fn default_cookie_secure(base_url: &str) -> bool {
        base_url.trim_start().to_ascii_lowercase().starts_with("https://")
    }
}

/// Resolved auth chain configuration.
///
/// The derived `Default` is intentionally neutral: an empty `chain` means
/// "not configured". The composition root (bootstrap `auth_wiring`) supplies
/// the build-profile default chain, keeping that decision out of this contract
/// crate per Rule 14 (configuration drives wiring).
#[derive(Debug, Clone, Default)]
pub struct AuthConfig {
    /// Ordered list of enabled plugin names. Empty = not configured.
    pub chain: Vec<String>,
    /// When true, anonymous requests are rejected with 401.
    pub require_authentication: bool,
    pub local: LocalAuthConfig,
    pub oauth: Option<OAuthConfig>,
}

#[derive(Debug, Clone, Default)]
pub struct LocalAuthConfig {
    pub mock_user_id: Option<String>,
    pub mock_user_name: Option<String>,
    pub allow_mock_headers: bool,
}

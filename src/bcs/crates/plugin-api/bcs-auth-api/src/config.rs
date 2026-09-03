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
    /// Path to redirect to after a successful login. Empty falls back to `/`
    /// via [`OAuthConfig::success_redirect_location`].
    pub success_redirect_path: String,
}

impl OAuthConfig {
    pub fn idle_timeout_secs(&self) -> u64 {
        self.idle_timeout_minutes * 60
    }

    /// The `Location` to redirect the browser to after a successful login.
    /// Empty/whitespace falls back to `/`, so a default-constructed
    /// [`OAuthConfig`] (e.g. in tests) behaves the same as the configured
    /// default rather than emitting an empty `Location`.
    pub fn success_redirect_location(&self) -> &str {
        let path = self.success_redirect_path.trim();
        if path.is_empty() {
            "/"
        } else {
            path
        }
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

#[cfg(test)]
mod tests {
    use super::OAuthConfig;

    #[test]
    fn success_redirect_location_defaults_to_root_when_empty() {
        // A default-constructed config (e.g. tests) emits `/`, never an empty
        // `Location` (which a browser treats as "stay on the current URL").
        let config = OAuthConfig {
            success_redirect_path: String::new(),
            ..OAuthConfig::default()
        };
        assert_eq!(config.success_redirect_location(), "/");
    }

    #[test]
    fn success_redirect_location_uses_configured_path() {
        let config = OAuthConfig {
            success_redirect_path: "/dashboard?login=success".to_string(),
            ..OAuthConfig::default()
        };
        assert_eq!(config.success_redirect_location(), "/dashboard?login=success");
    }
}

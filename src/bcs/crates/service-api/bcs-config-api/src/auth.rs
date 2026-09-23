//! BCS auth configuration contract types.
//!
//! Leaf contract types for the auth plugin chain (`[auth]`), the optional
//! deployment-provided user identity extraction SDK (`[auth_sdk]`), and the
//! OAuth session + provider settings (`[auth.oauth]`).
//!
//! Lives in this leaf crate (no BCS implementation dependencies); downstream
//! code depends on the `bcs_config_api` re-exports rather than this module
//! path directly, so the split stays transparent to existing callers.

use std::collections::BTreeMap;

use secrecy::Secret;
use serde::{Deserialize, Serialize};

use crate::logging::default_true;
use crate::{deserialize_optional_secret, serialize_optional_secret};

// ---------------------------------------------------------------------------
// Auth SDK
// ---------------------------------------------------------------------------

/// Configuration for optional deployment-provided user identity extraction.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AuthSdkConfig {
    #[serde(default)]
    pub client_id: Option<String>,
    #[serde(default)]
    pub secret_key: Option<String>,
    #[serde(default)]
    pub secret_key_secret: Option<String>,
    #[serde(default)]
    pub app_key: Option<String>,
    #[serde(default)]
    pub app_name: Option<String>,
    #[serde(default)]
    pub remote_server_domain: Option<String>,
    #[serde(default = "default_true")]
    pub use_remote_login_check: bool,
}

impl AuthSdkConfig {
    /// Pure check on this struct's fields, no env access.
    ///
    /// Use [`AuthSdkConfig::is_complete_with_env_view`] when you also want to
    /// account for env-provided overrides.
    pub fn fields_complete(&self) -> bool {
        self.client_id.as_deref().is_some_and(|s| !s.is_empty())
            && self.secret_key.as_deref().is_some_and(|s| !s.is_empty())
            && self.app_key.as_deref().is_some_and(|s| !s.is_empty())
    }

    /// Check completeness, considering an injected env view.
    ///
    /// Caller supplies the env view. This keeps `bcs-config-api` free of env
    /// access once the deprecated bridge is removed.
    pub fn is_complete_with_env_view(&self, env: &dyn AuthSdkEnvView) -> bool {
        let has_client_id =
            self.client_id.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_CLIENT_ID");
        let has_secret_key =
            self.secret_key.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_SECRET_KEY");
        let has_app_key =
            self.app_key.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_APP_KEY");
        has_client_id && has_secret_key && has_app_key
    }
}

/// Abstract env view for AuthSdk completeness check, injected by caller.
pub trait AuthSdkEnvView {
    fn has(&self, var: &str) -> bool;
}

// ---------------------------------------------------------------------------
// Auth plugin chain
// ---------------------------------------------------------------------------

/// Configuration for the auth plugin chain (which plugins are enabled, in what
/// order). Consumed by bootstrap to build `bcs_auth_api::AuthConfig`.
///
/// An empty `chain` means "not configured" — bootstrap falls back to the
/// build-profile default (`bcs_auth_api::AuthConfig::default`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AuthChainConfig {
    /// Ordered list of enabled plugin names: `agentpass` | `cookie` | `session`
    /// | `local`. Empty/omitted → fall back to the build-profile default.
    #[serde(default)]
    pub chain: Vec<String>,
    /// When true, anonymous requests are rejected with 401.
    #[serde(default)]
    pub require_authentication: bool,
    /// Local-mock plugin: user_id to emit (only used when `local` is in chain).
    #[serde(default)]
    pub mock_user_id: Option<String>,
    /// Local-mock plugin: display name to emit.
    #[serde(default)]
    pub mock_user_name: Option<String>,
    /// Local-mock plugin: allow X-Mock-* request headers to override identity.
    #[serde(default)]
    pub allow_mock_headers: bool,
    /// OAuth session settings. Required for the `oauth_session` plugin and the
    /// `/auth/*` routes. Omitted → OAuth disabled (routes return 404, the
    /// `oauth_session` plugin logs a warning if requested).
    #[serde(default)]
    pub oauth: Option<OAuthSettings>,
}

/// OAuth session + provider settings, deserialized from `[auth.oauth]`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OAuthSettings {
    /// HMAC secret for signing/verifying session JWTs. Required to mount
    /// `/auth/*`; an absent/empty secret keeps OAuth disabled.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub jwt_secret: Option<Secret<String>>,
    /// Idle timeout in minutes for issued session JWTs. Default: 30.
    #[serde(default = "default_oauth_idle_timeout_minutes")]
    pub idle_timeout_minutes: u64,
    /// Public base URL used to build redirect URIs:
    /// `{base_url}/auth/callback/{provider}`.
    pub base_url: String,
    /// Override for the session cookie `Secure` attribute. When omitted it is
    /// derived from `base_url` (https → secure). Set `false` for local HTTP dev.
    #[serde(default)]
    pub cookie_secure: Option<bool>,
    /// Path to redirect to after a successful login (the `Location` emitted by
    /// `/auth/callback/{provider}` and the OpenAPI v1 login flow). Defaults to
    /// `/`. Must be a relative path starting with a single `/`; an absolute URL
    /// or protocol-relative `//host` is rejected at load time (open-redirect
    /// guard). A query string is allowed, e.g. `/?login=success`.
    #[serde(default = "default_oauth_success_redirect_path")]
    pub success_redirect_path: String,
    /// OAuth provider instances keyed by instance name, deserialized from
    /// `[auth.oauth.providers.<name>]`. The key is the instance name used as the
    /// `HashMap` key, the `/auth/url` entry, and the `{provider}` in
    /// `/auth/callback/{provider}`. Empty → no providers → `/auth/*` stays
    /// unmounted. `BTreeMap` keeps iteration/logging order deterministic.
    #[serde(default)]
    pub providers: BTreeMap<String, ProviderSettings>,
}

/// One OAuth provider instance, deserialized from `[auth.oauth.providers.<name>]`.
///
/// The shape is uniform across provider kinds; the concrete implementation is
/// selected by `kind` (defaulting to the map key when omitted).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderSettings {
    /// Which built-in provider implementation to construct (`google` | `github`
    /// | `wechat` | `alipay` | …). When omitted, defaults to the map key — so
    /// the common 1:1 case (`[auth.oauth.providers.github]`) needs no `kind`.
    /// Set it explicitly to run multiple instances of one kind under distinct names.
    #[serde(default)]
    pub kind: Option<String>,
    /// OAuth client id.
    pub client_id: String,
    /// OAuth client secret.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub client_secret: Option<Secret<String>>,
    #[serde(default)]
    pub client_secret_secret: Option<String>,
    /// RSA private key in PEM format — used by Alipay for request signing.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub private_key: Option<Secret<String>>,
    /// Alipay RSA public key in PEM format — used to verify gateway responses.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub alipay_public_key: Option<Secret<String>>,
}

impl ProviderSettings {
    /// The provider kind to construct: explicit `kind`, else the instance name.
    pub fn resolved_kind<'a>(&'a self, name: &'a str) -> &'a str {
        self.kind.as_deref().unwrap_or(name)
    }
}

impl OAuthSettings {
    /// Validate the resolved OAuth settings at config-load time, so operator
    /// mistakes fail fast at startup instead of surfacing as a runtime 404.
    ///
    /// Checks (per provider instance): non-empty `client_id`, and a
    /// route-safe instance name (no `/`, no whitespace) since the name becomes
    /// the `{provider}` path segment. Unknown `kind` cannot be checked here —
    /// the set of valid kinds lives in the bootstrap factory — so it is
    /// validated there; this keeps the contract crate free of impl knowledge.
    pub fn validate(&self) -> Result<(), String> {
        // success_redirect_path must be a same-origin relative path: a leading
        // single `/` only. An absolute URL (http://host) or a protocol-relative
        // `//host` would let a misconfigured value redirect users off-site
        // right after they authenticate, so reject it at load time.
        let redirect = self.success_redirect_path.trim();
        if !redirect.starts_with('/') || redirect.starts_with("//") {
            return Err(format!(
                "auth.oauth.success_redirect_path must be a relative path starting with a single '/' (got {:?})",
                self.success_redirect_path
            ));
        }
        for (name, p) in &self.providers {
            if name.is_empty() {
                return Err("auth.oauth.providers has an empty instance name".to_string());
            }
            if name.contains('/') || name.chars().any(|c| c.is_whitespace()) {
                return Err(format!(
                    "auth.oauth.providers.{name}: instance name must not contain '/' or whitespace (it is used in /auth/callback/{{provider}})"
                ));
            }
            if p.client_id.trim().is_empty() {
                return Err(format!(
                    "auth.oauth.providers.{name}: client_id must not be empty"
                ));
            }
        }
        Ok(())
    }
}

fn default_oauth_idle_timeout_minutes() -> u64 {
    30
}

fn default_oauth_success_redirect_path() -> String {
    "/".to_string()
}

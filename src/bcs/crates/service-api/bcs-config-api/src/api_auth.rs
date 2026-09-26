//! V1 API auth configuration contract (Task 6, spec §4 of
//! `2026-09-18-v1-api-auth-plugin-chain-design.md`).
//!
//! Declares the public schema for `[api.auth]` and built-in `GatewayApiAuthConfig`.
//! The contract layer performs only **shape** validation (chain name rules,
//! reserved-name exclusivity, success_redirect_path, session timeout,
//! trusted_browser_origins, public_base_url, gateway field rules). Cross-
//! checks that depend on the registered source inventory (every chain entry
//! references a registered source; OAuth source requires OAuth-common
//! fields; cookie-emitting source requires origins; etc.) live in the
//! bootstrap `api_auth_registry` module.
//!
//! Common reserved fields are recognized at the struct level. Every other
//! key under `[api.auth]` is carried through `ApiAuthConfig::plugin_tables`
//! as a structured map and validated by the bootstrap registry against each
//! registered source's `validate` fn.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use url::Url;

// ---------------------------------------------------------------------------
// ApiConfig: parent of `[api]` section
// ---------------------------------------------------------------------------

/// `[api]` section. Currently only holds the V1 auth configuration.
///
/// `#[serde(deny_unknown_fields)]` so future expansion is forced to go
/// through this struct (prevents silent typos in `[api.<future-section>]`).
/// `Default::default()` gives `auth: None` — the spec §4.3 compat mode.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApiConfig {
    /// V1 auth chain configuration. Absent → compat mode (no new-chain
    /// validation, no behavior change). Present → `chain` is required and
    /// the bootstrap `validate_api_auth` runs against the registered
    /// production inventory.
    #[serde(default)]
    pub auth: Option<ApiAuthConfig>,
}

// ---------------------------------------------------------------------------
// ApiAuthConfig: `[api.auth]`
// ---------------------------------------------------------------------------

/// The `[api.auth]` section.
///
/// `chain` is the only required field (a present `[api.auth]` table missing
/// `chain` is the spec's "显式空表不是兼容" case — it fails at deserialization).
/// All other reserved common fields are optional with explicit defaults.
///
/// Every key under `[api.auth]` that is not one of the reserved common
/// fields is carried through `plugin_tables` as a structured map entry.
/// The bootstrap registry rejects unknown plugin names at startup (they
/// are not silently dropped). Per-plugin field validation is delegated to
/// each source's registered `validate` fn.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiAuthConfig {
    /// Ordered list of enabled source names. Names must satisfy the §4.2
    /// name rule (lowercase letter first; rest = lowercase/digits/underscore)
    /// and must not collide with a reserved common field name. Required,
    /// non-empty, no duplicates.
    pub chain: Vec<String>,

    /// Public base URL for V1 auth routes. Required iff an OAuth provider
    /// is in `chain`. Absolute HTTP(S) URL; no userinfo/query/fragment.
    #[serde(default)]
    pub public_base_url: Option<String>,

    /// Logical secret name for the session signing key. Required iff an
    /// OAuth provider is in `chain`. Resolved by bootstrap through the
    /// configured SecretAccessPort; not stored as raw bytes here.
    #[serde(default)]
    pub session_signing_key_secret: Option<String>,

    /// Idle timeout (minutes) for issued OAuth session JWTs. Default 30.
    /// Must be positive; seconds-conversion (× 60) must not overflow `u64`.
    #[serde(default = "default_session_idle_timeout_minutes")]
    pub session_idle_timeout_minutes: u64,

    /// Site-relative path to redirect to after a successful login. Default
    /// `/`. Must start with a single `/`; `//host`, absolute URLs and bare
    /// `path` (no leading slash) are rejected as open-redirect guards.
    #[serde(default = "default_success_redirect_path")]
    pub success_redirect_path: String,

    /// Exact scheme/host/port origins allowed for browser cookie-injecting
    /// requests (CSRF defense for the Cookie Secure binding). Required iff
    /// a cookie-emitting source is in `chain`. Each entry is an absolute
    /// `https://host[:port]` with no userinfo/path/query/fragment/wildcard.
    #[serde(default)]
    pub trusted_browser_origins: Option<Vec<String>>,

    /// Open-ended plugin parameter tables — every key under `[api.auth]`
    /// that is not a reserved common field above is captured here. The
    /// bootstrap registry iterates this map to locate each registered
    /// source's table; unknown names are rejected, present-but-unused
    /// tables are still structure-validated (but their secrets are NOT
    /// resolved at this task — validate-only, never build).
    #[serde(flatten)]
    pub plugin_tables: BTreeMap<String, serde_json::Value>,
}

impl ApiAuthConfig {
    /// Reserved common-field names at the `[api.auth]` level. Used by:
    ///
    /// - serde's field-deselection (these are explicit fields above), and
    /// - `is_reserved_field_name`, which refuses them as chain source names
    ///   (chain entries cannot shadow a primary key — `"chain"` is never a
    ///   valid plugin name).
    pub fn reserved_field_names() -> &'static [&'static str] {
        &[
            "chain",
            "public_base_url",
            "session_signing_key_secret",
            "session_idle_timeout_minutes",
            "success_redirect_path",
            "trusted_browser_origins",
        ]
    }

    /// True iff `name` is a reserved common-field name (and thus cannot
    /// be used as a plugin source name in `chain`).
    pub fn is_reserved_field_name(name: &str) -> bool {
        Self::reserved_field_names().contains(&name)
    }

    /// Pure shape-validation (no registry access). Checks §4.2:
    /// - chain non-empty, no duplicates, name rules, reserved-name
    ///   exclusivity;
    /// - success_redirect_path leading-slash / no `//` / no absolute URL;
    /// - session_idle_timeout_minutes positive and non-overflowing seconds;
    /// - trusted_browser_origins, when present, are exact scheme/host/port;
    /// - public_base_url, when present, is absolute HTTP(S) with no
    ///   userinfo/query/fragment.
    ///
    /// Cross-checks that depend on the registered source inventory
    /// (unknown source name, OAuth-required fields, cookie-required
    /// origins, etc.) live in the bootstrap `validate_api_auth`.
    pub fn validate(&self) -> Result<(), String> {
        if self.chain.is_empty() {
            return Err("api.auth.chain must not be empty".to_string());
        }
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for name in &self.chain {
            if name.is_empty() {
                return Err("api.auth.chain entries must not be empty".to_string());
            }
            validate_chain_name(name)?;
            if Self::is_reserved_field_name(name) {
                return Err(format!(
                    "api.auth.chain entry '{name}' shadows a reserved common field; \
                     choose a different source name"
                ));
            }
            if !seen.insert(name.clone()) {
                return Err(format!(
                    "api.auth.chain entry '{name}' is a duplicate; chain entries must be unique"
                ));
            }
        }
        validate_success_redirect_path(&self.success_redirect_path)?;
        if self.session_idle_timeout_minutes == 0 {
            return Err(
                "api.auth.session_idle_timeout_minutes must be a positive integer".to_string(),
            );
        }
        if self.session_idle_timeout_minutes.checked_mul(60).is_none() {
            return Err(format!(
                "api.auth.session_idle_timeout_minutes={} would overflow when converted to seconds",
                self.session_idle_timeout_minutes
            ));
        }
        if let Some(origins) = &self.trusted_browser_origins {
            for origin in origins {
                validate_browser_origin(origin)?;
            }
        }
        if let Some(raw) = &self.public_base_url {
            validate_public_base_url(raw)?;
        }
        Ok(())
    }
}

fn default_session_idle_timeout_minutes() -> u64 {
    30
}

fn default_success_redirect_path() -> String {
    "/".to_string()
}

fn validate_chain_name(name: &str) -> Result<(), String> {
    let first = name.chars().next().ok_or_else(|| {
        "api.auth.chain entries must not be empty".to_string()
    })?;
    if !first.is_ascii_lowercase() {
        return Err(format!(
            "api.auth.chain entry '{name}' must start with a lowercase letter"
        ));
    }
    for c in name.chars().skip(1) {
        if !(c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_') {
            return Err(format!(
                "api.auth.chain entry '{name}' must contain only lowercase letters, digits, \
                 or underscores (found '{c}')"
            ));
        }
    }
    Ok(())
}

fn validate_success_redirect_path(raw: &str) -> Result<(), String> {
    let trimmed = raw.trim();
    if !trimmed.starts_with('/') {
        return Err(format!(
            "api.auth.success_redirect_path must start with a single '/' (got {raw:?})"
        ));
    }
    if trimmed.starts_with("//") {
        return Err(format!(
            "api.auth.success_redirect_path must not start with '//' (open-redirect guard, \
             got {raw:?})"
        ));
    }
    Ok(())
}

fn validate_browser_origin(raw: &str) -> Result<(), String> {
    let parsed = Url::parse(raw.trim()).map_err(|e| {
        format!("api.auth.trusted_browser_origins entry '{raw}' is not a valid URL: {e}")
    })?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(format!(
            "api.auth.trusted_browser_origins entry '{raw}' must use http(s) scheme"
        ));
    }
    if parsed.host_str().is_none() {
        return Err(format!(
            "api.auth.trusted_browser_origins entry '{raw}' must have a host"
        ));
    }
    if let Some(host) = parsed.host_str() {
        if host.contains('*') {
            return Err(format!(
                "api.auth.trusted_browser_origins entry '{raw}' must not use a wildcard host"
            ));
        }
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(format!(
            "api.auth.trusted_browser_origins entry '{raw}' must not contain userinfo"
        ));
    }
    let path = parsed.path();
    if !path.is_empty() && path != "/" {
        return Err(format!(
            "api.auth.trusted_browser_origins entry '{raw}' must not contain a path"
        ));
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err(format!(
            "api.auth.trusted_browser_origins entry '{raw}' must not contain query or fragment"
        ));
    }
    Ok(())
}

fn validate_public_base_url(raw: &str) -> Result<(), String> {
    let parsed = Url::parse(raw.trim()).map_err(|e| {
        format!("api.auth.public_base_url is not a valid URL: {e}")
    })?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("api.auth.public_base_url must use http(s) scheme".to_string());
    }
    if parsed.host_str().is_none() {
        return Err("api.auth.public_base_url must have a host".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("api.auth.public_base_url must not contain userinfo".to_string());
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err(
            "api.auth.public_base_url must not contain a query or fragment".to_string(),
        );
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// GatewayApiAuthConfig: built-in typed plugin-table struct for `gateway`
// ---------------------------------------------------------------------------

/// `[api.auth.gateway]` — built-in typed plugin table for Gateway signed
/// principal verification. The `gateway` source is the only built-in
/// typed source declared in Task 6; other built-ins (alipay/github/google/
/// wechat) are validated by `api_auth_registry.rs` registered `validate`
/// fns that operate on `&serde_json::Value`, not via typed `Option<...>`
/// fields on `ApiAuthConfig`. This keeps the public contract surface
/// uniform: plugins extend via registration rather than by adding typed
/// fields to the contract struct.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayApiAuthConfig {
    /// Accepted JWT issuers. A token whose `iss` claim matches any entry
    /// is accepted. Defaults to both `gateway` and `backend`.
    #[serde(default = "default_gateway_issuers")]
    pub issuers: Vec<String>,

    /// Accepted JWT `aud` claim. Defaults to `bcs`.
    #[serde(default = "default_gateway_audience")]
    pub audience: String,

    /// Signing key id (`kid`) to look up. Defaults to `bare`.
    #[serde(default = "default_gateway_key_id")]
    pub key_id: String,

    /// Logical secret name for the signing key. Required (non-blank).
    /// Resolved by bootstrap through the SecretAccessPort; not stored as
    /// raw bytes here.
    #[serde(default)]
    pub signing_key_secret: Option<String>,
}

impl Default for GatewayApiAuthConfig {
    fn default() -> Self {
        Self {
            issuers: default_gateway_issuers(),
            audience: default_gateway_audience(),
            key_id: default_gateway_key_id(),
            signing_key_secret: None,
        }
    }
}

impl GatewayApiAuthConfig {
    /// Built-in `gateway` field rules (spec §4.2):
    /// - `issuers` non-empty, no duplicates, no blanks;
    /// - `audience` non-blank;
    /// - `key_id` non-blank;
    /// - `signing_key_secret` present and non-blank.
    pub fn validate(&self) -> Result<(), String> {
        if self.issuers.is_empty() {
            return Err("api.auth.gateway.issuers must not be empty".to_string());
        }
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for issuer in &self.issuers {
            if issuer.trim().is_empty() {
                return Err(
                    "api.auth.gateway.issuers must not contain blank entries".to_string(),
                );
            }
            if !seen.insert(issuer.clone()) {
                return Err(
                    "api.auth.gateway.issuers must not contain duplicates".to_string(),
                );
            }
        }
        if self.audience.trim().is_empty() {
            return Err("api.auth.gateway.audience must not be blank".to_string());
        }
        if self.key_id.trim().is_empty() {
            return Err("api.auth.gateway.key_id must not be blank".to_string());
        }
        match &self.signing_key_secret {
            None => {
                return Err(
                    "api.auth.gateway.signing_key_secret is required".to_string(),
                );
            }
            Some(v) if v.trim().is_empty() => {
                return Err(
                    "api.auth.gateway.signing_key_secret must not be blank".to_string(),
                );
            }
            _ => {}
        }
        Ok(())
    }
}

fn default_gateway_issuers() -> Vec<String> {
    vec!["gateway".to_string(), "backend".to_string()]
}

fn default_gateway_audience() -> String {
    "bcs".to_string()
}

fn default_gateway_key_id() -> String {
    "bare".to_string()
}

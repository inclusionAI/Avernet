//! Independent V1 API authentication configuration (`[api.*]` section).
//!
//! This schema is deliberately separate from the legacy `auth.rs` contract
//! (`[auth]`): the legacy section keeps its frozen shape and compat semantics,
//! while `[api.auth]` is the new V1 API auth chain with an explicit
//! compatibility rule — an *absent* `auth` subsection is valid and keeps
//! legacy behavior; once the subsection is present it must be complete
//! (`chain` is required).
//!
//! Layering:
//! - The common (host) layer parses reserved public fields strictly and
//!   retains every remaining `[api.auth.<name>]` plugin table verbatim in
//!   [`ApiAuthConfig::plugins`]; classification is exhaustive — a key is
//!   either a named reserved field or a plugin table.
//! - The bootstrap registry (`bcs::api_auth_registry`) resolves plugin names
//!   against registered sources and runs per-table schema validation. Because
//!   serde `deny_unknown_fields` cannot be combined with `flatten`, unknown
//!   keys are *retained* here and rejected at the registry stage instead of
//!   being silently dropped.
//! - Value-domain enforcement (String / Bool / i64 / arrays / objects only;
//!   no floats, datetimes, or nulls in the `api.auth` subtree) happens in the
//!   shared config parse entry before any TOML->JSON conversion or merge.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// The `[api]` configuration section. A deployment-wide container for the
/// V1 public API surface; only `auth` exists today.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApiConfig {
    /// `[api.auth]`. Absent keeps compatibility semantics: the V1 API auth
    /// chain is simply not configured and nothing consumes it.
    ///
    /// `skip_serializing_if` keeps the strict value-domain contract intact on
    /// the round trip: printing a default config must not emit an explicit
    /// `"auth": null`, which this section's own validation rejects (absent
    /// and null are not equivalent). Consumers that overlay `--print-config`
    /// output and load it back — like the caller-execution e2e story — would
    /// otherwise break against a config that never configured `[api.auth]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auth: Option<ApiAuthConfig>,
}

/// The `[api.auth]` V1 API-auth source-chain section.
///
/// Reserved public fields are the named struct fields; every other key under
/// `[api.auth]` is a plugin parameter table retained for registry validation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ApiAuthConfig {
    /// Ordered source-name chain. Required and non-empty when the section is
    /// configured. Duplicates and illegal names are rejected
    /// ([`ApiAuthConfig::validate_chain_shape`]); unregistered names are
    /// rejected by the bootstrap registry.
    pub chain: Vec<String>,
    /// External base URL of the V1 auth routes; callbacks are built as
    /// `{public_base_url}/callback/{provider}`. Required when any enabled
    /// source is an OAuth provider.
    #[serde(default)]
    pub public_base_url: Option<String>,
    /// Logical reference to the session JWT signing key in the secret
    /// backend (a key name, never the key material). Required when any
    /// enabled source is an OAuth provider.
    #[serde(default)]
    pub session_signing_key_secret: Option<String>,
    /// In-site relative path redirected to after a successful login.
    /// Defaults to `/` during registry validation when OAuth is enabled.
    /// Open-redirect shapes (absolute or protocol-relative URLs, backslashes,
    /// control characters) are rejected by the bootstrap registry.
    #[serde(default)]
    pub success_redirect_path: Option<String>,
    /// OAuth session idle timeout in minutes. Defaults to 30 when absent;
    /// converted once to seconds via [`validate_idle_timeout_minutes`] and
    /// never re-converted downstream.
    #[serde(default)]
    pub session_idle_timeout_minutes: Option<u64>,
    /// Exact-match browser origins (scheme/host/port) protecting cookie
    /// writes against CSRF. Required and non-empty when any enabled source
    /// uses browser cookies; forbidden otherwise.
    #[serde(default)]
    pub trusted_browser_origins: Vec<String>,
    /// Remaining `[api.auth.<source>]` plugin parameter tables, retained
    /// verbatim for the registry phase. Registry-validated tables use
    /// descriptor schemas (deny_unknown_fields semantics are enforced by each
    /// registered source's `validate`), so an unregistered source table is a
    /// startup error, not a silently ignored key.
    #[serde(flatten)]
    pub plugins: BTreeMap<String, serde_json::Value>,
}

/// Reserved `[api.auth]` public field names. Plugin tables cannot use these
/// names: serde routes them into the typed fields above, and the registry
/// also rejects registrations declaring them. Any key *not* in this list that
/// is not a plugin table is a configuration error.
pub const RESERVED_API_AUTH_FIELD_NAMES: &[&str] = &[
    "chain",
    "public_base_url",
    "session_signing_key_secret",
    "success_redirect_path",
    "session_idle_timeout_minutes",
    "trusted_browser_origins",
];

/// Source/plugin name rule: starts with a lowercase letter, followed by
/// lowercase letters, digits, or underscores.
pub fn is_valid_api_source_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_lowercase())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

impl ApiAuthConfig {
    /// Common-layer chain-shape validation (no registry knowledge): the
    /// chain must be non-empty, free of duplicates, and every entry must be
    /// a legal source name. Reserved-plugin classification is serde-level and
    /// registry-level; this check runs at config-load time and again inside
    /// `validate_api_auth`.
    pub fn validate_chain_shape(&self) -> Result<(), String> {
        if self.chain.is_empty() {
            return Err("api.auth.chain must not be empty".to_string());
        }
        let mut seen = std::collections::HashSet::new();
        for (idx, name) in self.chain.iter().enumerate() {
            if !is_valid_api_source_name(name) {
                return Err(format!(
                    "api.auth.chain[{idx}]: source name is illegal (must start with a lowercase letter and contain only lowercase letters, digits, or underscores)"
                ));
            }
            if !seen.insert(name) {
                return Err(format!(
                    "api.auth.chain[{idx}]: duplicate source in the chain"
                ));
            }
        }
        Ok(())
    }
}

/// Converts a positive `session_idle_timeout_minutes` value to session
/// `idle_secs` with checked arithmetic. Called exactly once during
/// `validate_api_auth`; the engine receives the result (`now + idle_secs`
/// still needs its own checked_add at runtime).
pub fn validate_idle_timeout_minutes(minutes: u64) -> Result<u64, String> {
    if minutes == 0 {
        return Err("api.auth.session_idle_timeout_minutes must be positive".into());
    }
    minutes.checked_mul(60)
        .ok_or_else(|| "api.auth.session_idle_timeout_minutes overflows seconds".into())
}

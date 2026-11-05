//! Open two-phase registry for V1 API-auth sources (`[api.auth]`).
//!
//! Phase one (`validate_api_auth`) runs after the final deep-merge and env
//! expansion, against the structured `[api.auth]` section parsed by the
//! common layer (`bcs_config_api::api_auth`). It:
//!
//! - strictly identifies reserved public fields (serde-classified upstream;
//!   every remaining key is a plugin table that must be registered),
//! - rejects duplicate/illegal/reserved registrations, empty chains,
//!   duplicate chain entries, illegal names, and unregistered sources,
//! - structurally validates every present plugin table via the registered
//!   descriptor's `validate` — including registered-but-not-enabled tables,
//!   which never gain access to secrets,
//! - enforces the two-sided public-field rules: OAuth public fields are
//!   required exactly when an OAuth source is enabled (and rejected
//!   otherwise), `trusted_browser_origins` is required exactly when a
//!   cookie-using source is enabled,
//! - normalizes the OAuth public format (URL, in-site redirect, checked
//!   minutes->seconds conversion) into the verbatim [`ValidatedApiAuthConfig`].
//!
//! Prototype scope: registrations carry `name` / `capabilities` / `validate`;
//! Task 12 adds the `build` pointer and the construction context in place.
//!
//! Error contract (spec C3): every message is a field path plus a category;
//! raw configuration values are never echoed.

use std::collections::BTreeMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use bcs_api_http::PrincipalVerifier;
use bcs_auth_api::session::AuthSessionIdentityPort;
use bcs_auth_oauth::OAuthSessionEngine;
use bcs_config_api::api_auth::{
    ApiAuthConfig, RESERVED_API_AUTH_FIELD_NAMES, is_valid_api_source_name,
    validate_idle_timeout_minutes,
};
use bcs_service_api::port::secret::SecretAccessPort;

/// Capabilities a registered API-auth source declares to the host.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ApiAuthCapabilities {
    /// The source consumes browser cookies (cookie/SSO style writes), so
    /// `trusted_browser_origins` must be configured for CSRF protection.
    pub uses_browser_cookie: bool,
    /// The source drives the OAuth login/callback flows, so the OAuth public
    /// fields are required and normalized.
    pub is_oauth_provider: bool,
}

/// The one construction context handed to every registered source's
/// `build` (plan Task 12). It carries HANDLES only:
///
/// - `options` are the SAME value-domain JSON parameters the descriptor
///   validated in phase one (`ValidatedApiAuthConfig.plugin_options`, no
///   TOML/JSON/float/string round-trip in between; a chain source without
///   a table arrives as `Value::Null`);
/// - `secret_access` is the secret PORT, never a resolved value — the
///   source resolves the secrets it needs only when it is ENABLED;
/// - `sessions` is the strict session identity port over the one store
///   the login entries use;
/// - `oauth` is the shared OAuth engine context, constructed by bootstrap
///   when (and only when) an OAuth source is enabled.
///
/// No `Debug` is derived on purpose: nothing here may ever print resolved
/// secret material (spec §8.4 redaction; the context never carries values).
pub struct ApiAuthBuildContext {
    /// The registered source's own name (the chain entry position being
    /// built).
    pub name: String,
    /// The already-validated `[api.auth.<name>]` parameter table.
    pub options: serde_json::Value,
    /// Secret resolution port; errors must carry logical field paths only.
    pub secret_access: Arc<dyn SecretAccessPort>,
    /// Strict session identity port (the shared login-session store).
    pub sessions: Arc<dyn AuthSessionIdentityPort>,
    /// The shared strict OAuth engine context. `None` for non-OAuth
    /// sources; always `Some` for every source built while an OAuth
    /// source is enabled.
    pub oauth: Option<Arc<OAuthSourceContext>>,
}

/// The shared OAuth verification context bootstrap constructs exactly once
/// per configured chain when an OAuth source is enabled: ONE engine
/// instance (Task 8 lifecycle over the single session store) and the
/// allowlist of chain-enabled OAuth provider names, in CHAIN order.
///
/// No `Debug` derivation: the engine embeds the session HMAC secret.
pub struct OAuthSourceContext {
    /// Strict session engine built from `ValidatedOAuthSettings.idle_secs`
    /// AS-IS (default/minutes conversion happened once in Task 6).
    pub engine: Arc<OAuthSessionEngine>,
    /// Provider instance names enabled in the chain (chain order). A
    /// verified session whose provider is outside this list is Missing:
    /// an unlisted source does not participate in the configured chain.
    pub allowed_providers: Vec<String>,
}

/// The build pointer every registered source supplies. It receives the
/// validated options plus the shared handles and resolves ITS OWN secrets
/// (enabled sources only). Errors are startup-fatal strings carrying a
/// logical field path plus a category, never raw configuration values.
pub type ApiAuthSourceBuild =
    fn(ApiAuthBuildContext) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>>;

/// A registered API-auth source.
#[derive(Clone)]
pub struct ApiAuthSourceRegistration {
    /// Source name; must be a legal name and must not collide with a reserved
    /// `[api.auth]` public field name.
    pub name: String,
    /// Declared capabilities driving the chain cross-field rules.
    pub capabilities: ApiAuthCapabilities,
    /// Structural validation for the source's `[api.auth.<name>]` parameter
    /// table. Receives the limited value-domain JSON (strings, bools, i64
    /// integers, arrays, objects); unknown-field rejection
    /// (deny_unknown_fields semantics) belongs to this descriptor. Called for
    /// registered tables even when the source is not enabled; it must not
    /// access secrets — secret resolution happens only in the build phase
    /// (Task 12). The same validated parameters flow to `build`; no
    /// re-conversion happens in between.
    pub validate: fn(&serde_json::Value) -> Result<(), String>,
    /// Per-chain-entry construction: wrap the shared primitives (or build
    /// the source's own verifier) and return the delivery `PrincipalVerifier`
    /// pinned to this source's fixed provenance name.
    pub build: ApiAuthSourceBuild,
}

/// The inventory-submitted artifact: a constructor thunk for one source
/// registration, wrapped in a tuple newtype. Two const-correctness
/// constraints shape this: registration values embed `String` descriptors
/// and cannot be const-built (hence thunks, not values), and the registry's
/// marker-trait impl is orphan-rule-blocked on a bare fn-pointer alias
/// (hence the newtype). Downstream crates submit:
///
/// ```ignore
/// inventory::submit! { ApiAuthSourceRegistrationFactory(make_my_registration) }
/// ```
///
/// Bootstrap merges the thunks into the production inventory in
/// `production_registrations()`; illegal, reserved, or duplicate names
/// stay startup-fatal in `index_registrations`.
pub struct ApiAuthSourceRegistrationFactory(pub fn() -> ApiAuthSourceRegistration);

inventory::collect!(ApiAuthSourceRegistrationFactory);

/// Normalized OAuth session settings; consumed as-is by the build phase.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedOAuthSettings {
    pub public_base_url: String,
    pub session_signing_key_secret: String,
    pub success_redirect_path: String,
    pub idle_secs: u64,
}

/// The validated `[api.auth]` result consumed by Task 12's assembly.
#[derive(Debug, Clone, PartialEq)]
pub struct ValidatedApiAuthConfig {
    pub chain: Vec<String>,
    pub plugin_options: std::collections::BTreeMap<String, serde_json::Value>,
    pub trusted_browser_origins: Vec<String>,
    pub oauth: Option<ValidatedOAuthSettings>,
}

/// Validates the final (deep-merged, env-expanded) `[api.auth]` section
/// against the registered sources. `oauth: None` means a valid
/// not-OAuth-enabled configuration; when OAuth is enabled, every
/// [`ValidatedOAuthSettings`] field is required.
///
/// All errors are startup-fatal configuration errors carrying field path +
/// category only.
pub fn validate_api_auth(
    config: &ApiAuthConfig,
    registrations: &[ApiAuthSourceRegistration],
) -> Result<ValidatedApiAuthConfig, String> {
    config.validate_chain_shape()?;

    let index = index_registrations(registrations)?;

    // Chain resolution: every entry must be a registered source.
    let mut uses_browser_cookie = false;
    let mut is_oauth_provider = false;
    for (idx, name) in config.chain.iter().enumerate() {
        let Some(reg) = index.get(name.as_str()) else {
            return Err(format!("api.auth.chain[{idx}]: source is not registered"));
        };
        uses_browser_cookie |= reg.capabilities.uses_browser_cookie;
        is_oauth_provider |= reg.capabilities.is_oauth_provider;
    }

    // Structural validation for every present plugin table (enabled or not).
    // Structural only: descriptors receive the public JSON table and have no
    // secret-supplying path in this prototype.
    for (name, table) in &config.plugins {
        let Some(reg) = index.get(name.as_str()) else {
            return Err(format!(
                "api.auth.{name}: source table is not registered (category: unknown_source)"
            ));
        };
        (reg.validate)(table).map_err(|err| format!("api.auth.{name}: {err}"))?;
    }

    // Only chain-enabled sources flow to the build-facing options map.
    let mut plugin_options = BTreeMap::new();
    for name in &config.chain {
        if let Some(table) = config.plugins.get(name) {
            plugin_options.insert(name.clone(), table.clone());
        }
    }

    // Two-sided OAuth rule: fields required exactly when enabled.
    let oauth = if is_oauth_provider {
        let public_base_url =
            required_oauth_field(config.public_base_url.as_deref(), "api.auth.public_base_url")?;
        let (normalized_base_url, base_url) = validate_public_base_url(&public_base_url)?;
        let session_signing_key_secret = required_oauth_field(
            config.session_signing_key_secret.as_deref(),
            "api.auth.session_signing_key_secret",
        )?;
        let success_redirect_path = validate_success_redirect_path(
            config.success_redirect_path.as_deref().unwrap_or("/"),
            &base_url,
        )?;
        let idle_secs =
            validate_idle_timeout_minutes(config.session_idle_timeout_minutes.unwrap_or(30))?;
        Some(ValidatedOAuthSettings {
            public_base_url: normalized_base_url,
            session_signing_key_secret,
            success_redirect_path,
            idle_secs,
        })
    } else {
        reject_configured_oauth_fields(config)?;
        None
    };

    // Two-sided origin rule: required exactly when a cookie source is enabled.
    if uses_browser_cookie {
        if config.trusted_browser_origins.is_empty() {
            return Err(
                "api.auth.trusted_browser_origins: required and must not be empty while the chain enables a cookie source"
                    .to_string(),
            );
        }
    } else if !config.trusted_browser_origins.is_empty() {
        return Err(
            "api.auth.trusted_browser_origins: configured while the chain enables no cookie source"
                .to_string(),
        );
    }

    Ok(ValidatedApiAuthConfig {
        chain: config.chain.clone(),
        plugin_options,
        trusted_browser_origins: config.trusted_browser_origins.clone(),
        oauth,
    })
}

/// Indexes the registrations, rejecting duplicate, illegal, and reserved
/// names. Registration names are code-owned (not operator config), so these
/// messages may identify the offending name — unlike config values.
fn index_registrations(
    registrations: &[ApiAuthSourceRegistration],
) -> Result<BTreeMap<&str, &ApiAuthSourceRegistration>, String> {
    let mut index: BTreeMap<&str, &ApiAuthSourceRegistration> = BTreeMap::new();
    for registration in registrations {
        if !is_valid_api_source_name(&registration.name) {
            return Err(format!(
                "api auth source registry: registration name `{}` is illegal (must start with a lowercase letter and contain only lowercase letters, digits, or underscores)",
                registration.name
            ));
        }
        if RESERVED_API_AUTH_FIELD_NAMES.contains(&registration.name.as_str()) {
            return Err(format!(
                "api auth source registry: registration name `{}` is reserved as a public field name and cannot be used as a plugin table name",
                registration.name
            ));
        }
        if index.insert(registration.name.as_str(), registration).is_some() {
            return Err(format!(
                "api auth source registry: duplicate registration name `{}`",
                registration.name
            ));
        }
    }
    Ok(index)
}

/// Required-presence helper for OAuth public fields. Absent or blank values
/// error with the field path; the value's content is never echoed.
fn required_oauth_field(value: Option<&str>, path: &str) -> Result<String, String> {
    match value {
        Some(inner) if !inner.trim().is_empty() => Ok(inner.to_string()),
        _ => Err(format!(
            "{path}: required and must not be empty while an OAuth source is enabled"
        )),
    }
}

/// Validates the OAuth public base URL and returns the normalized URL string
/// plus the parsed URL used for redirect origin comparison.
///
/// Accepts absolute http(s) URLs with a host and no userinfo, query, or
/// fragment; plain http is only allowed for loopback hosts, which is the
/// declared local-deployment rule (non-local deployments must use https).
/// Relative URLs cannot produce a host and are rejected. Runs at startup,
/// before any secret resolution or provider client construction.
fn validate_public_base_url(raw: &str) -> Result<(String, url::Url), String> {
    const PATH: &str = "api.auth.public_base_url";
    let trimmed = raw.trim();
    let url = url::Url::parse(trimmed)
        .map_err(|_| format!("{PATH}: must be an absolute http(s) URL (relative URLs are rejected)"))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(format!("{PATH}: scheme must be http(s)"));
    }
    if url.host_str().is_none() {
        return Err(format!("{PATH}: must include a host"));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(format!("{PATH}: must not contain userinfo"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(format!("{PATH}: must not contain a query or fragment"));
    }
    if url.scheme() == "http" && !url.host().is_some_and(is_declared_local_host) {
        return Err(format!(
            "{PATH}: plain http is only allowed for loopback hosts in the declared local-deployment rule; non-local deployments must use https"
        ));
    }
    // Normalize the same way the openapi_v1 base-URL rule does: without a
    // trailing slash, so `{public_base_url}/callback/{provider}` composes
    // predictably.
    let mut normalized = url;
    let path = normalized.path().trim_end_matches('/').to_string();
    normalized.set_path(&path);
    Ok((
        normalized.to_string().trim_end_matches('/').to_string(),
        normalized,
    ))
}

/// The declared local deployment rule: loopback hosts (and `localhost`).
fn is_declared_local_host(host: url::Host<&str>) -> bool {
    match host {
        url::Host::Domain(name) => name.eq_ignore_ascii_case("localhost"),
        url::Host::Ipv4(address) => address.is_loopback(),
        url::Host::Ipv6(address) => address.is_loopback(),
    }
}

/// Validates the post-login redirect path. It must be an in-site relative
/// path starting with a single `/`, without backslashes or control
/// characters, and it must still resolve against the configured public base
/// origin (open-redirect guard). Any value whose resolution changes the
/// origin is rejected.
fn validate_success_redirect_path(raw: &str, base: &url::Url) -> Result<String, String> {
    const PATH: &str = "api.auth.success_redirect_path";
    if raw.is_empty() {
        return Err(format!("{PATH}: must not be empty"));
    }
    if !raw.starts_with('/') || raw.starts_with("//") {
        return Err(format!(
            "{PATH}: must be an in-site relative path starting with a single '/'"
        ));
    }
    if raw.contains('\\') {
        return Err(format!("{PATH}: must not contain backslashes"));
    }
    if raw.chars().any(char::is_control) {
        return Err(format!(
            "{PATH}: must not contain control characters (CR/LF or others)"
        ));
    }
    let resolved = base
        .join(raw)
        .map_err(|_| format!("{PATH}: does not resolve against the configured base URL"))?;
    if resolved.origin() != base.origin() {
        return Err(format!(
            "{PATH}: must still resolve to the configured public_base_url origin (open-redirect guard)"
        ));
    }
    Ok(raw.to_string())
}

/// Rejects any configured OAuth public field when no OAuth source is enabled:
/// a cookie-only (or gateway-only) chain cannot bind OAuth names and gain
/// wrong expectations from inert parameters.
fn reject_configured_oauth_fields(config: &ApiAuthConfig) -> Result<(), String> {
    let mut configured = Vec::new();
    if config.public_base_url.is_some() {
        configured.push("api.auth.public_base_url");
    }
    if config.session_signing_key_secret.is_some() {
        configured.push("api.auth.session_signing_key_secret");
    }
    if config.success_redirect_path.is_some() {
        configured.push("api.auth.success_redirect_path");
    }
    if config.session_idle_timeout_minutes.is_some() {
        configured.push("api.auth.session_idle_timeout_minutes");
    }
    if configured.is_empty() {
        return Ok(());
    }
    Err(format!(
        "api.auth: OAuth public fields configured while no OAuth source is enabled in the chain: {} (category: oauth_fields_without_oauth_source)",
        configured.join(", ")
    ))
}

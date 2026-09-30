//! Bootstrap two-phase registry for the V1 `[api.auth]` plugin chain
//! (Task 6, spec §4 of the v1-api-auth-plugin-chain design).
//!
//! The contract layer (`bcs_config_api::api_auth`) only validates field
//! *shapes*. This module performs the second phase: cross-checks against
//! the *registered source inventory*. Production registrations are exposed
//! via [`default_api_auth_registrations`]; downstream tasks register
//! additional sources (`cookie`, `agentpass`, ...) and inject their own
//! `validate` fns at composition time.
//!
//! Task 6 registered only `validate`-only descriptors. Task 12 extends each
//! registration with a `build` fn: the composition root invokes `build` for
//! every chain-enabled source after validation, resolving secrets ONLY for
//! those sources. Present-but-unused plugin tables are structure-validated
//! but never have their secrets touched or their constructors invoked.

use std::collections::{BTreeMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use bcs_api_http::PrincipalVerifier;
use bcs_config_api::{ApiAuthConfig, GatewayApiAuthConfig};
use bcs_service_api::port::secret::SecretAccessPort;

/// Capabilities a registered source advertises. Drives the chain-level
/// cross-checks in [`validate_api_auth`]:
///
/// - `is_oauth_provider = true` ⇒ enabling this source requires
///   `public_base_url` + `session_signing_key_secret` (the OAuth-common
///   fields). Conversely, configuring either of those fields with no
///   OAuth source in chain is an error.
/// - `uses_browser_cookie = true` ⇒ enabling this source requires
///   `trusted_browser_origins` non-empty. Conversely, configuring
///   `trusted_browser_origins` with no cookie source in chain is an error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ApiAuthCapabilities {
    pub uses_browser_cookie: bool,
    pub is_oauth_provider: bool,
}

/// Shared OAuth context the composition root injects into every OAuth-source
/// `build` fn (Task 12, spec §6 "OAuth providers 共享严格会话校验实现").
///
/// `engine` is THE one strict session engine of the `[api.auth]` chain
/// family — every OAuth source verifies the same `bcs_session` cookie
/// through it, never a per-source copy. `allowed_providers` is the closed
/// allowlist of OAuth provider names the new chain accepts (the OAuth source
/// names in the configured chain); a verified session whose provider is not
/// in the list is a 403 per spec §7.2.
pub struct OAuthSourceContext {
    pub engine: Arc<bcs_auth_oauth::OAuthSessionEngine>,
    pub allowed_providers: Vec<String>,
}

/// Construction context handed to a source's [`ApiAuthSourceRegistration::build`]
/// fn. Secrets are resolved ONLY through the injected
/// [`SecretAccessPort`] — a build fn must never read process env or files
/// for credential material, and error messages must carry only the logical
/// field path (never a resolved value).
pub struct ApiAuthBuildContext {
    /// The configured source (instance) name being built.
    pub name: String,
    /// The source's plugin table (`[api.auth.<name>]`), verbatim.
    pub options: serde_json::Value,
    /// Secret resolution port. Called only for THIS source's own fields.
    pub secret_access: Arc<dyn SecretAccessPort>,
    /// The strict identity/session port backing the shared engine.
    pub sessions: Arc<dyn bcs_auth_api::AuthSessionIdentityPort>,
    /// Present iff an OAuth source is in the chain (shared engine +
    /// allowlist). Non-OAuth sources must not consume it.
    pub oauth: Option<Arc<OAuthSourceContext>>,
}

/// Build-phase fn pointer: produces the source's verifier from the
/// construction context. Invoked once per enabled source during startup
/// composition; a failure aborts startup (spec §4.3: 无法初始化的启用插件
/// 一律启动失败).
pub type ApiAuthSourceBuildFn = fn(
    ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>>;

/// A registered API auth source descriptor.
///
/// Task 6 registers only the `validate` portion. Task 12 extends this struct
/// *in place* with the `build` pointer and the construction context it
/// needs; do not split this into a separate "BuilderRegistration" type
/// (that would force every registration site to be edited twice).
pub struct ApiAuthSourceRegistration {
    pub name: &'static str,
    pub capabilities: ApiAuthCapabilities,
    pub validate: fn(&serde_json::Value) -> Result<(), String>,
    /// Build phase (Task 12). Validates-only descriptors (test fakes) must
    /// supply a fn that answers the build call too — a descriptor without a
    /// builder cannot construct its verifier.
    pub build: ApiAuthSourceBuildFn,
}

/// Validate a fully-loaded `[api.auth]` section against the registered
/// source inventory.
///
/// Call this from `validate_loaded_config` (the post-load validation path)
/// only when `config.api.auth` is `Some`. The contract layer's
/// [`ApiAuthConfig::validate`] runs first (field-shape checks); this fn
/// then runs the registry-level cross-checks:
///
/// 1. Duplicate registrations rejected.
/// 2. Every chain entry references a registered source (unknown → error).
/// 3. Every chain-referenced source has a present plugin table (no table
///    → error: "chain references source but configuration missing").
/// 4. Each chain-referenced plugin table is validated via its registered
///    `validate` fn.
/// 5. Tables present but NOT in chain: also structure-validated (each is fed
///    to its registered `validate` fn; unknown-table names are rejected).
///    Secrets are NOT resolved at this phase — validate only, never build.
/// 6. Cross-checks against capabilities:
///    - OAuth source in chain requires `public_base_url` and
///      `session_signing_key_secret`. Conversely, configuring either
///      OAuth-common field with no OAuth source in chain is an error.
///    - Cookie-emitting source in chain requires `trusted_browser_origins`
///      non-empty. Conversely, configuring `trusted_browser_origins` with
///      no cookie source is an error.
///
/// Returns `Ok` for absent `[api.auth]` (compat mode) — but the caller is
/// expected to skip this fn entirely when `config.api.auth` is `None`, so
/// the no-op path is rarely exercised.
pub fn validate_api_auth(
    auth: &ApiAuthConfig,
    registrations: &[ApiAuthSourceRegistration],
) -> Result<(), String> {
    // Phase 1: contract-level shape validation.
    auth.validate()?;

    // Phase 2: registry-level cross-checks.
    let by_name: BTreeMap<&'static str, &ApiAuthSourceRegistration> = {
        let mut seen: HashSet<&'static str> = HashSet::new();
        let mut map = BTreeMap::new();
        for r in registrations {
            if !seen.insert(r.name) {
                return Err(format!(
                    "duplicate api_auth source registration: '{}'",
                    r.name
                ));
            }
            map.insert(r.name, r);
        }
        map
    };

    let chain_set: HashSet<&str> = auth.chain.iter().map(|s| s.as_str()).collect();

    // 3. Every chain entry must be a registered source.
    for name in &auth.chain {
        if !by_name.contains_key(name.as_str()) {
            return Err(format!(
                "api.auth.chain references unknown source '{name}'; \
                 it is not registered in the source inventory"
            ));
        }
    }

    // 4. Every chain-referenced source must have a plugin table present.
    for name in &auth.chain {
        if !auth.plugin_tables.contains_key(name) {
            return Err(format!(
                "api.auth.chain references source '{name}' but no \
                 [api.auth.{name}] table is configured"
            ));
        }
    }

    // 5. Validate every chain-referenced plugin table via its registered
    //    `validate` fn.
    for name in &auth.chain {
        let table = &auth.plugin_tables[name];
        let r = by_name[name.as_str()];
        (r.validate)(table).map_err(|e| format!("api.auth.{name}: {e}"))?;
    }

    // 6. Tables present but NOT in chain: also structure-validated (but
    //    secrets are NOT resolved at this task — validate only, never
    //    build).
    for (name, table) in &auth.plugin_tables {
        if chain_set.contains(name.as_str()) {
            continue;
        }
        match by_name.get(name.as_str()) {
            Some(r) => {
                (r.validate)(table).map_err(|e| format!("api.auth.{name}: {e}"))?;
            }
            None => {
                return Err(format!(
                    "api.auth.{name} is an unknown source — not registered in the source \
                     inventory. Even when not referenced from chain, present plugin tables \
                     must reference a registered source."
                ));
            }
        }
    }

    // 7. Cross-checks against advertised capabilities.
    let has_oauth = auth
        .chain
        .iter()
        .any(|n| by_name.get(n.as_str()).is_some_and(|r| r.capabilities.is_oauth_provider));
    let has_cookie = auth
        .chain
        .iter()
        .any(|n| by_name.get(n.as_str()).is_some_and(|r| r.capabilities.uses_browser_cookie));

    if has_oauth {
        if auth.public_base_url.is_none() {
            return Err(
                "api.auth.public_base_url is required when an OAuth provider is in chain"
                    .to_string(),
            );
        }
        if auth.session_signing_key_secret.is_none() {
            return Err(
                "api.auth.session_signing_key_secret is required when an OAuth provider \
                 is in chain"
                    .to_string(),
            );
        }
    } else {
        if auth.public_base_url.is_some() {
            return Err(
                "api.auth.public_base_url is only used with OAuth providers; no OAuth \
                 source is in chain"
                    .to_string(),
            );
        }
        if auth.session_signing_key_secret.is_some() {
            return Err(
                "api.auth.session_signing_key_secret is only used with OAuth providers; \
                 no OAuth source is in chain"
                    .to_string(),
            );
        }
    }

    if has_cookie {
        match &auth.trusted_browser_origins {
            None => {
                return Err(
                    "api.auth.trusted_browser_origins is required when a cookie-emitting \
                     source is in chain"
                        .to_string(),
                );
            }
            Some(v) if v.is_empty() => {
                return Err(
                    "api.auth.trusted_browser_origins must not be empty when a \
                     cookie-emitting source is in chain"
                        .to_string(),
                );
            }
            _ => {}
        }
    } else if auth.trusted_browser_origins.is_some() {
        return Err(
            "api.auth.trusted_browser_origins is only used with cookie-emitting sources; \
             none is in chain"
                .to_string(),
        );
    }

    Ok(())
}

/// Production inventory of built-in API auth source registrations.
///
/// Task 6 ships these five built-ins:
/// - `gateway` — Gateway signed principal verifier. Not an OAuth provider;
///   does not emit browser cookies.
/// - `alipay`, `github`, `google`, `wechat` — OAuth login providers that
///   also write session cookies (browser-bound).
///
/// Downstream tasks register additional sources (e.g. internal SSO
/// `cookie`/`agentpass`) at composition time; tests may inject validate-
/// only descriptors directly without adding to this list.
pub fn default_api_auth_registrations() -> Vec<ApiAuthSourceRegistration> {
    vec![
        ApiAuthSourceRegistration {
            name: "gateway",
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: false,
                is_oauth_provider: false,
            },
            validate: validate_gateway_table,
            build: crate::api_auth_wiring::build_gateway_source,
        },
        ApiAuthSourceRegistration {
            name: "alipay",
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: validate_alipay_table,
            build: crate::api_auth_wiring::build_oauth_source,
        },
        ApiAuthSourceRegistration {
            name: "github",
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: validate_github_table,
            build: crate::api_auth_wiring::build_oauth_source,
        },
        ApiAuthSourceRegistration {
            name: "google",
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: validate_google_table,
            build: crate::api_auth_wiring::build_oauth_source,
        },
        ApiAuthSourceRegistration {
            name: "wechat",
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: validate_wechat_table,
            build: crate::api_auth_wiring::build_oauth_source,
        },
    ]
}

// ---------------------------------------------------------------------------
// Built-in validate fns
// ---------------------------------------------------------------------------

fn validate_gateway_table(value: &serde_json::Value) -> Result<(), String> {
    let gateway: GatewayApiAuthConfig =
        serde_json::from_value(value.clone())
            .map_err(|e| format!("invalid [api.auth.gateway] table: {e}"))?;
    gateway.validate()
}

/// Require the named string field to be present and non-blank in the plugin
/// table. Returns the trimmed value for callers that want to inspect
/// further (e.g. RSA format), which Task 6 does not.
fn require_non_blank_string(
    table: &serde_json::Value,
    field: &str,
    source: &str,
) -> Result<String, String> {
    let value = table
        .get(field)
        .ok_or_else(|| format!("api.auth.{source}.{field} is required"))?;
    let s = value
        .as_str()
        .ok_or_else(|| format!("api.auth.{source}.{field} must be a string"))?;
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return Err(format!("api.auth.{source}.{field} must not be blank"));
    }
    Ok(trimmed.to_string())
}

/// Reject any field not in the allowed set. Plugin table schemas are fixed
/// per source; unknown fields cannot be routed silently to the runtime.
fn deny_unknown_fields_except(
    table: &serde_json::Value,
    allowed: &[&str],
    source: &str,
) -> Result<(), String> {
    let obj = table
        .as_object()
        .ok_or_else(|| format!("api.auth.{source} must be table-shaped"))?;
    for key in obj.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(format!(
                "api.auth.{source}: unknown field '{key}' (allowed: {})",
                allowed.join(", ")
            ));
        }
    }
    Ok(())
}

fn validate_alipay_table(value: &serde_json::Value) -> Result<(), String> {
    const ALLOWED: &[&str] = &[
        "client_id",
        "private_key_secret",
        "alipay_public_key_secret",
    ];
    deny_unknown_fields_except(value, ALLOWED, "alipay")?;
    require_non_blank_string(value, "client_id", "alipay")?;
    require_non_blank_string(value, "private_key_secret", "alipay")?;
    require_non_blank_string(value, "alipay_public_key_secret", "alipay")?;
    // RSA key-format verification is deferred to a later task that has
    // SecretAccessPort access — Task 6 only validates the field shape.
    Ok(())
}

fn validate_simple_oauth(
    value: &serde_json::Value,
    source: &'static str,
) -> Result<(), String> {
    let allowed: &[&str] = &["client_id", "client_secret_secret"];
    deny_unknown_fields_except(value, allowed, source)?;
    require_non_blank_string(value, "client_id", source)?;
    require_non_blank_string(value, "client_secret_secret", source)?;
    Ok(())
}

fn validate_github_table(value: &serde_json::Value) -> Result<(), String> {
    validate_simple_oauth(value, "github")
}

fn validate_google_table(value: &serde_json::Value) -> Result<(), String> {
    validate_simple_oauth(value, "google")
}

fn validate_wechat_table(value: &serde_json::Value) -> Result<(), String> {
    validate_simple_oauth(value, "wechat")
}

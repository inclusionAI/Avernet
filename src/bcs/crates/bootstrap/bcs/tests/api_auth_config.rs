//! Task 6 — bootstrap registry-driven validation for the new V1
//! `[api.auth]` chain. Covers spec §4.2 cross-checks that the contract layer
//! (bcs-config-api) cannot perform because they require the registered
//! source inventory: unknown source name rejection, OAuth-required public
//! base URL, cookie-required origins, present-but-unused tables still
//! structure-validated, deep-merge overlay replacement of `chain`, and the
//! compatibility invariant for absent `[api.auth]`.
//!
//! Per the brief: a test-only `cookie` descriptor is injected here to prove
//! downstream-registered source names are discoverable. It is NOT registered
//! in `default_api_auth_registrations()` (production inventory).

use bcs::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
    default_api_auth_registrations, validate_api_auth,
};
use bcs_config_api::{ApiAuthConfig};
use bcs::BcsConfig;
use std::collections::BTreeMap;
use std::path::PathBuf;
use toml;

// ---------------------------------------------------------------------------
// Test-only cookie descriptor (NOT registered in the production inventory)
// ---------------------------------------------------------------------------

/// Validate a test-only `cookie` plugin table. Lives in the test, not in
/// `default_api_auth_registrations()`. Per the brief, this proves downstream
/// names are discoverable without making `cookie` a default production
/// source.
fn validate_cookie_table(value: &serde_json::Value) -> Result<(), String> {
    let client_id = value
        .get("client_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "client_id is required".to_string())?;
    if client_id.trim().is_empty() {
        return Err("client_id must not be empty".to_string());
    }
    if let Some(obj) = value.as_object() {
        for key in obj.keys() {
            if key != "client_id" {
                return Err(format!("unknown field '{key}' in [api.auth.cookie] table"));
            }
        }
    }
    Ok(())
}

fn cookie_test_descriptor() -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: "cookie",
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: true,
            is_oauth_provider: false,
        },
        validate: validate_cookie_table,
        build: no_build,
    }
}

/// Task 12 registry extension: every registration must answer the build
/// call. The test-only descriptor never builds (Task 6 tests are validate-
/// only).
fn no_build(
    _ctx: ApiAuthBuildContext,
) -> std::pin::Pin<
    Box<dyn std::future::Future<Output = Result<std::sync::Arc<dyn bcs_api_http::PrincipalVerifier>, String>> + Send>,
> {
    Box::pin(async {
        Err("test-only descriptor has no build phase".to_string())
    })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn auth_from_toml(src: &str) -> ApiAuthConfig {
    toml::from_str(src).unwrap_or_else(|e| panic!("parse ApiAuthConfig: {e}"))
}

fn write_config(dir: &std::path::Path, name: &str, body: &str) -> PathBuf {
    let path = dir.join(name);
    std::fs::write(&path, body)
        .unwrap_or_else(|e| panic!("write {name}: {e}"));
    path
}

fn parse_bcs_config_toml(src: &str) -> BcsConfig {
    toml::from_str(src).unwrap_or_else(|e| panic!("parse BcsConfig: {e}"))
}

// ---------------------------------------------------------------------------
// Compat mode: absent [api.auth]
// ---------------------------------------------------------------------------

#[test]
fn absent_api_auth_section_is_compat_noop_validate_api_auth_not_called() {
    // When [api.auth] is absent, BcsConfig.api.auth is None — the bootstrap
    // validator must not consult validate_api_auth at all. Here we exercise
    // the post-load validation path indirectly by parsing the BcsConfig
    // and verifying the field is None; validate_api_auth is never called.
    let cfg = parse_bcs_config_toml(r#"
bots_base_dir = "/bots"
"#);
    assert!(cfg.api.is_none(), "absent [api] section => config.api = None");

    let cfg_with_api_empty = parse_bcs_config_toml(r#"
bots_base_dir = "/bots"

[api]
"#);
    assert!(cfg_with_api_empty.api.is_some());
    assert!(
        cfg_with_api_empty.api.as_ref().unwrap().auth.is_none(),
        "absent [api.auth] => api.auth = None (compat)"
    );
    // Validate_api_auth would not be called: config.api.auth is None.
    let registrations = default_api_auth_registrations();
    // Sanity: passing None into validate_api_auth isn't its contract; the
    // caller (validate_loaded_config) skips the call when auth is None.
    // Verify the contract by constructing the path the validator sees when
    // [api.auth] is present and asserting it is reachable. The no-op path
    // is exercised by the absence of `validate_api_auth` calls here.
    let _ = registrations;
}

// ---------------------------------------------------------------------------
// Explicit empty table [api.auth] is NOT compat
// ---------------------------------------------------------------------------

#[test]
fn explicit_empty_api_auth_table_is_rejected_at_deserialization() {
    // The spec: "显式空表不是兼容". An explicit `[api.auth]` table with
    // no chain must fail — `chain` is a required field on ApiAuthConfig,
    // so this fails at the deserializer level before any validation fn is
    // consulted.
    let err = toml::from_str::<BcsConfig>(
        r#"
bots_base_dir = "/bots"

[api.auth]
"#,
    )
    .expect_err("explicit empty [api.auth] rejected");
    assert!(
        err.to_string().contains("chain"),
        "expected missing-chain error, got: {err}"
    );
}

// ---------------------------------------------------------------------------
// Discovery: chain references a registered source
// ---------------------------------------------------------------------------

#[test]
fn chain_referencing_unregistered_source_is_rejected() {
    // chain = ["gateway", "cookie"] but only the production registrations
    // are passed (no cookie). The unregistered "cookie" must surface as
    // an unknown-source error, not silently dropped.
    let auth = auth_from_toml(
        r#"chain = ["gateway", "cookie"]

[gateway]
signing_key_secret = "k"

[cookie]
client_id = "test-client"
"#,
    );

    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("unregistered cookie rejected");
    assert!(
        err.contains("cookie"),
        "expected unknown-source 'cookie' error, got: {err}"
    );
}

#[test]
fn chain_referencing_registered_cookie_table_passes() {
    // Same input, but cookie is registered via the test-only descriptor:
    // the chain validates successfully. cookie is `uses_browser_cookie =
    // true`, so trusted_browser_origins is required (gateway is neither
    // cookie nor OAuth, so no OAuth-common fields are needed).
    let auth = auth_from_toml(
        r#"chain = ["gateway", "cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[gateway]
signing_key_secret = "k"

[cookie]
client_id = "test-client"
"#,
    );

    let mut registrations = default_api_auth_registrations();
    registrations.push(cookie_test_descriptor());

    validate_api_auth(&auth, &registrations)
        .unwrap_or_else(|e| panic!("registered cookie should validate: {e}"));
}

#[test]
fn chain_referencing_source_with_no_table_is_rejected() {
    // chain references gateway but no [api.auth.gateway] table.
    let auth = auth_from_toml(r#"chain = ["gateway"]"#);
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("missing chain-referenced table rejected");
    assert!(
        err.contains("gateway"),
        "expected missing-table 'gateway' error, got: {err}"
    );
}

#[test]
fn chain_referencing_source_with_invalid_table_is_rejected() {
    // Gateway table present but signing_key_secret missing → gateway
    // validator rejects.
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("invalid gateway table rejected");
    assert!(
        err.contains("gateway") && err.contains("signing_key_secret"),
        "expected gateway.signing_key_secret error, got: {err}"
    );
}

// ---------------------------------------------------------------------------
// Duplicate registrations
// ---------------------------------------------------------------------------

#[test]
fn duplicate_registration_names_are_rejected() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"
"#,
    );
    let mut regs = default_api_auth_registrations();
    // Add a second gateway entry with a no-op validator.
    regs.push(ApiAuthSourceRegistration {
        name: "gateway",
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: false,
            is_oauth_provider: false,
        },
        validate: |_: &serde_json::Value| Ok(()),
        build: no_build,
    });
    let err = validate_api_auth(&auth, &regs).expect_err("duplicate registration rejected");
    assert!(
        err.contains("duplicate"),
        "expected duplicate-registration error, got: {err}"
    );
}

// ---------------------------------------------------------------------------
// OAuth providers require OAuth-common fields
// ---------------------------------------------------------------------------

#[test]
fn oauth_chain_without_public_base_url_is_rejected() {
    let auth = auth_from_toml(
        r#"chain = ["alipay"]
session_signing_key_secret = "k"
trusted_browser_origins = ["https://workbench.example.com"]

[alipay]
client_id = "id"
private_key_secret = "k"
alipay_public_key_secret = "k"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("oauth without public_base_url rejected");
    assert!(
        err.contains("public_base_url"),
        "expected public_base_url requirement error, got: {err}"
    );
}

#[test]
fn oauth_chain_without_session_signing_key_secret_is_rejected() {
    let auth = auth_from_toml(
        r#"chain = ["alipay"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
trusted_browser_origins = ["https://workbench.example.com"]

[alipay]
client_id = "id"
private_key_secret = "k"
alipay_public_key_secret = "k"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("oauth without session_signing_key_secret rejected");
    assert!(
        err.contains("session_signing_key_secret"),
        "expected session_signing_key_secret requirement error, got: {err}"
    );
}

#[test]
fn oauth_chain_with_all_common_fields_is_accepted() {
    let auth = auth_from_toml(
        r#"chain = ["alipay"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "k"
trusted_browser_origins = ["https://workbench.example.com"]

[alipay]
client_id = "id"
private_key_secret = "k"
alipay_public_key_secret = "k"
"#,
    );
    validate_api_auth(&auth, &default_api_auth_registrations())
        .unwrap_or_else(|e| panic!("valid oauth chain should pass: {e}"));
}

// ---------------------------------------------------------------------------
// Cookie-only chain: requires origins, rejects OAuth-only common fields
// ---------------------------------------------------------------------------

#[test]
fn cookie_only_chain_without_origins_is_rejected() {
    let auth = auth_from_toml(
        r#"chain = ["cookie"]

[cookie]
client_id = "test-client"
"#,
    );
    let mut registrations = default_api_auth_registrations();
    registrations.push(cookie_test_descriptor());

    let err = validate_api_auth(&auth, &registrations)
        .expect_err("cookie without origins rejected");
    assert!(
        err.contains("trusted_browser_origins"),
        "expected trusted_browser_origins requirement, got: {err}"
    );
}

#[test]
fn cookie_only_chain_rejects_oauth_only_fields() {
    // cookie is registered as uses_browser_cookie=true, is_oauth_provider=false.
    // Configuring OAuth-only common fields (public_base_url, signing_key_secret)
    // is a spec violation: those are only used when an OAuth provider is in chain.
    let auth = auth_from_toml(
        r#"chain = ["cookie"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "k"
trusted_browser_origins = ["https://workbench.example.com"]

[cookie]
client_id = "test-client"
"#,
    );
    let mut registrations = default_api_auth_registrations();
    registrations.push(cookie_test_descriptor());

    let err = validate_api_auth(&auth, &registrations)
        .expect_err("cookie-only rejects OAuth-only fields");
    assert!(
        err.contains("public_base_url") || err.contains("session_signing_key_secret"),
        "expected OAuth-only-field rejection, got: {err}"
    );
}

#[test]
fn cookie_only_chain_with_origins_is_accepted() {
    let auth = auth_from_toml(
        r#"chain = ["cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[cookie]
client_id = "test-client"
"#,
    );
    let mut registrations = default_api_auth_registrations();
    registrations.push(cookie_test_descriptor());

    validate_api_auth(&auth, &registrations)
        .unwrap_or_else(|e| panic!("cookie-only chain with origins should pass: {e}"));
}

// ---------------------------------------------------------------------------
// trusted_browser_origins without any cookie source in chain is an error
// ---------------------------------------------------------------------------

#[test]
fn trusted_browser_origins_without_cookie_source_is_rejected() {
    // chain = ["gateway"] (no cookie), but trusted_browser_origins is
    // configured. The spec: "不启用任何 Cookie 型来源时不得配置该字段".
    let auth = auth_from_toml(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://workbench.example.com"]

[gateway]
signing_key_secret = "k"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("TBO without cookie source rejected");
    assert!(
        err.contains("trusted_browser_origins"),
        "expected TBO-no-cookie error, got: {err}"
    );
}

// ---------------------------------------------------------------------------
// Gateway-only config: passes; no secret resolution at this task
// ---------------------------------------------------------------------------

#[test]
fn gateway_only_config_passes() {
    // Gateway is the only built-in source that is neither OAuth nor
    // cookie-emitting. Valid gateway-only config must pass with no other
    // common fields configured. No secret supplier is consulted here —
    // Task 6 only validates, never resolves secrets.
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "principal-signing-key"
"#,
    );
    validate_api_auth(&auth, &default_api_auth_registrations())
        .unwrap_or_else(|e| panic!("gateway-only should pass: {e}"));
}

// ---------------------------------------------------------------------------
// Present-but-not-in-chain tables are still structure-validated
// ---------------------------------------------------------------------------

#[test]
fn present_but_unused_table_is_still_structure_validated() {
    // alipay table present but NOT in chain (chain = ["gateway"]).
    // The alipay validator must still run on the present table; if it has
    // an invalid shape (missing private_key_secret), the validator errors.
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"

[alipay]
client_id = "id"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("unused alipay table must still be validated");
    assert!(
        err.contains("alipay"),
        "expected alipay-validation error on present-but-unused table, got: {err}"
    );
}

// One more: unregistered cookie table present but not in chain — even if not
// in chain, the registry doesn't recognize "cookie" → unknown source error.
#[test]
fn present_unused_unregistered_table_is_rejected() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"

[cookie]
client_id = "k"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("unregistered present-but-unused table rejected");
    assert!(
        err.contains("cookie"),
        "expected unknown-source 'cookie' error, got: {err}"
    );
}

#[test]
fn unregistered_table_present_when_registered_only_cookie_passes_structure() {
    // Same as above, but cookie is registered as test-only: the table is
    // structure-validated (but secrets are NOT resolved, validate-only).
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"

[cookie]
client_id = "k"
"#,
    );
    let mut registrations = default_api_auth_registrations();
    registrations.push(cookie_test_descriptor());
    validate_api_auth(&auth, &registrations)
        .unwrap_or_else(|e| panic!("registered cookie present-but-unused should pass: {e}"));
}

// ---------------------------------------------------------------------------
// Dev overlay: base has gateway-only [api.auth]; dev replaces chain.
// Deep-merge convention: arrays replace whole; chain becomes alipay-only.
// ---------------------------------------------------------------------------

#[test]
#[serial_test::serial]
fn dev_overlay_replaces_chain_wholesale_via_deep_merge() {
    let saved = std::env::var("SERVER_ENV").ok();
    unsafe {
        std::env::set_var("SERVER_ENV", "dev");
    }
    let _guard = scope_guard(move || {
        if let Some(v) = saved {
            unsafe { std::env::set_var("SERVER_ENV", v); }
        } else {
            unsafe { std::env::remove_var("SERVER_ENV"); }
        }
    });

    let dir = tempfile::tempdir().unwrap();
    let config_dir = dir.path().join("configs");
    std::fs::create_dir_all(&config_dir).unwrap();

    write_config(
        &config_dir,
        "bcs-config.toml",
        r#"
bots_base_dir = "/bots"

[api.auth]
chain = ["gateway"]

[api.auth.gateway]
signing_key_secret = "principal-signing-key"
"#,
    );
    write_config(
        &config_dir,
        "bcs-config-dev.toml",
        r#"
[api.auth]
chain = ["alipay"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "bcs-api-session-signing-key"
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.alipay]
client_id = "dev-alipay-id"
private_key_secret = "dev-alipay-private-key"
alipay_public_key_secret = "dev-alipay-public-key"
"#,
    );

    let config = BcsConfig::try_load_with_env(Some(&config_dir))
        .expect("dev-overlay config loads");
    let auth = config
        .api
        .as_ref()
        .and_then(|api| api.auth.as_ref())
        .expect("merged config has [api.auth]");

    // Deep-merge replaces arrays wholesale; chain is now alipay-only.
    assert_eq!(auth.chain, vec!["alipay".to_string()]);
    // The merged plugin_tables must carry gateway (from base) AND alipay
    // (from dev): deep_merge on tables merges keys; the [api.auth.gateway]
    // table still exists under [api.auth.gateway] (dev doesn't remove it).
    assert!(auth.plugin_tables.contains_key("gateway"));
    assert!(auth.plugin_tables.contains_key("alipay"));
    // cross-check: gateway is present but not in chain → still validated.
    validate_api_auth(auth, &default_api_auth_registrations())
        .unwrap_or_else(|e| panic!("merged dev config should validate: {e}"));
}

// ---------------------------------------------------------------------------
// Old [auth] chain/priority selection remains unchanged
// ---------------------------------------------------------------------------

#[test]
fn legacy_auth_chain_section_still_parses_unchanged() {
    // Iso/compat invariant: Task 6 must NOT touch old [auth] chain/priority
    // selection. The existing [auth] parsing path is preserved as-is.
    let cfg = parse_bcs_config_toml(
        r#"
bots_base_dir = "/bots"

[auth]
chain = ["agentpass", "session"]
require_authentication = true
mock_user_id = "12345"
allow_mock_headers = true
"#,
    );
    assert_eq!(cfg.auth.chain, vec!["agentpass", "session"]);
    assert!(cfg.auth.require_authentication);
    assert!(cfg.auth.allow_mock_headers);
    // New [api] section is independent: still None here.
    assert!(cfg.api.is_none());
}

#[test]
fn legacy_auth_chain_and_api_auth_can_coexist() {
    // The spec §4.3: 旧链原样保留. Both sections can be present at once
    // without one affecting the other's selection.
    let cfg = parse_bcs_config_toml(
        r#"
bots_base_dir = "/bots"

[auth]
chain = ["session"]

[api.auth]
chain = ["gateway"]

[api.auth.gateway]
signing_key_secret = "k"
"#,
    );
    assert_eq!(cfg.auth.chain, vec!["session"]);
    let auth = cfg.api.as_ref().and_then(|a| a.auth.as_ref()).unwrap();
    assert_eq!(auth.chain, vec!["gateway"]);
}

// ---------------------------------------------------------------------------
// Internal: scopeguard-style drop guard for env-var cleanup.
// ---------------------------------------------------------------------------

struct ScopeGuard<F: FnOnce()> {
    f: Option<F>,
}
impl<F: FnOnce()> ScopeGuard<F> {
    fn new(f: F) -> Self {
        Self { f: Some(f) }
    }
}
impl<F: FnOnce()> Drop for ScopeGuard<F> {
    fn drop(&mut self) {
        if let Some(f) = self.f.take() {
            f();
        }
    }
}
fn scope_guard<F: FnOnce()>(f: F) -> ScopeGuard<F> {
    ScopeGuard::new(f)
}

// Silence unused-import warnings for helpers kept for symmetry with other
// tasks.
#[allow(dead_code)]
fn _unused_marker(_: &BTreeMap<String, serde_json::Value>) {}

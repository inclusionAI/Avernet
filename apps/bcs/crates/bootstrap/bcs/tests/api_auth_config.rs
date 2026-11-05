//! Bootstrap integration tests for the independent `[api.auth]` section.
//!
//! These tests read the FULL configuration (the `[api.auth]` subtree inside
//! `BcsConfig`) and drive the two-phase source registry:
//!
//! 1. value-domain guards + serde parse (reserved fields vs plugin tables),
//! 2. registry validation (`bcs::api_auth_registry::validate_api_auth`).
//!
//! All reject-style assertions check for sanitized error text only
//! (field path + category); raw configuration values must never be echoed.

use std::sync::Mutex;

use bcs::api_auth_registry::{
    ApiAuthCapabilities, ApiAuthSourceRegistration, ValidatedApiAuthConfig, validate_api_auth,
};
use bcs::BcsConfig;
use serial_test::serial;

// ---------------------------------------------------------------------------
// Test descriptors
// ---------------------------------------------------------------------------

/// Structural-only descriptor: records the exact JSON parameters it received
/// so tests can assert the registry forwards the same values to Task 12's
/// build step without re-conversion (no secret reads, no build calls).
fn recording_describe_and_accept(table: &serde_json::Value) -> Result<(), String> {
    RECORDED_TABLES.lock().unwrap().push(table.clone());
    Ok(())
}

/// Structural-only descriptor used by tests that do not inspect recorded args.
fn allow_any(_table: &serde_json::Value) -> Result<(), String> {
    Ok(())
}

/// Structural-only descriptor that rejects its table with a category-only
/// message, exercising the registry's field-path prefixing.
fn reject_with_unknown_field(_table: &serde_json::Value) -> Result<(), String> {
    Err("unknown_field: client_id must be a string".to_string())
}

static RECORDED_TABLES: Mutex<Vec<serde_json::Value>> = Mutex::new(Vec::new());

fn recorded_tables() -> Vec<serde_json::Value> {
    let mut guard = RECORDED_TABLES.lock().unwrap();
    std::mem::take(&mut *guard)
}

/// The build pointer these registry-phase tests never expect to run: an
/// assembly attempt would surface it, not silent acceptance.
fn test_build(
    _ctx: bcs::api_auth_registry::ApiAuthBuildContext,
) -> std::pin::Pin<
    Box<
        dyn std::future::Future<
                Output = Result<
                    std::sync::Arc<dyn bcs_api_http::PrincipalVerifier>,
                    String,
                >,
            > + Send,
    >,
> {
    Box::pin(async { Err("test registration build must not be called".to_string()) })
}

fn registration(
    name: &str,
    uses_browser_cookie: bool,
    is_oauth_provider: bool,
    validate: fn(&serde_json::Value) -> Result<(), String>,
) -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: name.to_string(),
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie,
            is_oauth_provider,
        },
        validate,
        build: test_build,
    }
}

/// The registrations used by most tests: `gateway` (token source),
/// `cookie` (browser cookie source), `github` (OAuth provider).
/// None of these are default production registrations; they are test
/// descriptors injected per test.
fn test_registrations() -> Vec<ApiAuthSourceRegistration> {
    vec![
        registration("gateway", false, false, allow_any),
        registration("cookie", true, false, allow_any),
        registration("github", false, true, allow_any),
    ]
}

/// Directly constructs an `[api.auth]` section without touching the full
/// config (used only for registry-hygiene cases on the registration inputs).
fn api_auth_section(body: &str) -> bcs_config_api::api_auth::ApiAuthConfig {
    toml::from_str(body).unwrap()
}

// ---------------------------------------------------------------------------
// Config helpers (full-file, real parse path)
// ---------------------------------------------------------------------------

/// Writes the content to a temp `.toml` file and loads it through
/// `BcsConfig::from_file`, i.e. the real value-domain-guarded parse path.
fn parse_full(content: &str) -> Result<BcsConfig, String> {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bcs-config.toml");
    std::fs::write(&path, content).unwrap();
    BcsConfig::from_file(&path).map_err(|e| e.to_string())
}

/// Minimal valid full config whose `[api.auth]` body is provided by the test.
fn full_config_with(api_auth_body: &str) -> String {
    format!(
        r#"
bots_base_dir = "/tmp/bcs-api-auth-test"
bind = "127.0.0.1"
port = 21000

[api.auth]
{api_auth_body}
"#
    )
}

/// Parses the full config and runs registry validation; both phases' errors
/// flatten to a single `Result` so every reject case asserts "the layered
/// pipeline rejects this input".
fn run_pipeline(
    content: &str,
    registrations: &[ApiAuthSourceRegistration],
) -> Result<ValidatedApiAuthConfig, String> {
    let config = parse_full(content)?;
    let auth = config
        .api
        .auth
        .as_ref()
        .ok_or_else(|| "api.auth missing from config".to_string())?;
    validate_api_auth(auth, registrations)
}

fn expect_ok(
    content: &str,
    registrations: &[ApiAuthSourceRegistration],
    case: &str,
) -> ValidatedApiAuthConfig {
    run_pipeline(content, registrations)
        .unwrap_or_else(|err| panic!("expected acceptance ({case}): {err}"))
}

fn expect_rejected(
    content: &str,
    registrations: &[ApiAuthSourceRegistration],
    case: &str,
) -> String {
    match run_pipeline(content, registrations) {
        Ok(_) => panic!("expected rejection ({case}), but the pipeline accepted it"),
        Err(err) => err,
    }
}

/// Standard OAuth-enabled body: chain + a valid set of public fields.
fn oauth_body(public_base_url: &str, extra: &str) -> String {
    format!(
        "chain = [\"github\"]\n\
         public_base_url = \"{public_base_url}\"\n\
         session_signing_key_secret = \"bcs-api-session-signing-key\"\n\
         success_redirect_path = \"/\"\n\
         {extra}"
    )
}

/// Default valid public URL used by cases that focus on other fields.
const VALID_OAUTH_URL: &str = "https://bcs.example.com/openapi/v1/auth";

// ---------------------------------------------------------------------------
// Registry discovery (step 2/3 skeleton tests)
// ---------------------------------------------------------------------------

#[test]
fn registered_sources_resolve_from_a_full_config() {
    let validated = expect_ok(
        &full_config_with(
            r#"chain = ["gateway", "cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.gateway]
audience = "bcs"

[api.auth.cookie]
client_id = "test-client"
"#,
        ),
        &test_registrations(),
        "full enrollment",
    );

    assert_eq!(validated.chain, ["gateway", "cookie"]);
    assert_eq!(validated.oauth, None, "no OAuth source enabled");
    assert_eq!(
        validated.trusted_browser_origins,
        ["https://workbench.example.com"]
    );
    assert_eq!(validated.plugin_options["gateway"]["audience"], "bcs");
    assert_eq!(validated.plugin_options["cookie"]["client_id"], "test-client");
}

#[test]
fn unregistered_plugin_table_is_rejected_by_the_registry() {
    let err = expect_rejected(
        &full_config_with(
            r#"chain = ["gateway"]

[api.auth.mystery]
client_id = "somebody-else"
"#,
        ),
        &test_registrations(),
        "unregistered plugin table",
    );
    assert!(
        err.contains("api.auth.mystery"),
        "error should name the unknown table path: {err}"
    );
    assert!(
        !err.contains("somebody-else"),
        "error must not echo raw config values: {err}"
    );
}

#[test]
fn unregistered_chain_source_is_rejected() {
    let err = expect_rejected(
        &full_config_with(r#"chain = ["gateway"]"#),
        // Empty registry: the same input must be rejected when the source is
        // not registered, instead of being silently ignored.
        &[],
        "unregistered chain source",
    );
    assert!(err.contains("api.auth.chain[0]"), "unexpected error: {err}");
    assert!(err.contains("not registered"), "unexpected error: {err}");
}

#[test]
fn descriptor_errors_are_prefixed_with_the_table_path() {
    let registrations = vec![registration("dingtalk_sso", true, false, reject_with_unknown_field)];
    let err = expect_rejected(
        &full_config_with(
            r#"chain = ["dingtalk_sso"]
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.dingtalk_sso]
client_id = 3
"#,
        ),
        &registrations,
        "descriptor rejection",
    );
    assert!(
        err.starts_with("api.auth.dingtalk_sso: "),
        "descriptor error should carry the table path: {err}"
    );
    assert!(err.contains("unknown_field"), "unexpected error: {err}");
    assert!(
        !err.contains(": 3"),
        "error must not echo raw table values: {err}"
    );
}

#[test]
fn registry_rejects_duplicate_reserved_and_illegal_registrations() {
    let naive = api_auth_section(r#"chain = ["gateway"]"#);

    let duplicated = vec![
        registration("cookie", true, false, allow_any),
        registration("cookie", true, false, allow_any),
    ];
    let err = validate_api_auth(&naive, &duplicated).unwrap_err();
    assert!(err.contains("duplicate registration"), "unexpected: {err}");

    let reserved = vec![registration("chain", false, false, allow_any)];
    let err = validate_api_auth(&naive, &reserved).unwrap_err();
    assert!(err.contains("reserved"), "unexpected: {err}");

    let illegal = vec![registration("Gateway", false, false, allow_any)];
    let err = validate_api_auth(&naive, &illegal).unwrap_err();
    assert!(err.contains("illegal"), "unexpected: {err}");
}

#[test]
fn empty_chain_is_rejected_from_a_full_config() {
    let err = expect_rejected(
        &full_config_with("chain = []"),
        &test_registrations(),
        "empty chain",
    );
    assert!(err.contains("api.auth.chain"), "unexpected error: {err}");
    assert!(err.contains("empty"), "unexpected error: {err}");
}

#[test]
fn explicit_empty_api_auth_table_is_not_compatible() {
    // An explicit `[api.auth]` table without a chain is a configuration
    // error; it must not slide through as a compatible default.
    let err = parse_full(
        r#"
bots_base_dir = "/tmp/bcs-api-auth-test"

[api.auth]
"#,
    )
    .unwrap_err();
    assert!(err.contains("chain"), "unexpected error: {err}");
}

#[test]
fn absent_api_section_keeps_legacy_compatibility() {
    let config = parse_full(
        r#"
bots_base_dir = "/tmp/bcs-api-auth-test"

[auth]
chain = ["session"]
"#,
    )
    .unwrap();
    assert!(config.api.auth.is_none());
    // The legacy chain is untouched by the new section.
    assert_eq!(config.auth.chain, ["session"]);
}

// ---------------------------------------------------------------------------
// Public format table (3b)
// ---------------------------------------------------------------------------

#[test]
fn public_base_url_accepts_absolute_https_and_local_loopback_http() {
    for (raw, normalized) in [
        (
            "https://bcs.example.com/openapi/v1/auth",
            "https://bcs.example.com/openapi/v1/auth",
        ),
        (
            "http://127.0.0.1:21000/openapi/v1/auth",
            "http://127.0.0.1:21000/openapi/v1/auth",
        ),
        ("http://localhost:8443/auth", "http://localhost:8443/auth"),
        ("http://[::1]:21000/auth", "http://[::1]:21000/auth"),
        ("http://[0:0:0:0:0:0:0:1]/auth", "http://[::1]/auth"),
    ] {
        let content = oauth_body(raw, "");
        let validated = expect_ok(
            &full_config_with(&content),
            &test_registrations(),
            &format!("accept {raw}"),
        );
        let oauth = validated.oauth.expect("OAuth enabled");
        assert_eq!(oauth.public_base_url, normalized);
        assert_eq!(oauth.idle_secs, 1800, "default 30 minutes -> 1800 seconds");
    }
}

#[test]
fn public_base_url_rejects_relative_non_http_userinfo_query_fragment_remote_http() {
    for (category, raw) in [
        ("relative URL", "bcs.example.com/openapi/v1/auth"),
        ("non http(s) scheme", "ftp://bcs.example.com/auth"),
        ("userinfo", "https://user:supersecret@bcs.example.com/auth"),
        ("query", "https://bcs.example.com/auth?token=1"),
        ("fragment", "https://bcs.example.com/auth#top"),
        ("remote plain http", "http://bcs.example.com/auth"),
        ("remote IPv6 plain http", "http://[2001:db8::1]/auth"),
    ] {
        let err = expect_rejected(
            &full_config_with(&oauth_body(raw, "")),
            &test_registrations(),
            category,
        );
        assert!(
            err.contains("api.auth.public_base_url"),
            "unexpected error for {category}: {err}"
        );
        assert!(
            !err.contains("supersecret"),
            "error must not echo config values: {err}"
        );
    }
}

#[test]
fn success_redirect_path_accepts_in_site_paths() {
    for path in ["/", "/workbench"] {
        let content = format!(
            "chain = [\"github\"]\n\
             public_base_url = \"{VALID_OAUTH_URL}\"\n\
             session_signing_key_secret = \"sk\"\n\
             success_redirect_path = \"{path}\"\n"
        );
        let validated = expect_ok(
            &full_config_with(&content),
            &test_registrations(),
            "in-site redirect",
        );
        let oauth = validated.oauth.expect("OAuth enabled");
        assert_eq!(oauth.success_redirect_path, path);
    }
}

#[test]
fn success_redirect_path_rejects_origin_changing_and_control_values() {
    for (category, value_literal) in [
        ("absolute URL", "\"https://evil.example\""),
        ("protocol relative", "\"//evil.example\""),
        // The empty value is its own TOML-shape-legal reject case.
        ("empty", "\"\""),
        ("no leading slash", "\"workbench\""),
        // TOML literal (single-quoted) string avoids double-escaping the
        // backslash case.
        ("backslash", "'/work\\bench'"),
        ("carriage return", "\"/workbench\\r\""),
        ("line feed", "\"/workbench\\n\""),
        ("control char", "\"/workbench\\u0007\""),
    ] {
        let content = format!(
            "chain = [\"github\"]\n\
             public_base_url = \"{VALID_OAUTH_URL}\"\n\
             session_signing_key_secret = \"sk\"\n\
             success_redirect_path = {value_literal}\n"
        );
        let err = expect_rejected(
            &full_config_with(&content),
            &test_registrations(),
            category,
        );
        assert!(
            err.contains("api.auth.success_redirect_path"),
            "unexpected error for {category}: {err}"
        );
        assert!(
            !err.contains("evil.example"),
            "error must not echo raw values: {err}"
        );
    }
}

#[test]
fn idle_timeout_accepts_positive_conversions_and_defaults() {
    for (minutes_line, idle_secs) in [
        // Absent -> the 30-minute default normalizes to 1800 seconds.
        (String::new(), 1800u64),
        ("1".to_string(), 60),
        ("30".to_string(), 1800),
        // The conversion stays exact above the f64-exact integer range.
        ("9007199254740993".to_string(), 9007199254740993 * 60),
    ] {
        let minutes_clause = if minutes_line.is_empty() {
            String::new()
        } else {
            format!("session_idle_timeout_minutes = {minutes_line}\n")
        };
        let content = oauth_body(VALID_OAUTH_URL, &minutes_clause);
        let validated = expect_ok(
            &full_config_with(&content),
            &test_registrations(),
            &minutes_line,
        );
        assert_eq!(
            validated.oauth.expect("OAuth enabled").idle_secs,
            idle_secs
        );
    }
}

#[test]
fn idle_timeout_rejects_zero_and_overflow() {
    for (category, minutes_line) in [
        ("zero", "session_idle_timeout_minutes = 0"),
        (
            "checked_mul overflow",
            "session_idle_timeout_minutes = 9223372036854775807",
        ),
        ("float", "session_idle_timeout_minutes = 1.5"),
    ] {
        let content = oauth_body(VALID_OAUTH_URL, &format!("{minutes_line}\n"));
        let err = expect_rejected(
            &full_config_with(&content),
            &test_registrations(),
            category,
        );
        assert!(
            err.contains("api.auth.session_idle_timeout_minutes"),
            "unexpected error for {category}: {err}"
        );
    }
    // Invalid values never silently fall back to the 30-minute default.
}

#[test]
fn oauth_enabled_requires_all_public_params() {
    for (case, content) in [
        (
            "missing public_base_url",
            "chain = [\"github\"]\nsession_signing_key_secret = \"sk\"\n\
             success_redirect_path = \"/\"\n",
        ),
        (
            "missing session_signing_key_secret",
            "chain = [\"github\"]\npublic_base_url = \"https://bcs.example.com\"\n\
             success_redirect_path = \"/\"\n",
        ),
    ] {
        let err =
            expect_rejected(&full_config_with(content), &test_registrations(), case);
        assert!(err.contains("api.auth"), "unexpected error for {case}: {err}");
    }
}

#[test]
fn cookie_only_chain_requires_origins_and_cannot_bind_oauth_names() {
    // A cookie-only chain still requires trusted_browser_origins ...
    let err = expect_rejected(
        &full_config_with(r#"chain = ["cookie"]"#),
        &test_registrations(),
        "cookie chain without origins",
    );
    assert!(
        err.contains("api.auth.trusted_browser_origins"),
        "unexpected error: {err}"
    );

    // ... and an OAuth public field configured without any enabled OAuth
    // source is a startup error, so a cookie-only chain cannot bind OAuth
    // names.
    let err = expect_rejected(
        &full_config_with(
            r#"chain = ["cookie"]
trusted_browser_origins = ["https://workbench.example.com"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
"#,
        ),
        &test_registrations(),
        "cookie chain binding oauth names",
    );
    assert!(err.contains("OAuth"), "unexpected error: {err}");
}

#[test]
fn trusted_origins_require_a_cookie_source() {
    // trusted_browser_origins configured while no cookie source is enabled
    // is rejected just as strictly as a missing list under cookie chains.
    let err = expect_rejected(
        &full_config_with(&oauth_body(
            VALID_OAUTH_URL,
            "trusted_browser_origins = [\"https://workbench.example.com\"]\n",
        )),
        &test_registrations(),
        "origins without cookie source",
    );
    assert!(
        err.contains("api.auth.trusted_browser_origins"),
        "unexpected error: {err}"
    );
}

// ---------------------------------------------------------------------------
// Final overlay tests (step 4)
// ---------------------------------------------------------------------------

fn write_overlay_result(base: &str, overlay: &str) -> Result<BcsConfig, String> {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("bcs-config.toml"), base).unwrap();
    // The env overlay file must exist under whichever suffix the ambient
    // SERVER_ENV resolves to; write one copy per suffix.
    for suffix in ["local", "dev", "pre", "prod", "gray"] {
        std::fs::write(
            dir.path().join(format!("bcs-config-{suffix}.toml")),
            overlay,
        )
        .unwrap();
    }
    bcs::BcsConfig::try_load_with_env(Some(&dir.path().to_path_buf()))
}

fn write_overlay(base: &str, overlay: &str) -> BcsConfig {
    write_overlay_result(base, overlay).expect("overlay config loads")
}

const OVERLAY_BASE: &str = r#"
bots_base_dir = "/tmp/bcs-api-auth-test"
bind = "127.0.0.1"
port = 21000

[auth]
chain = ["session"]

[api.auth]
chain = ["gateway"]
session_idle_timeout_minutes = 30

[api.auth.gateway]
audience = "bcs"
"#;

#[test]
fn overlay_replaces_chain_and_keeps_legacy_auth_unchanged() {
    let config = write_overlay(
        OVERLAY_BASE,
        r#"
[api.auth]
chain = ["github", "cookie"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "bcs-api-session-signing-key"
success_redirect_path = "/workbench"
session_idle_timeout_minutes = 1
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.github]
client_id = "cid"

[api.auth.cookie]
client_id = "c1"
"#,
    );

    // The overlay REPLACES the chain (no merged concatenation); the base
    // `gateway` table remains present as a not-enabled table.
    let api_auth = config.api.auth.as_ref().expect("api.auth present");
    assert_eq!(api_auth.chain, ["github", "cookie"]);
    assert!(api_auth.plugins.contains_key("gateway"));
    assert!(api_auth.plugins.contains_key("github"));
    assert!(api_auth.plugins.contains_key("cookie"));

    // Legacy [auth] untouched by the new section: a new explicit api.auth
    // config does not change what the native human/legacy router accepts.
    assert_eq!(config.auth.chain, ["session"]);

    let validated = validate_api_auth(api_auth, &test_registrations())
        .unwrap_or_else(|err| panic!("merged overlay config validates: {err}"));
    assert_eq!(validated.chain, ["github", "cookie"]);
    let oauth = validated.oauth.expect("oauth chain");
    assert_eq!(oauth.success_redirect_path, "/workbench");
    // The overlay REPLACES the base's 30-minute timeout (no merging).
    assert_eq!(api_auth.session_idle_timeout_minutes, Some(1));
    assert_eq!(oauth.idle_secs, 60);
    assert_eq!(
        validated.trusted_browser_origins,
        ["https://workbench.example.com"]
    );
}

#[test]
fn overlay_may_not_bring_illegal_global_values_into_the_final_config() {
    // The overlay enables an OAuth source first, so the final merged config
    // carries illegal values INTO the registry phase, which must reject them
    // before any secret/provider build could run.
    for (case, overlay, needle) in [
        (
            "userinfo url",
            "[api.auth]\nchain = [\"github\"]\n\
             public_base_url = \"https://user:pw@evil.example.com/auth\"\n\
             session_signing_key_secret = \"sk\"\n\
             success_redirect_path = \"/\"\n",
            "userinfo",
        ),
        (
            "open redirect",
            "[api.auth]\nchain = [\"github\"]\n\
             public_base_url = \"https://bcs.example.com/openapi/v1/auth\"\n\
             session_signing_key_secret = \"sk\"\n\
             success_redirect_path = \"//evil.example\"\n",
            "success_redirect",
        ),
        (
            "zero timeout",
            "[api.auth]\nchain = [\"github\"]\n\
             public_base_url = \"https://bcs.example.com/openapi/v1/auth\"\n\
             session_signing_key_secret = \"sk\"\n\
             success_redirect_path = \"/\"\n\
             session_idle_timeout_minutes = 0\n",
            "must be positive",
        ),
    ] {
        let config = write_overlay(OVERLAY_BASE, overlay);
        let api_auth = config.api.auth.as_ref().expect("api.auth present");
        let err = match validate_api_auth(api_auth, &test_registrations()) {
            Ok(_) => panic!("expected rejection ({case})"),
            Err(err) => err,
        };
        assert!(err.contains(needle), "expected {case} rejection, got: {err}");
    }
}

#[test]
fn overlay_honeypot_value_is_rejected_at_parse_time_not_ignored() {
    // A base-valid config whose overlay introduces an out-of-domain value is
    // rejected when the overlay file is parsed, even though nothing enabled
    // consumes the table and even though a later overlay could replace it.
    let err = write_overlay_result(
        OVERLAY_BASE,
        "[api.auth.honeypot]\nweight = 1.5\n",
    )
    .unwrap_err();
    assert!(
        err.contains("api.auth.honeypot"),
        "value-domain failure must surface: {err}"
    );
    assert!(
        err.contains("floating-point"),
        "value-domain category must surface: {err}"
    );
}

#[test]
fn overlay_gateway_only_chain_rejects_leftover_oauth_fields() {
    // Base stays gateway-only; the overlay adds an OAuth-only public field
    // without enabling any OAuth source. The final config must be rejected.
    let config = write_overlay(
        OVERLAY_BASE,
        "[api.auth]\nsuccess_redirect_path = \"/workbench\"\n",
    );
    let api_auth = config.api.auth.as_ref().expect("api.auth present");
    let err = match validate_api_auth(api_auth, &test_registrations()) {
        Ok(_) => panic!("expected rejection (gateway-only oauth field)"),
        Err(err) => err,
    };
    assert!(err.contains("OAuth"), "unexpected error: {err}");
}

// ---------------------------------------------------------------------------
// Factory consistency (4a)
// ---------------------------------------------------------------------------

#[test]
#[serial]
fn registry_forwards_exact_json_values_to_the_descriptors() {
    let registrations = vec![
        registration("gateway", false, false, recording_describe_and_accept),
        registration("cookie", true, false, recording_describe_and_accept),
    ];
    assert!(recorded_tables().is_empty(), "pre-clean");

    let validated = expect_ok(
        &full_config_with(
            r#"chain = ["gateway", "cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.gateway]
big = 9007199254740993
client_id = "gateway-client"

[api.auth.cookie]
client_id = "cookie-client"
"#,
        ),
        &registrations,
        "factory consistency",
    );

    // The descriptor saw exactly the values Task 12's build would receive:
    // big integers stay exact integers (no f64/string round-trip), strings
    // stay strings.
    let recorded = recorded_tables();
    assert_eq!(recorded.len(), 2, "both tables structurally validated");
    for table in &recorded {
        if table["client_id"] == "gateway-client" {
            assert_eq!(
                table["big"].as_i64(),
                Some(9007199254740993),
                "big integer must survive without f64 conversion"
            );
        }
    }
    assert_eq!(
        validated.plugin_options["gateway"]["big"].as_i64(),
        Some(9007199254740993)
    );
    assert_eq!(
        validated.plugin_options["gateway"]["client_id"],
        "gateway-client"
    );
}

#[test]
#[serial]
fn not_enabled_table_gets_structural_validation_but_no_pipeline_access() {
    let registrations = vec![
        registration("gateway", false, false, recording_describe_and_accept),
        registration("cookie", true, false, recording_describe_and_accept),
    ];
    assert!(recorded_tables().is_empty(), "pre-clean");

    let validated = expect_ok(
        &full_config_with(
            r#"chain = ["gateway"]

[api.auth.cookie]
client_id = "dormant-client"
"#,
        ),
        &registrations,
        "not-enabled table",
    );

    // The not-enabled cookie table was structurally validated exactly once,
    // with no secret reads (the registry prototype has no secret-supplying
    // path at all: descriptors only receive the public JSON table), and it
    // must not flow into the build-facing plugin_options.
    let recorded = recorded_tables();
    assert_eq!(recorded.len(), 1, "not-enabled table validated once");
    assert_eq!(recorded[0]["client_id"], "dormant-client");
    assert!(!validated.plugin_options.contains_key("cookie"));
    assert_eq!(validated.chain, ["gateway"]);
}

// ---------------------------------------------------------------------------
// Unknown-extension classification (config/loading.rs)
// ---------------------------------------------------------------------------

#[test]
fn unknown_extension_value_domain_failure_is_not_retried_as_toml() {
    // A JSON document with an api.auth value-domain violation must surface
    // its own error when the file extension is unknown: the TOML fallback is
    // reserved for JSON *syntax* parse failures only.
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bcs-config.conf");
    std::fs::write(
        &path,
        r#"{
      "bots_base_dir": "/tmp/bcs-api-auth-test",
      "api": {"auth": {"chain": ["gateway"], "bad": 1.5}}
    }"#,
    )
    .unwrap();
    let err = BcsConfig::from_file(&path)
        .err()
        .map(|e| e.to_string())
        .expect("value-domain failure must fail the load");
    assert!(
        err.contains("api.auth.bad"),
        "value-domain failure must surface: {err}"
    );
    assert!(
        err.contains("floating-point"),
        "value-domain category must surface: {err}"
    );
}

#[test]
fn omitted_success_redirect_defaults_to_root_only_when_oauth_is_enabled() {
    let content = "chain = [\"github\"]\n\
        public_base_url = \"https://bcs.example.com/openapi/v1/auth\"\n\
        session_signing_key_secret = \"sk\"\n";
    let validated = expect_ok(
        &full_config_with(content), &test_registrations(), "default redirect",
    );
    assert_eq!(validated.oauth.expect("OAuth enabled").success_redirect_path, "/");
    let gateway = expect_ok(
        &full_config_with("chain = [\"gateway\"]"),
        &test_registrations(), "no OAuth defaults on gateway-only chains",
    );
    assert!(gateway.oauth.is_none());
}

//! Library-target tests for session file and BCS API URL resolution.
//!
//! These tests moved out of `lib.rs` to keep that file below the size rule;
//! names still resolve from the crate root through `use super::*`.

use super::*;

use clap::Parser;
use serial_test::serial;
use tempfile::TempDir;

use super::tests::{safe_remove_var, safe_set_var, write_session_file};

fn apply_env(key: &str, value: Option<&str>) {
    match value {
        Some(value) => safe_set_var(key, value),
        None => safe_remove_var(key),
    }
}

// Same RAII helper intentionally duplicated per test target: the test sources
// are not shareable across targets by plan decision, so fix both copies together.

/// Temporarily apply environment overrides; every touched key is restored on
/// drop.
///
/// Combine with `#[serial]`: the guard restores values but does not serialize
/// tests. Bind it with `let _env = EnvVars::apply(..)`, never `let _ = ...`,
/// which drops the guard immediately.
struct EnvVars {
    saved: Vec<(&'static str, Option<String>)>,
}

impl EnvVars {
    fn apply(overrides: &[(&'static str, Option<&str>)]) -> Self {
        let saved = overrides
            .iter()
            .map(|(key, _)| (*key, std::env::var(key).ok()))
            .collect();
        for (key, value) in overrides {
            apply_env(key, *value);
        }
        Self { saved }
    }
}

impl Drop for EnvVars {
    fn drop(&mut self) {
        for (key, value) in &self.saved {
            apply_env(key, value.as_deref());
        }
    }
}

/// Parse the library's flattened global args the way a consumer's CLI does.
#[derive(clap::Parser)]
struct TestGlobalArgs {
    #[command(flatten)]
    globals: GlobalArgs,
}

fn global_args(url: Option<&str>) -> GlobalArgs {
    GlobalArgs {
        url: url.map(str::to_string),
        cookie: None,
        log_level: "info".to_string(),
        json: false,
        debug: false,
    }
}

#[test]
fn test_normalize_bcs_api_url_strips_query_and_fragment_and_rejects_other_schemes() {
    assert_eq!(
        normalize_bcs_api_url("wss://example.org/base/ws/bot?q=1#f").as_deref(),
        Some("https://example.org/base")
    );
    assert_eq!(
        normalize_bcs_api_url("ws://example.org/ws/bot").as_deref(),
        Some("http://example.org")
    );
    assert!(normalize_bcs_api_url("file:///tmp/a").is_none());
    assert!(normalize_bcs_api_url("   ").is_none());
}

/// clap capture layer: an explicit `--url` wins over both environment
/// variables before the resolver ever runs.
#[test]
#[serial]
fn test_clap_capture_prefers_explicit_url_over_environment_urls() {
    let _env = EnvVars::apply(&[
        ("MOLTIS_BCS_URL", Some("https://moltis.example.com")),
        ("BCS_API_BASE_URL", Some("https://base.example.com")),
    ]);

    let parsed = TestGlobalArgs::try_parse_from([
        "bcs-cli",
        "--url",
        "https://explicit.example.com",
    ])
    .unwrap();

    assert_eq!(
        parsed.globals.url.as_deref(),
        Some("https://explicit.example.com")
    );
    assert_eq!(
        resolve_bcs_url(&parsed.globals).unwrap(),
        "https://explicit.example.com"
    );
}

/// clap capture layer: without `--url`, `MOLTIS_BCS_URL` lands in
/// `GlobalArgs::url`, so the resolver's explicit-argument branch consumes it
/// before `BCS_API_BASE_URL` is consulted.
#[test]
#[serial]
fn test_clap_capture_captures_moltis_before_api_base_env() {
    let _env = EnvVars::apply(&[
        ("MOLTIS_BCS_URL", Some("wss://moltis.example.com/ws/bot")),
        ("BCS_API_BASE_URL", Some("https://base.example.com")),
        ("BOT_DATA_DIR", None),
    ]);

    let parsed = TestGlobalArgs::try_parse_from(["bcs-cli"]).unwrap();

    assert_eq!(
        parsed.globals.url.as_deref(),
        Some("wss://moltis.example.com/ws/bot")
    );
    assert_eq!(
        resolve_bcs_url(&parsed.globals).unwrap(),
        "https://moltis.example.com"
    );
}

/// Resolver layer: with a hand-built `url: None`, `BCS_API_BASE_URL` outranks
/// `MOLTIS_BCS_URL` and the session file; without it, `MOLTIS_BCS_URL`
/// outranks the session file.
#[test]
#[serial]
fn test_resolver_env_url_precedence_over_session() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        serde_json::json!({
            "token": "session-token",
            "api_base_url": "https://session.example.com"
        }),
    );
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", Some("wss://base.example.com/ws/bot")),
        ("MOLTIS_BCS_URL", Some("wss://moltis.example.com/ws/bot")),
    ]);

    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(&global_args(None), None, None).unwrap(),
        "https://base.example.com"
    );

    safe_remove_var("BCS_API_BASE_URL");

    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(&global_args(None), None, None).unwrap(),
        "https://moltis.example.com"
    );
}

/// Resolver layer: one session carrying both URL fields uses `api_base_url`.
#[test]
#[serial]
fn test_session_resolution_prefers_api_base_url_field() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        serde_json::json!({
            "token": "session-token",
            "api_base_url": "https://api.example.com",
            "bcs_url": "ws://session.example.com/ws/bot"
        }),
    );
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", None),
        ("MOLTIS_BCS_URL", None),
    ]);

    assert_eq!(
        resolve_session_bcs_url().unwrap().as_deref(),
        Some("https://api.example.com")
    );
    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(&global_args(None), None, None).unwrap(),
        "https://api.example.com"
    );
}

/// Resolver layer: session URL outranks an injected compiled default.
#[test]
#[serial]
fn test_resolver_prefers_session_url_over_injected_distribution_default() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        serde_json::json!({
            "token": "session-token",
            "api_base_url": "https://session.example.com"
        }),
    );
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", None),
        ("MOLTIS_BCS_URL", None),
        ("AGENTCLAW_ENV", Some("pre")),
    ]);

    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(
            &global_args(None),
            Some("https://pre.example.com"),
            Some("https://prod.example.com"),
        )
        .unwrap(),
        "https://session.example.com"
    );
}

/// Resolver layer: the injected compiled default outranks the localhost
/// fallback, and the runtime environment selects pre versus production.
#[test]
#[serial]
fn test_resolver_selects_injected_distribution_default_before_local() {
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", None),
        ("BCS_API_BASE_URL", None),
        ("MOLTIS_BCS_URL", None),
        ("AGENTCLAW_ENV", Some("pre")),
    ]);
    let args = global_args(None);
    let pre_url = Some("https://pre.example.com");
    let prod_url = Some("https://prod.example.com");

    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(&args, pre_url, prod_url).unwrap(),
        "https://pre.example.com"
    );

    safe_set_var("AGENTCLAW_ENV", "prod");

    assert_eq!(
        resolve_bcs_url_with_distribution_defaults(&args, pre_url, prod_url).unwrap(),
        "https://prod.example.com"
    );
}

/// Resolver layer: an invalid higher-priority URL is reported, never skipped
/// in favour of a lower-priority source.
#[test]
#[serial]
fn test_resolver_rejects_invalid_url_without_falling_back() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        serde_json::json!({
            "token": "session-token",
            "api_base_url": "https://session.example.com"
        }),
    );
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", Some("file:///tmp/a")),
        ("MOLTIS_BCS_URL", Some("https://moltis.example.com")),
    ]);

    let error = resolve_bcs_url_with_distribution_defaults(&global_args(None), None, None)
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("Invalid BCS API URL from BCS_API_BASE_URL"),
        "{error}"
    );

    let error = resolve_bcs_url_with_distribution_defaults(
        &global_args(Some("not-a-url")),
        None,
        None,
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("Invalid BCS API URL from --url"), "{error}");
}

/// A missing session file is `Ok(None)`; malformed JSON is a parse error that
/// the resolver surfaces instead of defaulting locally.
#[test]
#[serial]
fn test_session_info_missing_file_is_none_and_bad_json_is_an_error() {
    let temp_dir = TempDir::new().unwrap();
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", None),
        ("MOLTIS_BCS_URL", None),
    ]);

    assert!(load_optional_session_info().unwrap().is_none());

    let session_dir = temp_dir.path().join(".bcs");
    std::fs::create_dir_all(&session_dir).unwrap();
    std::fs::write(session_dir.join("session.json"), "not JSON").unwrap();

    let error = load_optional_session_info().unwrap_err().to_string();
    assert!(error.contains("Failed to parse session file"), "{error}");

    let error = resolve_bcs_url_with_distribution_defaults(
        &global_args(None),
        Some("https://pre.example.com"),
        Some("https://prod.example.com"),
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("Failed to parse session file"), "{error}");
}

use super::{
    Cli, Commands, NetworkEnv, TempDir, Write, detect_network_env, discover_token,
    is_localhost_url, json, load_optional_session_info, normalize_bcs_ws_url,
    resolve_bcs_url, safe_remove_var, safe_set_var, serial, write_session_file,
};
use clap::Parser;

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

/// A parsed command whose URL comes only from the resolver, never from clap.
fn health_cli() -> Cli {
    Cli {
        url: None,
        cookie: None,
        log_level: "info".to_string(),
        json: false,
        no_json: true,
        #[cfg(debug_assertions)]
        debug: false,
        command: Commands::Health,
    }
}

#[test]
fn test_normalize_bcs_ws_url_from_http_base() {
    assert_eq!(
        normalize_bcs_ws_url("http://localhost:21000").as_deref(),
        Some("ws://localhost:21000/ws/bot")
    );
}

#[test]
fn test_normalize_bcs_ws_url_from_https_base() {
    assert_eq!(
        normalize_bcs_ws_url("https://bcs.example.com").as_deref(),
        Some("wss://bcs.example.com/ws/bot")
    );
}

#[test]
fn test_normalize_bcs_ws_url_keeps_existing_ws_endpoints() {
    assert_eq!(
        normalize_bcs_ws_url("ws://localhost:21000/ws/bot").as_deref(),
        Some("ws://localhost:21000/ws/bot")
    );
    assert_eq!(
        normalize_bcs_ws_url("wss://bcs.example.com/ws/bot").as_deref(),
        Some("wss://bcs.example.com/ws/bot")
    );
}

/// Test token discovery priority: CLI arg > env var > session file
#[test]
fn test_token_discovery_priority_cli_first() {
    // CLI arg should take highest priority
    let explicit_token = "explicit-token-123";
    let result = discover_token(Some(explicit_token));
    assert!(result.is_ok());
    assert_eq!(result.unwrap(), explicit_token);
}

/// Test token discovery from BCN_BOT_TOKEN environment variable
#[test]
#[serial]
fn test_token_discovery_from_env_var() {
    let temp_dir = TempDir::new().unwrap();
    let data_dir = temp_dir.path().to_path_buf();

    // Save original env
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_token = std::env::var("BCN_BOT_TOKEN").ok();

    // Set test env vars
    safe_set_var("BOT_DATA_DIR", &data_dir);
    safe_set_var("BCN_BOT_TOKEN", "env-token-456");

    // No explicit token, should use env var
    let result = discover_token(None);

    // Restore env
    if let Some(orig) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", orig);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(orig) = original_token {
        safe_set_var("BCN_BOT_TOKEN", orig);
    } else {
        safe_remove_var("BCN_BOT_TOKEN");
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), "env-token-456");
}

/// Test token discovery from session file
#[test]
#[serial]
fn test_token_discovery_from_session_file() {
    let temp_dir = TempDir::new().unwrap();
    let data_dir = temp_dir.path().to_path_buf();
    let bcs_dir = data_dir.join(".bcs");
    std::fs::create_dir_all(&bcs_dir).unwrap();

    // Write session file
    let session_file = bcs_dir.join("session.json");
    let session_content = json!({
        "bot_uuid": "bot-test-789",
        "token": "file-token-789",
        "bcs_url": "ws://localhost:21000/ws/bot"
    });
    let mut file = std::fs::File::create(&session_file).unwrap();
    file.write_all(
        serde_json::to_string_pretty(&session_content)
            .unwrap()
            .as_bytes(),
    )
    .unwrap();

    // Save original env
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_token = std::env::var("BCN_BOT_TOKEN").ok();

    // Set test data dir, clear BCN_BOT_TOKEN
    safe_set_var("BOT_DATA_DIR", &data_dir);
    safe_remove_var("BCN_BOT_TOKEN");

    // No explicit token, no env var, should use session file
    let result = discover_token(None);

    // Restore env
    if let Some(orig) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", orig);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(orig) = original_token {
        safe_set_var("BCN_BOT_TOKEN", orig);
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), "file-token-789");
}

/// Test token discovery priority: CLI arg overrides env var
#[test]
#[serial]
fn test_token_cli_overrides_env() {
    let temp_dir = TempDir::new().unwrap();
    let data_dir = temp_dir.path().to_path_buf();

    // Save original env
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_token = std::env::var("BCN_BOT_TOKEN").ok();

    safe_set_var("BOT_DATA_DIR", &data_dir);
    safe_set_var("BCN_BOT_TOKEN", "env-token");

    // Explicit token should override env var
    let result = discover_token(Some("cli-token"));

    // Restore env
    if let Some(orig) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", orig);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(orig) = original_token {
        safe_set_var("BCN_BOT_TOKEN", orig);
    } else {
        safe_remove_var("BCN_BOT_TOKEN");
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), "cli-token");
}

/// Test token discovery priority: env var overrides session file
#[test]
#[serial]
fn test_token_env_overrides_file() {
    let temp_dir = TempDir::new().unwrap();
    let data_dir = temp_dir.path().to_path_buf();
    let bcs_dir = data_dir.join(".bcs");
    std::fs::create_dir_all(&bcs_dir).unwrap();

    // Write session file
    let session_file = bcs_dir.join("session.json");
    let session_content = json!({
        "bot_id": "bot-test",
        "token": "file-token",
        "bcs_url": "ws://localhost:21000/ws/bot"
    });
    let mut file = std::fs::File::create(&session_file).unwrap();
    file.write_all(
        serde_json::to_string_pretty(&session_content)
            .unwrap()
            .as_bytes(),
    )
    .unwrap();

    // Save original env
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_token = std::env::var("BCN_BOT_TOKEN").ok();

    safe_set_var("BOT_DATA_DIR", &data_dir);
    safe_set_var("BCN_BOT_TOKEN", "env-token");

    // Env var should override session file
    let result = discover_token(None);

    // Restore env
    if let Some(orig) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", orig);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(orig) = original_token {
        safe_set_var("BCN_BOT_TOKEN", orig);
    } else {
        safe_remove_var("BCN_BOT_TOKEN");
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), "env-token");
}

/// Test token discovery returns an empty token when no source is available,
/// allowing the CLI to proceed without authentication.
#[test]
#[serial]
fn test_token_not_found_returns_empty() {
    let temp_dir = TempDir::new().unwrap();
    let data_dir = temp_dir.path().to_path_buf();

    // Save original env
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_token = std::env::var("BCN_BOT_TOKEN").ok();

    // Set empty environment (no token anywhere)
    safe_set_var("BOT_DATA_DIR", &data_dir);
    safe_remove_var("BCN_BOT_TOKEN");

    // Should succeed with an empty token (no auth)
    let result = discover_token(None);

    // Restore env
    if let Some(orig) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", orig);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(orig) = original_token {
        safe_set_var("BCN_BOT_TOKEN", orig);
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), "");
}

#[test]
#[serial]
fn test_resolve_bcs_url_from_session_file() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        json!({
            "bot_uuid": null,
            "token": "session-token",
            "bcs_url": "ws://localhost:21000/ws/bot"
        }),
    );

    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_url = std::env::var("MOLTIS_BCS_URL").ok();
    let original_base_url = std::env::var("BCS_API_BASE_URL").ok();

    safe_set_var("BOT_DATA_DIR", temp_dir.path());
    safe_remove_var("MOLTIS_BCS_URL");
    safe_remove_var("BCS_API_BASE_URL");

    let cli = Cli {
        url: None,
        cookie: None,
        log_level: "info".to_string(),
        json: false,
        no_json: true,
        #[cfg(debug_assertions)]
        debug: false,
        command: Commands::Health,
    };
    let resolved = resolve_bcs_url(&cli).unwrap();

    if let Some(value) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", value);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(value) = original_url {
        safe_set_var("MOLTIS_BCS_URL", value);
    } else {
        safe_remove_var("MOLTIS_BCS_URL");
    }
    if let Some(value) = original_base_url {
        safe_set_var("BCS_API_BASE_URL", value);
    } else {
        safe_remove_var("BCS_API_BASE_URL");
    }

    assert_eq!(resolved, "http://localhost:21000");
}

#[test]
#[serial]
fn test_resolve_bcs_url_uses_distribution_default_or_local() {
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_url = std::env::var("MOLTIS_BCS_URL").ok();
    let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
    let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

    safe_remove_var("BOT_DATA_DIR");
    safe_remove_var("MOLTIS_BCS_URL");
    safe_remove_var("BCS_API_BASE_URL");
    safe_remove_var("AGENTCLAW_ENV");

    let cli = Cli {
        url: None,
        cookie: None,
        log_level: "info".to_string(),
        json: false,
        no_json: true,
        #[cfg(debug_assertions)]
        debug: false,
        command: Commands::Health,
    };
    let result = resolve_bcs_url(&cli);
    // Self-referential on purpose: the expectation derives from this build's compiled
    // defaults, so public builds pin only the localhost fallback here.
    let expected = bcs_cli::resolve_compiled_distribution_default_url()
        .unwrap()
        .unwrap_or_else(|| "http://127.0.0.1:21000".to_string());

    if let Some(value) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", value);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(value) = original_url {
        safe_set_var("MOLTIS_BCS_URL", value);
    } else {
        safe_remove_var("MOLTIS_BCS_URL");
    }
    if let Some(value) = original_base_url {
        safe_set_var("BCS_API_BASE_URL", value);
    } else {
        safe_remove_var("BCS_API_BASE_URL");
    }
    if let Some(value) = original_agentclaw_env {
        safe_set_var("AGENTCLAW_ENV", value);
    } else {
        safe_remove_var("AGENTCLAW_ENV");
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), expected);
}

#[test]
#[serial]
fn test_resolve_bcs_url_uses_pre_distribution_default_or_local() {
    let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
    let original_url = std::env::var("MOLTIS_BCS_URL").ok();
    let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
    let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

    safe_remove_var("BOT_DATA_DIR");
    safe_remove_var("MOLTIS_BCS_URL");
    safe_remove_var("BCS_API_BASE_URL");
    safe_set_var("AGENTCLAW_ENV", "pre");

    let cli = Cli {
        url: None,
        cookie: None,
        log_level: "info".to_string(),
        json: false,
        no_json: true,
        #[cfg(debug_assertions)]
        debug: false,
        command: Commands::Health,
    };
    let result = resolve_bcs_url(&cli);
    // Self-referential on purpose: the expectation derives from this build's compiled
    // defaults, so public builds pin only the localhost fallback here.
    let expected = bcs_cli::resolve_compiled_distribution_default_url()
        .unwrap()
        .unwrap_or_else(|| "http://127.0.0.1:21000".to_string());

    if let Some(value) = original_data_dir {
        safe_set_var("BOT_DATA_DIR", value);
    } else {
        safe_remove_var("BOT_DATA_DIR");
    }
    if let Some(value) = original_url {
        safe_set_var("MOLTIS_BCS_URL", value);
    } else {
        safe_remove_var("MOLTIS_BCS_URL");
    }
    if let Some(value) = original_base_url {
        safe_set_var("BCS_API_BASE_URL", value);
    } else {
        safe_remove_var("BCS_API_BASE_URL");
    }
    if let Some(value) = original_agentclaw_env {
        safe_set_var("AGENTCLAW_ENV", value);
    } else {
        safe_remove_var("AGENTCLAW_ENV");
    }

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), expected);
}

#[tokio::test]
async fn test_detect_network_env_localhost() {
    let env = detect_network_env("http://localhost:21000", false).await;
    assert_eq!(env, NetworkEnv::Prod);
}

#[tokio::test]
async fn test_detect_network_env_127() {
    let env = detect_network_env("http://127.0.0.1:21000", false).await;
    assert_eq!(env, NetworkEnv::Prod);
}

#[test]
fn test_is_localhost_url() {
    assert!(is_localhost_url("http://localhost:21000"));
    assert!(is_localhost_url("https://127.0.0.1:8080"));
    assert!(is_localhost_url("http://127.0.0.1"));
    assert!(!is_localhost_url("https://bcs.example.com"));
    assert!(!is_localhost_url("http://10.0.0.1:21000"));
}

/// clap capture layer: an explicit `--url` wins over both environment variables.
#[test]
#[serial]
fn test_clap_capture_prefers_explicit_url_over_environment_urls() {
    let _env = EnvVars::apply(&[
        ("MOLTIS_BCS_URL", Some("https://moltis.example.com")),
        ("BCS_API_BASE_URL", Some("https://base.example.com")),
    ]);

    let cli = Cli::try_parse_from([
        "bcs-cli",
        "--url",
        "https://explicit.example.com",
        "health",
    ])
    .unwrap();

    assert_eq!(cli.url.as_deref(), Some("https://explicit.example.com"));
    assert_eq!(
        resolve_bcs_url(&cli).unwrap(),
        "https://explicit.example.com"
    );
}

/// clap capture layer: without `--url`, `MOLTIS_BCS_URL` is captured into
/// `Cli::url`, so it reaches the resolver as the explicit argument and wins over
/// `BCS_API_BASE_URL` even though the resolver alone ranks it lower.
#[test]
#[serial]
fn test_clap_capture_captures_moltis_before_api_base_env() {
    let _env = EnvVars::apply(&[
        ("MOLTIS_BCS_URL", Some("wss://moltis.example.com/ws/bot")),
        ("BCS_API_BASE_URL", Some("https://base.example.com")),
        ("BOT_DATA_DIR", None),
    ]);

    let cli = Cli::try_parse_from(["bcs-cli", "health"]).unwrap();

    assert_eq!(
        cli.url.as_deref(),
        Some("wss://moltis.example.com/ws/bot")
    );
    assert_eq!(resolve_bcs_url(&cli).unwrap(), "https://moltis.example.com");
}

/// Resolver layer: with a hand-built `url: None`, `BCS_API_BASE_URL` outranks
/// both `MOLTIS_BCS_URL` and the session file; without it, `MOLTIS_BCS_URL`
/// outranks the session file.
#[test]
#[serial]
fn test_resolve_bcs_url_env_url_precedence_over_session() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        json!({
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
        resolve_bcs_url(&health_cli()).unwrap(),
        "https://base.example.com"
    );

    safe_remove_var("BCS_API_BASE_URL");

    assert_eq!(
        resolve_bcs_url(&health_cli()).unwrap(),
        "https://moltis.example.com"
    );
}

/// Resolver layer: one session carrying both URL fields uses `api_base_url`.
#[test]
#[serial]
fn test_resolve_bcs_url_prefers_session_api_base_url_field() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        json!({
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
        resolve_bcs_url(&health_cli()).unwrap(),
        "https://api.example.com"
    );
}

/// Resolver layer: an invalid higher-priority URL is returned as an error, and
/// `main` keeps its existing `unwrap_or_default()` treatment of that result.
#[test]
#[serial]
fn test_resolve_bcs_url_rejects_invalid_high_priority_url_without_fallback() {
    let temp_dir = TempDir::new().unwrap();
    write_session_file(
        &temp_dir,
        json!({
            "token": "session-token",
            "api_base_url": "https://session.example.com"
        }),
    );
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", Some("file:///tmp/a")),
        ("MOLTIS_BCS_URL", Some("https://moltis.example.com")),
    ]);

    let error = resolve_bcs_url(&health_cli()).unwrap_err().to_string();
    assert!(
        error.contains("Invalid BCS API URL from BCS_API_BASE_URL"),
        "{error}"
    );

    let mut cli = health_cli();
    cli.url = Some("not-a-url".to_string());
    let error = resolve_bcs_url(&cli).unwrap_err().to_string();
    assert!(error.contains("Invalid BCS API URL from --url"), "{error}");
}

/// A malformed session file is a resolver error, not a silent localhost default.
#[test]
#[serial]
fn test_resolve_bcs_url_reports_invalid_session_json() {
    let temp_dir = TempDir::new().unwrap();
    let session_dir = temp_dir.path().join(".bcs");
    std::fs::create_dir_all(&session_dir).unwrap();
    std::fs::write(session_dir.join("session.json"), "not JSON").unwrap();
    let _env = EnvVars::apply(&[
        ("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap())),
        ("BCS_API_BASE_URL", None),
        ("MOLTIS_BCS_URL", None),
        ("AGENTCLAW_ENV", None),
    ]);

    let error = resolve_bcs_url(&health_cli()).unwrap_err().to_string();
    assert!(error.contains("Failed to parse session file"), "{error}");
}

/// A missing session file is `Ok(None)`, never an error.
#[test]
#[serial]
fn test_load_optional_session_info_missing_file_is_none() {
    let temp_dir = TempDir::new().unwrap();
    let _env = EnvVars::apply(&[("BOT_DATA_DIR", Some(temp_dir.path().to_str().unwrap()))]);

    assert!(load_optional_session_info().unwrap().is_none());
}

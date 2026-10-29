use super::{
    Cli, Commands, NetworkEnv, TempDir, Write, detect_network_env, discover_token,
    is_localhost_url, json, normalize_bcs_api_url, normalize_bcs_ws_url, resolve_bcs_url,
    safe_remove_var, safe_set_var, serial, write_session_file,
};

#[test]
fn test_normalize_bcs_api_url_from_ws_endpoint() {
    assert_eq!(
        normalize_bcs_api_url("ws://localhost:21000/ws/bot").as_deref(),
        Some("http://localhost:21000")
    );
    assert_eq!(
        normalize_bcs_api_url("wss://bcs-pre.example.com/ws/bot").as_deref(),
        Some("https://bcs-pre.example.com")
    );
}

#[test]
fn test_normalize_bcs_api_url_keeps_http_base() {
    assert_eq!(
        normalize_bcs_api_url("https://bcs.example.com/").as_deref(),
        Some("https://bcs.example.com")
    );
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

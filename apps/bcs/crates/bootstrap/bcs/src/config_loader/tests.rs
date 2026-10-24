use super::*;
use serial_test::serial;

// =========================================================================
// Environment tests
// =========================================================================

#[test]
#[serial]
fn test_environment_resolve_prod() {
    unsafe { std::env::set_var("SERVER_ENV", "prod"); }
    assert_eq!(Environment::resolve(), Environment::Prod);
    unsafe { std::env::remove_var("SERVER_ENV"); }
}

#[test]
#[serial]
fn test_environment_resolve_gray() {
    unsafe { std::env::set_var("SERVER_ENV", "gray"); }
    assert_eq!(Environment::resolve(), Environment::Gray);
    unsafe { std::env::remove_var("SERVER_ENV"); }
}

#[test]
#[serial]
fn test_environment_resolve_pre() {
    unsafe { std::env::set_var("SERVER_ENV", "pre"); }
    assert_eq!(Environment::resolve(), Environment::Pre);
    unsafe { std::env::remove_var("SERVER_ENV"); }

    unsafe { std::env::set_var("SERVER_ENV", "prepub"); }
    assert_eq!(Environment::resolve(), Environment::Pre);
    unsafe { std::env::remove_var("SERVER_ENV"); }
}

#[test]
#[serial]
fn test_environment_resolve_dev() {
    unsafe { std::env::set_var("SERVER_ENV", "dev"); }
    assert_eq!(Environment::resolve(), Environment::Dev);
    unsafe { std::env::remove_var("SERVER_ENV"); }

    unsafe { std::env::set_var("SERVER_ENV", "stable"); }
    assert_eq!(Environment::resolve(), Environment::Dev);
    unsafe { std::env::remove_var("SERVER_ENV"); }

    unsafe { std::env::set_var("SERVER_ENV", "sit"); }
    assert_eq!(Environment::resolve(), Environment::Dev);
    unsafe { std::env::remove_var("SERVER_ENV"); }
}

#[test]
#[serial]
fn test_environment_resolve_local_fallback() {
    unsafe {
        std::env::remove_var("SERVER_ENV");
        std::env::remove_var("REAL_SERVER_ENV");
        std::env::remove_var("ALIPAY_APP_ENV");
    }
    assert_eq!(Environment::resolve(), Environment::Local);
}

#[test]
#[serial]
fn test_environment_resolve_unknown_maps_to_local() {
    unsafe { std::env::set_var("SERVER_ENV", "staging"); }
    assert_eq!(Environment::resolve(), Environment::Local);
    unsafe { std::env::remove_var("SERVER_ENV"); }
}

#[test]
#[serial]
fn test_environment_resolve_fallback_chain() {
    unsafe {
        std::env::remove_var("SERVER_ENV");
        std::env::remove_var("REAL_SERVER_ENV");
        std::env::set_var("ALIPAY_APP_ENV", "prod");
    }
    assert_eq!(Environment::resolve(), Environment::Prod);
    unsafe { std::env::remove_var("ALIPAY_APP_ENV"); }
}

#[test]
fn test_environment_config_suffix() {
    assert_eq!(Environment::Local.config_suffix(), "-local");
    assert_eq!(Environment::Dev.config_suffix(), "-dev");
    assert_eq!(Environment::Pre.config_suffix(), "-pre");
    assert_eq!(Environment::Prod.config_suffix(), "-prod");
    assert_eq!(Environment::Gray.config_suffix(), "-gray");
}

// =========================================================================
// Deep merge tests
// =========================================================================

#[test]
fn test_deep_merge_objects() {
    let mut base = serde_json::json!({"a": 1, "b": {"nested": 2}});
    let override_val = serde_json::json!({"b": {"nested": 3, "new": 4}, "c": 5});
    deep_merge(&mut base, &override_val);
    assert_eq!(base, serde_json::json!({"a": 1, "b": {"nested": 3, "new": 4}, "c": 5}));
}

#[test]
fn test_deep_merge_arrays_override() {
    let mut base = serde_json::json!({"items": [1, 2, 3]});
    let override_val = serde_json::json!({"items": [4, 5]});
    deep_merge(&mut base, &override_val);
    assert_eq!(base, serde_json::json!({"items": [4, 5]}));
}

#[test]
fn test_deep_merge_primitives() {
    let mut base = serde_json::json!({"name": "old", "count": 10});
    let override_val = serde_json::json!({"name": "new", "count": 20});
    deep_merge(&mut base, &override_val);
    assert_eq!(base, serde_json::json!({"name": "new", "count": 20}));
}

#[test]
fn test_deep_merge_nested_objects() {
    let mut base = serde_json::json!({
        "server": {
            "host": "localhost",
            "port": 8080,
            "tls": {
                "enabled": false,
                "cert": "old.pem"
            }
        }
    });
    let override_val = serde_json::json!({
        "server": {
            "port": 9090,
            "tls": {
                "enabled": true
            }
        }
    });
    deep_merge(&mut base, &override_val);
    assert_eq!(base, serde_json::json!({
        "server": {
            "host": "localhost",
            "port": 9090,
            "tls": {
                "enabled": true,
                "cert": "old.pem"
            }
        }
    }));
}

#[test]
fn test_deep_merge_null_override() {
    let mut base = serde_json::json!({"a": 1, "b": 2});
    let override_val = serde_json::json!({"a": null});
    deep_merge(&mut base, &override_val);
    assert_eq!(base, serde_json::json!({"a": null, "b": 2}));
}

#[test]
fn test_redact_sensitive_values_masks_nested_passwords_without_mutating_source() {
    let source = serde_json::json!({
        "bind": "0.0.0.0",
        "cache": {
            "redis": {
                "connection": {
                    "auth_mode": "redis",
                    "password": "redis-pass"
                },
                "provider": {
                    "password": "secondary-pass"
                }
            }
        }
    });

    let redacted = redact_sensitive_values(&source);

    assert_eq!(redacted["bind"], "0.0.0.0");
    assert_eq!(
        redacted["cache"]["redis"]["connection"]["password"],
        "<redacted>"
    );
    assert_eq!(
        redacted["cache"]["redis"]["provider"]["password"],
        "<redacted>"
    );
    assert_eq!(source["cache"]["redis"]["connection"]["password"], "redis-pass");
}

#[test]
fn test_redact_telemetry_extra_headers_as_a_sensitive_container() {
    let source = serde_json::json!({
        "telemetry": {
            "otlp_traces_endpoint": "https://collector.example.com/v1/traces",
            "extra_headers": {
                "x-collector-route": "collector-local",
                "authorization": "Bearer secret"
            }
        }
    });

    let redacted = redact_sensitive_values(&source);

    assert_eq!(
        redacted["telemetry"]["otlp_traces_endpoint"],
        "https://collector.example.com/v1/traces"
    );
    assert_eq!(redacted["telemetry"]["extra_headers"], "<redacted>");
    assert!(source["telemetry"]["extra_headers"].is_object());
}

// =========================================================================
// ConfigLoader tests (require temp files)
// =========================================================================

#[test]
fn test_config_loader_base_only() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("bcs-config.toml"), r#"
        bind = "0.0.0.0"
        port = 21000
        bots_base_dir = "/bots"
    "#).unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf())
        .with_environment(Environment::Local);

    let config: serde_json::Value = loader.load().unwrap();
    assert_eq!(config["bind"], "0.0.0.0");
    assert_eq!(config["port"], 21000);
}

#[test]
fn test_config_loader_merge() {
    let dir = tempfile::tempdir().unwrap();

    // Base config
    std::fs::write(dir.path().join("bcs-config.toml"), r#"
        bind = "127.0.0.1"
        port = 21000
        bots_base_dir = "/bots"

        [cache]
        type = "redis"

        [cache.redis.connection]
        type = "direct"
        host = "localhost"
        port = 6379
    "#).unwrap();

    // Dev override
    std::fs::write(dir.path().join("bcs-config-dev.toml"), r#"
        port = 22000

        [cache.redis.connection]
        auth_mode = "disabled"
    "#).unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf())
        .with_environment(Environment::Dev);

    let config: serde_json::Value = loader.load().unwrap();

    // Merged values
    assert_eq!(config["bind"], "127.0.0.1");  // From base
    assert_eq!(config["port"], 22000);        // Overridden
    assert_eq!(config["cache"]["redis"]["connection"]["host"], "localhost");  // From base
    assert_eq!(config["cache"]["redis"]["connection"]["port"], 6379);  // From base
    assert_eq!(config["cache"]["redis"]["connection"]["auth_mode"], "disabled");  // Merged in
}

#[test]
fn test_config_loader_missing_env_config() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("bcs-config.toml"), r#"
        bind = "0.0.0.0"
        port = 21000
        bots_base_dir = "/bots"
    "#).unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf())
        .with_environment(Environment::Prod);

    // Should succeed with base config only
    let config: serde_json::Value = loader.load().unwrap();
    assert_eq!(config["bind"], "0.0.0.0");
}

#[test]
fn test_config_loader_missing_base_config() {
    let dir = tempfile::tempdir().unwrap();
    let loader = ConfigLoader::new(dir.path().to_path_buf());

    let result: Result<serde_json::Value, _> = loader.load();
    assert!(result.is_err());
}

// =========================================================================
// ${VAR} environment-reference expansion tests
// =========================================================================

#[test]
fn expand_env_string_resolves_a_present_variable() {
    unsafe { std::env::set_var("BCS_TEST_CFG_TOKEN", "super-secret"); }
    assert_eq!(
        expand_env_string("prefix-${BCS_TEST_CFG_TOKEN}-suffix").unwrap(),
        "prefix-super-secret-suffix"
    );
    unsafe { std::env::remove_var("BCS_TEST_CFG_TOKEN"); }
}

#[test]
fn expand_env_string_resolves_multiple_tokens_and_keeps_literal_text() {
    unsafe {
        std::env::set_var("BCS_TEST_CFG_HOST", "example.com");
        std::env::set_var("BCS_TEST_CFG_PORT", "8443");
    }
    assert_eq!(
        expand_env_string("https://${BCS_TEST_CFG_HOST}:${BCS_TEST_CFG_PORT}/path").unwrap(),
        "https://example.com:8443/path"
    );
    unsafe {
        std::env::remove_var("BCS_TEST_CFG_HOST");
        std::env::remove_var("BCS_TEST_CFG_PORT");
    }
}

#[test]
fn expand_env_string_uses_default_when_unset() {
    unsafe { std::env::remove_var("BCS_TEST_CFG_ABSENT"); }
    assert_eq!(
        expand_env_string("${BCS_TEST_CFG_ABSENT:-fallback}").unwrap(),
        "fallback"
    );
}

#[test]
fn expand_env_string_uses_env_over_default_when_set() {
    unsafe { std::env::set_var("BCS_TEST_CFG_PRESENT", "real"); }
    assert_eq!(
        expand_env_string("${BCS_TEST_CFG_PRESENT:-fallback}").unwrap(),
        "real"
    );
    unsafe { std::env::remove_var("BCS_TEST_CFG_PRESENT"); }
}

#[test]
fn expand_env_string_errors_on_unset_without_default() {
    unsafe { std::env::remove_var("BCS_TEST_CFG_REQUIRED"); }
    let err = expand_env_string("${BCS_TEST_CFG_REQUIRED}").unwrap_err();
    let message = err.to_string();
    assert!(message.contains("BCS_TEST_CFG_REQUIRED"), "error should name the missing var: {message}");
}

#[test]
fn expand_env_string_errors_on_invalid_name() {
    // Names must start with a letter or underscore.
    let err = expand_env_string("${1INVALID}").unwrap_err();
    assert!(err.to_string().contains("invalid environment variable name"));
}

#[test]
fn expand_env_string_errors_on_unterminated_reference() {
    let err = expand_env_string("prefix-${NO_CLOSE").unwrap_err();
    assert!(err.to_string().contains("unterminated"));
}

#[test]
fn expand_env_string_leaves_text_without_references_untouched() {
    assert_eq!(
        expand_env_string("plain value with no reference").unwrap(),
        "plain value with no reference"
    );
}

#[test]
fn expand_env_vars_walks_objects_and_arrays_and_skips_non_strings() {
    unsafe { std::env::set_var("BCS_TEST_CFG_VAL", "expanded"); }
    let mut value = serde_json::json!({
        "url": "https://${BCS_TEST_CFG_VAL}/api",
        "keys": ["${BCS_TEST_CFG_VAL}", "literal"],
        "port": 21000,
        "nested": { "deep": "${BCS_TEST_CFG_VAL}" },
    });
    expand_env_vars(&mut value).unwrap();
    assert_eq!(value["url"], "https://expanded/api");
    assert_eq!(value["keys"][0], "expanded");
    assert_eq!(value["keys"][1], "literal");
    assert_eq!(value["port"], 21000); // numeric values are untouched
    assert_eq!(value["nested"]["deep"], "expanded");
    unsafe { std::env::remove_var("BCS_TEST_CFG_VAL"); }
}

#[test]
#[serial]
fn loader_expands_references_in_loaded_string_fields() {
    unsafe { std::env::set_var("BCS_TEST_CFG_BOTCHAT", "https://botchat.example.com"); }
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("bcs-config.toml"),
        r#"
            bind = "0.0.0.0"
            port = 21000
            bots_base_dir = "/bots"
            botchat_url = "${BCS_TEST_CFG_BOTCHAT}"
        "#,
    )
    .unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf())
        .with_environment(Environment::Prod);
    let config: serde_json::Value = loader.load().unwrap();

    assert_eq!(config["botchat_url"], "https://botchat.example.com");
    unsafe { std::env::remove_var("BCS_TEST_CFG_BOTCHAT"); }
}

#[test]
#[serial]
fn loader_errors_when_a_required_reference_is_unset() {
    unsafe { std::env::remove_var("BCS_TEST_CFG_MISSING"); }
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("bcs-config.toml"),
        r#"
            bind = "0.0.0.0"
            port = 21000
            bots_base_dir = "/bots"
            botchat_url = "${BCS_TEST_CFG_MISSING}"
        "#,
    )
    .unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf())
        .with_environment(Environment::Prod);
    let result: Result<serde_json::Value, _> = loader.load();
    let err = result.unwrap_err().to_string();
    assert!(err.contains("BCS_TEST_CFG_MISSING"), "error should name the missing var: {err}");
}

#[test]
fn test_real_local_config_routes_chat_digest_to_dedicated_file() {
    let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../configs/bcs-config-local.toml");
    let content = std::fs::read_to_string(config_path).unwrap();
    let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

    assert_eq!(
        config.logging.tags.get("bcs_chat_digest").map(String::as_str),
        Some("off")
    );

    let digest = config
        .logging
        .outputs
        .iter()
        .find(|output| output.name == "chat-digest")
        .expect("local config should include chat-digest output");

    assert_eq!(digest.path, "./logs");
    assert_eq!(digest.file, "bcs-chat-digest.log");
    assert_eq!(digest.targets, vec!["bcs_chat_digest"]);
}

#[test]
fn test_real_local_config_selects_env_secret_backend() {
    let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../configs/bcs-config-local.toml");
    let content = std::fs::read_to_string(config_path).unwrap();
    let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

    assert_eq!(config.secret.provider, "env");
    assert_eq!(
        config.secret.providers["env"]["prefix"].as_str(),
        Some("BCS_SECRET_")
    );
}

#[test]
fn test_real_local_config_routes_all_errors_to_common_error_file() {
    let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../configs/bcs-config-local.toml");
    let content = std::fs::read_to_string(config_path).unwrap();
    let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

    let common_error = config
        .logging
        .outputs
        .iter()
        .find(|output| output.name == "common-error")
        .expect("local config should include common-error output");

    assert_eq!(common_error.path, "./logs");
    assert_eq!(common_error.file, "common-error.log");
    assert_eq!(common_error.level, "error");
    assert_eq!(common_error.targets, vec!["*"]);
}

#[test]
fn test_real_base_config_accepts_dingtalk_lab_json_override() {
    let source_config_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../configs");
    let dir = tempfile::tempdir().unwrap();
    std::fs::copy(
        source_config_dir.join("bcs-config-local.toml"),
        dir.path().join("bcs-config.toml"),
    )
    .unwrap();
    std::fs::write(
        dir.path().join("bcs-config-local.json"),
        r#"{
          "bind": "127.0.0.1",
          "port": 21992,
          "bots_base_dir": "/tmp/dingtalk-lab/data/bots",
          "strict_container_validation": false,
          "bcs_endpoint": "http://127.0.0.1:21992",
          "botchat_url": "http://127.0.0.1:21992",
          "provider_stream_gray_enabled": true,
          "provider_stream_gray_created_by": ["197262", "999001"],
          "leader_election": {"enabled": false},
          "cache": {"type": "memory"},
          "database": {
            "type": "sqlite",
            "sqlite": {"path": "/tmp/dingtalk-lab/bcs.db"}
          },
          "collaboration": {
            "templates": {
              "storage_type": "file",
              "base_dir": "seeds/collaboration-templates",
              "default_language": "zh-CN"
            }
          },
          "auth_sdk": {
            "client_id": null,
            "secret_key": null,
            "app_key": null,
            "app_name": null,
            "remote_server_domain": null,
            "use_remote_login_check": false
          },
          "auth": {"chain": ["local"], "require_authentication": false},
          "bcsfuse": {"enabled": false},
          "security_gateway": {"provider": "noop"},
          "channels": {"enabled": true, "providers": {"dingtalk": {"enabled": true}}},
          "dingtalk_accounts": [
            {
              "account_id": "dummy_robot_code",
              "client_id": "dummy_app_key",
              "client_secret": "dummy_app_secret",
              "robot_code": "dummy_robot_code",
              "card_template_id": "dummy_template_id",
              "card_template_key": "content",
              "enable_streaming_cards": true,
              "enable_scene_group": true,
              "dm_policy": "open",
              "allowlist": ["*"]
            }
          ]
        }"#,
    )
    .unwrap();

    let loader = ConfigLoader::new(dir.path().to_path_buf()).with_environment(Environment::Local);
    let config: crate::config::BcsConfig = loader.load().unwrap();

    assert_eq!(config.port, 21992);
    assert!(config.channels.dingtalk_enabled());
    assert!(config.channels.enabled_provider_configs().contains_key("dingtalk"));
    assert_eq!(config.dingtalk_accounts.len(), 1);
}

#[test]
fn test_redact_caller_plugin_credentials_without_mutating_source() {
    let source = serde_json::json!({
        "caller_identity": {
            "plugin": "external",
            "options": {"app_key": "test-key", "provider_credential": "test-secret"},
            "jwt": {"signing_key_pem": "test-signing-material"}
        },
        "other_plugin": {"app_key": "test-key", "timeout_ms": 500}
    });
    let redacted = redact_sensitive_values(&source);
    assert_eq!(redacted["caller_identity"], "<redacted>");
    assert_eq!(redacted["other_plugin"]["app_key"], "<redacted>");
    assert_eq!(redacted["other_plugin"]["timeout_ms"], 500);
    assert_eq!(source["caller_identity"]["options"]["app_key"], "test-key");
}

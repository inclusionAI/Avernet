use super::*;
use std::collections::BTreeMap;
use std::path::PathBuf;
use super::validation::{validate_eventing_environment_policy, validate_loaded_config};
use secrecy::ExposeSecret;

#[test]
fn history_configuration_defaults_and_cutoff_round_trip() {
    let mut config = BcsConfig::default();
    assert_eq!(config.message_history.state_machine_cutoff_timestamp, 0);
    config.state_machine_history.persistence_enabled = true;
    config.message_history.state_machine_cutoff_timestamp = 1_790_000_000_000;
    let loaded: BcsConfig = serde_json::from_value(serde_json::to_value(&config).unwrap()).unwrap();
    assert!(loaded.state_machine_history.persistence_enabled);
    assert_eq!(loaded.message_history.state_machine_cutoff_timestamp, 1_790_000_000_000);
    assert!(validate_loaded_config(&loaded).is_ok());
    config.state_machine_history.persistence_enabled = false;
    assert!(validate_loaded_config(&config).is_ok());
    let absent: MessageHistoryConfig = toml::from_str("").unwrap();
    assert_eq!(absent.state_machine_cutoff_timestamp, 0);
    assert!(toml::from_str::<MessageHistoryConfig>("state_machine_cutoff_timestamp = -1").is_err());
    assert!(toml::from_str::<MessageHistoryConfig>("state_machine_cutoff_timestamp = \"invalid\"").is_err());
}

#[test]
fn only_group_message_delivery_is_ready_and_all_flows_default_off() {
    let mut config = BcsConfig::default();
    assert!(validate_loaded_config(&config).is_ok());
    config.message_delivery.flow_enabled.group = true;
    assert!(validate_loaded_config(&config).is_ok());
    config.message_delivery.flow_enabled.direct_a2a = true;
    let result = validate_loaded_config(&config);
    assert!(matches!(result, Err(error) if error.to_string().contains("queue_flow_not_ready")));
}

#[test]
fn message_delivery_invalid_enum_is_rejected() -> Result<(), serde_json::Error> {
    let mut config = serde_json::to_value(BcsConfig::default())?;
    config["message_delivery"] = serde_json::json!({"bots": {"bot": {
        "mode": "enabled", "max_running": 1, "max_queued": 10,
        "min_send_interval_ms": 100
    }}});
    assert!(serde_json::from_value::<BcsConfig>(config).is_err());
    Ok(())
}

#[test]
fn validate_run_store_selectors_accepts_known_and_rejects_unknown() {
    let mut config = BcsConfig::default();
    // Defaults (memory / memory) are valid.
    assert!(config.validate_run_store_selectors().is_ok());
    // Explicit known values are valid.
    config.async_chat_run_store = "persistent".to_string();
    config.bot_run_context_store = "redis".to_string();
    assert!(config.validate_run_store_selectors().is_ok());
    // Unknown chat-run selector (e.g. a typo) must fail startup.
    config.async_chat_run_store = "persisent".to_string();
    assert!(config.validate_run_store_selectors().is_err());
    config.async_chat_run_store = "persistent".to_string();
    // Unknown run-context selector must fail startup.
    config.bot_run_context_store = "reds".to_string();
    assert!(config.validate_run_store_selectors().is_err());
}

#[allow(unsafe_code)]
fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
    unsafe {
        std::env::set_var(key, value);
    }
}

#[allow(unsafe_code)]
fn safe_remove_var(key: &str) {
    unsafe {
        std::env::remove_var(key);
    }
}

#[test]
fn from_file_expands_env_references_in_string_fields() {
    safe_set_var("BCS_TEST_FROM_FILE_TOKEN", "expanded-via-from-file");
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bcs-config.toml");
    std::fs::write(
        &path,
        r#"
bots_base_dir = "/bots"
botchat_url = "${BCS_TEST_FROM_FILE_TOKEN}"
"#,
    )
    .unwrap();

    let config = BcsConfig::from_file(&path).unwrap();
    assert_eq!(config.botchat_url.as_deref(), Some("expanded-via-from-file"));
    safe_remove_var("BCS_TEST_FROM_FILE_TOKEN");
}

#[test]
fn from_file_errors_when_a_required_env_reference_is_unset() {
    safe_remove_var("BCS_TEST_FROM_FILE_MISSING");
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bcs-config.toml");
    std::fs::write(
        &path,
        r#"
bots_base_dir = "/bots"
botchat_url = "${BCS_TEST_FROM_FILE_MISSING}"
"#,
    )
    .unwrap();

    let result = BcsConfig::from_file(&path);
    let err = result.unwrap_err().to_string();
    assert!(
        err.contains("BCS_TEST_FROM_FILE_MISSING"),
        "error should name the missing var: {err}"
    );
}

#[test]
fn from_file_unknown_ext_json_with_unset_env_reports_env_error_not_toml() {
    // A valid JSON document with an unset `${VAR}` must surface the env
    // error directly, not be retried as TOML and mis-reported. Guards the
    // `_` (unknown extension) branch swallowing expansion errors.
    safe_remove_var("BCS_TEST_UNKNOWN_EXT_VAR");
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bcs-config"); // no extension -> unknown-ext branch
    std::fs::write(
        &path,
        r#"{"bots_base_dir":"/bots","botchat_url":"${BCS_TEST_UNKNOWN_EXT_VAR}"}"#,
    )
    .unwrap();

    let err = BcsConfig::from_file(&path).unwrap_err().to_string();
    assert!(
        err.contains("BCS_TEST_UNKNOWN_EXT_VAR"),
        "should surface the unset env-var error, got: {err}"
    );
    assert!(
        !err.to_lowercase().contains("toml"),
        "should not misreport as a TOML parse error, got: {err}"
    );
}

#[test]
fn test_default_config() {
    let config = BcsConfig::default();

    assert_eq!(config.bind, "127.0.0.1");
    assert_eq!(config.port, 21000);
    assert_eq!(config.bots_base_dir, PathBuf::from("/bots"));
    assert!(config.fusion_provider.is_none());
    assert_eq!(config.max_history_per_session, 1000);
    assert_eq!(config.provider_chat_run_timeout_ms, 10_800_000);
    assert_eq!(config.async_chat_run_timeout_ms, 7_500_000);
    assert!(config.security.outbound_url.block_private_networks);
    assert!(!config.security.outbound_url.allow_loopback);
    assert_eq!(
        config.group_session_ws.signing_key_secret,
        "bcn-group-session-ws-jwt"
    );
}

#[test]
fn provider_chat_run_timeout_can_be_configured() {
    let toml = r#"
        bots_base_dir = "/bots"
        provider_chat_run_timeout_ms = 14_400_000
    "#;

    let config: BcsConfig = toml::from_str(toml).expect("parse provider chat run timeout");

    assert_eq!(config.provider_chat_run_timeout_ms, 14_400_000);
}

#[test]
fn provider_chat_run_timeout_rejects_zero() {
    let toml = r#"
        bots_base_dir = "/bots"
        provider_chat_run_timeout_ms = 0
    "#;

    let config: BcsConfig = toml::from_str(toml).expect("parse provider chat run timeout");
    let error = validate_loaded_config(&config).expect_err("zero timeout must be rejected");

    assert!(
        error
            .to_string()
            .contains("provider_chat_run_timeout_ms must be greater than zero")
    );
}

#[test]
fn group_session_ws_signing_key_secret_can_be_configured() {
    let toml = r#"
        bots_base_dir = "/bots"

        [group_session_ws]
        signing_key_secret = "other_manual_teamclawgw_principal_signing_key"
    "#;

    let config: BcsConfig =
        toml::from_str(toml).expect("parse configurable group-session WebSocket secret name");

    assert_eq!(
        config.group_session_ws.signing_key_secret,
        "other_manual_teamclawgw_principal_signing_key"
    );
}

#[test]
fn blank_group_session_ws_signing_key_secret_is_rejected() {
    let tmp = tempfile::TempDir::new().expect("temp config dir");
    std::fs::write(
        tmp.path().join("bcs-config.toml"),
        r#"
        bots_base_dir = "/bots"

        [group_session_ws]
        signing_key_secret = " "
        "#,
    )
    .expect("write config");

    let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
        .expect_err("blank group-session WebSocket secret name rejected");

    assert!(err.contains("group_session_ws.signing_key_secret must not be blank"));
}

#[test]
fn provider_http_bypass_headers_parse_and_default() {
    let default_config = BcsConfig::default();
    assert!(default_config.provider_http.bypass_headers.is_empty());
    assert!(default_config.provider_http.queue_persistable_headers.is_empty());

    let toml = r#"
        bots_base_dir = "/bots"

        [provider_http]
        bypass_headers = ["X-Sandbox-Bypass"]
        queue_persistable_headers = ["x-sandbox-bypass"]
    "#;
    let config: BcsConfig = toml::from_str(toml).expect("parse [provider_http]");
    assert_eq!(
        config.provider_http.bypass_headers,
        vec!["X-Sandbox-Bypass".to_string()]
    );
    config
        .provider_http
        .validate()
        .expect("valid bypass header");
}

#[test]
fn provider_http_bypass_headers_reject_invalid_and_reserved_names() {
    for name in [
        "",
        "bad header",
        "Authorization",
        "Cookie",
        "Host",
        "Content-Length",
        "Content-Type",
        "X-BCS-Bot-Token",
        "X-BCS-Service-Key",
        "bcn-message-id",
        "x-bcn-protocol-version",
    ] {
        let config = ProviderHttpConfig {
            downlink_detection_source: Default::default(),
            queue_persistable_headers: Vec::new(),
            bypass_headers: vec![name.to_string()],
        };
        assert!(
            config.validate().is_err(),
            "header name {name:?} should be rejected"
        );
    }
}

#[test]
fn downlink_detection_source_defaults_to_legacy_and_rejects_unknown_values() {
    use bcs_domain::bot_provider::DownlinkDetectionSource;
    let legacy: ProviderHttpConfig = toml::from_str("").unwrap();
    assert_eq!(legacy.downlink_detection_source, DownlinkDetectionSource::Binding);
    let bots: ProviderHttpConfig = toml::from_str("downlink_detection_source = 'bot_connection_mode'").unwrap();
    assert_eq!(bots.downlink_detection_source, DownlinkDetectionSource::BotConnectionMode);
    assert!(toml::from_str::<ProviderHttpConfig>("downlink_detection_source = 'auto'").is_err());
}

#[test]
fn test_provider_stream_gray_config_defaults_to_full_rollout() {
    let config = BcsConfig::default();

    assert!(!config.provider_stream_gray_enabled);
}

#[test]
fn test_provider_stream_gray_config_can_enable_gray_mode() {
    let toml = r#"
        bots_base_dir = "/bots"
        provider_stream_gray_enabled = true
        provider_stream_gray_created_by = ["197262"]
    "#;

    let config: BcsConfig = toml::from_str(toml).expect("parse provider stream gray config");

    assert!(config.provider_stream_gray_enabled);
    assert_eq!(
        config.provider_stream_gray_created_by,
        vec!["197262".to_string()]
    );
}

#[test]
fn test_config_security_outbound_url_section_parses() {
    let toml = r#"
        bots_base_dir = "/bots"
        [security.outbound_url]
        block_private_networks = false
        allow_loopback = true
    "#;
    let config: BcsConfig = toml::from_str(toml).expect("parse [security.outbound_url]");
    assert!(!config.security.outbound_url.block_private_networks);
    assert!(config.security.outbound_url.allow_loopback);
}

#[test]
fn test_message_history_manager_worker_cutoff_defaults_disabled() {
    let config = MessageHistoryConfig::default();

    assert_eq!(config.cutoff_timestamp, 0);
    assert_eq!(config.manager_worker_cutoff_timestamp, u64::MAX);
}

#[test]
fn test_config_auth_chain_section_parses() {
    let toml = r#"
        bots_base_dir = "/bots"
        [auth]
        chain = ["agentpass", "session"]
        require_authentication = true
        mock_user_id = "12345"
        allow_mock_headers = true
    "#;
    let config: BcsConfig = toml::from_str(toml).expect("parse [auth]");
    assert_eq!(config.auth.chain, vec!["agentpass", "session"]);
    assert!(config.auth.require_authentication);
    assert!(config.auth.allow_mock_headers);

    // resolve honors a non-empty chain verbatim.
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    assert_eq!(resolved.chain, vec!["agentpass", "session"]);
    assert!(resolved.require_authentication);
    assert_eq!(resolved.local.mock_user_id.as_deref(), Some("12345"));
    assert!(resolved.local.allow_mock_headers);
}

#[test]
fn test_config_cors_section_parses() {
    let toml = r#"
        bots_base_dir = "/bots"
        [cors]
        allowed_origins = ["https://botchat.example.com", "http://localhost:8000"]
    "#;
    let config: BcsConfig = toml::from_str(toml).expect("parse [cors]");
    assert_eq!(
        config.cors.allowed_origins,
        vec![
            "https://botchat.example.com".to_string(),
            "http://localhost:8000".to_string(),
        ]
    );
}

#[test]
fn test_telemetry_config_defaults_without_section() {
    let config: BcsConfig = toml::from_str(r#"bots_base_dir = "/bots""#).unwrap();

    assert!(config.telemetry.enabled);
    assert_eq!(config.telemetry.service_name, "bcn");
    assert_eq!(config.telemetry.otlp_traces_endpoint, None);
    assert!(config.telemetry.extra_headers.is_empty());
}

#[test]
fn test_telemetry_config_parses_endpoint_and_extra_headers() {
    let config: BcsConfig = toml::from_str(
        r#"
bots_base_dir = "/bots"

[telemetry]
enabled = true
service_name = "bcn-prod"
otlp_traces_endpoint = "https://collector.example.com/v1/traces"

[telemetry.extra_headers]
x-collector-route = "collector-local"
"#,
    )
    .unwrap();

    assert_eq!(config.telemetry.service_name, "bcn-prod");
    assert_eq!(
        config.telemetry.otlp_traces_endpoint.as_deref(),
        Some("https://collector.example.com/v1/traces")
    );
    assert_eq!(
        config
            .telemetry
            .extra_headers
            .get("x-collector-route")
            .map(String::as_str),
        Some("collector-local")
    );
}

#[test]
fn test_telemetry_config_validation_rejects_invalid_endpoint_and_headers() {
    let invalid_endpoint = TelemetryConfig {
        otlp_traces_endpoint: Some("not-an-http-endpoint".to_string()),
        ..TelemetryConfig::default()
    };
    assert!(invalid_endpoint.validate().is_err());

    let invalid_header = TelemetryConfig {
        extra_headers: BTreeMap::from([("invalid header".to_string(), "value".to_string())]),
        ..TelemetryConfig::default()
    };
    assert!(invalid_header.validate().is_err());
}

#[test]
#[serial_test::serial]
fn test_config_auth_mock_user_id_env_override() {
    // Save and clear env vars so the test is hermetic.
    let saved_user_id = std::env::var("BCS_MOCK_USER_ID").ok();
    let saved_nick_name = std::env::var("BCS_MOCK_USER_NICK_NAME").ok();
    let saved_auth_mock = std::env::var("BCS_AUTH_MOCK").ok();
    safe_remove_var("BCS_MOCK_USER_ID");
    safe_remove_var("BCS_MOCK_USER_NICK_NAME");
    safe_remove_var("BCS_AUTH_MOCK");

    let config = BcsConfig::default();
    // Without env var, mock_user_id is None.
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    assert!(resolved.local.mock_user_id.is_none());
    assert!(!resolved.local.allow_mock_headers);

    // With env var, it fills in when config is absent.
    safe_set_var("BCS_MOCK_USER_ID", "99999");
    safe_set_var("BCS_MOCK_USER_NICK_NAME", "EnvUser");
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    assert_eq!(resolved.local.mock_user_id.as_deref(), Some("99999"));
    assert_eq!(resolved.local.mock_user_name.as_deref(), Some("EnvUser"));
    safe_remove_var("BCS_MOCK_USER_ID");
    safe_remove_var("BCS_MOCK_USER_NICK_NAME");

    safe_set_var("BCS_AUTH_MOCK", "1");
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    assert!(resolved.local.allow_mock_headers);
    safe_remove_var("BCS_AUTH_MOCK");

    // Config value takes priority over env var.
    let toml = r#"
        bots_base_dir = "/bots"
        [auth]
        mock_user_id = "from_config"
    "#;
    let config: BcsConfig = toml::from_str(toml).unwrap();
    safe_set_var("BCS_MOCK_USER_ID", "from_env");
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    assert_eq!(resolved.local.mock_user_id.as_deref(), Some("from_config"));
    safe_remove_var("BCS_MOCK_USER_ID");

    // Restore original env vars.
    if let Some(v) = saved_user_id {
        safe_set_var("BCS_MOCK_USER_ID", v);
    }
    if let Some(v) = saved_nick_name {
        safe_set_var("BCS_MOCK_USER_NICK_NAME", v);
    }
    if let Some(v) = saved_auth_mock {
        safe_set_var("BCS_AUTH_MOCK", v);
    }
}

#[test]
fn test_config_auth_chain_absent_falls_back_to_default() {
    let toml = r#"bots_base_dir = "/bots""#;
    let config: BcsConfig = toml::from_str(toml).expect("parse without [auth]");
    assert!(config.auth.chain.is_empty());

    // Empty chain → build-profile default applied in the composition root.
    let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
    let expected = if cfg!(debug_assertions) {
        vec!["local".to_string()]
    } else {
        vec![
            "agentpass".to_string(),
            "cookie".to_string(),
            "session".to_string(),
        ]
    };
    assert_eq!(resolved.chain, expected);
    // The contract crate's default is now neutral (empty), not build-profile.
    assert!(bcs_auth_api::AuthConfig::default().chain.is_empty());
}

#[test]
fn test_build_oauth_provider_known_kinds() {
    use bcs_config_api::ProviderSettings;

    let google = ProviderSettings {
        kind: None, // defaults to the instance name "google"
        client_id: "gid".to_string(),
        client_secret: None,
        client_secret_secret: None,
        private_key: None,
        alipay_public_key: None,
    };
    // Arc<dyn OAuthProvider> is not Debug, so match instead of .expect().
    match crate::auth_wiring::build_oauth_provider("google", &google) {
        Ok(p) => assert_eq!(p.name(), "google"),
        Err(e) => panic!("google provider should build: {e}"),
    }

    // Explicit kind decoupled from the instance name (multi-instance case).
    let gh = ProviderSettings {
        kind: Some("github".to_string()),
        client_id: "ghid".to_string(),
        client_secret: None,
        client_secret_secret: None,
        private_key: None,
        alipay_public_key: None,
    };
    match crate::auth_wiring::build_oauth_provider("github-partner", &gh) {
        Ok(p) => assert_eq!(p.name(), "github"),
        Err(e) => panic!("github provider should build: {e}"),
    }
}

#[test]
fn test_build_oauth_provider_unknown_kind_errors() {
    use bcs_config_api::ProviderSettings;

    let cfg = ProviderSettings {
        kind: Some("facebook".to_string()),
        client_id: "id".to_string(),
        client_secret: None,
        client_secret_secret: None,
        private_key: None,
        alipay_public_key: None,
    };
    let err = match crate::auth_wiring::build_oauth_provider("fb", &cfg) {
        Ok(_) => panic!("unknown kind must error, not silently drop"),
        Err(e) => e,
    };
    assert!(err.contains("unknown provider kind"), "got: {err}");
    assert!(err.contains("facebook"), "got: {err}");
}

#[test]
fn test_build_oauth_provider_empty_client_id_errors() {
    use bcs_config_api::ProviderSettings;

    let cfg = ProviderSettings {
        kind: None,
        client_id: "  ".to_string(),
        client_secret: None,
        client_secret_secret: None,
        private_key: None,
        alipay_public_key: None,
    };
    let err = match crate::auth_wiring::build_oauth_provider("google", &cfg) {
        Ok(_) => panic!("empty client_id must error"),
        Err(e) => e,
    };
    assert!(err.contains("client_id"), "got: {err}");
}

#[test]
fn fixed_loop_compiler_limits_parse_and_validate_without_enabling_execution() {
    let config: BcsConfig = toml::from_str(r#"
        bots_base_dir = "/bots"
        [collaboration.fixed_loop_limits]
        max_fixed_loop_iterations = 4
        max_fixed_loop_body_nodes = 8
        max_compiled_state_machine_nodes = 64
        max_compiled_state_machine_bytes = 65536
    "#).unwrap();
    assert_eq!(config.collaboration.fixed_loop_limits.max_fixed_loop_iterations, 4);
    assert!(!config.collaboration.loop_execution_enabled);
    assert!(!config.collaboration.nested_loop_execution_enabled);
    assert!(config.collaboration.fixed_loop_limits.validate().is_ok());
    let mut invalid = config.collaboration.fixed_loop_limits;
    invalid.max_compiled_state_machine_bytes = 0;
    assert!(invalid.validate().is_err());
    assert!(toml::from_str::<BcsConfig>(r#"
        bots_base_dir = "/bots"
        [collaboration.fixed_loop_limits]
        enabled = true
    "#).is_err());
}

#[test]
fn loop_execution_requires_explicit_boolean_opt_in() {
    let config: BcsConfig = toml::from_str("bots_base_dir = '/bots'\n[collaboration]\nloop_execution_enabled = true").unwrap();
    assert!(config.collaboration.loop_execution_enabled);
    assert!(toml::from_str::<BcsConfig>("bots_base_dir = '/bots'\n[collaboration]\nloop_execution_enabled = 'true'").is_err());
    assert!(toml::from_str::<BcsConfig>("bots_base_dir = '/bots'\n[collaboration]\nexperimental_fixed_loop_execution = true").is_err());
}

#[test]
fn nested_loop_execution_requires_total_loop_execution() {
    let invalid: BcsConfig = toml::from_str(
        "bots_base_dir = '/bots'\n[collaboration]\nnested_loop_execution_enabled = true",
    ).unwrap();
    assert!(validate_loaded_config(&invalid).unwrap_err().to_string().contains(
        "nested_loop_execution_enabled requires collaboration.loop_execution_enabled=true"
    ));
    let valid: BcsConfig = toml::from_str(
        "bots_base_dir = '/bots'\n[collaboration]\nloop_execution_enabled = true\nnested_loop_execution_enabled = true",
    ).unwrap();
    assert!(valid.collaboration.nested_loop_execution_enabled);
}

#[test]
fn removed_progression_recovery_switch_is_rejected() {
    for enabled in [false, true] {
        let config = format!("bots_base_dir = '/bots'\n[collaboration]\nexperimental_progression_recovery = {enabled}");
        let error = toml::from_str::<BcsConfig>(&config).unwrap_err();
        assert!(error.to_string().contains("unknown field `experimental_progression_recovery`"));
    }
}

#[test]
fn test_collaboration_template_storage_config_parses() {
    let toml = r#"
        bots_base_dir = "/bots"

        [collaboration.templates]
        storage_type = "mysql"
        base_dir = "seeds/collaboration-templates"
        default_language = "zh-CN"
    "#;

    let config: BcsConfig = toml::from_str(toml).expect("parse collaboration templates");

    assert_eq!(
        config.collaboration.templates.storage_type,
        CollaborationTemplateStorageKind::Mysql
    );
    assert_eq!(
        config.collaboration.templates.base_dir,
        PathBuf::from("seeds/collaboration-templates")
    );
    assert_eq!(config.collaboration.templates.default_language, "zh-CN");
}

#[test]
fn test_config_serde() {
    let json = r#"{
        "bind": "0.0.0.0",
        "port": 22000,
        "bots_base_dir": "/custom/bots",
        "max_history_per_session": 500
    }"#;

    let config: BcsConfig = serde_json::from_str(json).unwrap();

    assert_eq!(config.bind, "0.0.0.0");
    assert_eq!(config.port, 22000);
    assert_eq!(config.bots_base_dir, PathBuf::from("/custom/bots"));
    assert_eq!(config.max_history_per_session, 500);
}

#[test]
fn test_config_rejects_unknown_key() {
    let json = r#"{
        "bind": "0.0.0.0",
        "port": 22000,
        "bots_base_dir": "/custom/bots",
        "unknown_key": true
    }"#;

    let err = serde_json::from_str::<BcsConfig>(json).expect_err("unknown key rejected");
    assert!(err.to_string().contains("unknown field"));
}

#[test]
fn test_config_rejects_mist_section() {
    let toml = r#"
        bots_base_dir = "/bots"
        [mist]
        enabled = true
    "#;

    let err =
        toml::from_str::<BcsConfig>(toml).expect_err("public BCS rejects Ant-only mist config");
    assert!(err.to_string().contains("unknown field"));
}

#[test]
fn test_config_accepts_external_database_type() {
    let json = r#"{
        "bind": "0.0.0.0",
        "port": 22000,
        "bots_base_dir": "/custom/bots",
        "database": {
            "type": "postgres"
        }
    }"#;

    let config: BcsConfig = serde_json::from_str(json).expect("external db type parses");
    assert_eq!(
        config.database.database_type,
        DatabaseType::Other("postgres".to_string())
    );
}

#[test]
fn test_config_with_fusion_provider() {
    let json = r#"{
        "bind": "127.0.0.1",
        "port": 21000,
        "bots_base_dir": "/bots",
        "fusion_provider": {
            "provider": "anthropic",
            "model": "claude-3-opus",
            "api_key": "test-key",
            "base_url": "https://api.anthropic.com"
        }
    }"#;

    let config: BcsConfig = serde_json::from_str(json).unwrap();

    let fusion = config.fusion_provider.unwrap();
    assert_eq!(fusion.provider, "anthropic");
    assert_eq!(fusion.model, "claude-3-opus");
    assert_eq!(fusion.api_key, Some("test-key".to_string()));
    assert_eq!(
        fusion.base_url,
        Some("https://api.anthropic.com".to_string())
    );
}

#[test]
fn test_config_with_manifest_bundle_array_of_tables() {
    let toml = r#"
bots_base_dir = "/bots"

[[manifest.bundles]]
name = "bcsPanel"
url = "https://cdn.example.com/bcs-panel/1.0.0/index.js"
"#;

    let config: BcsConfig = toml::from_str(toml).unwrap();

    assert_eq!(config.manifest.schema_version, 1);
    assert_eq!(config.manifest.bundles.len(), 1);
    assert_eq!(config.manifest.bundles[0].name, "bcsPanel");
    assert_eq!(
        config.manifest.bundles[0].url.as_deref(),
        Some("https://cdn.example.com/bcs-panel/1.0.0/index.js")
    );
}

#[test]
fn test_config_with_manifest_bundle_file() {
    let toml = r#"
bots_base_dir = "/bots"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#;

    let config: BcsConfig = toml::from_str(toml).unwrap();

    assert_eq!(config.manifest.schema_version, 1);
    assert_eq!(config.manifest.bundles.len(), 1);
    assert_eq!(config.manifest.bundles[0].name, "bcsPanel");
    assert_eq!(config.manifest.bundles[0].url, None);
    assert_eq!(
        config.manifest.bundles[0].file.as_deref(),
        Some("assets/panel/dist/index.umd.js")
    );
}

#[test]
fn test_shipped_manifests_use_the_shared_panel_bundle() {
    for (source, uses_cdn) in [
        (include_str!("../../../../../configs/bcs-config-example.toml"), true),
        (include_str!("../../../../../configs/bcs-config-local.toml"), false),
        (
            r#"
bots_base_dir = "/bots"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
url = "https://cdn.example.com/bcs-panel/1.0.0/index.js"
"#,
            true,
        ),
    ] {
        let mut value = crate::config_loader::parse_config_content("toml", source, "checked-in config").unwrap();
        crate::config_loader::expand_env_vars(&mut value).unwrap();
        let config: BcsConfig = serde_json::from_value(value).unwrap();
        assert_eq!(config.manifest.bundles.len(), 1);
        let bundle = &config.manifest.bundles[0];
        assert_eq!(bundle.name, "bcsPanel");
        if uses_cdn {
            assert!(bundle.url.is_some());
            assert_eq!(bundle.file, None);
        } else {
            assert_eq!(bundle.url, None);
            assert_eq!(bundle.file.as_deref(), Some("assets/panel/dist/index.umd.js"));
        }
    }
}

#[test]
fn test_config_loader_resolves_local_paths_relative_to_config_root() {
    let dir = tempfile::tempdir().unwrap();
    let config_dir = dir.path().join("configs");
    std::fs::create_dir_all(&config_dir).unwrap();
    std::fs::write(
        config_dir.join("bcs-config.toml"),
        r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#,
    )
    .unwrap();

    let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
    let expected_root = std::fs::canonicalize(dir.path()).unwrap();
    let expected_manifest_file = expected_root
        .join("assets/panel/dist/index.umd.js")
        .display()
        .to_string();
    let expected_template_dir = expected_root.join("seeds/collaboration-templates");

    assert_eq!(
        config.manifest.bundles[0].file.as_deref(),
        Some(expected_manifest_file.as_str())
    );
    assert_eq!(
        config.collaboration.templates.base_dir,
        expected_template_dir
    );
}

#[test]
fn test_config_loader_resolves_local_paths_relative_to_custom_config_dir() {
    let dir = tempfile::tempdir().unwrap();
    let config_dir = dir.path().join("bcs-config");
    std::fs::create_dir_all(&config_dir).unwrap();
    std::fs::write(
        config_dir.join("bcs-config.toml"),
        r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#,
    )
    .unwrap();

    let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
    let expected_root = std::fs::canonicalize(&config_dir).unwrap();
    let expected_manifest_file = expected_root
        .join("assets/panel/dist/index.umd.js")
        .display()
        .to_string();
    let expected_template_dir = expected_root.join("seeds/collaboration-templates");

    assert_eq!(
        config.manifest.bundles[0].file.as_deref(),
        Some(expected_manifest_file.as_str())
    );
    assert_eq!(
        config.collaboration.templates.base_dir,
        expected_template_dir
    );
}

#[test]
fn test_config_defaults_on_partial_json() {
    // Only provide some fields, others should use defaults
    let json = r#"{
        "bots_base_dir": "/my/bots"
    }"#;

    let config: BcsConfig = serde_json::from_str(json).unwrap();

    assert_eq!(config.bind, "127.0.0.1"); // default
    assert_eq!(config.port, 21000); // default
    assert_eq!(config.bots_base_dir, PathBuf::from("/my/bots"));
    assert_eq!(config.max_history_per_session, 1000); // default
}

#[test]
fn test_fusion_provider_config() {
    let fusion = FusionProviderConfig {
        provider: "openai".to_string(),
        model: "gpt-4".to_string(),
        api_key: None,
        base_url: None,
    };

    let json = serde_json::to_string(&fusion).unwrap();
    let parsed: FusionProviderConfig = serde_json::from_str(&json).unwrap();

    assert_eq!(parsed.provider, "openai");
    assert_eq!(parsed.model, "gpt-4");
    assert!(parsed.api_key.is_none());
    assert!(parsed.base_url.is_none());
}

#[path = "tests_infrastructure.rs"]
mod infrastructure;

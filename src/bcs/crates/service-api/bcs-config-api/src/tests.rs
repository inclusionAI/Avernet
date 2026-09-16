use super::*;

#[test]
fn secret_reference_fields_deserialize_and_default() {
    let cfg: AuthSdkConfig = toml::from_str("client_id = \"id\"\nsecret_key_secret = \"auth-key\"").unwrap();
    assert_eq!(cfg.secret_key_secret.as_deref(), Some("auth-key"));
    let llm: LlmConfig = toml::from_str("api_key_secret = \"llm-key\"").unwrap();
    assert_eq!(llm.api_key_secret.as_deref(), Some("llm-key"));
    let account: DingTalkAccountConfig = toml::from_str("account_id = \"a\"\nclient_secret_secret = \"ding-key\"").unwrap();
    assert_eq!(account.client_secret_secret.as_deref(), Some("ding-key"));
    let provider: ProviderSettings = toml::from_str("client_id = \"id\"\nclient_secret_secret = \"oauth-key\"").unwrap();
    assert_eq!(provider.client_secret_secret.as_deref(), Some("oauth-key"));
}

#[test]
fn channel_config_accepts_nested_dingtalk_switch() {
    let cfg: ChannelConfigSection = toml::from_str(
        r#"
        enabled = true

        [dingtalk]
        enabled = true
    "#,
    )
    .expect("parse channel config");

    assert!(cfg.enabled);
    assert!(cfg.dingtalk.enabled);
    assert!(cfg.dingtalk_enabled());
}

#[test]
fn channel_config_accepts_flat_dingtalk_switch() {
    let cfg: ChannelConfigSection = toml::from_str(
        r#"
        enabled = true
        dingtalk_enabled = true
    "#,
    )
    .expect("parse channel config");

    assert!(cfg.enabled);
    assert!(cfg.dingtalk_enabled);
    assert!(cfg.dingtalk_enabled());
}

#[test]
fn channel_config_accepts_provider_map() {
    let cfg: ChannelConfigSection = toml::from_str(
        r#"
        enabled = true

        [providers.test_im]
        enabled = true
        callback_path = "/channels/test/callback"

        [providers.disabled_im]
        enabled = false
    "#,
    )
    .expect("parse channel provider config");

    assert!(cfg.provider_enabled("test_im"));
    assert!(!cfg.provider_enabled("disabled_im"));
    assert_eq!(
        cfg.providers["test_im"].options["callback_path"],
        "/channels/test/callback"
    );
    assert_eq!(
        cfg.enabled_provider_configs()
            .keys()
            .cloned()
            .collect::<Vec<_>>(),
        vec!["test_im".to_string()]
    );
}

#[test]
fn channel_config_maps_legacy_dingtalk_switch_to_provider() {
    let cfg: ChannelConfigSection = toml::from_str(
        r#"
        enabled = true
        dingtalk_enabled = true
    "#,
    )
    .expect("parse channel config");

    let providers = cfg.enabled_provider_configs();
    assert!(providers.contains_key("dingtalk"));
    assert!(providers["dingtalk"].enabled);
}

#[test]
fn default_logging_outputs_include_chat_digest_file() {
    let logging = LoggingConfig::default();

    let digest = logging
        .outputs
        .iter()
        .find(|output| output.name == "chat-digest")
        .expect("chat digest log output should be configured by default");

    assert_eq!(digest.file, "bcs-chat-digest.log");
    assert_eq!(digest.format, LogOutputFormat::Text);
    assert_eq!(digest.targets, vec!["bcs_chat_digest"]);
    assert_eq!(digest.max_keep_days, 7);
}

#[test]
fn delivery_monitor_defaults_to_raw_and_existing_formats_remain_compatible() {
    let logging = LoggingConfig::default();
    let output = logging.outputs.iter().find(|o| o.name == "message-delivery").unwrap();
    assert_eq!(output.file, "message-delivery.log"); assert_eq!(output.format, LogOutputFormat::Raw);
    assert_eq!(output.targets, vec!["bcs_message_delivery_monitor"]);
    for (value, format) in [("raw", LogOutputFormat::Raw), ("text", LogOutputFormat::Text), ("json", LogOutputFormat::Json)] {
        assert_eq!(serde_json::from_value::<LogOutputFormat>(serde_json::json!(value)).unwrap(), format);
    }
    assert_eq!(LogOutputFormat::default(), LogOutputFormat::Text);
}

#[test]
fn default_logging_outputs_include_json_message_file() {
    let logging = LoggingConfig::default();

    let messages = logging
        .outputs
        .iter()
        .find(|output| output.name == "messages")
        .expect("message log output should be configured by default");

    assert_eq!(messages.file, "bcs-messages.log");
    assert_eq!(messages.format, LogOutputFormat::Json);
    assert_eq!(messages.targets, vec!["bcs_message"]);
    assert_eq!(messages.max_keep_days, 7);
}

#[test]
fn default_logging_outputs_include_common_error_file() {
    let logging = LoggingConfig::default();

    let common_error = logging
        .outputs
        .iter()
        .find(|output| output.name == "common-error")
        .expect("common error log output should be configured by default");

    assert_eq!(common_error.file, "common-error.log");
    assert_eq!(common_error.level, "error");
    assert_eq!(common_error.format, LogOutputFormat::Text);
    assert_eq!(common_error.targets, vec!["*"]);
    assert_eq!(common_error.max_keep_days, 7);
}

#[test]
fn database_config_default_selects_sqlite_without_mysql() {
    let database = DatabaseConfig::default();

    assert_eq!(database.database_type, DatabaseType::Sqlite);
    assert_eq!(database.sqlite.path, "bcs.db");
    assert_eq!(database.mysql.database, "bcs");
}

#[test]
fn explicit_database_mysql_block_uses_mysql_defaults() {
    let toml = r#"
        type = "mysql"

        [mysql]
    "#;

    let database: DatabaseConfig = toml::from_str(toml).expect("parse database config");

    assert_eq!(database.database_type, DatabaseType::Mysql);
    assert_eq!(database.mysql.database, "bcs");
}

#[test]
fn oauth_providers_map_parses_and_resolves_kind() {
    let toml = r#"
        base_url = "https://bcs.example.com"
        jwt_secret = "s"

        [providers.google]
        client_id = "gid"
        client_secret = "gsecret"

        [providers.github-partner]
        kind = "github"
        client_id = "ghid"
    "#;
    let cfg: OAuthSettings = toml::from_str(toml).expect("parse providers map");
    assert_eq!(cfg.providers.len(), 2);

    // kind omitted → defaults to the instance (map) name.
    let g = &cfg.providers["google"];
    assert_eq!(g.kind, None);
    assert_eq!(g.resolved_kind("google"), "google");

    // explicit kind decoupled from the instance name.
    let p = &cfg.providers["github-partner"];
    assert_eq!(p.resolved_kind("github-partner"), "github");

    cfg.validate().expect("valid config");
}

#[test]
fn oauth_validate_rejects_empty_client_id() {
    let toml = r#"
        base_url = "https://bcs.example.com"
        [providers.google]
        client_id = ""
    "#;
    let cfg: OAuthSettings = toml::from_str(toml).unwrap();
    let err = cfg.validate().expect_err("empty client_id rejected");
    assert!(err.contains("client_id"), "got: {err}");
}

#[test]
fn oauth_validate_rejects_route_unsafe_name() {
    let toml = r#"
        base_url = "https://bcs.example.com"
        [providers."bad/name"]
        client_id = "id"
    "#;
    let cfg: OAuthSettings = toml::from_str(toml).unwrap();
    let err = cfg.validate().expect_err("'/' in name rejected");
    assert!(err.contains("bad/name"), "got: {err}");
}

#[test]
fn provider_settings_rejects_unknown_field() {
    // deny_unknown_fields guards against typos in a provider block.
    let toml = r#"
        client_id = "id"
        typo_field = "x"
    "#;
    let err = toml::from_str::<ProviderSettings>(toml).expect_err("unknown field rejected");
    assert!(err.to_string().contains("typo_field"), "got: {err}");
}

#[test]
fn oauth_provider_settings_with_alipay_keys() {
    // Exercise configuration parsing, not cryptographic key validation.
    let toml = r#"
        base_url = "https://bcs.example.com"
        jwt_secret = "s"

        [providers.alipay]
        kind = "alipay"
        client_id = "2021001234567890"
        private_key = "<test-private-key>"
        alipay_public_key = "<test-public-key>"
    "#;
    let cfg: OAuthSettings = toml::from_str(toml).expect("parse alipay provider");
    let alipay = &cfg.providers["alipay"];
    assert_eq!(alipay.resolved_kind("alipay"), "alipay");
    assert!(alipay.private_key.is_some());
    assert!(alipay.alipay_public_key.is_some());
    assert!(alipay.client_secret.is_none());
    cfg.validate().expect("valid config");
}

#[test]
fn wechat_provider_settings_parses() {
    let toml = r#"
        base_url = "https://bcs.example.com"
        jwt_secret = "s"

        [providers.wechat]
        kind = "wechat"
        client_id = "wx1234567890"
        client_secret = "<test-client-secret>"
    "#;
    let cfg: OAuthSettings = toml::from_str(toml).expect("parse wechat provider");
    let wechat = &cfg.providers["wechat"];
    assert_eq!(wechat.resolved_kind("wechat"), "wechat");
    assert!(wechat.private_key.is_none());
    assert!(wechat.alipay_public_key.is_none());
    cfg.validate().expect("valid config");
}

#[test]
fn eventing_defaults_are_safe_for_phase_zero_rollout() {
    let config = EventingConfig::default();

    assert!(!config.enabled);
    assert!(!config.dispatcher_enabled);
    assert_eq!(config.worker_concurrency, 64);
    assert_eq!(config.webhook.request_timeout_ms, 10_000);
    assert!(config.webhook.block_private_networks);
    assert_eq!(config.limits.max_filters_per_subscription, 64);
    config.validate().expect("default Eventing config is valid");
}

#[test]
fn eventing_enabled_and_dispatcher_enabled_are_independent_flags() {
    let record_only: EventingConfig = toml::from_str(
        r#"
        enabled = true
        dispatcher_enabled = false
        "#,
    )
    .expect("parse record-only Eventing config");
    let entirely_disabled: EventingConfig = toml::from_str(
        r#"
        enabled = false
        dispatcher_enabled = false
        "#,
    )
    .expect("parse disabled Eventing config");

    assert!(record_only.enabled);
    assert!(!record_only.dispatcher_enabled);
    assert!(!entirely_disabled.enabled);
}

#[test]
fn eventing_config_rejects_unknown_fields() {
    let unknown = toml::from_str::<EventingConfig>("unknown_field = true")
        .expect_err("unknown Eventing field rejected");
    assert!(unknown.to_string().contains("unknown_field"));
}

#[test]
fn eventing_webhook_private_network_policy_defaults_strict_and_can_be_configured() {
    let default = EventingConfig::default();
    assert!(default.webhook.block_private_networks);

    let configured = toml::from_str::<EventingConfig>(
        r#"
        [webhook]
        block_private_networks = false
        "#,
    )
    .expect("Eventing webhook private network policy should deserialize");
    assert!(!configured.webhook.block_private_networks);
}

#[test]
fn eventing_config_validates_timeout_concurrency_retention_and_limits() {
    let mut concurrency = EventingConfig::default();
    concurrency.worker_concurrency = 0;
    assert!(
        concurrency
            .validate()
            .expect_err("zero workers rejected")
            .contains("worker_concurrency")
    );

    let mut timeout = EventingConfig::default();
    timeout.webhook.request_timeout_ms = 30_001;
    assert!(
        timeout
            .validate()
            .expect_err("timeout rejected")
            .contains("request_timeout_ms")
    );

    let mut retention = EventingConfig::default();
    retention.event_retention_days = 0;
    assert!(
        retention
            .validate()
            .expect_err("retention rejected")
            .contains("event_retention_days")
    );

    let mut limits = EventingConfig::default();
    limits.limits.max_filters_per_subscription = 65;
    assert!(
        limits
            .validate()
            .expect_err("filter limit rejected")
            .contains("max_filters_per_subscription")
    );
}

#[test]
fn private_endpoint_allowlist_validates_wildcard_cidr_and_ports() {
    let config = toml::from_str::<EventingConfig>(
        r#"
        [[webhook.private_endpoint_allowlist]]
        host = "*.hooks.example.internal"
        cidrs = ["10.20.0.0/16"]
        ports = [443, 8443]
        "#,
    )
    .expect("private endpoint allowlist should deserialize");
    config.validate().expect("valid allowlist should pass");
    let entry = &config.webhook.private_endpoint_allowlist[0];
    assert!(entry.matches_host_and_port("a.hooks.example.internal", 8443));
    assert!(entry.matches_host_and_port("a.b.hooks.example.internal", 443));
    assert!(!entry.matches_host_and_port("hooks.example.internal", 443));
    assert!(!entry.matches_host_and_port("evilhooks.example.internal", 443));
    assert!(!entry.matches_host_and_port("a.hooks.example.internal", 9443));
}

#[test]
fn private_endpoint_allowlist_rejects_unsafe_or_ambiguous_rules() {
    for (host, cidr, ports) in [
        ("hooks.*.internal", "10.0.0.0/8", vec![443]),
        ("*.localhost", "10.0.0.0/8", vec![443]),
        ("*.hooks.internal", "10.0.0.1/8", vec![443]),
        ("*.hooks.internal", "169.254.0.0/16", vec![443]),
        ("*.hooks.internal", "10.0.0.0/8", vec![]),
    ] {
        let mut config = EventingConfig::default();
        config.webhook.private_endpoint_allowlist = vec![
            PrivateEndpointAllowlistEntryConfig {
                host: host.to_string(),
                cidrs: vec![cidr.to_string()],
                ports,
            },
        ];
        assert!(config.validate().is_err(), "rule should be rejected: {host}");
    }
}

#[test]
fn human_notify_config_parses_provider_array_and_options() {
    let value: toml::Value = toml::from_str(
        r#"
[[providers]]
name = "dingtalk"
enabled = true
robot_code = "ding-robot"
client_secret = "s3cret"

[[providers]]
name = "dummy"
"#,
    )
    .expect("toml must parse");
    let config: HumanNotifyConfig = value.try_into().expect("config must deserialize");
    assert_eq!(config.providers.len(), 2);
    let dingtalk = &config.providers[0];
    assert_eq!(dingtalk.name, "dingtalk");
    assert!(dingtalk.enabled);
    assert_eq!(
        dingtalk.options.get("robot_code").and_then(|v| v.as_str()),
        Some("ding-robot")
    );
    let dummy = &config.providers[1];
    assert_eq!(dummy.name, "dummy");
    assert!(dummy.enabled, "enabled must default to true when omitted");
}

#[test]
fn human_notify_config_defaults_to_disabled() {
    let config = HumanNotifyConfig::default();
    assert!(config.providers.is_empty());
}

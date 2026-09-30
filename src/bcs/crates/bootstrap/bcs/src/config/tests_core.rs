    use super::*;
    use secrecy::ExposeSecret;
    use std::path::PathBuf;

    pub(super) fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
        unsafe {
            std::env::set_var(key, value);
        }
    }

    #[allow(unsafe_code)]

    pub(super) fn safe_remove_var(key: &str) {
        unsafe {
            std::env::remove_var(key);
        }
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
            let config: BcsConfig = toml::from_str(source).unwrap();
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

    #[test]
    fn test_config_with_dingtalk() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "dingtalk_accounts": [
                {
                    "account_id": "test-account",
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "gateway_mode": false,
                    "enable_scene_group": true,
                    "dm_policy": "open",
                    "allowlist": ["*"]
                }
            ]
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.dingtalk_accounts.len(), 1);
        assert_eq!(config.dingtalk_accounts[0].account_id, "test-account");
        assert_eq!(
            config.dingtalk_accounts[0].client_id,
            Some("test-client-id".to_string())
        );
        assert!(config.dingtalk_accounts[0].client_secret.is_some());
        assert!(config.dingtalk_accounts[0].enable_scene_group);
    }

    #[test]
    fn test_config_with_cache_section() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "127.0.0.1"
port = 6379
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert_eq!(config.cache.cache_type, "redis");
        assert_eq!(config.cache.redis.connection.connection_type, "direct");
        assert_eq!(config.cache.redis.connection.port, Some(6379));
    }

    #[test]
    fn test_config_with_mysql() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "database": {
                "type": "mysql",
                "mysql": {
                    "database": "bcs",
                    "connection": {
                        "type": "direct",
                        "user": "bcs_user",
                        "password": "secret",
                        "host": "10.0.0.2",
                        "port": 11306
                    }
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.database.database_type, DatabaseType::Mysql);
        let mysql = config.database.mysql;
        assert_eq!(mysql.database, "bcs");
        assert_eq!(mysql.connection.user.as_deref(), Some("bcs_user"));
    }

    #[test]
    fn test_config_with_sqlite_database() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "database": {
                "type": "sqlite",
                "sqlite": {
                    "path": "custom-bcs.db"
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.database.database_type, DatabaseType::Sqlite);
        assert_eq!(config.database.sqlite.path, "custom-bcs.db");
    }

    #[test]
    fn session_files_config_parses_share_link_ttl_and_backend() {
        let toml_str = r#"
storage_backend = "baas"
multipart_threshold = 104857600
max_file_size = 5368709120
share_link_ttl = 3600

[share]
token_secret = "s3cret"
default_ttl_seconds = 86400

[backend]
endpoint = "http://baas:8080"
tenant = "teamclaw"
"#;
        let cfg: SessionFilesConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(cfg.storage_backend, "baas");
        assert_eq!(cfg.share_link_ttl, 3600);
        assert_eq!(
            cfg.backend["endpoint"],
            toml::Value::String("http://baas:8080".into())
        );
        assert_eq!(
            cfg.backend["tenant"],
            toml::Value::String("teamclaw".into())
        );
    }

    #[test]
    fn test_config_with_leader_election() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "leader_election": {
                "enabled": true,
                "provider": "distributed",
                "lease": {
                    "ttl_secs": 30,
                    "renewal_interval_secs": 10
                },
                "providers": {
                    "distributed": {
                        "zone": "default",
                        "lock_prefix": "bcs"
                    }
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert!(config.leader_election.is_some());
        let election = config.leader_election.unwrap();
        assert!(election.enabled);
        assert_eq!(election.provider.as_deref(), Some("distributed"));
        assert_eq!(election.lease.ttl_secs, 30);
        assert_eq!(election.lease.renewal_interval_secs, 10);
        let provider = election
            .providers
            .get("distributed")
            .expect("distributed provider options");
        assert_eq!(
            provider.get("zone").and_then(|value| value.as_str()),
            Some("default")
        );
        assert_eq!(
            provider.get("lock_prefix").and_then(|value| value.as_str()),
            Some("bcs")
        );
    }

    #[test]
    fn test_config_toml_format() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "127.0.0.1"
port = 6379

[leader_election]
enabled = true
provider = "distributed"

[leader_election.lease]
ttl_secs = 30
renewal_interval_secs = 10

[leader_election.providers.distributed]
zone = "default"
lock_prefix = "bcs"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert_eq!(config.bind, "0.0.0.0");
        assert_eq!(config.port, 21000);
        assert_eq!(config.cache.cache_type, "redis");
        assert!(config.leader_election.is_some());
        let election = config.leader_election.unwrap();
        assert!(election.enabled);
        assert_eq!(election.provider.as_deref(), Some("distributed"));
        assert_eq!(election.lease.ttl_secs, 30);
        let provider = election
            .providers
            .get("distributed")
            .expect("distributed provider options");
        assert_eq!(
            provider.get("zone").and_then(|value| value.as_str()),
            Some("default")
        );
        assert_eq!(
            provider.get("lock_prefix").and_then(|value| value.as_str()),
            Some("bcs")
        );
    }

    #[test]
    fn test_metrics_config_defaults() {
        let config = BcsConfig::default();
        assert!(!config.metrics.enabled);
        assert_eq!(config.metrics.mode, MetricsMode::Pull);
        assert_eq!(config.metrics.endpoint_path, "/metrics");
        assert!(config.metrics.validate().is_ok());
    }

    #[test]
    fn test_metrics_config_toml() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[metrics]
enabled = true
mode = "pull"
endpoint_path = "/metrics"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert!(config.metrics.enabled);
        assert_eq!(config.metrics.mode, MetricsMode::Pull);
        assert_eq!(config.metrics.endpoint_path, "/metrics");
    }

    #[test]
    fn test_config_cache_toml_with_redis_auth() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "redis.example.com"
port = 6379
auth_mode = "redis"
username = "bcs"
password = "redis-pass"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let connection = &config.cache.redis.connection;

        assert_eq!(connection.auth_mode, RedisAuthMode::Redis);
        assert_eq!(connection.username.as_deref(), Some("bcs"));
        assert_eq!(
            connection
                .password
                .as_ref()
                .map(|password| password.expose_secret().as_str()),
            Some("redis-pass")
        );
    }

    #[test]
    fn test_security_gateway_provider_options_parse_as_map() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[security_gateway]
provider = "agentpass"
dry_run = false

[security_gateway.providers.agentpass]
domain = "security-gateway.example.com"
endpoint = "/api/agentpass/zero_check.json"
timeout_ms = 300
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let provider = config
            .security_gateway
            .providers
            .get("agentpass")
            .expect("agentpass provider config");

        assert_eq!(config.security_gateway.provider, "agentpass");
        assert!(!config.security_gateway.dry_run);
        assert_eq!(
            provider.get("endpoint").and_then(|value| value.as_str()),
            Some("/api/agentpass/zero_check.json")
        );
        assert_eq!(
            provider.get("timeout_ms").and_then(|value| value.as_u64()),
            Some(300)
        );
    }

    #[test]
    fn test_user_directory_provider_options_parse_as_map() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[user_directory]
enabled = true
provider = "ldap"

[user_directory.providers.ldap]
base_url = "https://directory.example.com"
timeout_ms = 300
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let provider = config
            .user_directory
            .providers
            .get("ldap")
            .expect("ldap provider config");

        assert!(config.user_directory.enabled);
        assert_eq!(config.user_directory.provider.as_deref(), Some("ldap"));
        assert_eq!(
            provider.get("base_url").and_then(|value| value.as_str()),
            Some("https://directory.example.com")
        );
        assert_eq!(
            provider.get("timeout_ms").and_then(|value| value.as_u64()),
            Some(300)
        );
    }

    #[test]
    fn invite_public_claim_is_nested_and_disabled_by_default() {
        let default_config = BcsConfig::default();
        assert!(!default_config.invite.public_claim_enabled);
        assert_eq!(default_config.invite.public_claim_max_count, 1_000);

        let config: BcsConfig = toml::from_str(
            r#"
            bots_base_dir = "/bots"

            [invite]
            public_claim_enabled = true
            public_claim_max_count = 200
            "#,
        )
        .expect("parse invite public claim config");

        assert!(config.invite.public_claim_enabled);
        assert_eq!(config.invite.public_claim_max_count, 200);
    }

    #[test]
    fn eventing_section_parses_record_only_rollout() {
        let config: BcsConfig = toml::from_str(
            r#"
            bots_base_dir = "/bots"

            [eventing]
            enabled = true
            dispatcher_enabled = false

            "#,
        )
        .expect("parse Eventing config");

        assert!(config.eventing.enabled);
        assert!(!config.eventing.dispatcher_enabled);
    }

    #[test]
    fn checked_in_configs_parse_without_obsolete_eventing_auth_config() {
        for path in [
            concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../../configs/bcs-config-example.toml"
            ),
            concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../../configs/bcs-config-local.toml"
            ),
        ] {
            let source = std::fs::read_to_string(path).expect("read checked-in config");
            let _: BcsConfig = toml::from_str(&source).expect("parse checked-in config");
            assert!(!source.contains("secret_protector"));
            assert!(!source.contains("bcn-eventing-webhook-protector"));
        }
    }

    #[test]
    fn checked_in_local_config_enables_event_delivery() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../configs/bcs-config-local.toml"
        );
        let source = std::fs::read_to_string(path).expect("read checked-in local config");
        let config: BcsConfig = toml::from_str(&source).expect("parse checked-in local config");

        assert!(config.eventing.enabled);
        assert!(config.eventing.dispatcher_enabled);
    }

    /// Regression guard for the `issuer` → `issuers` field rename: the
    /// checked-in configs must parse under `deny_unknown_fields`, and the old
    /// scalar `issuer` key must not reappear. See PR #1799 follow-up.
    /// `bcs-config-prod.toml` is deployment-local and untracked, so only the
    /// example and local configs are covered.

    #[test]
    fn checked_in_configs_parse_with_array_issuers_and_reject_legacy_scalar() {
        let targets: &[(&str, &str)] = &[
            ("example", concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../../configs/bcs-config-example.toml"
            )),
            ("local", concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../../configs/bcs-config-local.toml"
            )),
        ];
        for (label, path) in targets {
            let source = std::fs::read_to_string(path)
                .unwrap_or_else(|e| panic!("read {label} config {path}: {e}"));
            let config: BcsConfig = toml::from_str(&source)
                .unwrap_or_else(|e| panic!("parse {label} config {path}: {e}"));
            assert_eq!(
                config.gateway_principal.issuers,
                vec!["gateway".to_string(), "backend".to_string()],
                "{label} config gateway_principal.issuers must accept both issuers"
            );
            assert!(
                !source.contains("issuer = "),
                "{label} config {path} still carries the legacy scalar `issuer` key",
            );
        }
    }

    #[test]
    fn human_notify_section_parses() {
        let config: BcsConfig = toml::from_str(
            r#"
bots_base_dir = "/tmp/bots"

[[human_notify.providers]]
name = "dummy"
enabled = true
"#,
        )
        .expect("config parses");
        assert_eq!(config.human_notify.providers.len(), 1);
        assert_eq!(config.human_notify.providers[0].name, "dummy");
        assert!(config.human_notify.providers[0].enabled);
    }

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
fn downlink_detection_source_defaults_to_legacy_and_rejects_unknown_values() {
    use bcs_domain::bot_provider::DownlinkDetectionSource;
    let legacy: ProviderHttpConfig = toml::from_str("").unwrap();
    assert_eq!(legacy.downlink_detection_source, DownlinkDetectionSource::Binding);
    let bots: ProviderHttpConfig = toml::from_str("downlink_detection_source = 'bot_connection_mode'").unwrap();
    assert_eq!(bots.downlink_detection_source, DownlinkDetectionSource::BotConnectionMode);
    assert!(toml::from_str::<ProviderHttpConfig>("downlink_detection_source = 'auto'").is_err());
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
fn removed_progression_recovery_switch_is_rejected() {
    for enabled in [false, true] {
        let config = format!("bots_base_dir = '/bots'\n[collaboration]\nexperimental_progression_recovery = {enabled}");
        let error = toml::from_str::<BcsConfig>(&config).unwrap_err();
        assert!(error.to_string().contains("unknown field `experimental_progression_recovery`"));
    }
}

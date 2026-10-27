use super::*;

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
fn default_openapi_public_collaboration_base_url_uses_internal_path() {
    assert_eq!(
        OpenApiV1Config::default()
            .validated_public_collaboration_base_url()
            .unwrap(),
        "http://127.0.0.1:21000/api/v1/collaboration"
    );
}

#[test]
fn openapi_v1_public_base_url_is_validated_and_normalized() {
    let cfg: OpenApiV1Config = toml::from_str(
        r#"public_collaboration_base_url = "https://gateway.example.com/api/v1/collaboration/""#,
    )
    .unwrap();

    assert_eq!(
        cfg.validated_public_collaboration_base_url().unwrap(),
        "https://gateway.example.com/api/v1/collaboration"
    );
}

#[test]
fn openapi_v1_public_base_url_rejects_unsafe_or_non_absolute_values() {
    for value in [
        "",
        "/api/v1/collaboration",
        "ftp://gateway.example.com/api/v1/collaboration",
        "https://user@gateway.example.com/api/v1/collaboration",
        "https://gateway.example.com/api/v1/collaboration?tenant=x",
        "https://gateway.example.com/api/v1/collaboration#fragment",
    ] {
        let cfg = OpenApiV1Config {
            public_collaboration_base_url: value.to_string(),
            ..Default::default()
        };
        assert!(
            cfg.validated_public_collaboration_base_url().is_err(),
            "expected invalid URL: {value}"
        );
    }
}

#[test]
fn internal_collaboration_base_url_falls_back_to_collaboration_base_url() {
    let cfg = OpenApiV1Config::default();
    assert_eq!(
        cfg.validated_internal_collaboration_base_url().unwrap(),
        cfg.validated_public_collaboration_base_url().unwrap(),
    );
}

#[test]
fn internal_collaboration_base_url_uses_independent_value_when_set() {
    let cfg: OpenApiV1Config = toml::from_str(
        r#"public_collaboration_base_url = "https://gw.example.com/openapi/v1/collaboration"
        internal_collaboration_base_url = "https://gw.example.com/api/v1/collaboration""#,
    )
    .unwrap();
    assert_eq!(
        cfg.validated_public_collaboration_base_url().unwrap(),
        "https://gw.example.com/openapi/v1/collaboration",
    );
    assert_eq!(
        cfg.validated_internal_collaboration_base_url().unwrap(),
        "https://gw.example.com/api/v1/collaboration",
    );
}

#[test]
fn internal_collaboration_base_url_rejects_unsafe_values() {
    for value in [
        "/api/v1/collaboration",
        "ftp://gw.example.com/api/v1/collaboration",
        "https://user@gw.example.com/api/v1/collaboration",
        "https://gw.example.com/api/v1/collaboration?tenant=x",
    ] {
        let cfg = OpenApiV1Config {
            internal_collaboration_base_url: Some(value.to_string()),
            ..Default::default()
        };
        assert!(
            cfg.validated_internal_collaboration_base_url().is_err(),
            "expected invalid URL: {value}"
        );
    }
}

#[test]
fn test_config_rejects_legacy_group_storage() {
    let json = r#"{
        "bind": "0.0.0.0",
        "port": 21000,
        "bots_base_dir": "/bots",
        "group_storage": {
            "storage_type": "sqlite"
        }
    }"#;

    let err =
        serde_json::from_str::<BcsConfig>(json).expect_err("legacy group_storage rejected");
    assert!(err.to_string().contains("unknown field"));
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
fn test_config_rejects_legacy_leader_election_fields() {
    let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[leader_election]
enabled = true
provider = "distributed"
lock_ttl_secs = 30
renewal_interval_secs = 10
"#;

    let err = toml::from_str::<BcsConfig>(toml)
        .expect_err("legacy leader_election fields must be rejected");
    assert!(err.to_string().contains("unknown field"));
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
fn test_metrics_config_rejects_invalid_mode() {
    let json = r#"{
        "bots_base_dir": "/bots",
        "metrics": {
            "mode": "push"
        }
    }"#;

    let err = serde_json::from_str::<BcsConfig>(json).expect_err("invalid mode rejected");
    assert!(err.to_string().contains("unknown variant"));
}

#[test]
fn test_metrics_config_validates_endpoint_path() {
    let mut config = MetricsConfig::default();
    config.endpoint_path = "metrics".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/groups/{id}".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/ws/bot".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/groups".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/groups/metrics".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/bots/metrics".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/actors/metrics".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/chat/runs/metrics".to_string();
    assert!(config.validate().is_err());

    config.endpoint_path = "/metrics".to_string();
    assert!(config.validate().is_ok());

    config.endpoint_path = "/botmetrics".to_string();
    assert!(config.validate().is_ok());
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
fn test_config_rejects_top_level_redis_section() {
    let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[redis]
host = "127.0.0.1"
port = 6379
skip_auth = true
"#;

    let err =
        toml::from_str::<BcsConfig>(toml).expect_err("top-level redis should be rejected");
    assert!(err.to_string().contains("redis"));
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
fn test_security_gateway_rejects_legacy_top_level_provider_options() {
    let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[security_gateway]
provider = "agentpass"
dry_run = false
endpoint = "/api/agentpass/zero_check.json"
"#;

    let err = toml::from_str::<BcsConfig>(toml)
        .expect_err("legacy top-level provider options should be rejected");
    assert!(err.to_string().contains("endpoint"));
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
fn test_user_directory_rejects_legacy_top_level_provider_options() {
    let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[user_directory]
enabled = true
provider = "ldap"
base_url = "https://directory.example.com"
"#;

    let err = toml::from_str::<BcsConfig>(toml)
        .expect_err("legacy top-level provider options should be rejected");
    assert!(err.to_string().contains("base_url"));
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
fn invalid_eventing_bounds_fail_loaded_config_validation() {
    let mut config = BcsConfig::default();
    config.eventing.worker_concurrency = 0;

    let error = validate_loaded_config(&config)
        .expect_err("invalid Eventing concurrency rejected")
        .to_string();

    assert!(error.contains("eventing.worker_concurrency"));
}

#[test]
fn production_rejects_eventing_http_loopback() {
    let mut loopback = BcsConfig::default();
    loopback.eventing.enabled = true;
    loopback.eventing.webhook.allow_http_loopback = true;
    let loopback_error = validate_eventing_environment_policy(
        &loopback,
        crate::config_loader::Environment::Prod,
    )
    .expect_err("production loopback HTTP rejected");
    assert!(loopback_error.contains("allow_http_loopback"));
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
        let mut value = crate::config_loader::parse_config_content("toml", &source, path).unwrap();
        crate::config_loader::expand_env_vars(&mut value).unwrap();
        let _: BcsConfig = serde_json::from_value(value).expect("parse checked-in config");
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
    let mut value = crate::config_loader::parse_config_content("toml", &source, path).unwrap();
    crate::config_loader::expand_env_vars(&mut value).unwrap();
    let config: BcsConfig = serde_json::from_value(value).expect("parse checked-in local config");

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
        let mut value = crate::config_loader::parse_config_content("toml", &source, path).unwrap();
        crate::config_loader::expand_env_vars(&mut value).unwrap();
        let config: BcsConfig = serde_json::from_value(value)
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

// ---------------------------------------------------------------------
// [bot_authority] trusted-app authority scope (bot-authority spec §3.1)

#[test]
fn bot_authority_trusted_apps_parse_and_resolve() {
    let toml = r#"
        bots_base_dir = "/bots"

        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:sync"]

        [[bot_authority.trusted_apps]]
        app = "backend-b"
        tenant = "absent"
        actions = ["ownership:read", "ownership:accept", "ownership:reject"]
    "#;
    let config: BcsConfig =
        toml::from_str(toml).expect("parse trusted-app authority scope");
    config.bot_authority.validate().expect("valid rules");

    let policy = config
        .bot_authority
        .resolve("local")
        .expect("resolve policy");
    assert_eq!(policy.env(), "local");
    assert_eq!(policy.rules().len(), 2);
}

#[test]
fn bot_authority_unknown_action_fails_startup() {
    let config: BcsConfig = toml::from_str(
        r#"
        bots_base_dir = "/bots"

        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:delete"]
    "#,
    )
    .expect("parse");
    let error = config
        .bot_authority
        .validate()
        .expect_err("unknown action must fail configuration loading");
    assert!(error.contains("unknown action"), "{error}");
    //_load via file
    let tmp = tempfile::TempDir::new().expect("temp config dir");
    std::fs::write(tmp.path().join("bcs-config.toml"), r#"
        bots_base_dir = "/bots"
        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:whatever"]
        "#,
    )
    .expect("write config");
    let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
        .expect_err("unknown action must abort startup");
    assert!(err.contains("unknown action"), "{err}");
}

#[test]
fn bot_authority_wildcards_and_duplicate_rule_keys_fail_startup() {
    for (toml, expected) in [
        ("bot-1*", "wildcard"),
        (" hacer", "plain"),
    ] {
        let config: BcsConfig = toml::from_str(&format!(
            r#"
            bots_base_dir = "/bots"

            [[bot_authority.trusted_apps]]
            app = "{toml}"
            tenant = "tenant-a"
            actions = ["managers:sync"]
        "#
        ))
        .expect("parse");
        let error = config
            .bot_authority
            .validate()
            .expect_err("wildcard/padded app name fails loading");
        assert!(error.contains(expected), "{error}");
    }

    let duplicate: BcsConfig = toml::from_str(
        r#"
        bots_base_dir = "/bots"

        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:sync"]

        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:sync"]
    "#,
    )
    .expect("parse");
    let error = duplicate
        .bot_authority
        .validate()
        .expect_err("duplicate rule keys fail startup");
    assert!(error.contains("duplicate rule key"), "{error}");
}

#[test]
fn bot_authority_tenant_and_shape_errors_fail_startup() {

    // A missing tenant field is a load failure (the
    // reserved "absent" spelling is the only way to
    // match tenantless callers).
    let tmp = tempfile::TempDir::new().expect("temp config dir");
    std::fs::write(
        tmp.path().join("bcs-config.toml"),
        r#"
        bots_base_dir = "/bots"
        [[bot_authority.trusted_apps]]
        app = "backend"
        actions = ["managers:sync"]
        "#,
    )
    .expect("write config");
    let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
        .expect_err("missing tenant field fails loading");
    let combined = format!("{err}");
    assert!(
        combined.to_lowercase().contains("tenant")
            || combined.to_lowercase().contains("missing field"),
        "load error should mention the missing tenant: {combined}"
    );

    // An unknown field is a load failure.
    let tmp = tempfile::TempDir::new().expect("temp config dir");
    std::fs::write(
        tmp.path().join("bcs-config.toml"),
        r#"
        bots_base_dir = "/bots"
        [[bot_authority.trusted_apps]]
        app = "backend"
        tenant = "tenant-a"
        actions = ["managers:sync"]
        future_field = true
        "#,
    )
    .expect("write config");
    let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
        .expect_err("unknown field fails loading");
    assert!(
        err.contains("unknown field") || err.contains("future_field"),
        "{err}"
    );

    // The legacy schema fails LOUDLY, never half-applied
    // (bot-authority simplification invariant).
    let tmp = tempfile::TempDir::new().expect("temp config dir");
    std::fs::write(
        tmp.path().join("bcs-config.toml"),
        r#"
        bots_base_dir = "/bots"
        [[bot_authority.trusted_apps]]
        app_id = 7
        env = "local"
        tenant = "tenant-a"
        bot_ids = ["bot-1"]
        actions = ["managers:sync"]
        "#,
    )
    .expect("write config");
    let err = BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
        .expect_err("legacy rule schema must fail loading loudly");
    assert!(
        err.contains("unknown field")
            || err.contains("app_id")
            || err.contains("bot_ids"),
        "legacy fields must surface as load errors: {err}"
    );
}

#[test]
fn bot_authority_default_denies_everything() {
    let config: BcsConfig =
        toml::from_str(r#"bots_base_dir = "/bots""#).expect("parse");
    assert!(config.bot_authority.trusted_apps.is_empty());
    config.bot_authority.validate().expect("default validates");
    let policy = config.bot_authority.resolve("local").expect("resolve");
    assert!(policy.rules().is_empty());
    assert_eq!(
        config.bot_authority.trusted_apps.len(),
        0,
        "no default rules: every trusted app denied"
    );
}

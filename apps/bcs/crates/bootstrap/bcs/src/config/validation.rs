//! Configuration validation helpers.

use std::collections::BTreeMap;
use std::path::Path;

#[allow(unused_imports)]
use super::types::*;
#[allow(unused_imports)]
use super::defaults::*;

impl BcsConfig {
    pub fn validate_metrics(&self) -> Result<(), String> {
        self.metrics.validate()?;
        if self.metrics.enabled && !cfg!(feature = "prometheus-metrics") {
            return Err(
                "metrics.enabled=true requires the bcs prometheus-metrics Cargo feature"
                    .to_string(),
            );
        }
        Ok(())
    }

    pub fn validate_run_store_selectors(&self) -> Result<(), String> {
        match self.async_chat_run_store.as_str() {
            "memory" | "persistent" => {}
            other => {
                return Err(format!(
                    "async_chat_run_store must be 'memory' or 'persistent', got '{other}'"
                ))
            }
        }
        match self.bot_run_context_store.as_str() {
            "memory" | "redis" => {}
            other => {
                return Err(format!(
                    "bot_run_context_store must be 'memory' or 'redis', got '{other}'"
                ))
            }
        }
        Ok(())
    }

    pub fn validate_api_keys(&self) -> Result<(), String> {
        let mut seen_sha = std::collections::HashSet::new();
        let mut seen_name = std::collections::HashSet::new();
        for k in &self.api_keys {
            if k.sha256.len() != 64
                || !k
                    .sha256
                    .chars()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            {
                return Err(format!(
                    "api_key {} has invalid sha256 (must be 64 lowercase hex chars)",
                    k.name
                ));
            }
            if !seen_sha.insert(k.sha256.clone()) {
                return Err(format!("duplicate api_key sha256 (name: {})", k.name));
            }
            if !seen_name.insert(k.name.clone()) {
                return Err(format!("duplicate api_key name: {}", k.name));
            }
        }
        Ok(())
    }
}

pub(super) fn is_api_route_namespace(path: &str) -> bool {
    // NOTE: This blocks known API route namespaces, not a complete Axum route
    // inventory. Exact route collisions still surface during router
    // construction; update this list when adding new top-level API roots.
    const API_ROOTS: &[&str] = &[
        "/me",
        "/bots",
        "/admin",
        "/actors",
        "/friends",
        "/groups",
        "/collaboration",
        "/chat",
        "/onboard",
        "/manifest",
    ];

    API_ROOTS.iter().any(|root| path_is_under_root(path, root))
}

pub(super) fn path_is_under_root(path: &str, root: &str) -> bool {
    path == root
        || path
            .strip_prefix(root)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

pub(super) fn validate_http_base_url(value: &str, field_name: &str) -> Result<(), String> {
    let raw = value.trim();
    if raw.is_empty() {
        return Err(format!("{field_name} must not be blank"));
    }
    let url = url::Url::parse(raw)
        .map_err(|error| format!("{field_name} must be an absolute HTTP(S) URL: {error}"))?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err(format!(
            "{field_name} must be an absolute HTTP(S) URL"
        ));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(format!("{field_name} must not contain userinfo"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(format!("{field_name} must not contain query or fragment"));
    }
    Ok(())
}

pub(super) fn validate_loaded_config(config: &BcsConfig) -> Result<(), Box<dyn std::error::Error>> {
    // Readiness is code-owned; other business flows retain legacy delivery.
    config.message_delivery.validate_ready_flows(&[bcs_config_api::message_delivery::DeliveryFlowKey::Group])?;
    if config.provider_chat_run_timeout_ms == 0 {
        return Err(Box::new(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "provider_chat_run_timeout_ms must be greater than zero",
        )));
    }
    config.eventing.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.gateway_principal.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.group_session_ws.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.telemetry.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.collaboration.fixed_loop_limits.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.provider_http.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    if let Some(base_url) = config.friend_work_order_base_url.as_deref() {
        validate_http_base_url(base_url, "friend_work_order_base_url").map_err(|e| {
            Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
                as Box<dyn std::error::Error>
        })?;
    }
    config
        .openapi_v1
        .validated_public_collaboration_base_url()
        .map_err(|e| {
            Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
                as Box<dyn std::error::Error>
        })?;
    config.validate_metrics().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.validate_api_keys().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    if let Some(oauth) = config.auth.oauth.as_ref() {
        oauth.validate().map_err(|e| {
            Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
                as Box<dyn std::error::Error>
        })?;
    }
    // Validate the V1 `[api.auth]` chain against the registered production
    // source inventory (Task 6 two-phase registry). Skipped when `[api.auth]`
    // is absent (spec §4.3 compat mode — preserve current Gateway-only
    // routes; no behavior change, no implicit gateway append, no implicit
    // values from `[auth]`/`[gateway_principal]`).
    if let Some(auth) = config
        .api
        .as_ref()
        .and_then(|api| api.auth.as_ref())
    {
        crate::api_auth_registry::validate_api_auth(
            auth,
            &crate::api_auth_registry::default_api_auth_registrations(),
        )
        .map_err(|e| {
            Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
                as Box<dyn std::error::Error>
        })?;
    }
    Ok(())
}

pub(super) fn validate_loaded_config_for_environment(
    config: &BcsConfig,
    environment: crate::config_loader::Environment,
) -> Result<(), Box<dyn std::error::Error>> {
    validate_loaded_config(config)?;
    validate_eventing_environment_policy(config, environment).map_err(|error| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, error))
            as Box<dyn std::error::Error>
    })
}

pub(super) fn validate_eventing_environment_policy(
    config: &BcsConfig,
    environment: crate::config_loader::Environment,
) -> Result<(), String> {
    if !config.eventing.enabled
        || !matches!(
            environment,
            crate::config_loader::Environment::Pre
                | crate::config_loader::Environment::Prod
                | crate::config_loader::Environment::Gray
        )
    {
        return Ok(());
    }
    if config.eventing.webhook.allow_http_loopback {
        return Err(
            "eventing.webhook.allow_http_loopback must be false outside local/development"
                .to_string(),
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

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

}

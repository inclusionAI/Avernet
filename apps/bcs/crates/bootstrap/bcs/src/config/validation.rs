use super::root::BcsConfig;

fn validate_http_base_url(value: &str, field_name: &str) -> Result<(), String> {
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
    // Trusted-app authority scope: unknown actions,
    // wildcard strings, duplicate rule keys, and shape
    // errors abort startup (spec §3.1).
    config.bot_authority.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.group_session_ws.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.bot_ws_admission.validate().map_err(|e| {
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
    if config.collaboration.nested_loop_execution_enabled
        && !config.collaboration.loop_execution_enabled
    {
        return Err(Box::new(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "collaboration.nested_loop_execution_enabled requires collaboration.loop_execution_enabled=true",
        )));
    }
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

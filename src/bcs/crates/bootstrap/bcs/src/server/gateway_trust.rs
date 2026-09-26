//! Gateway principal verifier construction + test-signing-key constant.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

pub(super) fn gateway_principal_signing_key(material: Option<&str>) -> crate::Result<&str> {
    material
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig("Gateway Principal signing key is required".to_string())
        })
}

pub(crate) fn build_gateway_principal_verifier(
    config: &GatewayPrincipalConfig,
    material: Option<&str>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let signing_key = gateway_principal_signing_key(material)?;
    let trust = GatewayPrincipalTrust::new(
        config.issuers.clone(),
        config.audience.clone(),
        config.key_id.clone(),
    )
    .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    let verifier = GatewayPrincipalTokenVerifier::new(signing_key.as_bytes(), trust)
        .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    Ok(Arc::new(verifier))
}

pub(super) fn build_gateway_principal_verifier_from_process(
    config: &GatewayPrincipalConfig,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    let material = std::env::var(&config.signing_key_env).ok();
    build_gateway_principal_verifier(config, material.as_deref())
}

pub(super) async fn build_gateway_principal_verifier_from_secret_access(
    config: &GatewayPrincipalConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let secret_name = config
        .signing_key_secret
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());

    if let Some(secret_name) = secret_name {
        let record = secret_access.get_secret(secret_name).await.map_err(|_| {
            crate::BcsError::InvalidConfig(format!(
                "Gateway Principal signing key secret '{secret_name}' is required"
            ))
        })?;
        return build_gateway_principal_verifier(config, Some(record.value.as_str()));
    }

    build_gateway_principal_verifier_from_process(config)
}

pub(super) const GROUP_SESSION_WS_TEST_SIGNING_KEY: &str = "test-only-group-session-key-at-least-32-bytes";

pub(crate) fn gateway_principal_verifier_for_tests() -> Arc<dyn PrincipalVerifier> {
    build_gateway_principal_verifier(
        &GatewayPrincipalConfig::default(),
        Some("test-only-gateway-principal-signing-key"),
    )
    .expect("default Gateway Principal test verifier")
}

//! Extracted verbatim from `server_infrastructure.rs`: authentication
//! resource construction owned by the bootstrap composition root — the
//! gateway-principal secret/verifier assembly plus the auth-plugin-chain
//! build. Pure responsibility move: construction order, inputs, and error
//! paths are unchanged.
use super::*;

/// Gateway-principal auth material shared across the server bootstrap:
/// the secret access backing group-session tokens, the invite-token
/// secret, and the verifier the OpenAPI admission gate runs under.
pub(super) struct GatewayPrincipalAuthResources {
    pub(super) group_session_secret_access: Arc<dyn SecretAccessPort>,
    pub(super) invite_token_secret: Vec<u8>,
    pub(super) gateway_principal_verifier: Arc<dyn PrincipalVerifier>,
}

pub(super) async fn build_gateway_principal_auth_resources(
    config: &BcsConfig,
) -> crate::Result<GatewayPrincipalAuthResources> {
    let group_session_secret_access = crate::http_adapter::build_secret_access(&config).await?;
    let invite_token_secret = resolve_invite_token_secret(&config);
    let gateway_principal_verifier = build_gateway_principal_verifier_from_secret_access(
        &config.gateway_principal,
        group_session_secret_access.clone(),
    )
    .await?;
    info!(
        issuers = ?config.gateway_principal.issuers,
        audience = %config.gateway_principal.audience,
        key_id = %config.gateway_principal.key_id,
        "Gateway Principal verifier initialized"
    );
    Ok(GatewayPrincipalAuthResources {
        group_session_secret_access,
        invite_token_secret,
        gateway_principal_verifier,
    })
}

/// Auth-plugin-chain resources carried by `BcsServerState`.
pub(super) struct AuthChainResources {
    pub(super) auth_config: bcs_auth_api::AuthConfig,
    pub(super) user_identity_port: Option<Arc<dyn bcs_auth_api::UserIdentityPort>>,
    /// Strict session identity port over the SAME store as
    /// `user_identity_port`: session issuance/rotation/revocation for both
    /// HTTP login entries, and the seed handle for integration tests.
    pub(super) auth_session_identity: Option<Arc<dyn bcs_auth_api::session::AuthSessionIdentityPort>>,
    pub(super) auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
}

pub(super) fn build_auth_chain_resources(
    config: &BcsConfig,
    infrastructure_plugins: &InfrastructurePlugins,
    extensions: &BcsServerExtensions,
    bot_registry: &Arc<dyn BotRegistryCoreService>,
) -> crate::Result<AuthChainResources> {
    let auth_config = crate::auth_wiring::resolve_auth_config(
        &config.auth,
        crate::config_loader::Environment::resolve().as_str(),
    );
    // One identity build over ONE store instance: the legacy display port
    // and the strict session port share the same in-memory map / DB rows,
    // so sessions installed through the strict port are visible to the
    // display readers.
    let identity_ports = match infrastructure_plugins.db() {
        Some(db) => crate::identity_wiring::db_identity_ports(
            infrastructure_plugins.db_kind(),
            db,
        ),
        None => crate::identity_wiring::memory_identity_ports(),
    };
    let user_identity_port = Some(identity_ports.user_identity.clone());
    let auth_session_identity = Some(identity_ports.auth_session_identity.clone());
    let auth_chain = Arc::new(
        crate::auth_wiring::try_build_auth_chain_with_factories(
            &auth_config,
            Arc::clone(bot_registry),
            Some(identity_ports.user_identity.clone()),
            Some(identity_ports.auth_session_identity.clone()),
            &extensions.auth_plugin_factories,
        )
        .map_err(crate::BcsError::InvalidConfig)?,
    );
    Ok(AuthChainResources {
        auth_config,
        user_identity_port,
        auth_session_identity,
        auth_chain,
    })
}

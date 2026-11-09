//! Extracted verbatim from `server_infrastructure.rs`: authentication
//! resource construction owned by the bootstrap composition root — the
//! gateway-principal secret/verifier assembly, the auth-plugin-chain build,
//! and the explicit `[api.auth]` selection (V1 API auth plan, Task 12).
use super::*;

/// Gateway-principal auth material shared across the server bootstrap:
/// the secret access backing group-session tokens, the invite-token
/// secret, and the compat Gateway verifier the OpenAPI admission gate
/// runs under. The verifier is `None` in explicit `[api.auth]` mode:
/// the chain fully decides the V1 selection there, so the legacy
/// `[gateway_principal]` signing key is never read (N4) — only the
/// friend-auth-sync resigner keeps its own, independent contract.
pub(super) struct GatewayPrincipalAuthResources {
    pub(super) group_session_secret_access: Arc<dyn SecretAccessPort>,
    pub(super) invite_token_secret: Vec<u8>,
    pub(super) gateway_principal_verifier: Option<Arc<dyn PrincipalVerifier>>,
}

pub(super) async fn build_gateway_principal_auth_resources(
    config: &BcsConfig,
) -> crate::Result<GatewayPrincipalAuthResources> {
    let group_session_secret_access = crate::http_adapter::build_secret_access(&config).await?;
    let invite_token_secret = resolve_invite_token_secret(&config);
    let gateway_principal_verifier = if config.api.auth.is_some() {
        info!(
            "explicit [api.auth] selected: the legacy gateway principal verifier is not built"
        );
        None
    } else {
        let verifier = build_gateway_principal_verifier_from_secret_access(
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
        Some(verifier)
    };
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

/// The complete authentication selection for one server startup: the legacy
/// plugin chain (native Human router falls back to it unchanged) plus the
/// V1 principal verifier that ends up as the ONE Stateful-wrapped Arc shared
/// by the main ApiState and the group-session connection-token router.
pub(super) struct AuthenticationSelection {
    pub(super) auth_config: bcs_auth_api::AuthConfig,
    pub(super) user_identity_port: Option<Arc<dyn bcs_auth_api::UserIdentityPort>>,
    pub(super) auth_session_identity:
        Option<Arc<dyn bcs_auth_api::session::AuthSessionIdentityPort>>,
    pub(super) auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
    /// The final V1 principal verifier (composite in explicit
    /// `[api.auth]` mode; the legacy Gateway verifier in compat mode).
    pub(super) principal_verifier: Arc<dyn PrincipalVerifier>,
    pub(super) origins: Option<bcs_api_http::TrustedBrowserOrigins>,
    /// The explicit-chain V1 auth facade for the login-family routes; the
    /// compat path keeps `None` and the `[auth.oauth]`-derived mount.
    pub(super) explicit_api_auth: Option<crate::api_auth_wiring::ExplicitApiAuthFacade>,
}

impl AuthenticationSelection {
    /// Attach the composed per-actual-source CSRF origins (explicit
    /// cookie-source chains only) to the V1 state.
    pub(super) fn apply_origins(&self, state: bcs_api_http::ApiState) -> bcs_api_http::ApiState {
        match &self.origins {
            Some(origins) => state.with_trusted_browser_origins(origins.clone()),
            None => state,
        }
    }
}

/// Split the V1 selection outcome into the pieces the server state and
/// routers consume: the final principal verifier (ONE Arc later wrapped
/// by the Stateful outer gate), the composed CSRF origins, and the
/// explicit-chain auth facade (`None` keeps the compat `[auth.oauth]`
/// mount; an OAuth-less explicit chain keeps the `/user` projection).
pub(super) fn resolve_v1_selection(
    selection: crate::api_auth_wiring::ApiAuthSelection,
    gateway_principal_verifier: Option<Arc<dyn PrincipalVerifier>>,
) -> crate::Result<(
    Arc<dyn PrincipalVerifier>,
    Option<bcs_api_http::TrustedBrowserOrigins>,
    Option<crate::api_auth_wiring::ExplicitApiAuthFacade>,
)> {
    match selection {
        crate::api_auth_wiring::ApiAuthSelection::Explicit(built) => {
            let facade = match (&built.auth_service, &built.public_base_url) {
                (Some(auth_service), Some(public_base_url)) => {
                    Some(crate::api_auth_wiring::ExplicitApiAuthFacade {
                        auth_service: Arc::clone(auth_service),
                        public_base_url: public_base_url.clone(),
                    })
                }
                _ => None,
            };
            Ok((built.verifier, built.origins, facade))
        }
        crate::api_auth_wiring::ApiAuthSelection::Compat => {
            let verifier = gateway_principal_verifier.ok_or_else(|| {
                crate::BcsError::InvalidConfig(
                    "the compat selection requires the Gateway Principal verifier, which was not built"
                        .to_string(),
                )
            })?;
            Ok((verifier, None, None))
        }
    }
}

/// Select and assemble the whole V1 authentication surface in one pass:
/// the legacy auth chain resources, and — via `api_auth_wiring` — either
/// the compat Gateway-only selection or the fully assembled explicit
/// `[api.auth]` chain. `gateway_principal_verifier` carries the compat
/// verifier (already resolved); an explicit chain ignores it and builds
/// from its own configuration.
pub(super) async fn build_authentication_selection(
    config: &BcsConfig,
    infrastructure_plugins: &InfrastructurePlugins,
    extensions: &BcsServerExtensions,
    bot_registry: &Arc<dyn BotRegistryCoreService>,
    group_session_secret_access: Arc<dyn SecretAccessPort>,
    gateway_principal_verifier: Option<Arc<dyn PrincipalVerifier>>,
) -> crate::Result<AuthenticationSelection> {
    let auth_chain = build_auth_chain_resources(
        config,
        infrastructure_plugins,
        extensions,
        bot_registry,
    )?;
    let selection = crate::api_auth_wiring::select_v1_authentication(
        config,
        auth_chain.auth_session_identity.clone(),
        group_session_secret_access,
    )
    .await?;
    let (principal_verifier, origins, explicit_api_auth) =
        resolve_v1_selection(selection, gateway_principal_verifier)?;
    Ok(AuthenticationSelection {
        auth_config: auth_chain.auth_config,
        user_identity_port: auth_chain.user_identity_port,
        auth_session_identity: auth_chain.auth_session_identity,
        auth_chain: auth_chain.auth_chain,
        principal_verifier,
        origins,
        explicit_api_auth,
    })
}

/// Sync wrapper over [`crate::api_auth_wiring::select_v1_authentication`]
/// for the in-memory server constructor paths, which run without a runtime
/// on the calling stack. An absent `[api.auth]` never spawns anything.
#[cfg(any(test, feature = "test-utils"))]
pub(super) fn select_v1_authentication_blocking(
    config: &BcsConfig,
    auth_session_identity: Option<Arc<dyn bcs_auth_api::session::AuthSessionIdentityPort>>,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<crate::api_auth_wiring::ApiAuthSelection> {
    if config.api.auth.is_none() {
        return Ok(crate::api_auth_wiring::ApiAuthSelection::Compat);
    }
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for api auth selection")
                    .block_on(crate::api_auth_wiring::select_v1_authentication(
                        config,
                        auth_session_identity,
                        secret_access,
                    ))
            })
            .join()
            .expect("api auth selection thread panicked")
    })
}

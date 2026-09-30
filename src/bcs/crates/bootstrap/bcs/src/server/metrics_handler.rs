//! server implementation.
use super::*;


pub(super) async fn metrics_handler(State(state): State<Arc<BcsServerState>>) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    metrics.refresh_on_scrape(&state).await;
    (
        [(CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")],
        metrics.render(),
    )
        .into_response()
}



pub(super) struct HttpRequestMetricsGuard {
    pub(super) metrics: Arc<crate::metrics::MetricsRuntime>,
    pub(super) route: String,
    pub(super) method: String,
    pub(super) start: Instant,
    pub(super) completed: bool,

}

impl HttpRequestMetricsGuard {

pub(super) fn new(
        metrics: Arc<crate::metrics::MetricsRuntime>,
        route: String,
        method: String,
    ) -> Self {
        Self {
            metrics,
            route,
            method,
            start: Instant::now(),
            completed: false,
        }
    }

pub(super) fn complete(mut self, status: StatusCode) {
        self.completed = true;
        self.metrics.record_http_request(
            &self.route,
            &self.method,
            status,
            self.start.elapsed(),
        );
    }
}



impl Drop for HttpRequestMetricsGuard {
    fn drop(&mut self) {
        if !self.completed {
            // Axum drops the request future when the upstream connection is
            // abandoned. This is the application-side equivalent of a proxy
            // reporting 499; there is no HTTP response status to inspect.
            self.metrics.record_http_request_cancelled(
                &self.route,
                &self.method,
                self.start.elapsed(),
            );
        }
    }
}



pub(super) async fn http_metrics_middleware(
    State(state): State<Arc<BcsServerState>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return next.run(req).await;
    };
    if req.uri().path() == metrics.endpoint_path {
        return next.run(req).await;
    }

    let method = req.method().as_str().to_string();
    let route = req
        .extensions()
        .get::<MatchedPath>()
        .map(|matched| matched.as_str().to_string())
        .unwrap_or_else(|| "unmatched".to_string());
    let guard = HttpRequestMetricsGuard::new(metrics, route, method);
    let response = next.run(req).await;
    let status = response.status();
    guard.complete(status);
    response
}



/// BCS server state.
pub struct BcsServerState {
    /// Configuration.
    pub config: BcsConfig,

    /// Services bundle.
    pub services: Services,

    /// Run channel manager for routing events back to clients (legacy, fallback).
    pub run_channels: Arc<RunChannelManager>,

    /// Bot socket sender registry owned by the WebSocket adapter.
    pub bot_connections: Arc<BotConnectionRegistry>,

    /// Workbench frontend sender registry owned by the WebSocket adapter.
    pub frontend_connections: Arc<WorkbenchConnectionRegistry>,

    /// Run-channel registry owned by the WebSocket adapter.
    pub frontend_run_channels: Arc<RunChannelManager>,

    /// Coordination echo deduplication store shared by bot WebSocket reconnects.
    pub coordination_processed: Arc<Mutex<std::collections::HashMap<String, u64>>>,

    /// Leader election port used by health and lifecycle.
    pub leader_election: Arc<dyn LeaderElectionPort>,

    /// Production lifecycle orchestrator for services with explicit startup/shutdown.
    pub lifecycle: Arc<Mutex<LifecycleOrchestrator>>,

    /// bcsfuse HTTP client (present when bcsfuse integration is enabled).
    pub fuse_client: Option<Arc<FuseClient>>,

    /// Provider credential repo used by HTTP auth adapter token resolution.
    pub provider_credentials: Arc<dyn ProviderCredentialRepoPort>,

    /// Runtime gray list controlling provider 2.0 SSE rollout by bot creator.
    pub provider_stream_gray_list: Arc<ProviderStreamGrayList>,

    /// Host-mounted channel provider HTTP ingress routes.
    pub channel_http_ingress: Option<Arc<ChannelHttpIngressRegistry>>,

    /// Snapshot port for low-cardinality group metrics.
    pub group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality group session metrics.
    pub group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality bot metrics.
    pub bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort>,

    /// Snapshot port for low-cardinality direct chat run metrics.
    pub direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort>,

    /// Optional Prometheus metrics runtime.
    pub metrics: Option<Arc<crate::metrics::MetricsRuntime>>,

    /// Auth plugin chain (built once at startup; shared by HTTP state and WS upgrade).
    pub auth_chain: Arc<bcs_auth_api::AuthPluginChain>,

    /// Auth chain configuration.
    pub auth_config: bcs_auth_api::AuthConfig,

    /// Gateway-signed Principal verifier retained for the V1 HTTP adapter composition.
    pub gateway_principal_verifier: Arc<dyn PrincipalVerifier>,

    /// Invite-token HMAC secret resolved once for every HTTP surface.
    pub invite_token_secret: Vec<u8>,

    /// Completed V1 HTTP adapter state assembled from the same runtime services as legacy HTTP.
    pub openapi_v1: ApiState,

    /// Bot attributes application service owned by the Provider-scoped HTTP adapter.
    pub internal_bot_attributes_service: Arc<dyn bcs_service_api::InternalBotAttributesService>,

    /// Configured secret source used for the session-bound Workbench credential.
    pub group_session_secret_access: Arc<dyn SecretAccessPort>,

    /// Shared OAuth identity port (used to build `/auth/*` route state).
    pub user_identity_port: Option<Arc<dyn bcs_auth_api::UserIdentityPort>>,

    /// User-controlled outbound HTTP URL security policy.
    pub outbound_url_guard: OutboundUrlGuard,

    /// Process-local organization-admin invocation callback associations.
    pub admin_invocation_runs: Arc<AdminInvocationStore>,

    /// Edge-permission ConnectService (real DB-backed impl in the production
    /// path; Noop in the in-memory/dev path). Injected into the HTTP adapter
    /// `HttpAppState` for the `/friends/*` connect endpoints.
    pub connect_service: Arc<dyn bcs_service_api::application::ConnectService>,

    /// Edge-permission AdmissionService (real DB-backed impl in the production
    /// path; Noop in the in-memory/dev path). Injected into the HTTP adapter
    /// `HttpAppState` for collaboration authorization decisions.
    pub admission_service: Arc<dyn bcs_service_api::application::AdmissionService>,
}



impl std::fmt::Debug for BcsServerState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BcsServerState")
            .field("config", &self.config)
            .field("services", &"<Services>")
            .field("run_channels", &"<RunChannelManager>")
            .field("bot_connections", &"<BotConnectionRegistry>")
            .field("frontend_connections", &"<WorkbenchConnectionRegistry>")
            .field("frontend_run_channels", &"<RunChannelManager>")
            .field("coordination_processed", &"<CoordinationProcessed>")
            .field("leader_election", &"<LeaderElectionPort>")
            .field("lifecycle", &"<LifecycleOrchestrator>")
            .field("provider_credentials", &"<ProviderCredentialRepoPort>")
            .field("provider_stream_gray_list", &"<ProviderStreamGrayList>")
            .field("channel_http_ingress", &self.channel_http_ingress.is_some())
            .field("group_metrics_snapshot", &"<GroupMetricsSnapshotPort>")
            .field(
                "group_session_metrics_snapshot",
                &"<GroupSessionMetricsSnapshotPort>",
            )
            .field("bot_metrics_snapshot", &"<BotMetricsSnapshotPort>")
            .field("direct_chat_run_snapshot", &"<DirectChatRunSnapshotPort>")
            .field("metrics", &"<MetricsRuntime>")
            .field("auth_chain", &"<AuthPluginChain>")
            .field("auth_config", &self.auth_config)
            .field("gateway_principal_verifier", &"<PrincipalVerifier>")
            .field("invite_token_secret", &"<redacted>")
            .field("openapi_v1", &"<ApiState>")
            .field(
                "internal_bot_attributes_service",
                &"<InternalBotAttributesService>",
            )
            .field("group_session_secret_access", &"<SecretAccessPort>")
            .field("outbound_url_guard", &self.outbound_url_guard)
            .field("connect_service", &"<ConnectService>")
            .field("admission_service", &"<AdmissionService>")
            .finish()
    }
}



/// Optional composition-root extensions supplied by an embedding binary.
///
/// Public startup uses `Default`; internal binaries can inject implementations
/// of the public plugin contracts without adding private SDKs to this crate.
#[derive(Clone, Default)]
pub struct BcsServerExtensions {
    pub auth_plugin_factories: Vec<AuthPluginFactory>,
    pub llm_provider: Option<Arc<dyn LlmChatCompletionPort>>,
    pub user_directory_plugin: Option<Arc<dyn UserDirectoryPlugin>>,
    pub leader_election: Option<LeaderElectionRegistration>,
}



#[derive(Clone)]
pub(super) struct ProviderRepoBundle {
    pub(super) bot_providers: Arc<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort>,
    pub(super) provider_repo: Arc<dyn ProviderRepoPort>,
    pub(super) provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
    pub(super) provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    pub(super) organization_candidates: Arc<dyn bcs_service_api::OrganizationCandidateReadPort>,
}



pub(super) fn memory_provider_repos(bots: Arc<dyn bcs_service_api::BotRepoPort>, source: bcs_domain::bot_provider::DownlinkDetectionSource) -> ProviderRepoBundle {
    let store = Arc::new(MemoryProviderStore::new());
    let bot_providers = Arc::new(bcs_bot_store::provider::MemoryBotProviderStore::new(bots, store.clone()));
    let provider_bindings = Arc::new(bcs_bot_store::provider::ProviderBindingProjection::new(store.clone(), bot_providers.clone(), source));
    ProviderRepoBundle {
        bot_providers,
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings,
        organization_candidates: store,
    }
}



pub(super) fn db_sql_flavor(db_kind: &DbPluginKind) -> DbSqlFlavor {
    match db_kind {
        DbPluginKind::LocalSqlite => DbSqlFlavor::Sqlite,
        DbPluginKind::Mysql => DbSqlFlavor::Mysql,
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no SQL flavor wiring",
                provider
            )
        }
    }
}



pub(super) fn db_provider_repos(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    source: bcs_domain::bot_provider::DownlinkDetectionSource,
) -> ProviderRepoBundle {
    let store = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbProviderStore::sqlite(db_plugin)),
        DbPluginKind::Mysql => Arc::new(DbProviderStore::mysql(db_plugin)),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no provider store wiring",
                provider
            )
        }
    };
    let provider_bindings = Arc::new(bcs_bot_store::provider::ProviderBindingProjection::new(store.clone(), store.clone(), source));
    ProviderRepoBundle {
        bot_providers: store.clone(),
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings,
        organization_candidates: store,
    }
}



pub(super) fn memory_organization_services(
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> = Arc::new(MemoryOrganizationRepo::new());
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}



pub(super) fn db_organization_services(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbOrganizationStore::sqlite(db_plugin.clone())),
        DbPluginKind::Mysql => Arc::new(DbOrganizationStore::mysql(db_plugin.clone())),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no organization store wiring",
                provider
            )
        }
    };
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}



pub(super) fn build_organization_services(
    organization_repo: Arc<dyn OrganizationRepoPort>,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_core: Arc<dyn OrganizationCoreService> = Arc::new(OrganizationCore::new(
        crate::env::resolve_env(),
        organization_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
        provider_repos.organization_candidates.clone(),
        bot_registry,
    ));
    let organization_management: Arc<dyn OrganizationManagementService> = Arc::new(
        OrganizationManagement::new(provider_core, organization_core.clone()),
    );
    (organization_core, organization_management)
}



pub(super) fn build_provider_services_with_webhook_url_guard(
    repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn bcs_service_api::RelationCoreService>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    webhook_url_guard: OutboundUrlGuard,
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    bot_catalog_cleanup: Arc<dyn BotCatalogCleanupPort>,
) -> (
    Arc<dyn ProviderCoreService>,
    Arc<dyn ProviderBotCoreService>,
    Arc<dyn ProviderManagementService>,
) {
    let provider_core_impl = Arc::new(ProviderCore::new_with_webhook_url_guard(
        repos.provider_repo.clone(),
        repos.provider_credentials.clone(),
        repos.provider_bindings.clone(),
        registry.clone(),
        webhook_url_guard,
    ).with_bot_provider_repo(repos.bot_providers.clone()));
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core_impl;
    let mut provider_management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry,
        relation,
    )
    .with_channel_binding_cleanup(channel_binding_cleanup)
    .with_bot_catalog_cleanup(bot_catalog_cleanup);
    if let Some(user_directory) = user_directory {
        provider_management = provider_management.with_user_directory(user_directory);
    }
    provider_management = provider_management.with_control_plane(control_plane);
    let provider_management: Arc<dyn ProviderManagementService> = Arc::new(provider_management);
    (provider_core, provider_bot_core, provider_management)
}



pub(super) fn create_user_directory_plugin(
    config: &BcsConfig,
) -> crate::Result<Option<Arc<dyn UserDirectoryPlugin>>> {
    let directory = &config.user_directory;
    if !directory.enabled {
        return Ok(None);
    }

    let provider = directory
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "user_directory.provider is required when user_directory.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = directory
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();
    build_registered_user_directory(config, provider, provider_config)?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "user_directory provider '{provider}' is not available in this binary"
            ))
        })
        .map(|registration| Some(registration.plugin))
}



pub(super) fn create_provider_stream_gray_list(config: &BcsConfig) -> Arc<ProviderStreamGrayList> {
    let entries = config.provider_stream_gray_created_by.clone();
    if config.provider_stream_gray_enabled {
        Arc::new(ProviderStreamGrayList::new(entries))
    } else {
        Arc::new(ProviderStreamGrayList::new_disabled(entries))
    }
}



pub(super) fn outbound_url_guard_from_config(config: &BcsConfig) -> OutboundUrlGuard {
    let policy = &config.security.outbound_url;
    OutboundUrlGuard::new(policy.block_private_networks, policy.allow_loopback)
}



pub(super) fn gateway_principal_signing_key(material: Option<&str>) -> crate::Result<&str> {
    material
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig("Gateway Principal signing key is required".to_string())
        })
}



pub(super) fn build_gateway_principal_verifier(
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



pub(super) fn group_session_test_secret_access(config: &BcsConfig) -> Arc<dyn SecretAccessPort> {
    Arc::new(InMemorySecretAccess::with_entries([(
        config
            .group_session_ws
            .signing_key_secret
            .trim()
            .to_string(),
        String::new(),
        GROUP_SESSION_WS_TEST_SIGNING_KEY.to_string(),
    )]))
}



pub(super) fn build_secret_access_blocking(config: &BcsConfig) -> crate::Result<Arc<dyn SecretAccessPort>> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for secret provider build")
                    .block_on(crate::http_adapter::build_secret_access(config))
            })
            .join()
            .expect("secret provider build thread panicked")
    })
}



pub(super) async fn build_group_session_token_port(
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionTokenPort>> {
    let secret_name = config.signing_key_secret.trim();
    let secret = secret_access.get_secret(secret_name).await.map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' is required"
        ))
    })?;
    let tokens = GroupSessionJwtService::new(&secret.value).map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' must resolve to non-empty material"
        ))
    })?;
    Ok(Arc::new(tokens))
}



pub(super) async fn build_group_session_connection_service(
    sessions: Arc<dyn bcs_service_api::application::v1::SessionService>,
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionConnectionService>> {
    let tokens = build_group_session_token_port(config, secret_access).await?;
    Ok(Arc::new(GroupSessionConnectionServiceImpl::new(
        sessions, tokens,
    )))
}



pub(super) fn build_invite_code_service(
    config: &BcsConfig,
    db_plugin: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_kind: Option<&DbPluginKind>,
    invite_token_secret: Vec<u8>,
) -> Arc<dyn bcs_service_api::application::v1::InviteCodeService> {
    let repo: Arc<dyn bcs_service_api::port::repo::InviteCodeRepoPort> = match (db_plugin, db_kind) {
        (Some(db_plugin), Some(db_kind)) => match db_kind {
            DbPluginKind::LocalSqlite => Arc::new(DbInviteCodeStore::sqlite(db_plugin)),
            DbPluginKind::Mysql => Arc::new(DbInviteCodeStore::mysql(db_plugin)),
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no invite-code store wiring",
                    provider
                )
            }
        },
        _ => Arc::new(MemoryInviteCodeRepo::with_data_dir(config.bots_base_dir.clone())),
    };
    Arc::new(InviteCodeServiceImpl::new(
        repo,
        invite_token_secret,
        config.invite.public_claim_max_count,
    ))
}



pub(super) async fn resolve_secret_value(
    name: Option<&str>,
    access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    let Some(name) = name.map(str::trim).filter(|v| !v.is_empty()) else { return Ok(None); };
    let record = access.get_secret(name).await.map_err(|e| crate::BcsError::InvalidConfig(format!("{field} '{name}' unavailable: {e}")))?;
    if record.value.trim().is_empty() { return Err(crate::BcsError::InvalidConfig(format!("{field} '{name}' is empty"))); }
    Ok(Some(record.value))
}



pub(super) async fn resolve_token_secret_secret(
    secret_key: Option<&str>,
    secret_access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    resolve_secret_value(secret_key, secret_access, field).await
}



pub(super) fn resolve_invite_token_secret(config: &BcsConfig) -> Vec<u8> {
    config
        .invite
        .token_secret
        .as_deref()
        .map(str::as_bytes)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            tracing::warn!(
                "invite.token_secret not configured — generating random secret (tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        })
}



pub(super) struct CandidateSearchBindings {
    pub(super) worker_profiles: Arc<dyn WorkerProfileCoreService>,
    pub(super) legacy: Arc<dyn BotCandidateSearchCoreService>,
    pub(super) openapi_v1: Arc<dyn BotCandidateSearchCoreService>,
}



pub(super) fn build_candidate_search_bindings(
    config: &BcsConfig,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    fuse_client: Option<Arc<FuseClient>>,
) -> CandidateSearchBindings {
    let worker_profiles: Arc<dyn WorkerProfileCoreService> = match fuse_client {
        Some(client) => Arc::new(FuseWorkerProfileService::new(client)),
        None => Arc::new(EmptyWorkerProfileCoreService),
    };
    let candidate_search: Arc<dyn BotCandidateSearchCoreService> =
        Arc::new(BotCandidateSearchCore::new(
            registry,
            friends,
            worker_profiles.clone(),
            config.bcsfuse.recommend_min_score,
        ));

    CandidateSearchBindings {
        worker_profiles,
        legacy: candidate_search.clone(),
        openapi_v1: candidate_search,
    }
}

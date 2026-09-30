//! Server state types (BcsServerState, BcsServerExtensions, BcsServer).
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

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

    /// Strict `AuthSessionIdentityPort` over the SAME store instance as
    /// `user_identity_port` (Task 11, spec §8.5: shared records must not
    /// have two independent writers). `None` only when no identity store is
    /// wired (contract-test states).
    pub auth_session_identities: Option<Arc<dyn bcs_auth_api::AuthSessionIdentityPort>>,

    /// The strict OAuth session engine (JWT signing + CAS install/rotate/
    /// revoke) built once at startup when `[auth.oauth]` carries a secret.
    /// Both OAuth entrypoints' login/refresh/logout drive sessions through
    /// it; integration tests seed sessions through the same engine.
    pub oauth_session_engine: Option<Arc<bcs_auth_oauth::OAuthSessionEngine>>,

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

    /// Task 12: the assembled `[api.auth]` chain — composite verifier, V1
    /// OAuth login facade (when an OAuth source is in chain), and CSRF
    /// origins. `Some` only when `[api.auth]` is ACTIVE. Published once,
    /// after all source builds succeeded (a build failure aborts startup
    /// before this field exists), and honored by BOTH the V1 ApiState and
    /// the standalone group-session connection-token router.
    pub built_api_auth: Option<crate::api_auth_wiring::BuiltApiAuth>,
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
            .field("built_api_auth", &self.built_api_auth.as_ref().map(|b| b.sources.join(",")))
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

#[allow(clippy::too_many_arguments)]
pub(super) fn build_openapi_v1_state(
    config: &BcsConfig,
    invite_token_secret: Vec<u8>,
    control_plane_repo: Arc<dyn BotControlPlaneRepoPort>,
    provider_repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    friends: Arc<dyn FriendCoreService>,
    candidate_search: Arc<dyn BotCandidateSearchCoreService>,
    friend_requests: Arc<dyn FriendRequestCoreService>,
    relation: Arc<dyn RelationCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    session_launch: Arc<dyn bcs_service_api::SessionLaunchService>,
    group_management: Arc<dyn GroupManagementService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    judge_available: bool,
    session_repo: Arc<dyn SessionRepoPort>,
    group_message_history: Arc<dyn GroupMessageHistoryService>,
    session_files: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
    system_message: Arc<dyn SystemMessageService>,
    bot_management: Arc<dyn bcs_service_api::BotManagementService>,
    bot_onboarding: Arc<dyn bcs_service_api::BotOnboardingService>,
    collaboration_templates: Arc<dyn CollaborationTemplateService>,
    invite_code_service: Arc<dyn bcs_service_api::application::v1::InviteCodeService>,
    invite_code_gate_enabled: bool,
    public_invite_code_claim_enabled: bool,
    principal_verifier: Arc<dyn PrincipalVerifier>,
    connect_service: Arc<dyn bcs_service_api::application::ConnectService>,
    participant_view_bindings: Arc<dyn bcs_service_api::port::ParticipantViewBindingPort>,
    event_subscription_service: Arc<dyn bcs_service_api::application::v1::EventSubscriptionService>,
    group_event_subscription_provisioner: Arc<
        dyn bcs_service_api::application::v1::GroupEventSubscriptionProvisioner,
    >,
) -> (
    ApiState,
    Arc<dyn bcs_service_api::InternalBotAttributesService>,
) {
    let relation_env = crate::env::resolve_env();
    let provider_registration = Arc::new(bcs_bot::core::provider_registration::ProviderRegistrationCore::new(
        provider_repos.provider_repo.clone(),
        provider_repos.provider_credentials.clone(),
        provider_repos.provider_bindings.clone(),
        provider_repos.bot_providers.clone(),
        registry.clone(), relation.clone(), relation_env.clone(),
        config.openapi_v1.registration_self_service_provider_ids.clone(),
        outbound_url_guard_from_config(config),
    ));
    let control_plane = Arc::new(BotControlPlaneCore::new(
        control_plane_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
    ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
    let bot_service = Arc::new(BotServiceImpl::new(
        control_plane.clone(),
        registry.clone(),
        friends.clone(),
        connect_service.clone(),
        candidate_search,
        BotServiceConfig {
            env: relation_env.clone(),
        },
    ));
    let internal_bot_attributes_service = Arc::new(InternalBotAttributesServiceImpl::new(
        control_plane,
        BotServiceConfig {
            env: relation_env.clone(),
        },
    ));
    let mut group_service = GroupServiceImpl::new(
        groups.clone(),
        registry.clone(),
        friends.clone(),
        relation.clone(),
        sessions.clone(),
        group_management,
        GroupServiceConfig {
            relation_env: relation_env.clone(),
        },
    )
    .with_participant_view_bindings(participant_view_bindings.clone())
    .with_collaboration_runtime(collaboration_runtime.clone());
    if config.eventing.enabled {
        group_service =
            group_service.with_event_subscription_provisioner(group_event_subscription_provisioner);
    }
    let group_service = Arc::new(group_service);
    let session_service = Arc::new(
        SessionServiceImpl::new(
            session_launch,
            sessions.clone(),
            groups.clone(),
            registry.clone(),
            friends.clone(),
            relation,
            session_repo,
            group_message_history,
            collaboration_runtime.clone(),
            system_message.clone(),
            SessionServiceConfig { relation_env },
        )
        .with_participant_view_bindings(participant_view_bindings),
    );
    let session_file_url_projector = SessionFileUrlProjector::new(
        config
            .openapi_v1
            .validated_internal_collaboration_base_url()
            .expect("OpenAPI V1 internal collaboration URL was validated at config load"),
    )
    .expect("validated OpenAPI V1 session-file URL projector");
    let session_file_service = Arc::new(SessionFileApplicationServiceImpl::new(
        session_files,
        sessions.clone(),
        groups.clone(),
        registry.clone(),
        system_message.clone(),
        Arc::new(session_file_url_projector.clone()),
    ));
    let invitation_groups = groups.clone();
    let invitation_sessions = sessions.clone();
    let invite: Arc<dyn InviteService> =
        Arc::new(bcs_group::application::invite::InviteServiceImpl {
            registry: registry.clone(),
            group: groups,
            session: sessions,
            system_message,
            token_secret: invite_token_secret.clone(),
            default_ttl_seconds: config.invite.default_ttl_seconds,
            base_url: config.invite.base_url.clone(),
            group_link_url: config.invite.group_link_url.clone(),
            session_link_url: config.invite.session_link_url.clone(),
        });
    let invitation_service = Arc::new(
        InvitationFriendshipServiceImpl::new(
            friends,
            friend_requests,
            invitation_groups,
            invitation_sessions,
            registry,
            invite,
            invite_token_secret.clone(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: config.invite.default_ttl_seconds,
            },
        )
        .with_friend_connection_service(connect_service),
    );
    let register_service: Arc<dyn bcs_service_api::application::v1::RegisterService> =
        Arc::new(bcs_app_register::RegisterServiceImpl::new(
            bot_management,
            bot_onboarding,
            invite_token_secret.clone(),
        ).with_provider_registration(provider_registration));
    let collaboration_template_service: Arc<
        dyn bcs_service_api::application::v1::CollaborationTemplateService,
    > = Arc::new(V1CollaborationTemplateServiceImpl::new(
        collaboration_templates,
    ));
    let collaboration_definition_service: Arc<
        dyn bcs_service_api::application::v1::CollaborationDefinitionService,
    > = Arc::new(V1CollaborationDefinitionServiceImpl::new(
        collaboration_runtime.clone(),
        judge_available,
    ));

    (
        ApiState::new(
            group_service,
            session_service.clone(),
            session_service,
            invitation_service.clone(),
            register_service,
            invitation_service.clone(),
            principal_verifier,
        )
        .with_bot_service(bot_service)
        .with_invite_code_service(invite_code_service)
        .with_invite_code_gate_enabled(invite_code_gate_enabled)
        .with_public_invite_code_claim_enabled(public_invite_code_claim_enabled)
        .with_friend_connection_service(invitation_service)
        .with_session_file_service(session_file_service, session_file_url_projector)
        .with_event_subscription_service(event_subscription_service)
        .with_collaboration_template_service(collaboration_template_service)
        .with_collaboration_definition_service(collaboration_definition_service)
        .with_collaboration_runtime_service(collaboration_runtime.clone())
        .with_manifest_config(
            crate::config_loader::Environment::resolve()
                .as_str()
                .to_string(),
            config.manifest.clone(),
        ),
        internal_bot_attributes_service,
    )
}

impl BcsServerState {
    /// Create a default state for testing.
    #[cfg(test)]
    pub fn default_for_test() -> Self {
        let mut config = BcsConfig::default();
        config.bots_base_dir =
            std::env::temp_dir().join(format!("bcs-default-state-test-{}", uuid::Uuid::new_v4()));
        Arc::try_unwrap(BcsServer::new_allowing_private_outbound_for_tests(config).state)
            .expect("test server state has one owner")
    }
}

/// BCS server.
pub struct BcsServer {
    pub(super) config: BcsConfig,
    pub(super) state: Arc<BcsServerState>,
}

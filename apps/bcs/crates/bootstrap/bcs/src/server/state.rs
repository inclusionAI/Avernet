//! Extracted verbatim from `server.rs`: composition-root state, its constructors, and the BcsServer struct.
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

    /// Strict Session authorization snapshot Core (bot-authority
    /// plan Task 7): shared by the V1 connect authorization
    /// and the final-writer delivery authorization.
    pub session_authorization_core:
        Arc<dyn bcs_service_api::core::SessionAuthorizationCoreService>,

    /// Operation-boundary delivery admission (spec §8.2 — 已准入
    /// 输出不追溯): consulted once per accepted operation and per
    /// independent resource event; permits are frozen at admission.
    pub delivery_admission:
        Arc<dyn bcs_service_api::application::v1::DeliveryAdmissionService>,

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

    /// Rate limiter for `/ws/bot` upgrades; `None` when admission control is disabled.
    pub bot_ws_admission: Option<Arc<bcs_ws::bot::BotWsAdmission>>,

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
            .field("session_authorization_core", &"<SessionAuthorizationCoreService>")
            .field("delivery_admission", &"<DeliveryAdmissionService>")
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

#[cfg(any(test, feature = "test-utils"))]
impl Default for BcsServerState {
    fn default() -> Self {
        let config = BcsConfig::default();
        let group_session_secret_access = build_secret_access_blocking(&config)
            .expect("Secret provider configuration must be valid");
        let invite_token_secret = resolve_invite_token_secret(&config);
        let gateway_principal_verifier =
            build_gateway_principal_verifier_from_process(&config.gateway_principal)
                .expect("Gateway Principal verifier configuration must be valid");
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        // The SAME shared memory state is the authority
        // Repo facet (one Arc: authority/profile/Provider
        // register/registration share it, spec §7).
        let bot_authority_repo = crate::bot_authority_wiring::memory_authority_repo(
            &bot_repo,
            config.bot_authority.legacy_created_by_fallback,
        );
        let provider_repos = memory_provider_repos(bot_repo.clone(), config.provider_http.downlink_detection_source);
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc: Arc<BotCore> = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();
        // F.1/F.2 dual-write wiring: relation store must be created BEFORE
        // friend_store and provider_management so it can be injected into both.
        let relation_store: Arc<RelationCore> = Arc::new(RelationCore::memory());
        let user_directory =
            create_user_directory_plugin(&config).expect("default user directory config is valid");
        let provider_control_plane: Arc<dyn BotControlPlaneCoreService> =
            Arc::new(BotControlPlaneCore::new(
                control_plane_repo.clone(),
                provider_repos.provider_repo.clone(),
                provider_repos.provider_bindings.clone(),
            ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let channel_binding_cleanup = Arc::new(DeferredChannelBindingCleanupPort::default());
        let (provider_core, provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry.clone(),
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>,
                user_directory.clone(),
                outbound_url_guard.clone(),
                provider_control_plane.clone(),
                channel_binding_cleanup.clone(),
                build_bot_catalog_cleanup(&config),
            );
        let (organization_core, organization_management) = memory_organization_services(
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );
        let event_repo = crate::eventing_wiring::memory_event_repo();
        let group_event_factory =
            crate::eventing_wiring::event_record_factory(&config, event_repo.clone());
        let group_repo = Arc::new(
            MemoryGroupRepo::new().with_event_store(event_repo.clone(), crate::env::resolve_env()),
        );
        if let Some(store) = &provider_repos.memory_store {
            store.bind_group_scope_source(group_repo.clone(), Arc::downgrade(&provider_repos.bot_providers));
        }
        let group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort> = group_repo.clone();
        let group_repo_for_session: Arc<dyn GroupRepoPort> = group_repo.clone();
        let sessions = Arc::new(
            GroupCore::with_repo(group_repo).with_collaboration_registry(bot_registry.clone()).with_policy_gate(provider_repos.memory_store.as_ref().expect("memory Provider store").policy_gate()).with_event_record_factory(group_event_factory.clone()),
        );
        let router = Arc::new(MessageRouter::new());
        let fusion = Arc::new(LocalFusionService::new(config.bots_base_dir.clone()));
        let proposals = Arc::new(ProposalStore::new());
        let friend_repo = Arc::new(MemoryFriendRepo::new());
        let friend_store: Arc<FriendCore> =
            Arc::new(FriendCore::with_repo(friend_repo).with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            ));
        let friend_request_store: Arc<FriendRequestCore> = Arc::new(FriendRequestCore::with_repo(
            Arc::new(MemoryFriendRequestRepo::new()),
            friend_store.clone(),
            bot_registry.clone(),
        ));
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let invite_code_service =
            build_invite_code_service(&config, None, None, invite_token_secret.clone());
        let bot_authority = crate::bot_authority_wiring::build(
            bot_authority_repo,
            &config,
            invite_code_service.clone(),
            config.invite.invite_code_gate_enabled,
            crate::env::resolve_env().as_str(),
        )
        .expect("bot authority trusted-app scope must be valid");

        let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend_store.clone())
            .with_bot_authority(bot_authority.hook.clone())
            .with_uplink_config(config.uplink.clone())
            .with_legacy_created_by_fallback(
                config.bot_authority.legacy_created_by_fallback,
            )
            .with_bot_core(bot_core_arc.clone())
            .with_organization(organization_core.clone())
            .with_relation(relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>)
            .with_connection_control(
                bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>
            );
        if let Some(user_directory) = user_directory.clone() {
            bot_use_cases = bot_use_cases.with_user_directory(user_directory);
        }
        let bot_use_cases = Arc::new(bot_use_cases);
        let output_permits = Arc::new(bcs_ws::shared::OutputPermitStore::new());
        let frontend_connections = Arc::new(
            WorkbenchConnectionRegistry::with_bot_query(bot_use_cases.clone())
                .with_scope_changes_enabled(
                    !config
                        .leader_election
                        .as_ref()
                        .is_some_and(|leader_election| leader_election.enabled),
                )
                .with_output_permits(output_permits.clone()),
        );
        let run_channels = Arc::new(
            RunChannelManager::new()
                .with_output_permits(output_permits)
                .with_connection_retirement(frontend_connections.clone()),
        );
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(outbound_url_guard.clone())
                .with_chat_run_timeout_ms(config.provider_chat_run_timeout_ms),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(ws_bot_delivery, provider_transport.clone()),
        );
        let bot_delivery = maybe_wrap_bot_delivery(&config, raw_bot_delivery);
        let raw_frontend_delivery: Arc<dyn FrontendDeliveryPort> =
            Arc::new(WorkbenchFrontendDelivery::new(
                frontend_connections.clone(),
                frontend_run_channels.clone(),
            ));
        let frontend_delivery = maybe_wrap_frontend_delivery(&config, raw_frontend_delivery);
        let interceptors =
            create_interceptor_chain(&config).expect("default security gateway config is valid");
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let session_repo = Arc::new(MemorySessionRepo::new().with_event_store(event_repo.clone()));
        // Strict authorization snapshot read (bot-authority plan
        // Task 7): the narrow Session snapshot Core plus the
        // operation-boundary delivery admission built over the same
        // Group Core snapshot read.
        let session_authorization_core: Arc<
            dyn bcs_service_api::core::SessionAuthorizationCoreService,
        > = Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
            session_repo.clone(),
        ));
        let delivery_admission: Arc<
            dyn bcs_service_api::application::v1::DeliveryAdmissionService,
        > = Arc::new(bcs_app_session::DeliveryAdmissionServiceImpl::new(
            sessions.clone(),
            session_authorization_core.clone(),
            Some(bot_authority.hook.clone()),
        ));
        frontend_connections.set_delivery_admission(delivery_admission.clone());
        let output_admission: Arc<dyn bcs_service_api::OutputAdmissionPort> =
            Arc::new(bcs_ws::web::WorkbenchOutputAdmission::new(
                frontend_connections.clone(),
                delivery_admission.clone(),
            ));
        let message_repo: Arc<dyn MessageRepoPort> =
            Arc::new(MemoryMessageRepo::new().with_event_store(event_repo.clone()).with_session_registry(session_repo.session_registry()));
        let group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort> =
            session_repo.clone();
        let session_management: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo_for_session.clone())
                .with_collaboration_registry(bot_registry.clone())
                .with_bot_runtime(bot_use_cases.clone())
                .with_event_record_factory(group_event_factory.clone())
                .with_opening_message_delivery(message_repo.clone(), frontend_delivery.clone()),
        );
        let bot_run_context: Arc<dyn BotRunContextPort> =
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new());
        let session_file_service = build_session_files_service_blocking(
            &config,
            crate::env::resolve_env(),
            None,
            None,
            session_repo.clone(),
        );
        let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
            config.async_chat_run_max_entries,
        ));
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)
            .expect("metrics runtime must initialize");
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_store.clone(),
                a2a_run_port.clone(),
                a2a_run_port.clone(),
            )
            .with_organization(organization_core.clone())
            .with_session_management(session_management.clone())
            .with_interceptors(interceptors.clone())
            .with_run_lifecycle_hook(direct_chat_run_lifecycle_hook(metrics.as_ref()))
            .with_bot_run_context(bot_run_context.clone())
            .with_bot_authority_hook(Some(bot_authority.hook.clone())),
        );
        let a2a_chat: Arc<dyn A2aChatService> = a2a_chat_impl.clone();
        let a2a_chat_runs: Arc<dyn A2aChatRunService> = a2a_chat_impl.clone();
        let a2a_chat_runs = maybe_wrap_a2a_chat_runs(&config, a2a_chat_runs);
        let direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort> = a2a_chat_impl.clone();
        let proposal_base_url = config
            .bcs_endpoint
            .clone()
            .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));
        let interaction_terminal_observer = Arc::new(InteractionTerminalObserver::default());
        let terminal_observer: Arc<dyn BotTerminalObserverPort> =
            Arc::new(CompositeBotTerminalObserver::new(vec![
                interaction_terminal_observer.clone(),
                Arc::new(AdminInvocationTerminalObserver::new(
                    admin_invocation_runs.clone(),
                    outbound_url_guard.clone(),
                )),
            ]));
        let state_machine_terminal_observer =
            Arc::new(DeferredStateMachineTerminalObserver::new(terminal_observer));
        let coordination_intents = create_coordination_intents(Arc::new(bcs_cache_local::InMemoryCachePlugin::new()));
        let message_flow_builder = create_message_flow_builder(
            coordination_intents.clone(),
            bot_registry.clone(),
            sessions.clone(),
            router.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            config.max_group_messages,
            interceptors.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            Some(message_repo.clone()),
            provider_stream_gray_list.clone(),
            state_machine_terminal_observer.clone(),
            config.provider_chat_run_timeout_ms,
            config.eventing.enabled.then(|| group_event_factory.clone()),
            crate::eventing_wiring::event_recorder(&config, event_repo.clone()),
            Arc::new(bcs_service_api::port::NoopHumanMentionNotifyPort),
            bot_authority.hook.clone(),
            Some(output_admission.clone()),
        );
        let pending_messages = message_flow_builder
            .pending_message_port()
            .expect("message flow pending reader requires run context");
        let group_message_history = create_group_message_history_service(
            sessions.clone(),
            bot_registry.clone(),
            bot_delivery.clone(),
            Arc::clone(&bot_connections),
            provider_transport.clone(),
            message_repo.clone(),
            session_repo.clone(),
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            config.message_history.new_participant_visible_limit,
            config.message_history.default_page_limit,
            config.message_history.max_page_limit,
            session_file_service.clone(),
            pending_messages,
            config.session_files.share.history_attachment_ttl_seconds,
            config.state_machine_history.persistence_enabled,
            config.message_history.state_machine_cutoff_timestamp,
            bot_authority.hook.clone(),
        );
        let system_message: Arc<dyn bcs_service_api::SystemMessageService> = {
            let dispatcher = SystemMessageDispatcherImpl::builder()
                .with_queue(message_flow_builder.system_queue_port())
                .with_registry(bot_registry.clone())
                .with_delivery(bot_delivery.clone())
                .with_frontend_delivery(frontend_delivery.clone())
                .with_bot_run_context(bot_run_context.clone())
                .with_provider_chat_run_timeout_ms(config.provider_chat_run_timeout_ms)
                .with_message_repo(message_repo.clone())
                .with_provider_stream_gray_list(provider_stream_gray_list.clone())
                .register(BotJoinedMessageProducer::new(group_message_history.clone()))
                .register(HumanJoinedMessageProducer::new())
                .register(ParticipantModeChangedMessageProducer)
                .register(GenericNotificationMessageProducer)
                .register(BotLeftMessageProducer)
                .register(SessionContextMessageProducer)
                .register(BotHiddenNoticeProducer)
                .build()
                .expect("system message dispatcher must be fully wired");
            Arc::new(SystemMessageServiceImpl::new(
                Arc::new(dispatcher),
                sessions.clone(),
            ))
        };
        let (message_flow, channel_slot) =
            finalize_message_flow(message_flow_builder, system_message.clone());
        let group_management_impl = Arc::new(
            GroupManagement::new(
                sessions.clone(),
                bot_registry.clone(),
                friend_store.clone(),
                relation_store.clone(),
                GroupConfig {
                    max_group_members: config.max_group_members,
                    max_groups_as_driver: config.max_groups_as_driver,
                    max_groups_as_member: config.max_groups_as_member,
                    relation_env: crate::env::resolve_env(),
                },
                session_management.clone(),
                system_message.clone(),
            )
            .with_channel_binding_cleanup(channel_binding_cleanup.clone())
            .with_participant_view_bindings(frontend_connections.clone())
            .with_outbound_url_guard(outbound_url_guard.clone())
            .with_bot_runtime(bot_use_cases.clone())
            .with_bot_authority(bot_authority.hook.clone())
            .with_session_authorization(session_authorization_core.clone()),
        );
        let group_proposals = Arc::new(GroupProposalUseCases::new(
            sessions.clone(),
            bot_registry.clone(),
            friend_store.clone(),
            proposals.clone(),
            session_management.clone(),
            system_message.clone(),
            GroupProposalUseCasesConfig {
                max_group_members: config.max_group_members,
                max_groups_as_driver: config.max_groups_as_driver,
                max_groups_as_member: config.max_groups_as_member,
                proposal_base_url,
                botchat_base_url: config.botchat_url.clone(),
            },
        ).with_bot_authority_hook(bot_authority.hook.clone()));
        let group_fusion = Arc::new(BcsGroupFusion::new(sessions.clone(), fusion.clone()));
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        let interactions = create_interaction_service(
            provider_transport.clone(),
            bot_connections.clone(),
            group_management_impl.clone(),
            frontend_delivery.clone(),
            config.async_chat_run_retention_ms,
        );
        interaction_terminal_observer.set_service(interactions.clone());
        let collaboration_store = Arc::new(
            MemoryCollaborationStore::new()
                .with_event_store(event_repo.clone())
                .with_session_repo(session_repo.clone()),
        );
        let judge_evaluator: Arc<dyn JudgeEvaluatorPort> = Arc::new(NoopJudgeEvaluator::default());
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
        let collaboration_runtime = Arc::new(
            CollaborationRuntime::new(
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store.clone(),
                collaboration_store,
                sessions.clone(),
                session_management.clone(),
                bot_delivery.clone(),
                judge_evaluator,
            )
            .with_bot_registry(bot_registry.clone())
            .with_bot_run_context(bot_run_context.clone())
            .with_provider_chat_run_timeout_ms(config.provider_chat_run_timeout_ms)
            .with_fixed_loop_limits(config.collaboration.fixed_loop_limits)
            .with_loop_execution_enabled(config.collaboration.loop_execution_enabled)
            .with_nested_loop_execution_enabled(config.collaboration.nested_loop_execution_enabled)
            .with_history_persistence(config.state_machine_history.persistence_enabled)
            .with_history_cutoff_timestamp(config.message_history.state_machine_cutoff_timestamp)
            .with_loop_instrumentation(state_machine_loop_instrumentation(metrics.as_ref()))
            .with_callback_url_guard(outbound_url_guard.clone())
            .with_session_channel_outbound(session_channel_outbound)
            .with_result_publisher(Arc::new(MessageFlowStateMachineResultPublisher::new(
                message_flow.clone(),
            )))
            .with_message_repo(message_repo.clone())
            .with_frontend_delivery(frontend_delivery.clone())
            .with_event_record_factory(crate::eventing_wiring::event_record_factory(
                &config,
                event_repo.clone(),
            )),
        );
        state_machine_terminal_observer.bind(collaboration_runtime.clone());
        let session_management = Arc::new(SessionManagementWithRuntimeCleanup::new(
            session_management.clone(),
            collaboration_runtime.clone(),
        ));
        let session_launch = Arc::new(SessionLaunchApplication::new(
            bot_registry.clone(),
            sessions.clone(),
            session_management.clone(),
            collaboration_runtime.clone(),
            system_message.clone(),
        )
        .with_bot_authority_hook(bot_authority.hook.clone()));
        let group_management = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                group_management_impl.clone(),
                collaboration_runtime.clone(),
            )),
        );
        let group_management_v1 = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                Arc::new((*group_management_impl).clone().for_v1_openapi()),
                collaboration_runtime.clone(),
            )),
        );
        let candidate_search = build_candidate_search_bindings(
            &config,
            bot_registry.clone(),
            friend_store.clone(),
            None,
        );
        let actor_directory: Arc<dyn bcs_service_api::ActorDirectoryService> =
            Arc::new(bcs_bot::ActorDirectory::new(
                bot_registry.clone(),
                friend_store.clone(),
                relation_store.clone(),
                candidate_search.worker_profiles,
                candidate_search.legacy,
            )
            .with_bot_authority_hook(bot_authority.hook.clone()));

        let collaboration_templates = build_standalone_collaboration_template_service(&config);
        let eventing_runtime = build_eventing_runtime_blocking(
            &config,
            event_repo,
            sessions.clone(),
            session_management.clone(),
            collaboration_runtime.clone(),
            bot_registry.clone(),
            bot_authority.hook.clone(),
            false,
        )
        .expect("default Eventing configuration must initialize");
        let (openapi_v1, internal_bot_attributes_service) = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_store.clone(),
            candidate_search.openapi_v1,
            friend_request_store,
            relation_store.clone(),
            session_management.clone(),
            session_launch.clone(),
            group_management_v1.clone(),
            collaboration_runtime.clone(),
            config.llm.is_enabled(),
            session_repo.clone(),
            group_message_history.clone(),
            session_file_service.clone(),
            system_message.clone(),
            collaboration_templates.clone(),
            invite_code_service.clone(),
            config.invite.invite_code_gate_enabled,
            config.invite.public_claim_enabled,
            gateway_principal_verifier.clone(),
            Arc::new(bcs_test_support::NoopConnectService),
            frontend_connections.clone(),
            eventing_runtime.service.clone(),
            eventing_runtime.group_provisioner.clone(),
            bot_authority.service.clone(),
            bot_authority.hook.clone(),
            bot_authority.core.clone(),
        );
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_binding_cleanup,
            session_channel_outbound_slot,
            memory_channel_repos(None),
            session_repo.clone(),
            message_flow.clone(),
            system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )
        .expect("default channel runtime must initialize");
        let channel_service = channel_runtime.service.clone();
        // Only mount the OpenAPI channel surface when the bridge is enabled.
        // When disabled, `channel_runtime.service` is `DisabledChannelService`
        // whose set_binding_status/update_binding_config/delete_binding all
        // return Ok(()) without persisting — mounting it would make PATCH/DELETE
        // falsely 200 for any binding id. Leaving the slot unset makes the
        // handlers fail-closed as 500 internal_error instead.
        let openapi_v1 = if channel_bridge_enabled(&config) {
            openapi_v1.with_channel_service(channel_service.clone())
        } else {
            openapi_v1
        };
        let provider_bot_events_impl = Arc::new(
            ProviderBotEvents::new(
                provider_bot_core.clone(),
                bot_run_context.clone(),
                message_flow.clone(),
            )
            .with_coordination_intents(coordination_intents.clone())
            .with_collaboration_runtime(collaboration_runtime.clone()),
        );
        let provider_event_ingest: Arc<dyn bcs_service_api::ProviderEventIngestService> =
            provider_bot_events_impl.clone();
        let provider_bot_events: Arc<dyn ProviderBotEventService> = provider_bot_events_impl;
        provider_transport.set_ingest(provider_event_ingest, bot_run_context.clone());
        let group_context_service: Arc<dyn bcs_service_api::GroupContextApplicationService> =
            Arc::new(bcs_app_session::GroupContextAddAccessGuard::new(
                Arc::new(bcs_app_group_context::GroupContextApplicationServiceImpl::new(
                    Arc::new(bcs_group_context_store::MemoryGroupContextRepo::new()),
                )),
                sessions.clone(),
                session_repo.clone(),
            ));
        let services = ServicesBuilder::default()
            .registry(bot_registry.clone())
            .bot_authority(bot_authority.service.clone())
            .bot_authority_hook(bot_authority.hook.clone())
            .group(sessions)
            .routing(router)
            .fusion(fusion)
            .proposal(proposals)
            .friend(friend_store)
            .relation(relation_store)
            .bot_delivery(bot_delivery)
            .bot_run_context(bot_run_context)
            .frontend_delivery(frontend_delivery)
            .message_flow(message_flow)
            .interactions(interactions)
            .group_message_history(group_message_history)
            .group_context(group_context_service)
            .a2a_chat(a2a_chat)
            .a2a_chat_runs(a2a_chat_runs)
            .collaboration_runtime(collaboration_runtime)
            .collaboration_templates(collaboration_templates)
            .actor_directory(actor_directory)
            .bot_query(bot_use_cases.clone())
            .bot_management(bot_use_cases.clone())
            .bot_runtime(bot_use_cases.clone())
            .bot_discovery(bot_use_cases)
            .provider_core(provider_core)
            .provider_bot_core(provider_bot_core)
            .provider_management(provider_management)
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(group_management.clone())
            .group_query(group_management_impl.clone())
            .workbench_sessions(group_management_impl)
            .group_proposals(group_proposals)
            .group_fusion(group_fusion)
            .system_message(system_message)
            .session_management(session_management.clone())
            .session_launch(session_launch)
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .session_files(session_file_service)
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        // Start JWT token expiry scanner
        let _token_expiry_handle = crate::token_expiry_scanner::spawn(
            bot_connections.clone(),
            services.bot_runtime.clone(),
            crate::token_expiry_scanner::DEFAULT_SCAN_INTERVAL,
        );

        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

        let (leader_election, lifecycle) = create_standalone_leader_lifecycle();
        register_eventing_lifecycles(
            &lifecycle,
            eventing_runtime.lifecycle.as_ref(),
            eventing_runtime.provisioning_lifecycle.as_ref(),
        );
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);

        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        let user_identity_port = Some(crate::identity_wiring::memory_user_identity_port());
        let auth_chain = Arc::new(crate::auth_wiring::build_auth_chain(
            &auth_config,
            bot_registry.clone(),
            user_identity_port.clone(),
        ));
        let bot_ws_admission = config.bot_ws_admission.build();

        Self {
            config,
            services,
            run_channels,
            bot_connections,
            frontend_connections,
            frontend_run_channels,
            session_authorization_core,
            delivery_admission,
            coordination_processed: Arc::new(Mutex::new(std::collections::HashMap::new())),
            leader_election,
            lifecycle,
            fuse_client: None,
            provider_credentials: provider_repos.provider_credentials.clone(),
            provider_stream_gray_list,
            channel_http_ingress: channel_runtime.http_ingress.clone(),
            group_metrics_snapshot,
            group_session_metrics_snapshot,
            bot_metrics_snapshot,
            direct_chat_run_snapshot,
            metrics,
            auth_chain,
            auth_config,
            bot_ws_admission,
            gateway_principal_verifier: openapi_v1.principal_verifier.clone(),
            invite_token_secret,
            openapi_v1,
            internal_bot_attributes_service,
            group_session_secret_access,
            user_identity_port,
            outbound_url_guard,
            admin_invocation_runs,
            connect_service: Arc::new(bcs_test_support::NoopConnectService),
            admission_service: Arc::new(bcs_test_support::NoopAdmissionService),
        }
    }
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

//! Construction wiring helpers (pre-impl BcsServer).
//! Split out from server.rs's bootstrap helpers.

//! BcsServer construction: impl BcsServer methods and bootstrap orchestration.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

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

/// Create fusion service: bcsfuse HTTP delegation or local fallback.
pub(super) fn create_fusion_service(
    config: &BcsConfig,
) -> (
    Arc<dyn bcs_service_api::FusionCoreService>,
    Option<Arc<FuseClient>>,
) {
    if config.bcsfuse.enabled {
        match FuseClientService::new(&config.bcsfuse, &config.bots_base_dir) {
            Ok(svc) => {
                info!(url = %config.bcsfuse.url, "bcsfuse integration enabled");
                let shared_client = svc.client();
                (Arc::new(svc), Some(shared_client))
            }
            Err(e) => {
                warn!(error = %e, "Failed to create FuseClientService, falling back to local fusion");
                (
                    Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
                    None,
                )
            }
        }
    } else {
        (
            Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
            None,
        )
    }
}

pub(super) fn lifecycle_with_leader<L>(
    name: &'static str,
    leader: Arc<L>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
)
where
    L: LeaderElectionPort + ServiceLifecycle + 'static,
{
    let leader_election: Arc<dyn LeaderElectionPort> = leader.clone();
    let lifecycle_service: Arc<dyn ServiceLifecycle> = leader;
    let mut lifecycle = LifecycleOrchestrator::new();
    lifecycle.register(name, lifecycle_service);
    (leader_election, Arc::new(Mutex::new(lifecycle)))
}

/// Register FuseClientLifecycle (and any other late-bound lifecycle adapters)
/// onto the orchestrator. Must run after fuse_client is constructed but
/// before BcsServer::run begins driving initialize_all/shutdown_all.
///
/// Sync helper — orchestrator is freshly built and has zero contention at
/// this point, so try_lock always succeeds. Avoids polluting the call sites
/// with async/await chains.
pub(super) fn register_late_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    fuse_client: Option<&Arc<FuseClient>>,
) {
    if let Some(client) = fuse_client {
        let adapter = Arc::new(bcs_fusion::FuseClientLifecycle::new(client.clone()));
        // try_lock cannot fail here: the orchestrator has just been built and
        // is not yet shared with any other task.
        let mut guard = lifecycle
            .try_lock()
            .expect("orchestrator should be uncontended at registration time");
        guard.register("fuse_client", adapter as Arc<dyn ServiceLifecycle>);
    }
}

pub(super) struct UseCaseBundle {
    pub(super) actor_directory: Arc<dyn bcs_service_api::ActorDirectoryService>,
    pub(super) candidate_search: Arc<dyn BotCandidateSearchCoreService>,
    pub(super) friend_use_cases: Arc<dyn bcs_service_api::FriendService>,
    pub(super) human_actors: Arc<dyn bcs_service_api::HumanActorService>,
    pub(super) bot_onboarding: Arc<dyn bcs_service_api::BotOnboardingService>,
    pub(super) bot_query: Arc<dyn bcs_service_api::BotQueryService>,
    pub(super) bot_management: Arc<dyn bcs_service_api::BotManagementService>,
    pub(super) bot_runtime: Arc<dyn bcs_service_api::BotRuntimeConnectionService>,
    pub(super) bot_discovery: Arc<dyn bcs_service_api::BotDiscoveryService>,
    pub(super) group_management: Arc<dyn bcs_service_api::GroupManagementService>,
    /// Dedicated `for_v1_openapi` twin of `group_management`, wired only into
    /// the OpenAPI V1 facade so V1 create-group runs the driver-anchored
    /// core branch while legacy HTTP keeps the legacy branch.
    pub(super) group_management_v1: Arc<dyn bcs_service_api::GroupManagementService>,
    pub(super) group_query: Arc<dyn bcs_service_api::GroupQueryService>,
    pub(super) workbench_sessions: Arc<dyn bcs_service_api::WorkbenchSessionService>,
    pub(super) interaction_authorization: Arc<dyn CanResolveInteraction>,
    pub(super) group_proposals: Arc<dyn bcs_service_api::GroupProposalService>,
    pub(super) group_fusion: Arc<dyn bcs_service_api::GroupFusionService>,
    pub(super) system_message: Arc<dyn bcs_service_api::SystemMessageService>,
}

pub(super) fn build_use_case_bundle(
    config: &BcsConfig,
    system_queue: Arc<dyn bcs_service_api::application::system_message::SystemMessageQueueService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    bot_core: Arc<BotCore>,
    organization_core: Arc<dyn OrganizationCoreService>,
    bot_connection_control: Arc<dyn bcs_service_api::BotConnectionControlPort>,
    group: Arc<dyn GroupCoreService>,
    proposal: Arc<dyn bcs_service_api::ProposalCoreService>,
    friend: Arc<dyn bcs_service_api::FriendCoreService>,
    friend_request: Arc<dyn bcs_service_api::FriendRequestCoreService>,
    relation: Arc<dyn bcs_service_api::RelationCoreService>,
    provider_control_plane: Arc<dyn BotControlPlaneCoreService>,
    edge_grants: Option<Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>>,
    fuse_client: Option<Arc<FuseClient>>,
    fusion: Arc<dyn bcs_service_api::FusionCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    group_message_history: Arc<dyn GroupMessageHistoryService>,
    session_management: Arc<dyn SessionManagementService>,
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    participant_view_bindings: Arc<dyn bcs_service_api::port::ParticipantViewBindingPort>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    callback_url_guard: OutboundUrlGuard,
    provider_stream_gray_list: Arc<ProviderStreamGrayList>,
    profile_store: Arc<dyn bcs_service_api::port::repo::PermissionProfileRepoPort>,
) -> UseCaseBundle {
    let candidate_search =
        build_candidate_search_bindings(config, bot_registry.clone(), friend.clone(), fuse_client);
    let actor_directory = bcs_bot::ActorDirectory::new(
        bot_registry.clone(),
        friend.clone(),
        relation.clone(),
        candidate_search.worker_profiles,
        candidate_search.legacy,
    );

    let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend.clone())
        .with_uplink_config(config.uplink.clone())
        .with_bot_core(bot_core.clone())
        .with_control_plane(provider_control_plane.clone())
        .with_organization(organization_core.clone())
        .with_relation(relation.clone())
        .with_connection_control(bot_connection_control.clone());
    if let Some(edge_grants) = edge_grants {
        bot_use_cases = bot_use_cases.with_edge_grants(edge_grants);
    }
    if let Some(user_directory) = user_directory {
        bot_use_cases = bot_use_cases.with_user_directory(user_directory);
    }
    let bot_use_cases = Arc::new(bot_use_cases);
    let system_message: Arc<dyn bcs_service_api::SystemMessageService> = {
        let mut disp_builder = SystemMessageDispatcherImpl::builder()
            .with_queue(system_queue)
            .with_registry(bot_registry.clone())
            .with_delivery(bot_delivery.clone())
            .with_frontend_delivery(frontend_delivery.clone())
            .with_bot_run_context(bot_run_context)
            .with_provider_chat_run_timeout_ms(config.provider_chat_run_timeout_ms)
            .with_provider_stream_gray_list(provider_stream_gray_list.clone())
            .register(BotJoinedMessageProducer::new(group_message_history.clone()))
            .register(HumanJoinedMessageProducer::new())
            .register(ParticipantModeChangedMessageProducer)
            .register(GenericNotificationMessageProducer)
            .register(BotLeftMessageProducer)
            .register(SessionContextMessageProducer)
            .register(BotHiddenNoticeProducer);
        if let Some(repo) = &message_repo {
            disp_builder = disp_builder.with_message_repo(repo.clone());
        }
        let dispatcher = disp_builder
            .build()
            .expect("system message dispatcher must be fully wired");
        Arc::new(SystemMessageServiceImpl::new(
            Arc::new(dispatcher),
            group.clone(),
        ))
    };
    let group_management = Arc::new(
        GroupManagement::new(
            group.clone(),
            bot_registry.clone(),
            friend.clone(),
            relation.clone(),
            GroupConfig {
                max_group_members: config.max_group_members,
                max_groups_as_driver: config.max_groups_as_driver,
                max_groups_as_member: config.max_groups_as_member,
                relation_env: crate::env::resolve_env(),
            },
            session_management.clone(),
            system_message.clone(),
        )
        .with_channel_binding_cleanup(channel_binding_cleanup)
        .with_participant_view_bindings(participant_view_bindings)
        .with_outbound_url_guard(callback_url_guard.clone())
        .with_bot_runtime(bot_use_cases.clone()),
    );
    let group_management_v1: Arc<dyn bcs_service_api::GroupManagementService> =
        Arc::new((*group_management).clone().for_v1_openapi());
    let proposal_base_url = config
        .bcs_endpoint
        .clone()
        .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));
    let group_proposals = Arc::new(GroupProposalUseCases::new(
        group.clone(),
        bot_registry.clone(),
        friend.clone(),
        proposal,
        session_management,
        system_message.clone(),
        GroupProposalUseCasesConfig {
            max_group_members: config.max_group_members,
            max_groups_as_driver: config.max_groups_as_driver,
            max_groups_as_member: config.max_groups_as_member,
            proposal_base_url,
            botchat_base_url: config.botchat_url.clone(),
        },
    ));

    UseCaseBundle {
        actor_directory: Arc::new(actor_directory),
        candidate_search: candidate_search.openapi_v1,
        friend_use_cases: Arc::new(bcs_friend::Friend::new(
            bot_registry.clone(),
            friend,
            friend_request,
            relation.clone(),
        )),
        human_actors: Arc::new(bcs_bot::HumanActor::new(
            bot_registry.clone(),
            relation.clone(),
        )),
        bot_onboarding: Arc::new(
            bcs_bot::BotOnboarding::new(
                bot_registry,
                relation,
                config.onboard_binding_enabled,
                config.default_visibility.clone(),
            )
            .with_profiles(profile_store),
        ),
        bot_query: bot_use_cases.clone(),
        bot_management: bot_use_cases.clone(),
        bot_runtime: bot_use_cases.clone(),
        bot_discovery: bot_use_cases,
        group_management: group_management.clone(),
        group_management_v1,
        group_query: group_management.clone(),
        workbench_sessions: group_management.clone(),
        interaction_authorization: group_management,
        group_proposals,
        group_fusion: Arc::new(BcsGroupFusion::new(group, fusion)),
        system_message,
    }
}

pub(super) fn create_interaction_service(
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
    authorization: Arc<dyn CanResolveInteraction>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    terminal_retention_ms: u64,
) -> Arc<dyn InteractionService> {
    let store = Arc::new(MemoryInteractionStore::new());
    let interaction_frontend = Arc::new(bcs_ws::web::WorkbenchInteractionDelivery::new(
        frontend_delivery,
    ));
    let interactions: Arc<dyn InteractionService> = Arc::new(InteractionManagement::new(
        store,
        authorization,
        provider_transport.clone(),
        interaction_frontend,
        terminal_retention_ms,
    ));
    provider_transport.set_interactions(interactions.clone());
    interactions
}

pub(super) fn create_coordination_intents(cache: Arc<dyn bcs_cache_api::CachePlugin>) -> Option<Arc<dyn bcs_service_api::port::CoordinationIntentPort>> {
    Some(Arc::new(bcs_coordination_store::CoordinationCacheStore::new(cache)))
}

pub(super) fn create_message_flow_builder(
    coordination_intents: Option<Arc<dyn bcs_service_api::port::CoordinationIntentPort>>,
    registry: Arc<dyn BotRegistryCoreService>,
    group: Arc<dyn GroupCoreService>,
    routing: Arc<dyn RoutingCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    bot_relay_turn_limit: i64,
    interceptors: Arc<InterceptorChain>,
    session_management: Arc<dyn SessionManagementService>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    provider_stream_gray_list: Arc<ProviderStreamGrayList>,
    bot_terminal_observer: Arc<dyn BotTerminalObserverPort>,
    provider_chat_run_timeout_ms: u64,
    event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
    event_recorder: Arc<dyn EventRecorderPort>,
    human_mention_notify: Arc<dyn bcs_service_api::port::HumanMentionNotifyPort>,
) -> BcsMessageFlow {
    let mut message_flow = BcsMessageFlow::new(
        group,
        routing,
        registry,
        bot_delivery.clone(),
        frontend_delivery.clone(),
    )
    .with_coordination_intents(coordination_intents)
    .with_bot_relay_turn_limit(bot_relay_turn_limit)
    .with_interceptors(interceptors)
    .with_session_management(session_management)
    .with_bot_run_context(bot_run_context)
    .with_provider_chat_run_timeout_ms(provider_chat_run_timeout_ms)
    .with_provider_stream_gray_list(provider_stream_gray_list)
    .with_bot_terminal_observer(bot_terminal_observer)
    .with_event_recorder(event_recorder)
    .with_human_mention_notify(human_mention_notify);
    if let Some(factory) = event_record_factory {
        message_flow = message_flow.with_event_record_factory(factory);
    }
    if let Some(repo) = message_repo {
        message_flow = message_flow.with_message_repo(repo);
    }
    message_flow
}

pub(super) fn finalize_message_flow(
    message_flow: BcsMessageFlow,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
) -> (Arc<dyn MessageFlowService>, ChannelSlot) {
    let message_flow = message_flow.with_system_message(system_message);
    let channel_slot = message_flow.channel_slot();
    let message_flow = Arc::new(message_flow);
    message_flow.retain_terminal_events();
    (message_flow, channel_slot)
}

pub(super) fn create_interceptor_chain(config: &BcsConfig) -> crate::Result<Arc<InterceptorChain>> {
    let mut chain = InterceptorChain::new();

    #[cfg(feature = "prometheus-metrics")]
    {
        if config.metrics.enabled {
            chain.set_block_hook(Arc::new(
                crate::metrics::MetricsDeliveryPolicyBlockHook::new(Arc::from(
                    bcs_config::resolve_env_str(),
                )),
            ));
        }
    }

    let sg = &config.security_gateway;
    let provider = sg.provider.trim();
    let gateway: Arc<dyn SecurityGatewayPort> = if provider.is_empty() || provider == "noop" {
        info!(
            provider = "noop",
            dry_run = sg.dry_run,
            "Initializing noop security gateway interceptor"
        );
        Arc::new(NoopSecurityGateway)
    } else {
        let provider_config = sg.providers.get(provider).cloned().unwrap_or_default();
        build_registered_security_gateway(config, provider, provider_config)?
            .ok_or_else(|| {
                crate::BcsError::InvalidConfig(format!(
                    "security_gateway provider '{provider}' is not available in this binary"
                ))
            })?
            .gateway
    };

    chain.push(SecurityInterceptor::new(gateway, sg.dry_run));

    Ok(Arc::new(chain))
}

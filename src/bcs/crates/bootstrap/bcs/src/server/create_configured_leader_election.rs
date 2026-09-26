//! server implementation.
use super::*;


pub(super) async fn create_configured_leader_election(
    config: &BcsConfig,
) -> Result<Option<LeaderElectionRegistration>> {
    let Some(election) = config.leader_election.as_ref() else {
        return Ok(None);
    };
    if !election.enabled {
        return Ok(None);
    }

    let provider = election
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "leader_election.provider is required when leader_election.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = election
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();

    build_registered_leader_election(config, provider, provider_config)
        .await?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "leader_election provider '{provider}' is not available in this binary"
            ))
        })
        .map(Some)
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



pub(super) fn register_channel_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    channel_lifecycles: &[Arc<dyn ServiceLifecycle>],
) {
    if channel_lifecycles.is_empty() {
        return;
    }
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    for (idx, service) in channel_lifecycles.iter().enumerate() {
        let name = match idx {
            0 => "channel_provider",
            1 => "channel_provider_1",
            2 => "channel_provider_2",
            _ => "channel_provider_extra",
        };
        guard.register(name, service.clone());
    }
}



pub(super) fn register_eventing_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    eventing_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
    provisioning_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
) {
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    if let Some(service) = eventing_lifecycle {
        guard.register("eventing", service.clone());
    }
    if let Some(service) = provisioning_lifecycle {
        guard.register("group-provisioning-reconciler", service.clone());
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



pub(super) struct DeferredStateMachineTerminalObserver {
    pub(super) runtime: std::sync::RwLock<Option<Arc<dyn CollaborationRuntimeService>>>,
    pub(super) next: Arc<dyn BotTerminalObserverPort>,

}

impl DeferredStateMachineTerminalObserver {

pub(super) fn new(next: Arc<dyn BotTerminalObserverPort>) -> Self {
        Self {
            runtime: std::sync::RwLock::new(None),
            next,
        }
    }

pub(super) fn bind(&self, runtime: Arc<dyn CollaborationRuntimeService>) {
        *self
            .runtime
            .write()
            .expect("terminal observer lock poisoned") = Some(runtime);
    }
}



#[async_trait]
impl BotTerminalObserverPort for DeferredStateMachineTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        self.next.observe(event.clone()).await;
        let Some(runtime) = self
            .runtime
            .read()
            .expect("terminal observer lock poisoned")
            .clone()
        else {
            return;
        };
        let state = match event.state {
            BotTerminalState::Final => bcs_service_api::ChatEventState::Final,
            BotTerminalState::Error => bcs_service_api::ChatEventState::Error,
            BotTerminalState::Aborted => bcs_service_api::ChatEventState::Aborted,
        };
        let payload = serde_json::json!({
            "state": match event.state {
                BotTerminalState::Final => "final",
                BotTerminalState::Error => "error",
                BotTerminalState::Aborted => "aborted",
            },
            "message": {
                "content": [{"type": "text", "text": event.text.clone()}]
            },
            "run_id": event.run_id.clone(),
        });
        tokio::spawn(async move {
            if let Err(error) = runtime
                .handle_bot_terminal_event(HandleBotTerminalEventCommand {
                    bot_id: event.bot_uuid,
                    run_id: event.run_id,
                    event_type: "chat.event".to_string(),
                    event_payload: payload,
                    state,
                    bcs_session_id: None,
                })
                .await
            {
                warn!(error = %error, "state-machine terminal observer failed");
            }
        });
    }
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



#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum JudgeLlmProviderKind {
    None,
    OpenAiCompatible,
    Anthropic,
}



pub(super) fn select_judge_llm_provider(config: &BcsConfig) -> crate::Result<JudgeLlmProviderKind> {
    match &config.llm.provider_type {
        LlmProviderType::None => Ok(JudgeLlmProviderKind::None),
        LlmProviderType::OpenAiCompatible => Ok(JudgeLlmProviderKind::OpenAiCompatible),
        LlmProviderType::Anthropic => Ok(JudgeLlmProviderKind::Anthropic),
        LlmProviderType::Other(provider) => Err(crate::BcsError::InvalidConfig(format!(
            "llm.type = '{}' is not available in this binary",
            provider
        ))),
    }
}



pub(super) fn create_public_judge_evaluator(config: &BcsConfig) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
    match select_judge_llm_provider(config)? {
        JudgeLlmProviderKind::None => Ok(Arc::new(NoopJudgeEvaluator::default())),
        JudgeLlmProviderKind::OpenAiCompatible => {
            let llm_config = resolve_llm_config(config);
            let llm_client =
                OpenAiCompatibleLlmClient::new(llm_config.clone()).map_err(|error| {
                    crate::BcsError::InvalidConfig(format!("invalid llm config: {error}"))
                })?;
            info!(
                model = %llm_config.model,
                base_url = %llm_config.base_url,
                structured_output = ?llm_config.structured_output,
                "OpenAI-compatible LLM judge enabled"
            );
            Ok(Arc::new(LlmJudgeService::new(
                Arc::new(llm_client),
                llm_config.model.clone(),
            )))
        }
        JudgeLlmProviderKind::Anthropic => {
            let llm_config = resolve_llm_config(config);
            let llm_client = AnthropicLlmClient::new(llm_config.clone()).map_err(|error| {
                crate::BcsError::InvalidConfig(format!("invalid llm config: {error}"))
            })?;
            info!(
                model = %llm_config.model,
                base_url = %llm_config.base_url,
                structured_output = ?llm_config.structured_output,
                "Anthropic LLM judge enabled"
            );
            Ok(Arc::new(LlmJudgeService::new(
                Arc::new(llm_client),
                llm_config.model.clone(),
            )))
        }
    }
}



pub(super) fn create_judge_evaluator(
    config: &BcsConfig,
    extensions: &BcsServerExtensions,
) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
    if let Some(llm_provider) = extensions.llm_provider.clone() {
        let llm_config = resolve_llm_config(config);
        info!(
            model = %llm_config.model,
            "Injected LLM judge provider enabled"
        );
        return Ok(Arc::new(LlmJudgeService::new(
            llm_provider,
            llm_config.model.clone(),
        )));
    }

    if let LlmProviderType::Other(provider) = &config.llm.provider_type {
        if let Some(llm_provider) = build_registered_llm_provider(config, provider)? {
            let llm_config = resolve_llm_config(config);
            info!(
                provider = %provider,
                model = %llm_config.model,
                "Registered LLM judge provider enabled"
            );
            return Ok(Arc::new(LlmJudgeService::new(
                llm_provider,
                llm_config.model.clone(),
            )));
        }
    }

    create_public_judge_evaluator(config)
}



pub(super) fn resolve_llm_config(config: &BcsConfig) -> LlmConfig {
    let mut llm_config = config.llm.clone();
    if llm_config
        .api_key
        .as_ref()
        .is_some_and(|api_key| api_key.expose_secret().trim().is_empty())
    {
        llm_config.api_key = None;
    }
    if llm_config.api_key.is_none() {
        if let Some(env_name) = llm_config
            .api_key_env
            .as_ref()
            .map(|env_name| env_name.trim())
            .filter(|env_name| !env_name.is_empty())
        {
            if let Ok(api_key) = std::env::var(env_name) {
                if !api_key.trim().is_empty() {
                    llm_config.api_key = Some(Secret::new(api_key));
                }
            }
        }
    }
    llm_config
}



pub(super) fn create_group_message_history_service(
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    bot_connections: Arc<BotConnectionRegistry>,
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
    message_repo: Arc<dyn MessageRepoPort>,
    session_repo: Arc<dyn SessionRepoPort>,
    cutoff_timestamp: u64,
    manager_worker_cutoff_timestamp: u64,
    new_participant_visible_limit: u64,
    default_page_limit: u32,
    max_page_limit: u32,
    session_file: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
    pending_messages: Arc<dyn bcs_service_api::PendingGroupMessagePort>,
    history_attachment_ttl: u64,
    persisted_state_machine_history: bool,
    state_machine_cutoff_timestamp: u64,
) -> Arc<dyn GroupMessageHistoryService> {
    let websocket_request: Arc<dyn GroupHistoryBotRequestPort> =
        Arc::new(BootstrapGroupHistoryBotRequestPort { bot_connections });
    let bot_request: Arc<dyn GroupHistoryBotRequestPort> = Arc::new(
        bcs_provider_http::HistoryRequestMux::new(websocket_request, provider_transport),
    );
    let fallback: Arc<dyn GroupMessageHistoryService> = Arc::new(BcsGroupMessageHistory::new(
        group.clone(),
        registry.clone(),
        bot_delivery,
        bot_request,
    ));
    Arc::new(MessageService::new(
        message_repo,
        fallback,
        session_repo,
        group,
        registry,
        session_file,
        pending_messages,
        cutoff_timestamp,
        manager_worker_cutoff_timestamp,
        new_participant_visible_limit,
        default_page_limit,
        max_page_limit,
        history_attachment_ttl,
    ).with_persisted_state_machine_history(persisted_state_machine_history, state_machine_cutoff_timestamp))
}



pub(super) fn maybe_wrap_bot_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn BotDeliveryPort>,
) -> Arc<dyn BotDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsBotDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}



pub(super) fn maybe_wrap_frontend_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn FrontendDeliveryPort>,
) -> Arc<dyn FrontendDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsFrontendDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}



pub(super) fn maybe_wrap_group_management(
    _config: &BcsConfig,
    service: Arc<dyn GroupManagementService>,
) -> Arc<dyn GroupManagementService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsGroupManagementService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}



pub(super) fn maybe_wrap_message_flow(
    _config: &BcsConfig,
    service: Arc<dyn MessageFlowService>,
) -> Arc<dyn MessageFlowService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedMessageFlowService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}



pub(super) fn maybe_wrap_a2a_chat_runs(
    _config: &BcsConfig,
    service: Arc<dyn A2aChatRunService>,
) -> Arc<dyn A2aChatRunService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedA2aChatRunService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}



pub(super) struct BootstrapGroupHistoryBotRequestPort {
    pub(super) bot_connections: Arc<BotConnectionRegistry>,
}



#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for BootstrapGroupHistoryBotRequestPort {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> std::result::Result<serde_json::Value, String> {
        let BotDeliveryTarget::WebSocket { bot_id } = target else {
            return Err("history request target is not a websocket bot".to_string());
        };
        self.bot_connections
            .send_request(&bot_id, method, params, timeout_ms)
            .await
    }

}

impl BcsServer {

/// Create a new BCS server.
    pub fn new(config: BcsConfig) -> Self {
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let group_session_secret_access = build_secret_access_blocking(&config)
            .expect("Secret provider configuration must be valid");
        let gateway_principal_verifier = std::thread::scope(|scope| {
            scope
                .spawn(|| {
                    tokio::runtime::Runtime::new()
                        .expect("temp runtime for Gateway Principal verifier build")
                        .block_on(build_gateway_principal_verifier_from_secret_access(
                            &config.gateway_principal,
                            group_session_secret_access.clone(),
                        ))
                })
                .join()
                .expect("Gateway Principal verifier build thread panicked")
        })
        .expect("Gateway Principal verifier configuration must be valid");
        Self::new_with_outbound_url_guards(
            config,
            outbound_url_guard.clone(),
            outbound_url_guard.clone(),
            outbound_url_guard,
            group_session_secret_access,
            gateway_principal_verifier,
            local_eventing_endpoints_allowed(),
        )
    }

pub fn new_allowing_private_outbound_for_tests(config: BcsConfig) -> Self {
        let group_session_secret_access = group_session_test_secret_access(&config);
        Self::new_with_outbound_url_guards(
            config,
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            group_session_secret_access,
            gateway_principal_verifier_for_tests(),
            true,
        )
    }
}

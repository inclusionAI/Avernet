//! Extracted verbatim from `server.rs`: use-case bundle, message flow, judge, and delivery wrapping.
use super::*;

use bcs_service_api::port::bot_authority::BotAuthorityPort;

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
    // The narrow authority hook consumed by the Bot
    // 删除/配置/状态 judgments and the Group
    // management control legs (plan Task 6.3).
    bot_authority_hook: Arc<dyn BotAuthorityPort>,
    // The strict Session authorization snapshot Core
    // consumed by the Workbench connect/send admission
    // gates (F4, spec §8.3).
    session_authorization: Arc<dyn bcs_service_api::core::SessionAuthorizationCoreService>,
) -> UseCaseBundle {
    let candidate_search =
        build_candidate_search_bindings(config, bot_registry.clone(), friend.clone(), fuse_client);
    let actor_directory = bcs_bot::ActorDirectory::new(
        bot_registry.clone(),
        friend.clone(),
        relation.clone(),
        candidate_search.worker_profiles,
        candidate_search.legacy,
    )
    .with_bot_authority_hook(bot_authority_hook.clone());

    let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend.clone())
        .with_bot_authority(bot_authority_hook.clone())
        .with_uplink_config(config.uplink.clone())
        .with_legacy_created_by_fallback(
            config.bot_authority.legacy_created_by_fallback,
        )
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
        .with_bot_runtime(bot_use_cases.clone())
        .with_bot_authority(bot_authority_hook.clone())
        .with_session_authorization(session_authorization),
    );
    let group_management_v1: Arc<dyn bcs_service_api::GroupManagementService> =
        Arc::new((*group_management).clone().for_v1_openapi());

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
        group_fusion: Arc::new(BcsGroupFusion::new(group, fusion)),
        system_message,
    }
}

pub(super) fn create_interaction_service(
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
    bot_connections: Arc<bcs_ws::bot::BotConnectionRegistry>,
    authorization: Arc<dyn CanResolveInteraction>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    terminal_retention_ms: u64,
) -> Arc<dyn InteractionService> {
    let store = Arc::new(MemoryInteractionStore::new());
    let interaction_frontend = Arc::new(bcs_ws::web::WorkbenchInteractionDelivery::new(
        frontend_delivery,
    ));
    // bot_connections: Arc<BotConnectionRegistry> 直接传给
    // InteractionProviderMux::new 的 websocket: Arc<dyn InteractionProviderPort>
    // 参数——函数参数位置是 Rust unsize coercion 的标准触发点，不需要显式
    // `as Arc<dyn ...>` 或额外包装（BotConnectionRegistry 已直接 impl
    // InteractionProviderPort）。
    let interaction_provider: Arc<dyn bcs_service_api::InteractionProviderPort> =
        Arc::new(bcs_provider_http::InteractionProviderMux::new(
            bot_connections,
            provider_transport.clone(),
        ));
    let interactions: Arc<dyn InteractionService> = Arc::new(InteractionManagement::new(
        store,
        authorization,
        interaction_provider,
        interaction_frontend,
        terminal_retention_ms,
    ));
    provider_transport.set_interactions(interactions.clone());
    interactions
}

pub(super) struct DeferredStateMachineTerminalObserver {
    runtime: std::sync::RwLock<Option<Arc<dyn CollaborationRuntimeService>>>,
    next: Arc<dyn BotTerminalObserverPort>,
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
    execution_cache: Arc<dyn bcs_cache_api::CachePlugin>,
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
    bot_authority_hook: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
    output_admission: Option<Arc<dyn bcs_service_api::OutputAdmissionPort>>,
) -> BcsMessageFlow {
    let mut message_flow = BcsMessageFlow::new(
        group,
        routing,
        registry,
        bot_delivery.clone(),
        frontend_delivery.clone(),
    )
    .with_execution_cache(execution_cache)
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
        .with_bot_authority_hook(Some(bot_authority_hook))
        .with_output_admission(output_admission)
}

#[cfg(any(test, feature = "test-utils"))]
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

fn create_public_judge_evaluator(config: &BcsConfig) -> crate::Result<Arc<dyn JudgeEvaluatorPort>> {
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

fn resolve_llm_config(config: &BcsConfig) -> LlmConfig {
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

#[cfg(test)]
mod judge_provider_tests {
    use super::*;
    use crate::plugins::LlmProviderFactory;
    use bcs_llm_api::{LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError};
    use bcs_service_api::{JudgeArtifact, JudgeRequest};
    use serde_json::json;

    struct RecordingLlm {
        requests: Mutex<Vec<LlmChatCompletionRequest>>,
    }

    #[async_trait::async_trait]
    impl LlmChatCompletionPort for RecordingLlm {
        async fn complete(
            &self,
            request: LlmChatCompletionRequest,
        ) -> std::result::Result<LlmChatCompletionResponse, LlmError> {
            self.requests.lock().await.push(request);
            Ok(LlmChatCompletionResponse {
                content: json!({
                    "outcome": "approved",
                    "reason": "ok",
                    "confidence": 0.9,
                    "checked_criteria": [],
                    "retry_instruction": "",
                })
                .to_string(),
                raw: json!({}),
            })
        }
    }

    fn test_llm_factory(_config: BcsConfig) -> crate::Result<Arc<dyn LlmChatCompletionPort>> {
        Ok(Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        }))
    }

    inventory::submit! {
        LlmProviderFactory {
            name: "test-internal-llm",
            build: test_llm_factory,
        }
    }

    fn judge_request() -> JudgeRequest {
        JudgeRequest {
            run_id: "run-1".to_string(),
            node_id: "judge".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["must pass".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({"question": "ready?"}),
            upstream_outputs: vec![JudgeArtifact {
                node_id: "work".to_string(),
                text: "candidate output".to_string(),
            }],
            artifact_text: "candidate output".to_string(),
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_public_provider_types() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;

        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );

        config.llm.provider_type = LlmProviderType::Anthropic;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::Anthropic
        );
    }

    #[test]
    fn anthropic_llm_provider_requires_api_key() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = None;

        let error = match create_judge_evaluator(&config, &BcsServerExtensions::default()) {
            Ok(_) => panic!("anthropic provider without an API key should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("anthropic api_key is required"));
    }

    #[test]
    fn anthropic_llm_provider_builds_judge_evaluator() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("anthropic-key".to_string()));

        create_judge_evaluator(&config, &BcsServerExtensions::default())
            .expect("valid anthropic provider should build a judge evaluator");
    }

    #[tokio::test]
    async fn none_llm_without_injection_uses_noop_judge() {
        let config = BcsConfig::default();
        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");

        let error = match evaluator.judge(judge_request()).await {
            Ok(_) => panic!("noop judge should reject LLM judge requests"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("requires an enabled LLM"));
    }

    #[tokio::test]
    async fn injected_llm_provider_is_used_when_present() {
        let llm = Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        });
        let llm_provider: Arc<dyn LlmChatCompletionPort> = llm.clone();
        let mut config = BcsConfig::default();
        config.llm.model = "custom-judge-model".to_string();
        let extensions = BcsServerExtensions {
            llm_provider: Some(llm_provider),
            ..BcsServerExtensions::default()
        };

        let evaluator = create_judge_evaluator(&config, &extensions).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
        let requests = llm.requests.lock().await;
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].model, "custom-judge-model");
    }

    #[tokio::test]
    async fn registered_llm_provider_is_selected_by_type() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Other("test-internal-llm".to_string());
        config.llm.model = "registered-model".to_string();

        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
    }
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
    bot_authority_hook: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
) -> Arc<dyn GroupMessageHistoryService> {
    let websocket_request: Arc<dyn GroupHistoryBotRequestPort> =
        Arc::new(BootstrapGroupHistoryBotRequestPort { bot_connections });
    let bot_request: Arc<dyn GroupHistoryBotRequestPort> = Arc::new(
        bcs_provider_http::HistoryRequestMux::new(websocket_request, provider_transport),
    );
    let fallback: Arc<dyn GroupMessageHistoryService> = Arc::new(
        BcsGroupMessageHistory::new(
            group.clone(),
            registry.clone(),
            bot_delivery,
            bot_request,
        )
        .with_bot_authority_hook(Some(bot_authority_hook)),
    );
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

struct BootstrapGroupHistoryBotRequestPort {
    bot_connections: Arc<BotConnectionRegistry>,
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

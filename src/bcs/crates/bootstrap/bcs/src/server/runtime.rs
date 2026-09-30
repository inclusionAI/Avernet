//! Runtime helpers (state machine observer, judge provider, lifecycle hooks, dispatch state).
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

pub(super) struct MessageFlowStateMachineResultPublisher {
    pub(super) message_flow: Arc<dyn MessageFlowService>,
    pub(super) message_repo: Arc<dyn MessageRepoPort>,
}

impl MessageFlowStateMachineResultPublisher {
    pub(super) fn new(
        message_flow: Arc<dyn MessageFlowService>,
        message_repo: Arc<dyn MessageRepoPort>,
    ) -> Self {
        Self {
            message_flow,
            message_repo,
        }
    }
}

#[async_trait]
impl StateMachineResultPublisherPort for MessageFlowStateMachineResultPublisher {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        let idempotency_key = format!("state-machine-result:{}", cmd.run_id);
        let saved = self.message_repo
            .append_message_with_id(idempotency_key.clone(), NewMessage {
                group_id: cmd.group_id.clone(),
                session_id: cmd.session_id.clone(),
                sender_id: cmd.sender_bot_id.clone(),
                sender_type: SenderType::Bot,
                message_type: "chat".to_string(),
                content: serde_json::Value::String(cmd.content.clone()),
                client_msg_id: Some(idempotency_key.clone()),
                owner_bot_id: None,
                created_at: cmd.created_at_ms,
                run_id: cmd.run_id.clone(),
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
            })
            .await
            .map_err(|error| {
                bcs_service_api::ServiceError::InternalError(format!(
                    "persist state-machine result before delivery: {error}"
                ))
            })?;
        if saved.group_id != cmd.group_id || saved.session_id != cmd.session_id || saved.run_id != cmd.run_id
            || saved.sender_id != cmd.sender_bot_id || saved.sender_type != SenderType::Bot
            || saved.message_type != "chat" || saved.content != serde_json::Value::String(cmd.content.clone())
            || saved.client_msg_id.as_ref() != Some(&idempotency_key) || saved.created_at != cmd.created_at_ms
            || saved.visibility_domain != Some(MessageVisibilityDomain::StateMachine) || saved.audience != Some(MessageAudience::Public) {
            return Err(bcs_service_api::ServiceError::Conflict("Chat result history conflicts with saved publication".into()));
        }
        self.message_flow
            .handle_web_send(WebSendCommand {
                caller: CallerContext::Bot(BotActor {
                    bot_uuid: cmd.sender_bot_id.clone(),
                }),
                group_id: cmd.group_id,
                session_id: Some(cmd.session_id),
                from_actor_id: cmd.sender_bot_id,
                from_name: None,
                message: cmd.content,
                mentions: Vec::new(),
                attachments: None,
                thinking: None,
                idempotency_key: Some(idempotency_key),
                source_im_message_id: None,
                channel_sender_identity: None,
                sender_conn_id: None,
                provider_bypass_headers: Vec::new(),
            })
            .await?;
        Ok(())
    }
}

pub(super) fn now_ms() -> u64 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_millis() as u64,
        Err(_) => 0,
    }
}

pub(super) fn create_standalone_leader_lifecycle() -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    let leader = Arc::new(StandaloneLeaderElection::local());
    lifecycle_with_leader("leader_election", leader)
}

pub(super) fn create_leader_lifecycle(
    leader_election: Option<LeaderElectionRegistration>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    if let Some(registration) = leader_election {
        let mut lifecycle = LifecycleOrchestrator::new();
        if let Some(service) = registration.lifecycle {
            lifecycle.register("leader_election", service);
        }
        info!("Using configured leader election provider");
        return (registration.leader, Arc::new(Mutex::new(lifecycle)));
    }

    create_standalone_leader_lifecycle()
}

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

pub(super) struct DeferredStateMachineTerminalObserver {
    pub(super) runtime: std::sync::RwLock<Option<Arc<dyn CollaborationRuntimeService>>>,
    pub(super) next: Arc<dyn BotTerminalObserverPort>,
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

pub(super) struct AgentCredentialBackfill {
    pub(super) registry: Arc<dyn BotRegistryCoreService>,
}

#[async_trait::async_trait]
impl bcs_ws::bot::AgentCredentialBackfillPort for AgentCredentialBackfill {
    async fn backfill(
        &self,
        bot_uuid: &str,
        agent_token: Option<String>,
        agent_code_header: Option<String>,
    ) {
        let agent_token_str = match &agent_token {
            Some(t) if !t.is_empty() => t.clone(),
            _ => return,
        };

        // agent_token: always write to memory only (not DB) for security
        self.registry
            .add_bot_info(bot_uuid, "agent_token", agent_token_str.clone())
            .await;

        let agent_code = agent_code_header.filter(|s| !s.is_empty());

        let Some(agent_code) = agent_code else {
            warn!(
                bot_uuid = %bot_uuid,
                "no agent_code resolved, skipping backfill"
            );
            return;
        };

        // agent_code: persist to DB
        if let Some(mut caps) = self.registry.load_from_storage(bot_uuid).await {
            if caps.agent_code.as_deref() == Some(&agent_code) {
                debug!(
                    bot_uuid = %bot_uuid,
                    "agent_code unchanged, skipping write"
                );
                return;
            }
            caps.agent_code = Some(agent_code.clone());
            if let Err(e) = self.registry.save_to_storage(bot_uuid, &caps).await {
                warn!(
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "failed to backfill agent_code"
                );
            } else {
                let _ = self.registry.register(bot_uuid.to_string(), caps).await;
                info!(
                    bot_uuid = %bot_uuid,
                    agent_code = %agent_code,
                    "agent_code backfilled"
                );
            }
        } else {
            warn!(
                bot_uuid = %bot_uuid,
                "bot not yet onboarded, skipping agent credential backfill"
            );
        }
    }
}

/// `GroupDispatchContextPort` backed by the core `GroupCoreService`. Lives in
/// the composition root, so it may depend on the core trait the WS adapter is
/// not allowed to name.
pub(super) struct CoreGroupDispatchContext {
    pub(super) group: Arc<dyn GroupCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupDispatchContextPort for CoreGroupDispatchContext {
    async fn participants(&self, group_id: &str) -> Option<Vec<bcs_service_api::Participant>> {
        self.group
            .get(group_id)
            .await
            .map(|group| group.participants)
    }
}

pub(super) fn bot_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        bot_runtime: state.services.bot_runtime.clone(),
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        bot_run_context: state.services.bot_run_context.clone(),
        bot_connections: state.bot_connections.clone(),
        run_channels: state.run_channels.clone(),
        task_callback: None,
        session_management: state.services.session_management.clone(),
        group_dispatch: Arc::new(CoreGroupDispatchContext {
            group: state.services.group.clone(),
        }),
        callback_dispatch: Arc::new(bcs_callback::SessionCallbackDispatcher::new(
            state.services.group.clone(),
            state.outbound_url_guard.clone(),
        )),
        system_message: Some(state.services.system_message.clone()),
        coordination_processed: state.coordination_processed.clone(),
        agent_credential_backfill: Some(Arc::new(AgentCredentialBackfill {
            registry: state.services.registry.clone(),
        })),
    })
}

pub(super) fn web_ws_dispatch_state(
    state: &Arc<BcsServerState>,
    group_session_connections: Option<Arc<dyn GroupSessionConnectionService>>,
) -> Arc<bcs_ws::web::WebDispatchState> {
    Arc::new(bcs_ws::web::WebDispatchState {
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        workbench_sessions: state.services.workbench_sessions.clone(),
        interactions: state.services.interactions.clone(),
        group_session_connections,
        frontend_connections: state.frontend_connections.clone(),
        run_channels: state.frontend_run_channels.clone(),
    })
}

pub(super) struct NoopWsLifecycleInstrumentationHook;

#[async_trait::async_trait]
impl WsLifecycleInstrumentationHook for NoopWsLifecycleInstrumentationHook {
    async fn accepted(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn registered(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn error(&self, _peer: WsPeer, _endpoint: &'static str, _kind: WsErrorKind) {}

    async fn closed(
        &self,
        _peer: WsPeer,
        _endpoint: &'static str,
        _close_reason: WsCloseReason,
        _duration: std::time::Duration,
    ) {
    }
}

pub(super) struct NoopDirectChatRunLifecycleHook;

#[async_trait::async_trait]
impl DirectChatRunLifecycleHook for NoopDirectChatRunLifecycleHook {
    async fn event(
        &self,
        _event: DirectChatRunEvent,
        _result: MetricsResult,
        _client_kind: DirectChatClientKind,
        _reason: DirectChatRunReason,
    ) {
    }
}

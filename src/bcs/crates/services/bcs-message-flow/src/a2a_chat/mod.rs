pub mod event_parser;
pub mod run_store;

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_protocol::{
    BcsFrame, ChannelInfo, ChannelSource, ChatSendParams, ContentBlock, GroupContext,
    MessageContent, RequestFrame,
};
use bcs_service_api::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    ActorStatus, AsyncA2aChatAccepted, AsyncA2aChatCommand,
    BlockingA2aChatCommand, BlockingA2aChatOutcome,
    BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotRegistryCoreService, BotRunContext, BotRunContextPort, CallerContext,
    ChatRunCancelCommand, ChatRunCleanupPort,
    ChatRunEventPort, ChatRunQueryCommand,
    ChatRunMetricCount, DeliveryBlockContext, DeliveryBlockReason, DeliveryBlockSurface,
    DeliveryMetricKind, DeliveryMetricTarget, DirectChatClientKind, DirectChatRunEvent,
    DirectChatRunLifecycleHook, DirectChatRunReason, DirectChatRunSnapshotPort,
    FriendCoreService, MetricsResult, OrganizationCoreService, RegisteredBot, ServiceError, ServiceResult,
};
use serde_json::Value;
use tokio::sync::mpsc;

pub use event_parser::{
    DetachDeliveryCallback, DrainOutcome, classify_detach_delivery_callback,
    drain_chat_event, drain_chat_event_with_mode,
};
pub use run_store::{
    ChatRunCompletionPolicy, ChatRunRecord, ChatRunState, ChatRunStore, MAX_CONTENT_BYTES,
};

#[derive(Clone)]
pub struct A2aChat {
    bot_delivery: Arc<dyn BotDeliveryPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    run_store: Arc<ChatRunStore>,
    chat_run_events: Arc<dyn ChatRunEventPort>,
    chat_run_cleanup: Arc<dyn ChatRunCleanupPort>,
    run_lifecycle_hook: Arc<dyn DirectChatRunLifecycleHook>,
    /// Optional run-context registry. When wired (via `with_bot_run_context`),
    /// each blocking/async chat registers a `BotRunContext` so HTTP-based
    /// provider callbacks against `/bot/events` can authenticate the run.
    /// Left `None` for tests / minimal setups that only target WS bots.
    bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    default_timeout_ms: u64,
    /// Outbound interceptor chain (security, audit, etc.). Default empty so
    /// existing callers/tests don't need to thread a chain. Bootstrap attaches
    /// the production chain via `with_interceptors`.
    interceptors: Arc<bcs_service_api::interceptor::InterceptorChain>,
    organization: Option<Arc<dyn OrganizationCoreService>>,
}

impl A2aChat {
    pub fn new(
        bot_delivery: Arc<dyn BotDeliveryPort>,
        run_store: Arc<ChatRunStore>,
        default_timeout_ms: u64,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
    ) -> Self {
        Self::new_with_run_ports(
            bot_delivery,
            run_store,
            default_timeout_ms,
            registry,
            friend,
            Arc::new(NoopChatRunEventPort),
            Arc::new(NoopChatRunCleanupPort),
        )
    }

    pub fn new_with_run_ports(
        bot_delivery: Arc<dyn BotDeliveryPort>,
        run_store: Arc<ChatRunStore>,
        default_timeout_ms: u64,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        chat_run_events: Arc<dyn ChatRunEventPort>,
        chat_run_cleanup: Arc<dyn ChatRunCleanupPort>,
    ) -> Self {
        Self {
            bot_delivery,
            registry,
            friend,
            run_store,
            chat_run_events,
            chat_run_cleanup,
            run_lifecycle_hook: Arc::new(NoopDirectChatRunLifecycleHook),
            bot_run_context: None,
            default_timeout_ms,
            interceptors: Arc::new(bcs_service_api::interceptor::InterceptorChain::new()),
            organization: None,
        }
    }

    /// Attach the production outbound interceptor chain (security, audit, ...).
    /// Returns Self for builder-style use during bootstrap wiring.
    pub fn with_interceptors(
        mut self,
        interceptors: Arc<bcs_service_api::interceptor::InterceptorChain>,
    ) -> Self {
        self.interceptors = interceptors;
        self
    }

    pub fn run_store(&self) -> Arc<ChatRunStore> {
        self.run_store.clone()
    }

    pub fn with_organization(
        mut self,
        organization: Arc<dyn OrganizationCoreService>,
    ) -> Self {
        self.organization = Some(organization);
        self
    }

    pub fn with_run_lifecycle_hook(
        mut self,
        run_lifecycle_hook: Arc<dyn DirectChatRunLifecycleHook>,
    ) -> Self {
        self.run_lifecycle_hook = run_lifecycle_hook;
        self
    }

    /// Attach a `BotRunContextPort` so each blocking/async chat registers a
    /// run context. Required to make HTTP-based provider `/bot/events`
    /// callbacks work for direct A2A chats; without it the callback will
    /// fail with `run_not_found` and the caller will time out.
    pub fn with_bot_run_context(
        mut self,
        bot_run_context: Arc<dyn BotRunContextPort>,
    ) -> Self {
        self.bot_run_context = Some(bot_run_context);
        self
    }

    /// Register a `BotRunContext` for the upcoming delivery so HTTP-based
    /// provider callbacks (`/bot/events`) can authenticate this run by
    /// `run_id`. No-op when `bot_run_context` is not wired (test setups /
    /// WS-only deployments).
    async fn register_bot_run_context(
        &self,
        run_id: &str,
        target_bot_id: &str,
        session_key: &str,
        timeout_ms: u64,
    ) {
        let Some(ctx) = &self.bot_run_context else {
            return;
        };
        ctx.put_context(BotRunContext {
            run_id: run_id.to_string(),
            bot_id: target_bot_id.to_string(),
            // A2A direct chat has no group; leave empty so `relay_final_chat_event`
            // can short-circuit and we don't accidentally route into an unrelated group.
            group_id: String::new(),
            // `session_key` is always populated by `resolve_session_key` (either
            // caller-supplied or auto-generated like `chat:<run_id[..8]>`).
            // Carrying it through lets workbench subscribers see the final too.
            bcs_session_id: Some(session_key.to_string()),
            deadline_ms: now_ms().saturating_add(timeout_ms),
            terminal: false,
        })
        .await;
    }

    async fn emit_run_lifecycle(
        &self,
        event: DirectChatRunEvent,
        result: MetricsResult,
        client_kind: DirectChatClientKind,
        reason: DirectChatRunReason,
    ) {
        self.run_lifecycle_hook
            .event(event, result, client_kind, reason)
            .await;
    }

    async fn fail_run_if_open_with_reason(
        &self,
        run_id: &str,
        error: &str,
        reason: DirectChatRunReason,
    ) -> ServiceResult<bool> {
        let Some(record) = self.run_store.get(run_id).await else {
            return Err(ServiceError::BotNotFound(format!("chat run {run_id}")));
        };
        if record.state.is_terminal() {
            return Ok(false);
        }
        let client_kind = direct_chat_client_kind(record.client.as_deref());
        if record.accumulated_content.is_empty() {
            let changed = self.run_store.mark_failed(run_id, error).await;
            if changed {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Failed,
                    MetricsResult::Error,
                    client_kind,
                    reason,
                )
                .await;
            }
            Ok(changed)
        } else {
            let changed = self.run_store.mark_completed(run_id, None).await;
            if changed {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Completed,
                    MetricsResult::Success,
                    client_kind,
                    DirectChatRunReason::None,
                )
                .await;
            }
            Ok(changed)
        }
    }
}

#[derive(Debug, Default)]
struct NoopChatRunEventPort;

#[async_trait]
impl ChatRunEventPort for NoopChatRunEventPort {
    async fn register(
        &self,
        _run_id: String,
        _session_key: String,
        _sender: mpsc::Sender<String>,
        _source: Option<String>,
        _from: Option<String>,
    ) {
    }

    async fn unregister(&self, _run_id: &str) {}
}

#[derive(Debug, Default)]
struct NoopChatRunCleanupPort;

#[async_trait]
impl ChatRunCleanupPort for NoopChatRunCleanupPort {
    async fn unregister(&self, _run_id: &str) {}
}

#[derive(Debug, Default)]
struct NoopDirectChatRunLifecycleHook;

#[async_trait]
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

#[async_trait]
impl DirectChatRunSnapshotPort for A2aChat {
    async fn direct_chat_run_counts(&self) -> ServiceResult<Vec<ChatRunMetricCount>> {
        Ok(self.run_store.metric_counts().await)
    }
}

#[async_trait]
impl A2aChatRunService for A2aChat {
    async fn run_blocking_chat(
        &self,
        cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        let (response_tx, mut response_rx) = mpsc::channel::<String>(16);
        self.chat_run_events
            .register(
                cmd.run_id.clone(),
                cmd.session_key.clone(),
                response_tx,
                Some("http-chat".to_string()),
                cmd.run_channel_from.clone(),
            )
            .await;
        self.register_bot_run_context(&cmd.run_id, &cmd.target_bot_id, &cmd.session_key, cmd.timeout_ms)
            .await;

        let chat_result = self
            .chat(A2aChatCommand {
                caller: cmd.caller.clone(),
                target_bot_id: cmd.target_bot_id.clone(),
                message: cmd.message,
                from_actor_id: cmd.from_actor_id,
                authenticated_staff_id: cmd.authenticated_staff_id,
                run_id: Some(cmd.run_id.clone()),
                async_mode: false,
                session_key: Some(cmd.session_key.clone()),
                timeout_ms: Some(cmd.timeout_ms),
                client: cmd.client.or_else(|| Some("http-chat".to_string())),
                tags: cmd.tags,
                response_mode: cmd.response_mode,
                caller_wait_mode: None,
                organization_code: cmd.organization_code,
            })
            .await;

        if let Err(err) = chat_result {
            self.chat_run_events.unregister(&cmd.run_id).await;
            return Err(err);
        }

        self.drain_blocking_run(
            cmd.caller,
            &cmd.run_id,
            &cmd.target_bot_id,
            &cmd.session_key,
            cmd.timeout_ms,
            &mut response_rx,
        )
        .await
    }

    async fn start_async_chat(
        &self,
        cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        let (response_tx, response_rx) = mpsc::channel::<String>(64);
        self.chat_run_events
            .register(
                cmd.run_id.clone(),
                cmd.session_key.clone(),
                response_tx,
                Some("http-chat-async".to_string()),
                cmd.run_channel_from.clone(),
            )
            .await;
        self.register_bot_run_context(&cmd.run_id, &cmd.target_bot_id, &cmd.session_key, cmd.timeout_ms)
            .await;

        let expires_at_ms = now_ms().saturating_add(cmd.timeout_ms);
        let chat_result = self
            .chat(A2aChatCommand {
                caller: cmd.caller,
                target_bot_id: cmd.target_bot_id.clone(),
                message: cmd.message,
                from_actor_id: cmd.from_actor_id,
                authenticated_staff_id: cmd.authenticated_staff_id,
                run_id: Some(cmd.run_id.clone()),
                async_mode: true,
                session_key: Some(cmd.session_key.clone()),
                timeout_ms: Some(cmd.timeout_ms),
                client: cmd.client.or_else(|| Some("http-chat-async".to_string())),
                tags: cmd.tags,
                response_mode: cmd.response_mode,
                caller_wait_mode: cmd.caller_wait_mode,
                organization_code: cmd.organization_code,
            })
            .await;

        let chat_outcome = match chat_result {
            Ok(outcome) => outcome,
            Err(err) => {
                self.chat_run_events.unregister(&cmd.run_id).await;
                return Err(err);
            }
        };

        if chat_outcome.status == "completed" {
            self.chat_run_events.unregister(&cmd.run_id).await;
        } else {
            let service = self.clone();
            let run_id = cmd.run_id.clone();
            let timeout_ms = cmd.timeout_ms;
            tokio::spawn(async move {
                service.drain_async_run(run_id, response_rx, timeout_ms).await;
            });
        }

        Ok(AsyncA2aChatAccepted {
            run_id: cmd.run_id,
            bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key,
            status: chat_outcome.status,
            expires_at_ms,
        })
    }

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        if cmd.wait_ms == 0 {
            A2aChatService::get_run(self, cmd.caller, &cmd.run_id).await
        } else {
            A2aChatService::wait_run(
                self,
                cmd.caller,
                &cmd.run_id,
                cmd.since_version,
                cmd.wait_ms,
            )
            .await
        }
    }

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        let status = A2aChatService::cancel_run(self, cmd.caller, &cmd.run_id).await?;
        self.chat_run_cleanup.unregister(&cmd.run_id).await;
        Ok(status)
    }
}

#[async_trait]
impl A2aChatService for A2aChat {
    async fn chat(&self, cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome> {
        let client_kind = direct_chat_client_kind(cmd.client.as_deref());
        let from_bot_id = bot_caller_id(&cmd.caller)?;
        self.ensure_source_owner(&from_bot_id, cmd.authenticated_staff_id.as_deref())
            .await?;
        let target_bot = if let Some(code) = cmd.organization_code.as_deref() {
            let organization = self.organization.as_ref().ok_or_else(|| {
                ServiceError::InvalidOperation {
                    message: "organization service is not configured".to_string(),
                    request_id: None,
                }
            })?;
            organization
                .authorize_pair(code, &from_bot_id, &cmd.target_bot_id)
                .await?;
            self.ensure_organization_target_reachable(&from_bot_id, &cmd.target_bot_id)
                .await?
        } else {
            self.ensure_target_reachable(&from_bot_id, &cmd.target_bot_id)
                .await?
        };
        if target_bot.status == ActorStatus::Hidden {
            let name = target_bot
                .capabilities
                .name
                .as_deref()
                .unwrap_or(&cmd.target_bot_id);
            return Err(ServiceError::BotHidden(name.to_string()));
        }
        let delivery_target = self
            .registry
            .resolve_delivery_target(&cmd.target_bot_id)
            .await?;
        let target_is_http_provider = delivery_target.is_http_provider();
        if !self.bot_delivery.is_available(&delivery_target).await {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Failed,
                MetricsResult::Error,
                client_kind,
                DirectChatRunReason::BotNotConnected,
            )
            .await;
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id));
        }

        let run_id = cmd
            .run_id
            .clone()
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let session_key = cmd
            .session_key
            .clone()
            .unwrap_or_else(|| format!("chat:{}", &run_id[..run_id.len().min(8)]));
        let timeout_ms = cmd.timeout_ms.unwrap_or(self.default_timeout_ms);
        let now_ms = now_ms();
        let expires_at_ms = now_ms.saturating_add(timeout_ms);
        let submit_on_provider_ack =
            cmd.async_mode
                && target_is_http_provider
                && is_detached_wait_mode(cmd.caller_wait_mode.as_deref());
        let completion_policy = if submit_on_provider_ack {
            ChatRunCompletionPolicy::DetachDeliveryAck
        } else {
            ChatRunCompletionPolicy::WaitForFinal
        };

        if let Err(err) = self
            .run_store
            .create(ChatRunRecord::new(
                run_id.clone(),
                cmd.target_bot_id.clone(),
                from_bot_id.clone(),
                session_key.clone(),
                now_ms,
                expires_at_ms,
                cmd.client.clone(),
                cmd.response_mode,
                completion_policy,
            ))
            .await
        {
            let reason = err.direct_chat_reason();
            let event = if reason == DirectChatRunReason::StoreCapacity {
                DirectChatRunEvent::CapacityRejected
            } else {
                DirectChatRunEvent::Failed
            };
            self.emit_run_lifecycle(event, MetricsResult::Error, client_kind, reason)
                .await;
            return Err(ServiceError::InternalError(format!("cannot accept run: {err}")));
        }
        self.emit_run_lifecycle(
            DirectChatRunEvent::Created,
            MetricsResult::Success,
            client_kind,
            DirectChatRunReason::None,
        )
        .await;

        let from_bot_name = self.sender_display_name(&from_bot_id).await;
        let frame = build_chat_send_frame(
            &run_id,
            &session_key,
            &cmd.target_bot_id,
            &from_bot_id,
            &from_bot_name,
            cmd.from_actor_id.as_deref().unwrap_or(&from_bot_id),
            &cmd.message,
            timeout_ms,
            &cmd.tags,
            cmd.caller_wait_mode.as_deref(),
        )?;

        // Outbound interceptor chain (security gateway etc.). Block here is a
        // hard refusal — the run is marked failed and the error surfaces to
        // the caller. Mirrors the missing-credential skip in
        // group_flow::apply_outbound_interceptors so legacy bots are not
        // mass-blocked.
        if !self.interceptors.is_empty() {
            use bcs_service_api::interceptor::{
                InterceptorDecision, OutboundMessage,
            };
            use bcs_domain::{GroupMessage, GroupMessageType, MessageRole};

            let credentials_pair = match (
                self.registry.get_agent_credentials(&from_bot_id).await,
                self.registry.get_agent_credentials(&cmd.target_bot_id).await,
            ) {
                (Some(c), Some(r))
                    if c.agent_code.as_deref().is_some_and(|s| !s.is_empty())
                        && r.agent_code.as_deref().is_some_and(|s| !s.is_empty()) =>
                {
                    Some((c, r))
                }
                _ => {
                    tracing::warn!(
                        sender = %from_bot_id,
                        receiver = %cmd.target_bot_id,
                        run_id = %run_id,
                        "skipping a2a outbound interceptor chain: missing agent_code (legacy/unregistered bot)"
                    );
                    None
                }
            };

            if let Some((caller, receiver)) = credentials_pair {
                let synthetic = GroupMessage {
                    id: run_id.clone(),
                    timestamp: 0,
                    sender: from_bot_id.clone(),
                    content: cmd.message.clone(),
                    message_type: GroupMessageType::default(),
                    bot_name: None,
                    role: MessageRole::default(),
                    run_id: String::new(),
                    history_meta: None,
                    metadata: None,
                };
                let mut outbound = OutboundMessage {
                    group_id: run_id.clone(),
                    message: synthetic,
                    receiver_bot_id: cmd.target_bot_id.clone(),
                    caller,
                    receiver,
                };
                let block_context = DeliveryBlockContext {
                    target: DeliveryMetricTarget::Bot,
                    delivery_kind: DeliveryMetricKind::Send,
                    surface: DeliveryBlockSurface::DirectChat,
                    reason: DeliveryBlockReason::PolicyBlocked,
                };
                if let InterceptorDecision::Block(reason) = self
                    .interceptors
                    .on_outbound_with_context(&mut outbound, block_context)
                    .await
                {
                    if self
                        .run_store
                        .mark_failed(&run_id, &format!("blocked:{}", reason.code))
                        .await
                    {
                        self.emit_run_lifecycle(
                            DirectChatRunEvent::Failed,
                            MetricsResult::Error,
                            client_kind,
                            DirectChatRunReason::Blocked,
                        )
                        .await;
                    }
                    tracing::warn!(
                        interceptor = %reason.interceptor_id,
                        code = %reason.code,
                        run_id = %run_id,
                        "a2a chat blocked by interceptor chain"
                    );
                    return Err(ServiceError::Forbidden(if reason.user_visible {
                        reason.message
                    } else {
                        "a2a chat blocked by policy".to_string()
                    }));
                }
            }
        }

        let delivery = match self
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
            })
            .await
        {
            Ok(delivery) => delivery,
            Err(error) => {
                let reason = direct_chat_service_error_reason(&error);
                if self.run_store.mark_failed(&run_id, error.to_string()).await {
                    self.emit_run_lifecycle(
                        DirectChatRunEvent::Failed,
                        MetricsResult::Error,
                        client_kind,
                        reason,
                    )
                    .await;
                }
                return Err(error);
            }
        };

        if !delivery.delivered {
            if self
                .run_store
                .mark_failed(&run_id, "bot_not_connected")
                .await
            {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Failed,
                    MetricsResult::Error,
                    client_kind,
                    DirectChatRunReason::BotNotConnected,
                )
                .await;
            }
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id));
        }

        if submit_on_provider_ack {
            if self.run_store.mark_submitted(&run_id).await {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Submitted,
                    MetricsResult::Success,
                    client_kind,
                    DirectChatRunReason::None,
                )
                .await;
            }
        } else if self.run_store.mark_running(&run_id).await {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Running,
                MetricsResult::Success,
                client_kind,
                DirectChatRunReason::None,
            )
            .await;
        }

        Ok(A2aChatOutcome {
            run_id,
            status: if submit_on_provider_ack {
                "submitted"
            } else if cmd.async_mode {
                "running"
            } else {
                "started"
            }
            .to_string(),
            response: None,
        })
    }

    async fn get_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let from_bot_id = bot_caller_id(&caller)?;
        let record = self
            .run_store
            .get(run_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?;
        ensure_run_owner(&record, &from_bot_id)?;
        Ok(run_status(&record, None))
    }

    async fn wait_run(
        &self,
        caller: CallerContext,
        run_id: &str,
        since_version: u64,
        wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus> {
        let from_bot_id = bot_caller_id(&caller)?;
        let record = self
            .run_store
            .get(run_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?;
        ensure_run_owner(&record, &from_bot_id)?;

        let record = if record.version > since_version || record.state.is_terminal() || wait_ms == 0
        {
            record
        } else {
            self.run_store
                .wait_update(run_id, since_version, Duration::from_millis(wait_ms))
                .await
                .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?
        };
        ensure_run_owner(&record, &from_bot_id)?;
        Ok(run_status(&record, None))
    }

    async fn record_run_event(&self, run_id: &str, event_json: &str) -> ServiceResult<bool> {
        let record = self
            .run_store
            .get(run_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?;
        if record.state.is_terminal() {
            return Ok(true);
        }

        let client_kind = direct_chat_client_kind(record.client.as_deref());
        if record.completion_policy == ChatRunCompletionPolicy::DetachDeliveryAck {
            return match classify_detach_delivery_callback(event_json) {
                DetachDeliveryCallback::Success => {
                    if self
                        .run_store
                        .mark_detach_delivery_acknowledged(run_id)
                        .await
                    {
                        self.emit_run_lifecycle(
                            DirectChatRunEvent::Running,
                            MetricsResult::Success,
                            client_kind,
                            DirectChatRunReason::None,
                        )
                        .await;
                    }
                    Ok(true)
                }
                DetachDeliveryCallback::Error(msg) => {
                    let reason = direct_chat_failure_reason(&msg);
                    if self.run_store.mark_failed(run_id, msg).await {
                        self.emit_run_lifecycle(
                            DirectChatRunEvent::Failed,
                            MetricsResult::Error,
                            client_kind,
                            reason,
                        )
                        .await;
                    }
                    Ok(true)
                }
                DetachDeliveryCallback::Ignored => Ok(false),
            };
        }

        let mut accumulated = record.accumulated_content.clone();
        let before = accumulated.clone();
        let terminal = match drain_chat_event_with_mode(
            event_json,
            &mut accumulated,
            record.response_mode,
        ) {
            DrainOutcome::Continue => {
                if self
                    .apply_run_content_change(run_id, &before, &accumulated)
                    .await
                    && record.state == ChatRunState::Pending
                {
                    self.emit_run_lifecycle(
                        DirectChatRunEvent::Running,
                        MetricsResult::Success,
                        client_kind,
                        DirectChatRunReason::None,
                    )
                    .await;
                }
                false
            }
            DrainOutcome::Final => {
                self.apply_run_content_change(run_id, &before, &accumulated)
                    .await;
                if self.run_store.mark_completed(run_id, None).await {
                    self.emit_run_lifecycle(
                        DirectChatRunEvent::Completed,
                        MetricsResult::Success,
                        client_kind,
                        DirectChatRunReason::None,
                    )
                    .await;
                }
                true
            }
            DrainOutcome::Error(msg) => {
                let reason = direct_chat_failure_reason(&msg);
                if self.run_store.mark_failed(run_id, msg).await {
                    self.emit_run_lifecycle(
                        DirectChatRunEvent::Failed,
                        MetricsResult::Error,
                        client_kind,
                        reason,
                    )
                    .await;
                }
                true
            }
        };
        Ok(terminal)
    }

    async fn fail_run_if_open(&self, run_id: &str, error: &str) -> ServiceResult<bool> {
        self.fail_run_if_open_with_reason(run_id, error, direct_chat_failure_reason(error))
            .await
    }

    async fn cancel_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let from_bot_id = bot_caller_id(&caller)?;
        let record = self
            .run_store
            .get(run_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?;
        ensure_run_owner(&record, &from_bot_id)?;
        let client_kind = direct_chat_client_kind(record.client.as_deref());
        let cancelled = self.run_store.mark_cancelled(run_id).await;
        if cancelled {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Cancelled,
                MetricsResult::Success,
                client_kind,
                DirectChatRunReason::None,
            )
            .await;
        }
        let record = self
            .run_store
            .get(run_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(format!("chat run {run_id}")))?;
        Ok(run_status(&record, Some(cancelled)))
    }

    async fn cleanup_expired(
        &self,
        now_ms: u64,
        retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)> {
        let client_kinds = self.run_store.metric_client_kinds().await;
        let (expired, dropped) = self.run_store.cleanup_expired(now_ms, retention_ms).await;
        for run_id in &expired {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Expired,
                MetricsResult::Error,
                client_kinds
                    .get(run_id)
                    .copied()
                    .unwrap_or(DirectChatClientKind::Unknown),
                DirectChatRunReason::Timeout,
            )
            .await;
        }
        for run_id in &dropped {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Dropped,
                MetricsResult::Success,
                client_kinds
                    .get(run_id)
                    .copied()
                    .unwrap_or(DirectChatClientKind::Unknown),
                DirectChatRunReason::None,
            )
            .await;
        }
        Ok((expired, dropped))
    }
}

impl A2aChat {
    async fn drain_blocking_run(
        &self,
        caller: CallerContext,
        run_id: &str,
        target_bot_id: &str,
        session_key: &str,
        timeout_ms: u64,
        response_rx: &mut mpsc::Receiver<String>,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        let timeout_duration = Duration::from_millis(timeout_ms);

        loop {
            match tokio::time::timeout(timeout_duration, response_rx.recv()).await {
                Ok(Some(event_str)) => {
                    let terminal = match self.record_run_event(run_id, &event_str).await {
                        Ok(terminal) => terminal,
                        Err(error) => {
                            self.chat_run_events.unregister(run_id).await;
                            return Err(error);
                        }
                    };
                    if terminal {
                        break;
                    }
                }
                Ok(None) => {
                    let status = A2aChatService::get_run(self, caller.clone(), run_id).await?;
                    let content = run_content(&status);
                    let _ = self
                        .fail_run_if_open_with_reason(
                            run_id,
                            "Bot channel closed without response",
                            DirectChatRunReason::BotNotConnected,
                        )
                        .await;
                    if content.is_empty() {
                        self.chat_run_events.unregister(run_id).await;
                        return Err(ServiceError::InternalError(
                            "Bot channel closed without response".to_string(),
                        ));
                    }
                    break;
                }
                Err(_) => {
                    let status = A2aChatService::get_run(self, caller.clone(), run_id).await?;
                    if run_content(&status).is_empty() {
                        let _ = self
                            .fail_run_if_open_with_reason(
                                run_id,
                                "Timeout waiting for bot response",
                                DirectChatRunReason::Timeout,
                            )
                            .await;
                        self.chat_run_events.unregister(run_id).await;
                        return Err(ServiceError::InternalError(
                            "Timeout waiting for bot response".to_string(),
                        ));
                    }
                    let _ = self
                        .fail_run_if_open_with_reason(
                            run_id,
                            "Timeout waiting for bot response",
                            DirectChatRunReason::Timeout,
                        )
                        .await;
                    break;
                }
            }
        }

        self.chat_run_events.unregister(run_id).await;
        let status = A2aChatService::get_run(self, caller, run_id).await?;
        if status.status == "failed" {
            return Err(ServiceError::InternalError(format!(
                "Bot error: {}",
                run_error_message(&status)
            )));
        }

        Ok(BlockingA2aChatOutcome {
            delivered: true,
            bot_uuid: target_bot_id.to_string(),
            session_id: session_key.to_string(),
            content: run_content(&status),
        })
    }

    async fn drain_async_run(
        &self,
        run_id: String,
        mut response_rx: mpsc::Receiver<String>,
        timeout_ms: u64,
    ) {
        let timeout_duration = Duration::from_millis(timeout_ms);
        loop {
            match tokio::time::timeout(timeout_duration, response_rx.recv()).await {
                Ok(Some(event_str)) => match self.record_run_event(&run_id, &event_str).await {
                    Ok(true) => break,
                    Ok(false) => {}
                    Err(_) => break,
                },
                Ok(None) => {
                    let _ = self
                        .fail_run_if_open_with_reason(
                            &run_id,
                            "bot channel closed without response",
                            DirectChatRunReason::BotNotConnected,
                        )
                        .await;
                    break;
                }
                Err(_) => {
                    let _ = self
                        .fail_run_if_open_with_reason(
                            &run_id,
                            "timeout",
                            DirectChatRunReason::Timeout,
                        )
                        .await;
                    break;
                }
            }
        }
        self.chat_run_events.unregister(&run_id).await;
    }

    async fn apply_run_content_change(
        &self,
        run_id: &str,
        before: &str,
        accumulated: &str,
    ) -> bool {
        if let Some(delta) = new_suffix(before, accumulated) {
            self.run_store.append_delta(run_id, delta).await
        } else if accumulated != before {
            self.run_store.replace_content(run_id, accumulated).await
        } else {
            false
        }
    }

    async fn ensure_source_owner(
        &self,
        from_bot_id: &str,
        authenticated_staff_id: Option<&str>,
    ) -> ServiceResult<()> {
        let Some(staff_id) = authenticated_staff_id.filter(|id| !id.is_empty()) else {
            return Ok(());
        };
        let created_by = self
            .registry
            .get(from_bot_id)
            .await
            .and_then(|bot| bot.created_by);
        if created_by.as_deref().is_some_and(|owner| owner != staff_id) {
            Err(ServiceError::Unauthorized(format!(
                "User {} is not the creator of bot {}",
                staff_id, from_bot_id
            )))
        } else {
            Ok(())
        }
    }

    async fn ensure_target_reachable(
        &self,
        from_bot_id: &str,
        target_bot_id: &str,
    ) -> ServiceResult<RegisteredBot> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        match target.capabilities.visibility.as_str() {
            "public" => Ok(target),
            "protected" if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            "protected" => Err(ServiceError::NotFriends(vec![target_bot_id.to_string()])),
            // "private" or unknown: friends can still reach, strangers get 404
            _ if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string())),
        }
    }

    async fn ensure_organization_target_reachable(
        &self,
        from_bot_id: &str,
        target_bot_id: &str,
    ) -> ServiceResult<RegisteredBot> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        match target.capabilities.visibility.as_str() {
            "public" | "protected" => Ok(target),
            _ if self.friend.are_friends(from_bot_id, target_bot_id).await => Ok(target),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string())),
        }
    }

    async fn sender_display_name(&self, from_bot_id: &str) -> String {
        self.registry
            .get(from_bot_id)
            .await
            .and_then(|bot| bot.capabilities.name)
            .filter(|name| !name.trim().is_empty())
            .unwrap_or_else(|| from_bot_id.to_string())
    }
}

fn bot_caller_id(caller: &CallerContext) -> ServiceResult<String> {
    match caller {
        CallerContext::Bot(bot) => Ok(bot.bot_uuid.clone()),
        CallerContext::Integration(client) => Ok(client.client_id.clone()),
        _ => Err(ServiceError::Unauthorized(
            "A2A chat requires a bot or integration caller".to_string(),
        )),
    }
}

fn is_detached_wait_mode(mode: Option<&str>) -> bool {
    mode.map(str::trim)
        .is_some_and(|mode| mode.eq_ignore_ascii_case("detached"))
}

fn direct_chat_client_kind(client: Option<&str>) -> DirectChatClientKind {
    run_store::direct_chat_client_kind(client)
}

#[doc(hidden)]
fn direct_chat_failure_reason(error: &str) -> DirectChatRunReason {
    // This is intentionally limited to bot-emitted chat.event error text.
    // Store and transport paths pass typed DirectChatRunReason values.
    let normalized = error.to_ascii_lowercase();
    if normalized.contains("timeout") {
        DirectChatRunReason::Timeout
    } else if normalized.contains("bot_not_connected") || normalized.contains("not connected") {
        DirectChatRunReason::BotNotConnected
    } else if normalized.contains("blocked") {
        DirectChatRunReason::Blocked
    } else if normalized.contains("capacity") {
        DirectChatRunReason::StoreCapacity
    } else {
        DirectChatRunReason::InternalError
    }
}

fn direct_chat_service_error_reason(error: &ServiceError) -> DirectChatRunReason {
    match error {
        ServiceError::BotNotConnected(_) => DirectChatRunReason::BotNotConnected,
        ServiceError::Forbidden(message) if message.to_ascii_lowercase().contains("blocked") => {
            DirectChatRunReason::Blocked
        }
        ServiceError::MessageLimitReached(_) => DirectChatRunReason::StoreCapacity,
        _ => DirectChatRunReason::InternalError,
    }
}

fn ensure_run_owner(record: &ChatRunRecord, from_bot_id: &str) -> ServiceResult<()> {
    if record.from_bot_id == from_bot_id {
        Ok(())
    } else {
        Err(ServiceError::Unauthorized(format!(
            "chat run {} does not belong to caller",
            record.run_id
        )))
    }
}

fn new_suffix<'a>(prev: &str, current: &'a str) -> Option<&'a str> {
    if current.len() > prev.len() && current.starts_with(prev) {
        Some(&current[prev.len()..])
    } else {
        None
    }
}

fn run_status(record: &ChatRunRecord, cancelled: Option<bool>) -> A2aRunStatus {
    let mut response = serde_json::json!({
        "content": record.accumulated_content,
        "run_id": record.run_id,
        "bot_uuid": record.bot_uuid,
        "from_bot_id": record.from_bot_id,
        "session_id": record.session_key,
        "state": record.state.as_str(),
        "error_message": record.error_message,
        "created_at_ms": record.created_at_ms,
        "updated_at_ms": record.updated_at_ms,
        "completed_at_ms": record.completed_at_ms,
        "expires_at_ms": record.expires_at_ms,
        "version": record.version,
        "content_truncated": record.content_truncated,
        "response_mode": record.response_mode,
        "is_terminal": record.state.is_terminal(),
        "client": record.client,
    });
    if let Some(cancelled) = cancelled {
        response["cancelled"] = Value::Bool(cancelled);
    }
    A2aRunStatus {
        run_id: record.run_id.clone(),
        status: record.state.as_str().to_string(),
        response: Some(response),
    }
}

fn run_content(status: &A2aRunStatus) -> String {
    status
        .response
        .as_ref()
        .and_then(|response| response.get("content"))
        .and_then(|content| content.as_str())
        .unwrap_or("")
        .to_string()
}

fn run_error_message(status: &A2aRunStatus) -> String {
    status
        .response
        .as_ref()
        .and_then(|response| response.get("error_message"))
        .and_then(|message| message.as_str())
        .unwrap_or("Unknown error")
        .to_string()
}

fn build_chat_send_frame(
    run_id: &str,
    session_key: &str,
    target_bot_id: &str,
    from_bot_id: &str,
    from_bot_name: &str,
    from_actor_id: &str,
    message: &str,
    timeout_ms: u64,
    tags: &[String],
    caller_wait_mode: Option<&str>,
) -> ServiceResult<BcsFrame> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let params = ChatSendParams {
        session_key: session_key.to_string(),
        bcs_group_id: session_key.to_string(),
        message: MessageContent {
            role: "user".to_string(),
            content: vec![ContentBlock::text(message)],
            timestamp,
        },
        channel: ChannelInfo {
            source: ChannelSource::Api,
            user_id: Some(from_bot_name.to_string()),
            actor_id: Some(from_bot_id.to_string()),
            actor_name: Some(from_bot_name.to_string()),
            thread_id: None,
        },
        session_context: GroupContext {
            session_id: session_key.to_string(),
            participants: vec![target_bot_id.to_string()],
            recipient: Some(target_bot_id.to_string()),
            recipient_name: None,
            recipient_role: None,
            delivery_type: Some("send".to_string()),
            originator: from_actor_id.to_string(),
            from: from_actor_id.to_string(),
            from_bot_id: Some(from_bot_id.to_string()),
            from_bot_owner: None,
            you_are_mentioned: true,
            is_sender: false,
            mentions: vec![target_bot_id.to_string()],
            response_directive: None,
            message: message.to_string(),
            routing_mode: None,
            group_type: None,
        },
        timeout_ms: Some(timeout_ms),
        idempotency_key: None,
        bcs_session_id: None,
        tags: tags.to_vec(),
    };
    let mut params = serde_json::to_value(params)?;
    if let Some(wait_mode) = caller_wait_mode.map(str::trim).filter(|mode| !mode.is_empty()) {
        if let Some(obj) = params.as_object_mut() {
            obj.insert(
                "extensions".to_string(),
                serde_json::json!({
                    "caller_wait_mode": wait_mode,
                }),
            );
        }
    }
    Ok(BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    )))
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direct_chat_failure_reason_maps_legacy_bot_error_text() {
        assert_eq!(
            direct_chat_failure_reason("Timeout waiting for bot response"),
            DirectChatRunReason::Timeout
        );
        assert_eq!(
            direct_chat_failure_reason("target bot is not connected"),
            DirectChatRunReason::BotNotConnected
        );
        assert_eq!(
            direct_chat_failure_reason("blocked:policy"),
            DirectChatRunReason::Blocked
        );
        assert_eq!(
            direct_chat_failure_reason("unexpected model error"),
            DirectChatRunReason::InternalError
        );
    }
}

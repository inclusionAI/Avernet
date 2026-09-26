use bcs_service_api::port::CoordinationIntentPort;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use bcs_domain::{Attachment, MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_protocol::{
    Attachment as WireAttachment, BcsFrame, ChannelInfo, ChannelSource, RequestFrame,
    apply_channel_info, build_chat_inject_frame, build_chat_send_frame,
    build_direct_chat_inject_frame, build_direct_chat_send_frame, now_ms,
};
use bcs_service_api::{
    ActiveBotRunContext, ActorKind, ActorStatus, BotAbortDeliveryCommand, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotEventCommand,
    BotEventOutcome, BotRegistryCoreService, BotRunContext, BotRunContextPort, BotRunScope,
    BotRunTransportOwner, BotTerminalObserverPort, CallerContext, ChannelService, ChatAbortCommand,
    CancelLatestQueuedMessageCommand, CancelLatestQueuedMessageOutcome, ChatAbortFailure,
    ChatAbortOutcome, ChatAbortScope, ChatEventState, DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
    DeliveryBlockContext, DeliveryBlockReason, DeliveryBlockSurface, DeliveryMetricKind,
    DeliveryMetricTarget, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, Group,
    GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome,
    GroupCoreService, GroupKind, GroupMessage, GroupMessageType, GroupStatus, GroupStrategy,
    HiddenMentionInfo, MESSAGE_LOG_SCHEMA_VERSION, MessageDeliveryResult, MessageFlowService,
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MessageLogTargetSummary, MessageRole, NoopBotTerminalObserver, Participant, ParticipantMode,
    ParticipantRole, PersistentGroupSendCommand, PersistentGroupSendOutcome,
    ProviderStreamGrayList, RouteParticipantOverlay, RoutingCoreService, RoutingDecision,
    RoutingTarget, ServiceError, ServiceResult, SessionManagementService, SessionStatus,
    SystemMessageEvent, SystemMessageService, TaskCompleteCommand, TaskCompleteOutcome,
    TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome, backfill_bot_names,
    interceptor::{
        BlockReason, InterceptorChain, InterceptorDecision, MessageInterceptor, OutboundMessage,
    },
    message_log_json,
    port::repo::{AppendMessageWithEvent, MessageRepoPort},
    port::{EventRecordFactoryPort, EventRecorderPort, HumanMentionNotifyPort, NewEvent},
    types::{EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventScope, EventSubject},
};
use chrono::{SecondsFormat, TimeZone, Utc};
use futures::{StreamExt, stream};
use regex::Regex;
use serde_json::Value;
use tracing::{info, warn, Instrument};

use crate::MSG_LOG_TARGET;
use crate::protocol_context::{group_context_input, group_type_wire};
use crate::task_store::TaskStore;

pub struct BcsMessageFlow {
    pub group: Arc<dyn GroupCoreService>,
    pub routing: Arc<dyn RoutingCoreService>,
    pub registry: Arc<dyn BotRegistryCoreService>,
    pub bot_delivery: Arc<dyn BotDeliveryPort>,
    pub frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    pub task_store: Arc<TaskStore>,
    pub bot_relay_turn_limit: i64,
    pub interceptors: Arc<InterceptorChain>,
    pub session_management: Option<Arc<dyn SessionManagementService>>,
    pub(crate) coordination_intents: Option<Arc<dyn CoordinationIntentPort>>,
    pub bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    pub provider_chat_run_timeout_ms: u64,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub message_repo: Option<Arc<dyn MessageRepoPort>>,
    pub event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
    pub event_recorder: Option<Arc<dyn EventRecorderPort>>,
    pub message_tracker: Arc<crate::message_tracker::MessageTracker>,
    /// Deprecated compatibility setting. Transport selection is owned by the
    /// HTTP Provider adapter and this value is no longer consulted.
    pub provider_stream_gray_list: Option<Arc<ProviderStreamGrayList>>,
    pub channel: Arc<OnceLock<Arc<dyn ChannelService>>>,
    pub bot_terminal_observer: Arc<dyn BotTerminalObserverPort>,
    pub human_mention_notify: Option<Arc<dyn HumanMentionNotifyPort>>,
    pub managed_deliveries: Option<Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>>,
    /// Composition supplies only Bots enabled for new Group admissions.
    pub group_delivery_limits: BTreeMap<String, u32>,
    pub delivery_policy: Option<Arc<crate::delivery_policy::LiveDeliveryPolicy>>,
    pub delivery_queue_ttl_ms: Option<i64>,
    pub queue_persistable_headers: Vec<String>,
    /// Retained policy for replies from already-admitted Group runs during drain.
    pub group_reply_delivery_limits: BTreeMap<String, u32>,
    pub delivery_shutdown: OnceLock<(tokio::sync::watch::Sender<bool>, tokio::sync::watch::Receiver<bool>)>,
    pub(crate) delivery_event_locks: tokio::sync::Mutex<BTreeMap<String, std::sync::Weak<tokio::sync::Mutex<()>>>>,
    terminal_owner: OnceLock<std::sync::Weak<BcsMessageFlow>>,
    terminal_slots: Arc<tokio::sync::Semaphore>,
    system_queue: Arc<crate::queued_system::QueuedSystemAdmission>,
}

impl BcsMessageFlow {
    pub fn new(
        group: Arc<dyn GroupCoreService>,
        routing: Arc<dyn RoutingCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        bot_delivery: Arc<dyn BotDeliveryPort>,
        frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    ) -> Self {
        Self {
            group,
            routing,
            registry,
            bot_delivery,
            frontend_delivery,
            task_store: Arc::new(TaskStore::new()),
            bot_relay_turn_limit: 0,
            interceptors: Arc::new(InterceptorChain::new()),
            session_management: None,
            coordination_intents: None,
            bot_run_context: None,
            provider_chat_run_timeout_ms: DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
            system_message: None,
            message_repo: None,
            event_record_factory: None,
            event_recorder: None,
            message_tracker: Arc::new(crate::message_tracker::MessageTracker::new()),
            provider_stream_gray_list: None,
            channel: Arc::new(OnceLock::new()),
            bot_terminal_observer: Arc::new(NoopBotTerminalObserver),
            human_mention_notify: None,
            managed_deliveries: None,
            group_delivery_limits: BTreeMap::new(),
            delivery_policy: None,
            delivery_queue_ttl_ms: None,
            group_reply_delivery_limits: BTreeMap::new(),
            queue_persistable_headers: Vec::new(),
            delivery_shutdown: OnceLock::new(),
            delivery_event_locks: Default::default(),
            terminal_owner: OnceLock::new(),
            terminal_slots: Arc::new(tokio::sync::Semaphore::new(64)),
            system_queue: Arc::new(crate::queued_system::QueuedSystemAdmission::default()),
        }
    }

    pub fn with_coordination_intents(mut self, port: Option<Arc<dyn CoordinationIntentPort>>) -> Self {
        self.coordination_intents = port;
        self
    }

    pub fn channel_slot(&self) -> Arc<OnceLock<Arc<dyn ChannelService>>> {
        self.channel.clone()
    }

    /// Composition installs a weak self-reference so accepted terminal work
    /// can survive a disconnected caller without retaining the service forever.
    pub fn retain_terminal_events(self: &Arc<Self>) {
        let _ = self.terminal_owner.set(Arc::downgrade(self));
        self.system_queue.bind(self);
    }

    pub fn system_queue_port(&self) -> Arc<dyn bcs_service_api::application::system_message::SystemMessageQueueService> {
        self.system_queue.clone()
    }

    pub fn with_managed_deliveries(mut self, service: Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>) -> Self {
        self.managed_deliveries = Some(service);
        self
    }

    pub fn with_group_delivery_limits(mut self, limits: BTreeMap<String, u32>) -> Self {
        self.group_reply_delivery_limits = limits.clone();
        self.group_delivery_limits = limits;
        self
    }

    pub fn pending_message_port(
        &self,
    ) -> ServiceResult<Arc<dyn bcs_service_api::PendingGroupMessagePort>> {
        let run_context = self.bot_run_context.clone().ok_or_else(|| {
            ServiceError::InternalError(
                "pending message reader requires BotRunContextPort".to_string(),
            )
        })?;
        Ok(Arc::new(crate::pending_message::PendingMessageReader::new(
            self.message_tracker.clone(),
            run_context,
        )))
    }

    pub fn with_bot_terminal_observer(
        mut self,
        observer: Arc<dyn BotTerminalObserverPort>,
    ) -> Self {
        self.bot_terminal_observer = observer;
        self
    }

    pub fn with_system_message(mut self, system_message: Arc<dyn SystemMessageService>) -> Self {
        self.system_message = Some(system_message);
        self
    }

    pub fn with_human_mention_notify(
        mut self,
        human_mention_notify: Arc<dyn HumanMentionNotifyPort>,
    ) -> Self {
        self.human_mention_notify = Some(human_mention_notify);
        self
    }

    pub fn with_bot_relay_turn_limit(mut self, bot_relay_turn_limit: i64) -> Self {
        self.bot_relay_turn_limit = bot_relay_turn_limit;
        self
    }

    pub fn with_session_management(
        mut self,
        session_management: Arc<dyn SessionManagementService>,
    ) -> Self {
        self.session_management = Some(session_management);
        self
    }

    pub fn with_bot_run_context(mut self, run_context: Arc<dyn BotRunContextPort>) -> Self {
        self.bot_run_context = Some(run_context);
        self
    }

    pub fn with_provider_chat_run_timeout_ms(mut self, timeout_ms: u64) -> Self {
        self.provider_chat_run_timeout_ms = timeout_ms;
        self
    }

    pub fn with_task_store(mut self, task_store: Arc<TaskStore>) -> Self {
        self.task_store = task_store;
        self
    }

    pub fn with_message_repo(mut self, message_repo: Arc<dyn MessageRepoPort>) -> Self {
        self.message_repo = Some(message_repo);
        self
    }

    pub fn with_event_record_factory(
        mut self,
        event_record_factory: Arc<dyn EventRecordFactoryPort>,
    ) -> Self {
        self.event_record_factory = Some(event_record_factory);
        self
    }

    pub fn with_event_recorder(mut self, event_recorder: Arc<dyn EventRecorderPort>) -> Self {
        self.event_recorder = Some(event_recorder);
        self
    }

    pub fn with_provider_stream_gray_list(
        mut self,
        gray_list: Arc<ProviderStreamGrayList>,
    ) -> Self {
        self.provider_stream_gray_list = Some(gray_list);
        self
    }

    pub fn with_interceptor<I>(mut self, interceptor: I) -> Self
    where
        I: MessageInterceptor + 'static,
    {
        let mut chain = InterceptorChain::new();
        chain.push(interceptor);
        self.interceptors = Arc::new(chain);
        self
    }

    pub fn with_interceptors(mut self, interceptors: Arc<InterceptorChain>) -> Self {
        self.interceptors = interceptors;
        self
    }

    pub(crate) async fn register_send_context(
        &self,
        delivery_type: DeliveryType,
        delivery_target: &BotDeliveryTarget,
        frame: &BcsFrame,
        run_id: &str,
        bot_id: &str,
        group_id: &str,
        bcs_session_id: Option<&str>,
        provider_bypass_headers: &[(String, String)],
    ) -> ServiceResult<()> {
        if delivery_type != DeliveryType::Send {
            return Ok(());
        }
        if let Some(run_context) = &self.bot_run_context {
            let deadline_ms = now_ms().saturating_add(self.provider_chat_run_timeout_ms);
            let session_id = bcs_session_id.unwrap_or(group_id).to_string();
            run_context
                .put_context(BotRunContext {
                    run_id: run_id.to_string(),
                    bot_id: bot_id.to_string(),
                    group_id: group_id.to_string(),
                    bcs_session_id: Some(session_id.clone()),
                    deadline_ms,
                    terminal: false,
                })
                .await;
            let (transport_owner, provider_bypass_headers) = match delivery_target {
                BotDeliveryTarget::WebSocket { .. } => {
                    (BotRunTransportOwner::WebSocket, Vec::new())
                }
                BotDeliveryTarget::HttpProvider {
                    provider_id,
                    provider_bot_ref,
                    ..
                } => (
                    BotRunTransportOwner::HttpProvider {
                        provider_id: provider_id.clone(),
                        provider_bot_ref: provider_bot_ref.clone(),
                    },
                    provider_bypass_headers.to_vec(),
                ),
            };
            run_context
                .register_active_run(ActiveBotRunContext {
                    canonical_run_id: run_id.to_string(),
                    downstream_run_id: run_id.to_string(),
                    downstream_session_key: request_session_key(frame),
                    scope: BotRunScope {
                        group_id: group_id.to_string(),
                        session_id,
                        bot_id: bot_id.to_string(),
                    },
                    transport_owner,
                    provider_bypass_headers,
                    deadline_ms,
                })
                .await?;
        }
        Ok(())
    }

    pub(crate) async fn discard_send_context(&self, run_id: &str) -> ServiceResult<()> {
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(());
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(());
        };
        let _ = run_context.mark_terminal(run_id).await;
        run_context
            .remove_active_run(&context.scope, &context.canonical_run_id)
            .await?;
        Ok(())
    }

    pub(crate) async fn complete_send_context(&self, run_id: &str) -> ServiceResult<()> {
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(());
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(());
        };
        let _ = run_context.mark_terminal(&context.canonical_run_id).await;
        run_context
            .remove_active_run(&context.scope, &context.canonical_run_id)
            .await?;
        Ok(())
    }
}

pub(crate) fn request_session_key(frame: &BcsFrame) -> Option<String> {
    let BcsFrame::Request(request) = frame else {
        return None;
    };
    request
        .params
        .as_ref()
        .and_then(|params| params.get("session_key"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

#[async_trait]
impl MessageFlowService for BcsMessageFlow {
    async fn get_delivery_policy(&self, caller: CallerContext) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.delivery_policy.as_ref().ok_or_else(|| ServiceError::InternalError("delivery policy unavailable".into()))?.get(caller).await
    }
    async fn replace_delivery_policy(&self, caller: CallerContext, expected: u64, policy: bcs_config_api::message_delivery::DeliveryPolicy) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.delivery_policy.as_ref().ok_or_else(|| ServiceError::InternalError("delivery policy unavailable".into()))?.replace(caller, expected, policy).await
    }
    async fn resolve_managed_provider_run(&self, run_id: &str, provider_id: &str, bot_id: &str) -> ServiceResult<Option<BotRunContext>> {
        let Some(service) = &self.managed_deliveries else { return Ok(None); };
        for row in service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Run { bot: bot_id.into(), alias: run_id.into() }).await.map_err(|_| ServiceError::InternalError("managed Provider run lookup failed".into()))? {
            if !matches!(row.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching | bcs_domain::message_delivery::MessageDeliveryStatus::Running | bcs_domain::message_delivery::MessageDeliveryStatus::Unknown | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelling | bcs_domain::message_delivery::MessageDeliveryStatus::CancelUnknown) { continue; }
            if !row.state.may_have_been_sent || row.target_bot_id != bot_id { continue; }
            let Some(metadata) = row.transport_context_json.as_ref() else { continue; };
            if metadata.get("owner").and_then(|v| v.get("provider_id")).and_then(Value::as_str) != Some(provider_id) { continue; }
            if row.run_id.as_deref() != Some(run_id) && row.request_id.as_deref() != Some(run_id)
                && metadata.get("downstream_run_id").and_then(Value::as_str) != Some(run_id) { continue; }
            let owner: BotRunTransportOwner = serde_json::from_value(metadata.get("owner").cloned().unwrap_or(Value::Null))?;
            if !provider_owner_matches_target(&owner, &self.registry.resolve_delivery_target(bot_id).await?) {
                return Err(ServiceError::Forbidden("original Provider binding changed".into()));
            }
            return Ok(Some(BotRunContext {
                run_id: row.run_id.unwrap_or_default(), bot_id: row.target_bot_id, group_id: row.group_id,
                bcs_session_id: Some(row.session_id), deadline_ms: u64::MAX, terminal: false,
            }));
        }
        Ok(None)
    }
    async fn query_message_deliveries(&self, query: bcs_service_api::application::message_delivery::DeliveryStatusQuery) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::DeliveryStatusView>> {
        crate::delivery_control::query(self, query).await
    }
    async fn cancel_message_deliveries(&self, command: bcs_service_api::application::message_delivery::CancelMessageDeliveryCommand) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::CancelMessageDeliveryResult>> {
        crate::delivery_control::cancel(self, command).await
    }
    async fn resolve_message_delivery(&self, command: bcs_service_api::application::message_delivery::ResolveMessageDeliveryCommand) -> ServiceResult<bcs_service_api::application::message_delivery::DeliveryStatusView> {
        crate::delivery_control::resolve(self, command).await
    }
    async fn shutdown_managed_delivery(&self) -> ServiceResult<()> {
        if let Some((sender, completion)) = self.delivery_shutdown.get() {
            let _ = sender.send(true);
            let mut completion = completion.clone();
            tokio::time::timeout(std::time::Duration::from_secs(60), completion.wait_for(|done| *done)).await
                .map_err(|_| ServiceError::InternalError("queue shutdown timed out".into()))?
                .map_err(|_| ServiceError::InternalError("queue shutdown completion unavailable".into()))?;
        }
        Ok(())
    }
    async fn record_delivery_acceptance(&self, request_id: &str, bot_id: &str, downstream_run_id: Option<&str>) -> ServiceResult<()> {
        if let Some(service) = &self.managed_deliveries {
            service.accept_run(request_id, bot_id, downstream_run_id, Utc::now().timestamp_millis())
                .await.map_err(|_| ServiceError::InternalError("managed delivery ACK persistence failed".into()))?;
        }
        Ok(())
    }
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        handle_web_send(self, cmd).await
    }

    async fn handle_group_chat(&self, cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        handle_group_chat(self, cmd).await
    }

    async fn handle_persistent_group_send(
        &self,
        cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        handle_persistent_group_send(self, cmd).await
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        if self.managed_deliveries.is_some()
            && matches!(cmd.state, ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted)
            && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
            && let Some(owner) = self.terminal_owner.get().and_then(std::sync::Weak::upgrade)
        {
            // Bound retained payloads. While full, apply backpressure instead
            // of spawning an unbounded terminal retry queue.
            let permit = self.terminal_slots.clone().acquire_owned().await
                .map_err(|_| ServiceError::InternalError("terminal processing unavailable".into()))?;
            return tokio::spawn(async move {
                let _permit = permit;
                let result = crate::bot_event::handle_bot_event(&owner, cmd).await;
                if result.is_err() { tracing::warn!("retained terminal processing failed; delivery state remains authoritative"); }
                result
            }.in_current_span()).await.map_err(|error| ServiceError::InternalError(format!("terminal processing task failed: {error}")))?;
        }
        crate::bot_event::handle_bot_event(self, cmd).await
    }

    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        handle_group_callback(self, cmd).await
    }

    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        handle_chat_abort(self, cmd).await
    }

    async fn cancel_latest_queued_message(
        &self,
        cmd: CancelLatestQueuedMessageCommand,
    ) -> ServiceResult<CancelLatestQueuedMessageOutcome> {
        crate::delivery_control::cancel_latest_queued(self, cmd).await
    }

    async fn resolve_chat_abort_scope(
        &self,
        group_id: &str,
        run_id: &str,
    ) -> ServiceResult<Option<ChatAbortScope>> {
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(None);
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(None);
        };
        if context.scope.group_id != group_id {
            return Ok(None);
        }
        Ok(Some(ChatAbortScope {
            group_id: context.scope.group_id,
            session_id: context.scope.session_id,
            bot_id: context.scope.bot_id,
        }))
    }

    async fn rebind_channel_source_message(
        &self,
        source_run_id: &str,
        accepted_run_id: &str,
    ) -> ServiceResult<bool> {
        Ok(self
            .message_tracker
            .rebind_channel_source_message_id(source_run_id, accepted_run_id)
            .await)
    }

    async fn register_task_run_alias(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        let result = self
            .task_store
            .register_alias_for_dispatched_target(task_id, run_id, bot_id)
            .await;
        Ok(match result {
            Some(true) => TaskRunAliasRegistration::Registered,
            Some(false) => TaskRunAliasRegistration::Rejected,
            None => TaskRunAliasRegistration::NotTask,
        })
    }

    async fn handle_task_dispatch(
        &self,
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        crate::task_flow::handle_task_dispatch(self, cmd).await
    }

    async fn handle_task_message(
        &self,
        cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        crate::task_flow::handle_task_message(self, cmd).await
    }

    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        crate::task_flow::handle_task_complete(self, cmd).await
    }
}

pub(crate) async fn manager_worker_self_owner(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: Option<&str>,
    sender_id: &str,
) -> Option<String> {
    let group = flow.group.get(group_id).await?;
    if group.group_strategy == GroupStrategy::ManagerWorker {
        if let (Some(session_id), Some(session_mgmt)) =
            (session_id, flow.session_management.as_ref())
        {
            if let Ok(Some(session)) = session_mgmt.get(session_id).await {
                if session.group_id == group_id {
                    if let Some(participant) = session
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid == sender_id)
                    {
                        return (participant.is_bot()
                            && participant.role == ParticipantRole::Worker)
                            .then(|| sender_id.to_string());
                    }
                }
            }
        }

        if let Some(participant) = group.get_participant(sender_id) {
            if participant.is_bot() && participant.role == ParticipantRole::Worker {
                return Some(sender_id.to_string());
            }
        }
    } else {
        return None;
    }
    None
}

pub(crate) async fn apply_session_participant_scope(
    flow: &BcsMessageFlow,
    group: &mut Group,
    session_id: Option<&str>,
) -> ServiceResult<()> {
    let (Some(session_id), Some(session_mgmt)) = (session_id, flow.session_management.as_ref())
    else {
        return Ok(());
    };
    let session = session_mgmt
        .get(session_id)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
    if session.group_id != group.id {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "session '{}' does not belong to group '{}'",
                session_id, group.id
            ),
            request_id: None,
        });
    }
    if session.participants.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: format!("session '{}' has no participants", session_id),
            request_id: None,
        });
    }
    group.participants = session.participants;
    Ok(())
}

mod persistence;
pub(crate) use persistence::{try_persist_group_message, persisted_message_visibility};
use persistence::{persisted_inbound_content};
mod web_send;
pub use web_send::{handle_web_send};
mod group_send;
pub use group_send::{handle_group_chat, handle_persistent_group_send};
mod interceptors;
pub use interceptors::{apply_a2a_interceptors, apply_task_interceptors};
pub(crate) use interceptors::{apply_outbound_interceptors, apply_notify_outbound_policy};
mod callback_abort;
pub use callback_abort::{handle_group_callback, handle_chat_abort};
use callback_abort::{provider_owner_matches_target};
mod routing;
pub(crate) use routing::{apply_overlay_to_decision};
use routing::{resolve_group_chat_sender, verify_group_chat_caller_access, verify_group_chat_sender, verify_http_group_message_sender, is_human_bot_dm, verify_http_group_message_caller_access, sender_name_for_chat, build_route_overlay, build_explicit_mention_decision, callback_routable_message, mentions_all, delivery_result_summary, sender_display_name, preferred_sender_display_name, from_bot_owner};
mod delivery;
pub use delivery::{build_chat_abort_frame};
pub(crate) use delivery::{ContextProjection, context_projection_for_delivery, frame_protocol_version, frontend_domain_for_group};
use delivery::{frame_for_target, bot_delivery_kind, delivery_metric_kind, publish_web_user_message, publish_group_callback_event, publish_chat_abort_event, route_source_for_web_send, log_message_received, log_routing_digest, log_bot_deliver_result};

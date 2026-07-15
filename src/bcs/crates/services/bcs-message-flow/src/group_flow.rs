use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use bcs_domain::{Attachment, NewMessage, SenderType};
use bcs_protocol::{
    Attachment as WireAttachment, BcsFrame, RequestFrame, build_chat_inject_frame,
    build_chat_send_frame, build_direct_chat_inject_frame, build_direct_chat_send_frame,
    build_session_key, now_ms,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryResult, BotDeliveryTarget, BotEventCommand, BotEventOutcome, BotRunContext, BotRunContextPort,
    BotTerminalObserverPort, NoopBotTerminalObserver,
    BotRegistryCoreService, CallerContext, ChatAbortCommand, ChatAbortOutcome,
    DeliveryBlockContext, DeliveryBlockReason,
    DeliveryBlockSurface, DeliveryMetricKind, DeliveryMetricTarget, DeliveryType,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryResult,
    FrontendDeliveryTarget,
    Group, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome,
    GroupKind, GroupMessage, GroupMessageType, GroupCoreService, GroupStatus, GroupStrategy, HiddenMentionInfo, MessageDeliveryResult,
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MessageLogTargetSummary, MESSAGE_LOG_SCHEMA_VERSION, message_log_json,
    MessageFlowService, MessageRole, Participant, ParticipantMode, ParticipantRole, PersistentGroupSendCommand,
    PersistentGroupSendOutcome, ProviderStreamGrayList, ProviderTransportPreference,
    RouteParticipantOverlay, RoutingDecision, RoutingCoreService, RoutingTarget, ServiceError, ServiceResult,
    SessionManagementService, ChannelService,
    SystemMessageEvent, SystemMessageService,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration, WebSendCommand,
    WebSendOutcome,
    backfill_bot_names,
    interceptor::{BlockReason, InterceptorChain, InterceptorDecision, MessageInterceptor, OutboundMessage},
    port::repo::MessageRepoPort,
};
use regex::Regex;
use serde_json::Value;
use tracing::{debug, info, warn};

use crate::protocol_context::{group_context_input, group_type_wire};
use crate::task_store::TaskStore;
use crate::MSG_LOG_TARGET;

const DEFAULT_GROUP_BOT_CALLBACK_TIMEOUT_MS: u64 = 60 * 60 * 1_000;

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
    pub bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub message_repo: Option<Arc<dyn MessageRepoPort>>,
    pub message_tracker: Arc<crate::message_tracker::MessageTracker>,
    pub provider_stream_gray_list: Option<Arc<ProviderStreamGrayList>>,
    pub channel: Arc<OnceLock<Arc<dyn ChannelService>>>,
    pub bot_terminal_observer: Arc<dyn BotTerminalObserverPort>,
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
            bot_run_context: None,
            system_message: None,
            message_repo: None,
            message_tracker: Arc::new(crate::message_tracker::MessageTracker::new()),
            provider_stream_gray_list: None,
            channel: Arc::new(OnceLock::new()),
            bot_terminal_observer: Arc::new(NoopBotTerminalObserver),
        }
    }

    pub fn channel_slot(&self) -> Arc<OnceLock<Arc<dyn ChannelService>>> {
        self.channel.clone()
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

    pub fn with_task_store(mut self, task_store: Arc<TaskStore>) -> Self {
        self.task_store = task_store;
        self
    }

    pub fn with_message_repo(mut self, message_repo: Arc<dyn MessageRepoPort>) -> Self {
        self.message_repo = Some(message_repo);
        self
    }

    pub fn with_provider_stream_gray_list(
        mut self,
        gray_list: Arc<ProviderStreamGrayList>,
    ) -> Self {
        self.provider_stream_gray_list = Some(gray_list);
        self
    }

    pub(crate) async fn provider_transport_preference(
        &self,
        target_bot_id: &str,
        delivery_kind: &BotDeliveryKind,
        delivery_target: &BotDeliveryTarget,
    ) -> ProviderTransportPreference {
        if !matches!(
            delivery_kind,
            BotDeliveryKind::Send
                | BotDeliveryKind::TaskDispatch
                | BotDeliveryKind::TaskMessage
                | BotDeliveryKind::TaskResult
        ) {
            return ProviderTransportPreference::Callback;
        }
        if !matches!(
            delivery_target,
            BotDeliveryTarget::HttpProvider { protocol_version, .. } if protocol_version == "2.0"
        ) {
            return ProviderTransportPreference::Callback;
        }
        let Some(gray_list) = &self.provider_stream_gray_list else {
            return ProviderTransportPreference::Callback;
        };
        let created_by = self
            .registry
            .get(target_bot_id)
            .await
            .and_then(|bot| bot.created_by);
        if gray_list.contains(created_by.as_deref()) {
            ProviderTransportPreference::CallbackSse
        } else {
            ProviderTransportPreference::Callback
        }
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

    pub(crate) async fn record_successful_send_context(
        &self,
        delivery_type: DeliveryType,
        result: &BotDeliveryResult,
        run_id: &str,
        bot_id: &str,
        group_id: &str,
        bcs_session_id: Option<&str>,
    ) {
        if delivery_type != DeliveryType::Send || !result.delivered {
            return;
        }
        if let Some(run_context) = &self.bot_run_context {
            run_context
                .put_context(BotRunContext {
                    run_id: run_id.to_string(),
                    bot_id: bot_id.to_string(),
                    group_id: group_id.to_string(),
                    bcs_session_id: bcs_session_id.map(str::to_string),
                    deadline_ms: now_ms().saturating_add(DEFAULT_GROUP_BOT_CALLBACK_TIMEOUT_MS),
                    terminal: false,
                })
                .await;
        }
    }
}

#[async_trait]
impl MessageFlowService for BcsMessageFlow {
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

pub(crate) async fn try_persist_group_message(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: Option<&str>,
    sender_id: &str,
    sender_type: SenderType,
    message_type: &str,
    content: Value,
    client_msg_id: Option<&str>,
    owner_bot_id: Option<String>,
    run_id: &str,
) {
    let Some(ref repo) = flow.message_repo else {
        return;
    };
    let msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.unwrap_or("").to_string(),
        sender_id: sender_id.to_string(),
        sender_type,
        message_type: message_type.to_string(),
        content,
        client_msg_id: client_msg_id.map(str::to_string),
        owner_bot_id,
        created_at: now_ms(),
        run_id: run_id.to_string(),
    };
    if let Err(e) = repo.append_message(msg).await {
        warn!(
            group_id = %group_id,
            error = %e,
            "failed to persist group message to message store"
        );
    } else {
        info!(
            group_id = %group_id,
            sender_id = %sender_id,
            message_type,
            "group message persisted"
        );
    }
}

fn persisted_inbound_content(content: &str, attachments: Option<&[Attachment]>) -> Value {
    let Some(attachments) = attachments.filter(|items| !items.is_empty()) else {
        return Value::String(content.to_string());
    };
    serde_json::json!({
        "text": content,
        "attachments": attachments
            .iter()
            .map(Attachment::stable_metadata)
            .collect::<Vec<_>>(),
    })
}

#[cfg(test)]
mod attachment_persistence_tests {
    use bcs_domain::{Attachment, AttachmentType};

    use super::persisted_inbound_content;

    #[test]
    fn temporary_attachment_url_is_not_persisted_in_message_history() {
        let attachment = Attachment {
            attachment_id: "att-1".to_string(),
            attachment_type: AttachmentType::Image,
            file_name: "image".to_string(),
            mime_type: None,
            size: None,
            sha256: None,
            url: "https://download.example.com/image?token=temporary".to_string(),
            expires_at: None,
        };

        let persisted = persisted_inbound_content("look", Some(&[attachment]));

        assert_eq!(persisted["text"], "look");
        assert_eq!(persisted["attachments"][0]["attachment_id"], "att-1");
        assert!(persisted["attachments"][0].get("url").is_none());
        assert!(!persisted.to_string().contains("token=temporary"));
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
        if let (Some(session_id), Some(session_mgmt)) = (session_id, flow.session_management.as_ref()) {
            if let Ok(Some(session)) = session_mgmt.get(session_id).await {
                if session.group_id == group_id {
                    if let Some(participant) = session
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid == sender_id)
                    {
                        return (participant.is_bot() && participant.role == ParticipantRole::Worker)
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

pub async fn handle_web_send(
    flow: &BcsMessageFlow,
    cmd: WebSendCommand,
) -> ServiceResult<WebSendOutcome> {
    log_message_received(&cmd, MessageLogMode::FreeChat);

    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    if group.status == GroupStatus::Inactive {
        flow.group
            .update_status(&cmd.group_id, GroupStatus::Active)
            .await?;
        group = flow
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
    }

    if group.status != GroupStatus::Active {
        return Err(ServiceError::InvalidOperation {
            message: format!("group '{}' is not active", cmd.group_id),
            request_id: None,
        });
    }

    flow.group.reset_message_count(&cmd.group_id).await?;
    backfill_bot_names(flow.registry.as_ref(), &mut group).await;
    if let Some(ref bcs_session_id) = cmd.session_id {
        if let Some(ref session_mgmt) = flow.session_management {
            match session_mgmt.get(bcs_session_id).await {
                Ok(Some(sess)) => {
                    if sess.group_id != cmd.group_id {
                        return Err(ServiceError::InvalidOperation {
                            message: format!(
                                "session '{}' does not belong to group '{}'",
                                bcs_session_id, cmd.group_id
                            ),
                            request_id: None,
                        });
                    }
                    if sess.participants.is_empty() {
                        return Err(ServiceError::InvalidOperation {
                            message: format!("session '{}' has no participants", bcs_session_id),
                            request_id: None,
                        });
                    }
                    group.participants = sess.participants;
                    backfill_bot_names(flow.registry.as_ref(), &mut group).await;
                }
                Ok(None) => {
                    return Err(ServiceError::SessionNotFound(bcs_session_id.clone()));
                }
                Err(error) => {
                    return Err(ServiceError::InternalError(error.to_string()));
                }
            }
        }
    }

    let overlay = build_route_overlay(flow, &group).await;
    let decision = if group.group_kind == GroupKind::Dm {
        flow.routing
            .route_dm_with_overlay(&group, &cmd.message, &cmd.from_actor_id, &overlay)
            .await
    } else if cmd.mentions.is_empty() {
        flow.routing
            .route_with_overlay(&group, &cmd.message, None, &overlay)
            .await
    } else {
        build_explicit_mention_decision(&group, &cmd.mentions, &cmd.message, &overlay)
    };

    log_routing_digest(&cmd, &decision, MessageLogMode::FreeChat, route_source_for_web_send(&group, &cmd));

    let sender_display_name = preferred_sender_display_name(flow, &cmd).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.from_actor_id).await;
    let sender_type = if cmd.from_actor_id.starts_with("human_") {
        SenderType::Human
    } else {
        SenderType::Bot
    };
    try_persist_group_message(
        flow,
        &cmd.group_id,
        cmd.session_id.as_deref(),
        &cmd.from_actor_id,
        sender_type,
        "chat",
        persisted_inbound_content(&decision.cleaned_message, cmd.attachments.as_deref()),
        cmd.idempotency_key.as_deref(),
        None,
        "", // run_id: user messages don't associate with bot runs
    )
    .await;
    let mut active_run_ids = Vec::new();
    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();

    for target in &decision.targets {
        let run_id = uuid::Uuid::new_v4().to_string();
        let target_bot_id = target.bot_uuid.clone();
        let delivery_type = target.delivery_type;
        let outbound_candidate = GroupMessage {
            id: run_id.clone(),
            timestamp: now_ms(),
            sender: cmd.from_actor_id.clone(),
            content: decision.cleaned_message.clone(),
            message_type: GroupMessageType::Bot,
            bot_name: Some(sender_display_name.clone()),
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
        };
        let outbound_message = match apply_outbound_interceptors(
            flow,
            &cmd.group_id,
            &outbound_candidate,
            target,
        )
        .await
        {
            Ok(message) => message,
            Err(reason) => {
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(reason.message.as_str()),
                    Some("interceptor"),
                );
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(reason.message.clone()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(ServiceError::Unauthorized(reason.message)),
                });
                continue;
            }
        };
        let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
            Ok(target) => target,
            Err(error) => {
                let error_text = error.to_string();
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("resolve_target"),
                );
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &cmd,
            &decision,
            &target,
            &delivery_target,
            &run_id,
            &outbound_message.content,
            &sender_display_name,
            from_bot_owner.clone(),
        )
        .await;
        // FIXME(interceptor-modify): outbound_message.id and metadata are not
        // threaded into the frame. SecurityInterceptor rewrites .id with the
        // gateway-issued task_id, but downstream still sees run_id. Tracked
        // in the Phase-5 follow-up list (Modify semantics completeness).

        let delivery_kind = bot_delivery_kind(delivery_type);
        let provider_transport = flow
            .provider_transport_preference(&target_bot_id, &delivery_kind, &delivery_target)
            .await;
        let delivery = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind,
                provider_transport,
            })
            .await;

        match delivery {
            Ok(result) => {
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string).as_deref(),
                    None,
                );
                if delivery_type == DeliveryType::Send && result.delivered {
                    active_run_ids.push(run_id.clone());
                    flow.record_successful_send_context(
                        delivery_type,
                        &result,
                        &run_id,
                        &target_bot_id,
                        &cmd.group_id,
                        cmd.session_id.as_deref(),
                    )
                    .await;
                }
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string),
                ));
                bot_deliveries.push(result);
            }
            Err(error) => {
                let error_text = error.to_string();
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("deliver"),
                );
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    let frontend_deliveries = publish_web_user_message(flow, &cmd).await;

    // Notify when @-mentioned (Send) bots failed delivery. A failed delivery
    // is reported as "已离线" when the bot is genuinely unreachable
    // (is_available=false / no resolvable delivery target); when the bot is
    // still online (is_available=true) but the delivery itself failed (e.g. a
    // transient HTTP provider webhook error), it is reported as a retryable
    // delivery failure instead, so a still-active bot is not wrongly shown as
    // offline.
    {
        let failed_targets: Vec<&RoutingTarget> = decision
            .targets
            .iter()
            .filter(|t| t.delivery_type == DeliveryType::Send)
            .filter(|t| {
                bot_deliveries
                    .iter()
                    .any(|r| r.target_bot_id == t.bot_uuid && !r.delivered)
            })
            .collect();

        let mut offline_bot_names: Vec<String> = Vec::new();
        let mut failed_bot_names: Vec<String> = Vec::new();
        for t in &failed_targets {
            let name = group
                .get_participant(&t.bot_uuid)
                .and_then(|p| p.bot_name.clone())
                .unwrap_or_else(|| t.bot_uuid.clone());
            let is_online = match flow.registry.resolve_delivery_target(&t.bot_uuid).await {
                Ok(target) => flow.bot_delivery.is_available(&target).await,
                Err(_) => false,
            };
            if is_online {
                failed_bot_names.push(name);
            } else {
                offline_bot_names.push(name);
            }
        }

        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
            let receivers: Vec<Participant> = group
                .participants
                .iter()
                .filter(|p| p.is_bot() && p.bot_uuid != cmd.from_actor_id)
                .cloned()
                .collect();

            if !offline_bot_names.is_empty() {
                let names = offline_bot_names.join("、");
                let message = format!("Bot {} 已离线", names);
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message,
                    receivers: receivers.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }

            if !failed_bot_names.is_empty() {
                let names = failed_bot_names.join("、");
                let message = format!("消息投递给 Bot {} 失败，请稍后重试", names);
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message,
                    receivers,
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    if !decision.hidden_mentions.is_empty() {
        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
            for hidden in &decision.hidden_mentions {
                let event = SystemMessageEvent::BotHiddenNotice {
                    group_id: cmd.group_id.clone(),
                    mentioner_bot_id: cmd.from_actor_id.clone(),
                    hidden_bot_name: hidden.hidden_bot_name.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    // Notify when @-mentioned bots are muted (downgraded Send→Inject).
    // Aggregates multiple muted bots into a single system message.
    {
        let muted_bot_names: Vec<String> = decision
            .targets
            .iter()
            .filter(|t| {
                t.delivery_type == DeliveryType::Inject
                    && decision.mentions.contains(&t.bot_uuid)
            })
            .filter_map(|t| {
                overlay
                    .iter()
                    .find(|o| o.bot_uuid == t.bot_uuid)
                    .and_then(|row| {
                        let effective_mode = row
                            .mode
                            .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind));
                        if effective_mode == ParticipantMode::Muted
                            && row.status != ActorStatus::Hidden
                        {
                            Some(
                                row.bot_name
                                    .clone()
                                    .or_else(|| {
                                        group
                                            .get_participant(&t.bot_uuid)
                                            .and_then(|p| p.bot_name.clone())
                                    })
                                    .unwrap_or_else(|| t.bot_uuid.clone()),
                            )
                        } else {
                            None
                        }
                    })
            })
            .collect();

        if !muted_bot_names.is_empty() {
            if let Some(ref system_message) = flow.system_message {
                let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
                let names = muted_bot_names.join("、");
                let message = format!("Bot {} 已切换成禁言模式", names);
                let receivers: Vec<Participant> = group
                    .participants
                    .iter()
                    .filter(|p| p.is_bot() && p.bot_uuid != cmd.from_actor_id)
                    .cloned()
                    .collect();
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message,
                    receivers,
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    let primary_run_id = active_run_ids
        .first()
        .cloned()
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

    Ok(WebSendOutcome {
        primary_run_id,
        status: "started".to_string(),
        active_run_ids,
        bot_deliveries,
        frontend_deliveries,
        mentions: decision.mentions,
        hidden_mentions: decision.hidden_mentions,
        delivered_count: delivery_results
            .iter()
            .filter(|result| result.success)
            .count(),
        failed_count: delivery_results
            .iter()
            .filter(|result| !result.success)
            .count(),
        delivery_results,
    })
}

pub async fn handle_group_chat(
    flow: &BcsMessageFlow,
    cmd: GroupChatCommand,
) -> ServiceResult<GroupChatOutcome> {
    let group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    verify_group_chat_caller_access(flow, &group, &cmd.caller).await?;
    let sender_id = resolve_group_chat_sender(&cmd)?;
    verify_group_chat_sender(flow, &group, &sender_id, &cmd.caller).await?;
    let from_name = sender_name_for_chat(flow, &cmd.caller, &sender_id).await;

    let outcome = handle_web_send(
        flow,
        WebSendCommand {
            caller: cmd.caller,
            group_id: cmd.group_id.clone(),
            session_id: cmd.session_id,
            from_actor_id: sender_id,
            from_name,
            message: cmd.message,
            mentions: Vec::new(),
            attachments: None,
            thinking: None,
            idempotency_key: None,
            sender_conn_id: None,
        },
    )
    .await?;

    Ok(GroupChatOutcome {
        group_id: cmd.group_id,
        driver_bot_id: group.driver_bot,
        delivered_count: outcome.delivered_count,
        failed_count: outcome.failed_count,
        delivery_results: outcome.delivery_results,
        mentions: outcome.mentions,
        hidden_mentions: outcome.hidden_mentions,
    })
}

pub async fn handle_persistent_group_send(
    flow: &BcsMessageFlow,
    cmd: PersistentGroupSendCommand,
) -> ServiceResult<PersistentGroupSendOutcome> {
    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    verify_http_group_message_caller_access(flow, &group, &cmd.caller).await?;
    verify_http_group_message_sender(flow, &group, &cmd.sender, &cmd.caller).await?;
    if group.get_participant(&cmd.sender).is_none() {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' is not a participant of group '{}'",
            cmd.sender, cmd.group_id
        )));
    }

    backfill_bot_names(flow.registry.as_ref(), &mut group).await;

    if group.status != GroupStatus::Active {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "Group '{}' is not active (status: {:?})",
                cmd.group_id, group.status
            ),
            request_id: None,
        });
    }

    if cmd.max_group_messages > 0 {
        let count = flow.group.message_count(&cmd.group_id).await?;
        if count >= cmd.max_group_messages as usize {
            let _ = flow
                .group
                .update_status(&cmd.group_id, GroupStatus::Inactive)
                .await;
            return Err(ServiceError::MessageLimitReached(format!(
                "Group '{}' already has {} messages (max {})",
                cmd.group_id, count, cmd.max_group_messages
            )));
        }
    }

    let _ = flow.group.increment_message_count(&cmd.group_id).await;

    let message = GroupMessage {
        id: uuid::Uuid::new_v4().to_string(),
        timestamp: now_ms(),
        sender: cmd.sender.clone(),
        content: cmd.content.clone(),
        message_type: cmd.message_type,
        bot_name: None,
        role: cmd.role,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
    };

    let sender_type = if cmd.sender.starts_with("human_") {
        SenderType::Human
    } else {
        SenderType::Bot
    };
    try_persist_group_message(
        flow,
        &cmd.group_id,
        None,
        &cmd.sender,
        sender_type,
        "chat",
        Value::String(cmd.content.clone()),
        None,
        None,
        "", // run_id: persistent send
    )
    .await;

    let decision = if group.group_kind == GroupKind::Dm {
        let overlay = build_route_overlay(flow, &group).await;
        flow.routing
            .route_dm_with_overlay(&group, &message.content, message.sender.as_str(), &overlay)
            .await
    } else {
        flow.routing
            .route(&group, &message.content, Some(message.sender.as_str()))
            .await
    };

    let mut routed_to = Vec::new();
    let sender_display_name = sender_display_name(flow, &cmd.sender).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.sender).await;
    let synthetic_send = WebSendCommand {
        caller: cmd.caller.clone(),
        group_id: cmd.group_id.clone(),
        session_id: None,
        from_actor_id: cmd.sender.clone(),
        from_name: Some(sender_display_name.clone()),
        message: cmd.content.clone(),
        mentions: decision.mentions.clone(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        sender_conn_id: None,
    };
    for target in &decision.targets {
        let outbound = match apply_outbound_interceptors(flow, &cmd.group_id, &message, target).await
        {
            Ok(message) => message,
            Err(reason) => {
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target.bot_uuid,
                    interceptor = %reason.interceptor_id,
                    code = %reason.code,
                    "outbound message blocked by interceptor"
                );
                continue;
            }
        };
        routed_to.push(target.bot_uuid.clone());

        let run_id = uuid::Uuid::new_v4().to_string();
        let delivery_target = match flow.registry.resolve_delivery_target(&target.bot_uuid).await {
            Ok(delivery_target) => delivery_target,
            Err(error) => {
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target.bot_uuid,
                    error = %error,
                    "Failed to resolve delivery target"
                );
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &synthetic_send,
            &decision,
            target,
            &delivery_target,
            &run_id,
            &outbound.content,
            &sender_display_name,
            from_bot_owner.clone(),
        )
        .await;
        let delivery_kind = bot_delivery_kind(target.delivery_type);
        let provider_transport = flow
            .provider_transport_preference(&target.bot_uuid, &delivery_kind, &delivery_target)
            .await;
        let result = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind,
                provider_transport,
            })
            .await;
        match result {
            Ok(result) => {
                flow.record_successful_send_context(
                    target.delivery_type,
                    &result,
                    &run_id,
                    &target.bot_uuid,
                    &cmd.group_id,
                    None,
                )
                .await;
                if !result.delivered {
                    warn!(
                        session_id = %cmd.group_id,
                        bot_uuid = %target.bot_uuid,
                        error = ?result.error,
                        "Failed to send message to bot"
                    );
                }
            }
            Err(error) => {
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target.bot_uuid,
                    error = %error,
                    "Failed to send message to bot"
                );
            }
        }
    }

    if cmd.store_messages {
        flow.group
            .add_message(&cmd.group_id, message.clone())
            .await?;
    }

    Ok(PersistentGroupSendOutcome {
        message_id: message.id,
        routed_to,
        mentions: decision.mentions,
    })
}

pub(crate) async fn apply_outbound_interceptors(
    flow: &BcsMessageFlow,
    group_id: &str,
    message: &GroupMessage,
    target: &RoutingTarget,
) -> Result<GroupMessage, BlockReason> {
    if flow.interceptors.is_empty() {
        return Ok(message.clone());
    }

    // Backward compatibility: if either side has no agent_code, the security
    // gateway has no policy to evaluate. Pre-refactor BCS had no outbound
    // interceptor at all, so missing credentials must NOT silently block —
    // skip the chain and warn instead. Bots that have completed AgentPass
    // registration have credentials; legacy/dev bots that never registered
    // would otherwise be mass-blocked when security_gateway.dry_run=false.
    let caller = match flow.registry.get_agent_credentials(&message.sender).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %message.sender,
                receiver = %target.bot_uuid,
                "skipping outbound interceptor chain: sender has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message.clone());
        }
    };
    let receiver = match flow.registry.get_agent_credentials(&target.bot_uuid).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %message.sender,
                receiver = %target.bot_uuid,
                "skipping outbound interceptor chain: receiver has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message.clone());
        }
    };
    let mut outbound = OutboundMessage {
        group_id: group_id.to_string(),
        message: message.clone(),
        receiver_bot_id: target.bot_uuid.clone(),
        caller,
        receiver,
    };

    let block_context = DeliveryBlockContext {
        target: DeliveryMetricTarget::Bot,
        delivery_kind: delivery_metric_kind(target.delivery_type),
        surface: DeliveryBlockSurface::GroupMessage,
        reason: DeliveryBlockReason::PolicyBlocked,
    };
    match flow
        .interceptors
        .on_outbound_with_context(&mut outbound, block_context)
        .await
    {
        InterceptorDecision::Pass | InterceptorDecision::Modify => Ok(outbound.message),
        InterceptorDecision::Block(reason) => Err(reason),
    }
}

/// Run the outbound interceptor chain for an A2A (1:1 bot-to-bot) chat.
///
/// A2A chats have no group context — `context_tag` is purely a log/trace label
/// (typically the run_id). The synthetic GroupMessage carries only the three
/// fields current interceptors actually inspect: id, sender, content.
///
/// Returns the (possibly modified) message id on Pass/Modify, or the BlockReason.
pub async fn apply_a2a_interceptors(
    flow: &BcsMessageFlow,
    context_tag: &str,
    sender_bot_id: &str,
    target_bot_id: &str,
    message_id: &str,
    message_content: &str,
) -> Result<String, BlockReason> {
    apply_chain_for_bot_pair(
        flow,
        context_tag,
        sender_bot_id,
        target_bot_id,
        message_id,
        message_content,
        DeliveryBlockSurface::DirectChat,
        DeliveryMetricKind::Send,
    )
    .await
}

/// Run the outbound interceptor chain for a master-slave task dispatch.
///
/// Task dispatches are bot-to-bot directives within a group, but the wire
/// frame is built from raw JSON rather than a GroupMessage. This helper
/// adapts the chain accordingly. `context_tag` should be the task_id.
pub async fn apply_task_interceptors(
    flow: &BcsMessageFlow,
    context_tag: &str,
    driver_bot_id: &str,
    target_bot_id: &str,
    task_id: &str,
    message_content: &str,
) -> Result<String, BlockReason> {
    apply_chain_for_bot_pair(
        flow,
        context_tag,
        driver_bot_id,
        target_bot_id,
        task_id,
        message_content,
        DeliveryBlockSurface::Task,
        DeliveryMetricKind::TaskDispatch,
    )
    .await
}

/// Internal helper: run the chain on a synthetic OutboundMessage built from
/// primitive fields. Used by both A2A and task helpers above. Mirrors the
/// missing-credentials skip behavior in `apply_outbound_interceptors` so
/// legacy/unregistered bots are not silently blocked.
async fn apply_chain_for_bot_pair(
    flow: &BcsMessageFlow,
    context_tag: &str,
    sender_bot_id: &str,
    receiver_bot_id: &str,
    message_id: &str,
    message_content: &str,
    surface: DeliveryBlockSurface,
    delivery_kind: DeliveryMetricKind,
) -> Result<String, BlockReason> {
    if flow.interceptors.is_empty() {
        return Ok(message_id.to_string());
    }

    let caller = match flow.registry.get_agent_credentials(sender_bot_id).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %sender_bot_id,
                receiver = %receiver_bot_id,
                context = %context_tag,
                "skipping outbound interceptor chain: sender has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message_id.to_string());
        }
    };
    let receiver = match flow.registry.get_agent_credentials(receiver_bot_id).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %sender_bot_id,
                receiver = %receiver_bot_id,
                context = %context_tag,
                "skipping outbound interceptor chain: receiver has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message_id.to_string());
        }
    };

    // Synthetic GroupMessage: only the three fields SecurityInterceptor reads.
    // group_id slot is reused as a context tag for log correlation.
    let synthetic = GroupMessage {
        id: message_id.to_string(),
        timestamp: 0,
        sender: sender_bot_id.to_string(),
        content: message_content.to_string(),
        message_type: GroupMessageType::default(),
        bot_name: None,
        role: MessageRole::default(),
        run_id: String::new(),
        history_meta: None,
        metadata: None,
    };
    let mut outbound = OutboundMessage {
        group_id: context_tag.to_string(),
        message: synthetic,
        receiver_bot_id: receiver_bot_id.to_string(),
        caller,
        receiver,
    };

    let block_context = DeliveryBlockContext {
        target: DeliveryMetricTarget::Bot,
        delivery_kind,
        surface,
        reason: DeliveryBlockReason::PolicyBlocked,
    };
    match flow
        .interceptors
        .on_outbound_with_context(&mut outbound, block_context)
        .await
    {
        InterceptorDecision::Pass | InterceptorDecision::Modify => Ok(outbound.message.id),
        InterceptorDecision::Block(reason) => Err(reason),
    }
}

pub async fn handle_group_callback(
    flow: &BcsMessageFlow,
    cmd: GroupCallbackCommand,
) -> ServiceResult<GroupCallbackOutcome> {
    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    backfill_bot_names(flow.registry.as_ref(), &mut group).await;
    let overlay = build_route_overlay(flow, &group).await;
    let routable_message = callback_routable_message(&group, &cmd);
    let decision = if cmd.mentions.is_empty() || mentions_all(&cmd.mentions) {
        flow.routing
            .route_with_overlay(&group, &routable_message, None, &overlay)
            .await
    } else {
        build_explicit_mention_decision(&group, &cmd.mentions, &routable_message, &overlay)
    };

    if cmd.store_message {
        let group_message = GroupMessage {
            id: uuid::Uuid::new_v4().to_string(),
            timestamp: now_ms(),
            sender: "system".to_string(),
            content: cmd.message.clone(),
            message_type: GroupMessageType::System,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: cmd.metadata.clone(),
        };
        try_persist_group_message(
            flow,
            &cmd.group_id,
            None,
            "system",
            SenderType::System,
            "system",
            Value::String(cmd.message.clone()),
            None,
            None,
            "", // run_id: group callback
        )
        .await;
        if let Err(error) = flow.group.add_message(&cmd.group_id, group_message).await {
            warn!(
                group_id = %cmd.group_id,
                error = %error,
                "failed to persist group callback message"
            );
        }
    }

    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();
    let sender_display_name = "system".to_string();
    let callback_send = WebSendCommand {
        caller: CallerContext::Public,
        group_id: cmd.group_id.clone(),
        session_id: None,
        from_actor_id: "system".to_string(),
        from_name: Some(sender_display_name.clone()),
        message: cmd.message.clone(),
        mentions: decision.mentions.clone(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        sender_conn_id: None,
    };

    for target in &decision.targets {
        let run_id = uuid::Uuid::new_v4().to_string();
        let target_bot_id = target.bot_uuid.clone();
        let delivery_type = target.delivery_type;

        // Run outbound interceptor chain. The callback originates from "system"
        // and lacks AgentPass credentials, so SecurityInterceptor will skip the
        // chain per the missing-credentials guard. The hook is still wired so
        // future interceptors (audit, rate-limit) see callback traffic.
        let synthetic_message = GroupMessage {
            id: run_id.clone(),
            timestamp: now_ms(),
            sender: "system".to_string(),
            content: decision.cleaned_message.clone(),
            message_type: GroupMessageType::System,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: cmd.metadata.clone(),
        };
        let outbound_message =
            match apply_outbound_interceptors(flow, &cmd.group_id, &synthetic_message, target).await
            {
                Ok(message) => message,
                Err(reason) => {
                    warn!(
                        group_id = %cmd.group_id,
                        target_bot_id = %target_bot_id,
                        interceptor = %reason.interceptor_id,
                        code = %reason.code,
                        "group callback delivery blocked by interceptor chain"
                    );
                    delivery_results.push(delivery_result_summary(
                        &target_bot_id,
                        delivery_type,
                        false,
                        Some(reason.message.clone()),
                    ));
                    bot_deliveries.push(BotDeliveryResult {
                        target_bot_id,
                        delivered: false,
                        error: Some(ServiceError::Forbidden(reason.message)),
                    });
                    continue;
                }
            };

        let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
            Ok(target) => target,
            Err(error) => {
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error.to_string()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &callback_send,
            &decision,
            target,
            &delivery_target,
            &run_id,
            &outbound_message.content,
            &sender_display_name,
            None,
        )
        .await;

        let delivery_kind = bot_delivery_kind(delivery_type);
        let provider_transport = flow
            .provider_transport_preference(&target_bot_id, &delivery_kind, &delivery_target)
            .await;
        let delivery = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind,
                provider_transport,
            })
            .await;

        match delivery {
            Ok(result) => {
                flow.record_successful_send_context(
                    delivery_type,
                    &result,
                    &run_id,
                    &target_bot_id,
                    &cmd.group_id,
                    None,
                )
                .await;
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string),
                ));
                bot_deliveries.push(result);
            }
            Err(error) => {
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error.to_string()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    let frontend_deliveries = publish_group_callback_event(flow, &cmd).await;

    Ok(GroupCallbackOutcome {
        bot_deliveries,
        frontend_deliveries,
        mentions: decision.mentions,
        delivered_count: delivery_results
            .iter()
            .filter(|result| result.success)
            .count(),
        failed_count: delivery_results
            .iter()
            .filter(|result| !result.success)
            .count(),
        delivery_results,
    })
}

pub async fn handle_chat_abort(
    flow: &BcsMessageFlow,
    cmd: ChatAbortCommand,
) -> ServiceResult<ChatAbortOutcome> {
    let Some(group) = flow.group.get(&cmd.group_id).await else {
        warn!(
            group_id = %cmd.group_id,
            "group not found for chat.abort; returning success"
        );
        return Ok(ChatAbortOutcome {
            aborted: false,
            aborted_run_ids: Vec::new(),
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
        });
    };

    let session_key = build_session_key(&cmd.group_id);
    let participant_ids: Vec<String> = group
        .bot_participant_ids()
        .into_iter()
        .map(str::to_string)
        .collect();
    let has_participants = !participant_ids.is_empty();
    let mut bot_deliveries = Vec::new();

    for bot_id in participant_ids {
        let delivery_target = match flow.registry.resolve_delivery_target(&bot_id).await {
            Ok(target) => target,
            Err(error) => {
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: bot_id.clone(),
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        if !flow.bot_delivery.is_available(&delivery_target).await {
            debug!(bot_id = %bot_id, "bot target unavailable, skipping chat.abort");
            continue;
        }

        let delivery_run_id = cmd
            .run_id
            .clone()
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let delivery = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: delivery_run_id,
                frame: build_chat_abort_frame(&session_key, cmd.run_id.as_deref()),
                delivery_kind: BotDeliveryKind::Abort,
                provider_transport: Default::default(),
            })
            .await;

        match delivery {
            Ok(result) => bot_deliveries.push(result),
            Err(error) => {
                warn!(
                    group_id = %cmd.group_id,
                    bot_id = %bot_id,
                    error = %error,
                    "failed to deliver chat.abort"
                );
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: bot_id,
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    let aborted_run_ids = cmd.run_id.into_iter().collect::<Vec<_>>();
    let frontend_deliveries = publish_chat_abort_event(flow, &cmd.group_id, &aborted_run_ids).await;

    Ok(ChatAbortOutcome {
        aborted: !aborted_run_ids.is_empty() || !has_participants,
        aborted_run_ids,
        bot_deliveries,
        frontend_deliveries,
    })
}

fn resolve_group_chat_sender(cmd: &GroupChatCommand) -> ServiceResult<String> {
    if let Some(sender) = cmd
        .requested_sender_id
        .as_deref()
        .filter(|sender| !sender.is_empty())
    {
        return Ok(sender.to_string());
    }

    caller_actor_id(&cmd.caller).ok_or_else(|| {
        ServiceError::Unauthorized(
            "valid Human cookie or Bot token is required for this group message request"
                .to_string(),
        )
    })
}

fn caller_actor_id(caller: &CallerContext) -> Option<String> {
    match caller {
        CallerContext::Human(human) => Some(human.actor_id.clone()),
        CallerContext::Bot(bot) => Some(bot.bot_uuid.clone()),
        _ => None,
    }
}

async fn verify_group_chat_caller_access(
    flow: &BcsMessageFlow,
    group: &Group,
    caller: &CallerContext,
) -> ServiceResult<()> {
    match caller {
        CallerContext::Bot(bot) => match group.get_participant(&bot.bot_uuid) {
            Some(participant) if participant.is_bot() => Ok(()),
            _ => Err(ServiceError::Unauthorized(format!(
                "bot '{}' is not a participant of group '{}'",
                bot.bot_uuid, group.id
            ))),
        },
        CallerContext::Human(human) => {
            if human_has_group_access(flow, group, &human.actor_id, &human.staff_no).await {
                Ok(())
            } else {
                Err(ServiceError::Unauthorized(format!(
                    "current Human '{}' is not a participant and owns no Bot in group '{}'",
                    human.actor_id, group.id
                )))
            }
        }
        _ => Err(ServiceError::Unauthorized(
            "valid Human cookie or Bot token is required for this group message request"
                .to_string(),
        )),
    }
}

async fn verify_group_chat_sender(
    flow: &BcsMessageFlow,
    group: &Group,
    sender: &str,
    caller: &CallerContext,
) -> ServiceResult<()> {
    if sender.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "sender is required".to_string(),
            request_id: None,
        });
    }

    match caller {
        CallerContext::Bot(bot) => {
            if sender != bot.bot_uuid {
                return Err(ServiceError::Unauthorized(format!(
                    "bot caller '{}' cannot speak as another sender '{}'",
                    bot.bot_uuid, sender
                )));
            }
        }
        CallerContext::Human(_) => {
            verify_http_group_message_sender(flow, group, sender, caller).await?;
        }
        _ => {
            return Err(ServiceError::Unauthorized(
                "valid Human cookie or Bot token is required for this group message request"
                    .to_string(),
            ));
        }
    }

    if group.get_participant(sender).is_none() {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' is not a participant of group '{}'",
            sender, group.id
        )));
    }
    Ok(())
}

async fn verify_http_group_message_sender(
    flow: &BcsMessageFlow,
    group: &Group,
    sender: &str,
    caller: &CallerContext,
) -> ServiceResult<()> {
    if sender.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "sender is required".to_string(),
            request_id: None,
        });
    }

    let CallerContext::Human(human) = caller else {
        return Err(ServiceError::Unauthorized(
            "valid Human cookie is required for this group message request".to_string(),
        ));
    };

    if sender == human.actor_id {
        return Ok(());
    }

    if is_human_bot_dm(group) {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' must be the current Human '{}' in Human-Bot DM group '{}'",
            sender, human.actor_id, group.id
        )));
    }

    if let Some(bot) = flow.registry.get(sender).await {
        if bot.actor_kind == ActorKind::Bot
            && bot_belongs_to_staff(sender, bot.created_by.as_deref(), &human.staff_no)
        {
            return Ok(());
        }
    }

    Err(ServiceError::Unauthorized(format!(
        "sender '{}' must be the current Human '{}' or a Bot owned by them",
        sender, human.actor_id
    )))
}

fn is_human_bot_dm(group: &Group) -> bool {
    group.group_kind == GroupKind::Dm
        && group
            .participants
            .iter()
            .any(|participant| participant.actor_kind == ActorKind::Human)
        && group
            .participants
            .iter()
            .any(|participant| participant.actor_kind == ActorKind::Bot)
}

async fn verify_http_group_message_caller_access(
    flow: &BcsMessageFlow,
    group: &Group,
    caller: &CallerContext,
) -> ServiceResult<()> {
    let CallerContext::Human(human) = caller else {
        return Err(ServiceError::Unauthorized(
            "valid Human cookie is required for this group message request".to_string(),
        ));
    };

    if human_has_group_access(flow, group, &human.actor_id, &human.staff_no).await {
        Ok(())
    } else {
        Err(ServiceError::Unauthorized(format!(
            "current Human '{}' is not a participant and owns no Bot in group '{}'",
            human.actor_id, group.id
        )))
    }
}

async fn human_has_group_access(
    flow: &BcsMessageFlow,
    group: &Group,
    actor_id: &str,
    staff_no: &str,
) -> bool {
    if group
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == actor_id)
    {
        return true;
    }

    for participant in group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
    {
        let Some(bot) = flow.registry.get(&participant.bot_uuid).await else {
            continue;
        };
        if bot_belongs_to_staff(&participant.bot_uuid, bot.created_by.as_deref(), staff_no) {
            return true;
        }
    }

    false
}

fn bot_belongs_to_staff(bot_uuid: &str, created_by: Option<&str>, staff_no: &str) -> bool {
    if created_by == Some(staff_no) {
        return true;
    }
    if created_by.is_some() {
        return false;
    }
    bot_uuid
        .rsplit_once(':')
        .map(|(_, suffix)| suffix == staff_no)
        .unwrap_or(false)
}

async fn sender_name_for_chat(
    flow: &BcsMessageFlow,
    caller: &CallerContext,
    sender_id: &str,
) -> Option<String> {
    // A Human caller speaks either as themselves or as one of their owned bots
    // (the latter is authorized by `verify_group_chat_sender`). In both cases
    // the on-wire actor identity is the Human's staff_no, not a bot display
    // name, so resolve the staff_no before consulting the bot registry.
    if let CallerContext::Human(human) = caller {
        return Some(human.staff_no.clone());
    }

    if let Some(bot) = flow.registry.get(sender_id).await {
        if let Some(name) = bot
            .capabilities
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
        {
            return Some(name.to_string());
        }
    }

    None
}

async fn build_route_overlay(flow: &BcsMessageFlow, group: &Group) -> Vec<RouteParticipantOverlay> {
    let mut overlay = Vec::with_capacity(group.participants.len());
    for participant in &group.participants {
        let status = flow
            .registry
            .get(&participant.bot_uuid)
            .await
            .map(|bot| bot.status)
            .unwrap_or(ActorStatus::Online);
        overlay.push(RouteParticipantOverlay {
            bot_uuid: participant.bot_uuid.clone(),
            bot_name: participant.bot_name.clone(),
            actor_kind: participant.actor_kind,
            mode: participant.mode,
            status,
            is_driver: participant.bot_uuid == group.driver_bot,
        });
    }
    overlay
}

fn build_explicit_mention_decision(
    group: &Group,
    mention_uuids: &[String],
    message: &str,
    overlay: &[RouteParticipantOverlay],
) -> RoutingDecision {
    let overlay_map: std::collections::HashMap<&str, &RouteParticipantOverlay> = overlay
        .iter()
        .map(|o| (o.bot_uuid.as_str(), o))
        .collect();

    let mut valid_mentions: Vec<String> = Vec::new();
    let mut hidden_mentions: Vec<HiddenMentionInfo> = Vec::new();
    for mention in mention_uuids {
        if !group.participants.iter().any(|p| p.bot_uuid == *mention) {
            continue;
        }
        let is_hidden = overlay_map
            .get(mention.as_str())
            .map_or(false, |o| o.status == ActorStatus::Hidden);
        if is_hidden {
            let bot_name = overlay_map
                .get(mention.as_str())
                .and_then(|o| o.bot_name.clone())
                .unwrap_or_else(|| mention.clone());
            hidden_mentions.push(HiddenMentionInfo {
                hidden_bot_id: mention.clone(),
                hidden_bot_name: bot_name,
            });
        } else {
            valid_mentions.push(mention.clone());
        }
    }

    let targets = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .filter(|participant| {
            if group.group_strategy == GroupStrategy::ManagerWorker
                && participant.role != group.group_strategy.lead_role()
            {
                return false;
            }
            true
        })
        .map(|participant| {
            let is_mentioned = valid_mentions.contains(&participant.bot_uuid);
            let delivery_type = if !valid_mentions.is_empty() {
                if is_mentioned {
                    DeliveryType::Send
                } else {
                    DeliveryType::Inject
                }
            } else if participant.role == group.group_strategy.lead_role() {
                DeliveryType::Send
            } else {
                DeliveryType::Inject
            };

            RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver: participant.role == group.group_strategy.lead_role(),
                delivery_type,
            }
        })
        .collect();

    let cleaned_message = Regex::new(r"@([\w\p{Unified_Ideograph}:]+)")
        .map(|regex| regex.replace_all(message, "$1").to_string())
        .unwrap_or_else(|_| message.to_string());

    apply_overlay_to_decision(
        RoutingDecision {
            targets,
            mentions: valid_mentions,
            cleaned_message,
            hidden_mentions,
        },
        overlay,
    )
}

fn callback_routable_message(group: &Group, cmd: &GroupCallbackCommand) -> String {
    if cmd.mentions.is_empty() {
        return cmd.message.clone();
    }

    if mentions_all(&cmd.mentions) {
        return format!("@all {}", cmd.message);
    }

    let mention_prefixes: Vec<String> = cmd
        .mentions
        .iter()
        .map(|mention| {
            if let Some(participant) = group.participants.iter().find(|p| p.bot_uuid == *mention) {
                if let Some(ref name) = participant.bot_name {
                    return format!("@{}", name);
                }
            }
            format!("@{}", mention)
        })
        .collect();
    format!("{} {}", mention_prefixes.join(" "), cmd.message)
}

fn mentions_all(mentions: &[String]) -> bool {
    mentions
        .iter()
        .any(|mention| mention.eq_ignore_ascii_case("@all") || mention.eq_ignore_ascii_case("all"))
}

fn delivery_result_summary(
    bot_uuid: &str,
    delivery_type: DeliveryType,
    success: bool,
    error: Option<String>,
) -> MessageDeliveryResult {
    MessageDeliveryResult {
        bot_uuid: bot_uuid.to_string(),
        delivery_type,
        success,
        error,
    }
}

pub(crate) fn apply_overlay_to_decision(
    mut decision: RoutingDecision,
    overlay: &[RouteParticipantOverlay],
) -> RoutingDecision {
    use std::collections::{HashMap, HashSet};

    let overlay_map: HashMap<&str, &RouteParticipantOverlay> = overlay
        .iter()
        .map(|row| (row.bot_uuid.as_str(), row))
        .collect();

    let absent_humans: HashSet<String> = overlay
        .iter()
        .filter(|row| {
            row.actor_kind == ActorKind::Human
                && row
                    .mode
                    .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind))
                    == ParticipantMode::Absent
        })
        .map(|row| row.bot_uuid.clone())
        .collect();

    if !absent_humans.is_empty() {
        decision
            .mentions
            .retain(|mention| !absent_humans.contains(mention));
    }

    decision.targets = decision
        .targets
        .into_iter()
        .filter_map(|mut target| {
            if absent_humans.contains(&target.bot_uuid) {
                return None;
            }
            if let Some(row) = overlay_map.get(target.bot_uuid.as_str()) {
                let effective_mode = row
                    .mode
                    .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind));
                let forced_inject =
                    effective_mode == ParticipantMode::Muted || row.status == ActorStatus::Hidden;
                if target.delivery_type == DeliveryType::Send && forced_inject {
                    target.delivery_type = DeliveryType::Inject;
                    if row.status == ActorStatus::Hidden {
                        let bot_name = row.bot_name.clone().unwrap_or_else(|| target.bot_uuid.clone());
                        decision.hidden_mentions.push(HiddenMentionInfo {
                            hidden_bot_id: target.bot_uuid.clone(),
                            hidden_bot_name: bot_name,
                        });
                    }
                }
            }
            Some(target)
        })
        .collect();

    decision.mentions.retain(|m| !decision.hidden_mentions.iter().any(|h| h.hidden_bot_id == *m));

    decision
}

async fn sender_display_name(flow: &BcsMessageFlow, actor_id: &str) -> String {
    match flow.registry.get(actor_id).await {
        Some(bot) => bot.capabilities.name.clone().unwrap_or_else(|| {
            warn!(
                actor_id = %actor_id,
                phase = "from_name",
                "bcs_bots.name is empty for sender; returning blank from_name"
            );
            String::new()
        }),
        None => {
            warn!(
                actor_id = %actor_id,
                phase = "from_name",
                "registry has no row for sender; returning blank from_name"
            );
            String::new()
        }
    }
}

async fn preferred_sender_display_name(flow: &BcsMessageFlow, cmd: &WebSendCommand) -> String {
    if let Some(name) = cmd
        .from_name
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
    {
        return name.to_string();
    }
    sender_display_name(flow, &cmd.from_actor_id).await
}

async fn from_bot_owner(flow: &BcsMessageFlow, actor_id: &str) -> Option<String> {
    if actor_id.starts_with("human_") {
        None
    } else {
        flow.registry
            .get(actor_id)
            .await
            .and_then(|bot| bot.created_by)
    }
}

async fn frame_for_target(
    flow: &BcsMessageFlow,
    group: &Group,
    cmd: &WebSendCommand,
    decision: &RoutingDecision,
    target: &RoutingTarget,
    delivery_target: &BotDeliveryTarget,
    run_id: &str,
    content: &str,
    sender_display_name: &str,
    from_bot_owner: Option<String>,
) -> BcsFrame {
    let is_self = target.bot_uuid == cmd.from_actor_id;
    let protocol_version = frame_protocol_version(
        flow.registry.get_protocol_version(&target.bot_uuid).await,
        delivery_target,
    );
    let context_projection =
        context_projection_for_delivery(flow, group, cmd.session_id.as_deref()).await;
    let wire_attachments = cmd.attachments.as_ref().map(|attachments| {
        attachments
            .iter()
            .cloned()
            .map(WireAttachment::from)
            .collect()
    });
    match target.delivery_type {
        DeliveryType::Send => {
            if context_projection == ContextProjection::DirectBot {
                return build_direct_chat_send_frame(
                    run_id,
                    &cmd.group_id,
                    content,
                    &cmd.from_actor_id,
                    sender_display_name,
                    &target.bot_uuid,
                    &wire_attachments,
                    &cmd.thinking,
                    protocol_version,
                    cmd.session_id.as_deref(),
                );
            }
            let protocol_group = group_context_input(group);
            build_chat_send_frame(
                run_id,
                &cmd.group_id,
                &protocol_group,
                content,
                &cmd.from_actor_id,
                sender_display_name,
                &decision.mentions,
                &target.bot_uuid,
                &wire_attachments,
                &cmd.thinking,
                is_self,
                protocol_version,
                from_bot_owner,
                group_type_wire(group.group_strategy),
                cmd.session_id.as_deref(),
            )
        }
        DeliveryType::Inject => {
            if context_projection == ContextProjection::DirectBot {
                return build_direct_chat_inject_frame(
                    run_id,
                    &cmd.group_id,
                    content,
                    &cmd.from_actor_id,
                    sender_display_name,
                    &target.bot_uuid,
                    &wire_attachments,
                    protocol_version,
                    cmd.session_id.as_deref(),
                );
            }
            let protocol_group = group_context_input(group);
            build_chat_inject_frame(
                run_id,
                &cmd.group_id,
                &protocol_group,
                content,
                &cmd.from_actor_id,
                sender_display_name,
                &decision.mentions,
                &target.bot_uuid,
                &wire_attachments,
                is_self,
                protocol_version,
                from_bot_owner,
                group_type_wire(group.group_strategy),
                cmd.session_id.as_deref(),
            )
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ContextProjection {
    Group,
    DirectBot,
}

async fn context_projection_for_delivery(
    flow: &BcsMessageFlow,
    group: &Group,
    session_id: Option<&str>,
) -> ContextProjection {
    if let Some(projection) = context_projection_for_session(flow, session_id).await {
        return projection;
    }
    if is_human_bot_dm(group) {
        return ContextProjection::DirectBot;
    }
    ContextProjection::Group
}

async fn context_projection_for_session(
    flow: &BcsMessageFlow,
    session_id: Option<&str>,
) -> Option<ContextProjection> {
    let Some(session_id) = session_id else {
        return None;
    };
    let Some(session_management) = flow.session_management.as_ref() else {
        return None;
    };
    match session_management.get(session_id).await {
        Ok(Some(session)) => context_projection_from_meta(session.meta.as_ref()),
        Ok(None) => None,
        Err(error) => {
            warn!(%session_id, %error, "failed to load session for context projection");
            None
        }
    }
}

fn context_projection_from_meta(meta: Option<&Value>) -> Option<ContextProjection> {
    let Some(meta) = meta else {
        return None;
    };
    let projection = meta
        .get("channel")
        .and_then(|channel| channel.get("context_projection"))
        .or_else(|| meta.get("context_projection"))
        .and_then(Value::as_str);
    match projection {
        Some("direct_bot") => Some(ContextProjection::DirectBot),
        Some("group") => Some(ContextProjection::Group),
        _ => None,
    }
}

fn frame_protocol_version(protocol_version: u32, target: &BotDeliveryTarget) -> u32 {
    if target.is_http_provider() {
        protocol_version.max(3)
    } else {
        protocol_version
    }
}

fn bot_delivery_kind(delivery_type: DeliveryType) -> BotDeliveryKind {
    match delivery_type {
        DeliveryType::Send => BotDeliveryKind::Send,
        DeliveryType::Inject => BotDeliveryKind::Inject,
    }
}

fn delivery_metric_kind(delivery_type: DeliveryType) -> DeliveryMetricKind {
    match delivery_type {
        DeliveryType::Send => DeliveryMetricKind::Send,
        DeliveryType::Inject => DeliveryMetricKind::Inject,
    }
}

async fn publish_web_user_message(
    flow: &BcsMessageFlow,
    cmd: &WebSendCommand,
) -> Vec<FrontendDeliveryResult> {
    let event_json = build_workbench_user_event(flow, cmd).await;
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: frontend_target_for_web_send(cmd),
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: cmd.sender_conn_id,
        })
        .await;

    match delivery {
        Ok(result) => vec![result],
        Err(error) => {
            warn!(
                group_id = %cmd.group_id,
                error = %error,
                "failed to publish workbench user message"
            );
            Vec::new()
        }
    }
}

fn frontend_target_for_web_send(cmd: &WebSendCommand) -> FrontendDeliveryTarget {
    match cmd.session_id.clone() {
        Some(session_id) => FrontendDeliveryTarget::Session { session_id },
        None => FrontendDeliveryTarget::Group {
            group_id: cmd.group_id.clone(),
        },
    }
}

async fn publish_group_callback_event(
    flow: &BcsMessageFlow,
    cmd: &GroupCallbackCommand,
) -> Vec<FrontendDeliveryResult> {
    let run_id = uuid::Uuid::new_v4().to_string();
    let event = serde_json::json!({
        "bcs_group_id": cmd.group_id,
        "run_id": run_id,
        "state": "final",
        "message": {
            "role": "system",
            "content": [{"type": "text", "text": cmd.message}],
            "timestamp": now_ms(),
        },
    });
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "payload": event,
        "group_id": cmd.group_id,
        "bot_uuid": "system",
    });
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: cmd.group_id.clone(),
            },
            event_json: frame.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
        })
        .await;

    match delivery {
        Ok(result) => vec![result],
        Err(error) => {
            warn!(
                group_id = %cmd.group_id,
                error = %error,
                "failed to publish group callback event"
            );
            Vec::new()
        }
    }
}

async fn publish_chat_abort_event(
    flow: &BcsMessageFlow,
    group_id: &str,
    aborted_run_ids: &[String],
) -> Vec<FrontendDeliveryResult> {
    let event_json = build_chat_abort_event(group_id, aborted_run_ids);
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: group_id.to_string(),
            },
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
        })
        .await;

    match delivery {
        Ok(result) => vec![result],
        Err(error) => {
            warn!(
                group_id = %group_id,
                error = %error,
                "failed to publish chat.abort event"
            );
            Vec::new()
        }
    }
}

fn build_chat_abort_event(group_id: &str, aborted_run_ids: &[String]) -> String {
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat.abort",
        "group_id": group_id,
        "payload": {
            "run_id": aborted_run_ids.first(),
            "run_ids": aborted_run_ids,
        },
    });
    serde_json::to_string(&frame).unwrap_or_default()
}

async fn build_workbench_user_event(flow: &BcsMessageFlow, cmd: &WebSendCommand) -> String {
    let run_id = uuid::Uuid::new_v4().to_string();
    let session_key = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
    let role = match &cmd.caller {
        CallerContext::Human(_) => "user",
        _ => "assistant",
    };
    let from_name = preferred_sender_display_name(flow, cmd).await;
    let event = serde_json::json!({
        "run_id": run_id,
        "session_key": session_key,
        "bcs_session_id": cmd.session_id.as_deref(),
        "seq": 0,
        "state": "final",
        "message": {
            "role": role,
            "content": [{"type": "text", "text": cmd.message}],
            "from": cmd.from_actor_id,
            "from_name": from_name,
            "mentions": cmd.mentions,
        },
    });
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "payload": event,
        "group_id": cmd.group_id,
        "bot_uuid": cmd.from_actor_id,
        "bot_name": from_name,
    });
    serde_json::to_string(&frame).unwrap_or_default()
}

fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id.filter(|value| !value.is_empty()).unwrap_or(group_id)
}

fn delivery_type_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

fn route_source_for_web_send(group: &Group, cmd: &WebSendCommand) -> &'static str {
    if group.group_kind == GroupKind::Dm {
        "dm"
    } else if cmd.mentions.is_empty() {
        "routing_policy"
    } else {
        "explicit_mentions"
    }
}

fn log_message_received(cmd: &WebSendCommand, mode: MessageLogMode) {
    let content = MessageLogContent::from_text(&cmd.message);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::MessageReceived.as_str(),
        status = MessageLogStatus::Received.as_str(),
        mode = mode.as_str(),
        session_id = %effective_message_log_session_id(&cmd.group_id, cmd.session_id.as_deref()),
        group_id = %cmd.group_id,
        from_actor_id = %cmd.from_actor_id,
        from_name = %cmd.from_name.as_deref().unwrap_or(""),
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        mention_count = cmd.mentions.len(),
        mentions = %message_log_json(&cmd.mentions),
        "message_received"
    );
}

fn log_routing_digest(
    cmd: &WebSendCommand,
    decision: &RoutingDecision,
    mode: MessageLogMode,
    route_source: &'static str,
) {
    let content = MessageLogContent::from_text(&decision.cleaned_message);
    let targets_summary: Vec<MessageLogTargetSummary> = decision
        .targets
        .iter()
        .map(|target| MessageLogTargetSummary::new(&target.bot_uuid)
            .with_delivery_type(delivery_type_slug(target.delivery_type))
            .with_route_source(route_source))
        .collect();

    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::RouteDecided.as_str(),
        status = MessageLogStatus::Routed.as_str(),
        mode = mode.as_str(),
        session_id = %effective_message_log_session_id(&cmd.group_id, cmd.session_id.as_deref()),
        group_id = %cmd.group_id,
        from_actor_id = %cmd.from_actor_id,
        from_name = %cmd.from_name.as_deref().unwrap_or(""),
        route_source = route_source,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        target_count = decision.targets.len(),
        send_target_count = decision.targets.iter().filter(|target| target.delivery_type == DeliveryType::Send).count(),
        inject_target_count = decision.targets.iter().filter(|target| target.delivery_type == DeliveryType::Inject).count(),
        targets = %message_log_json(&targets_summary),
        mention_count = decision.mentions.len(),
        mentions = %message_log_json(&decision.mentions),
        hidden_mention_count = decision.hidden_mentions.len(),
        "route_decided"
    );
}

fn log_bot_deliver_result(
    group_id: &str,
    session_id: Option<&str>,
    run_id: &str,
    bot_id: &str,
    delivery_type: DeliveryType,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::FreeChat.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            to_bot_id = %bot_id,
            delivery_type = delivery_type_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::FreeChat.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            to_bot_id = %bot_id,
            delivery_type = delivery_type_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

pub fn build_chat_abort_frame(session_key: &str, run_id: Option<&str>) -> BcsFrame {
    let mut params = serde_json::json!({
        "session_key": session_key,
    });

    if let Some(run_id) = run_id {
        params["run_id"] = Value::String(run_id.to_string());
    }

    BcsFrame::Request(RequestFrame::new(
        uuid::Uuid::new_v4().to_string(),
        "chat.abort",
        Some(params),
    ))
}

//! System-message dispatcher implementation.
//!
//! Routes `SystemMessageEvent`s through registered producers and delivers
//! the resulting messages to recipients via `BotDeliveryPort`.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{DeliveryType, Group, GroupStrategy, NewMessage, Participant, SenderType, SystemGroupMessage, SystemMessageEvent, SystemMessageEventKind};
use bcs_protocol::{
    build_chat_inject_frame, build_chat_send_frame, now_ms, BotDeliveryKind, GroupContextInput,
    GroupContextParticipant,
};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryPort, BotDeliveryTarget, BotRegistryCoreService, BotRunContext, BotRunContextPort,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryTarget,
    ProviderStreamGrayList, ProviderTransportPreference,
    ServiceError, ServiceResult,
    SystemMessageDispatchOutcome, SystemMessageDispatcherService, SystemMessageProducerService,
    SystemMessageRecipientResult,
    port::repo::MessageRepoPort,
};
use bcs_service_api::core::BCS_SYSTEM_MESSAGE;
use futures::future::join_all;

/// Concrete dispatcher that holds a producer registry and delivery port.
pub struct SystemMessageDispatcherImpl {
    producers: HashMap<SystemMessageEventKind, Box<dyn SystemMessageProducerService>>,
    registry: Arc<dyn BotRegistryCoreService>,
    /// Delivery port for sending inject frames to target bots.
    delivery: Arc<dyn BotDeliveryPort>,
    /// Delivery port for publishing events to frontend WebSocket clients.
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    /// Optional run-context registry for HTTP-provider final callbacks.
    bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    /// Optional message repo for persisting system messages to history.
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    /// Optional gray list gating SSE transport for provider 2.0 sends.
    provider_stream_gray_list: Option<Arc<ProviderStreamGrayList>>,
}

impl SystemMessageDispatcherImpl {
    /// Return a builder for assembling the dispatcher.
    pub fn builder() -> SystemMessageDispatcherBuilder {
        SystemMessageDispatcherBuilder::default()
    }

    /// Decide the provider transport for a system-message delivery. Mirrors
    /// `BcsMessageFlow::provider_transport_preference`: only a `Send` to a
    /// provider 2.0 bot whose `created_by` is in the SSE gray list prefers SSE;
    /// everything else stays on the callback transport.
    async fn provider_transport_preference(
        &self,
        target_bot_id: &str,
        delivery_kind: &BotDeliveryKind,
        target: &BotDeliveryTarget,
    ) -> ProviderTransportPreference {
        if !matches!(delivery_kind, BotDeliveryKind::Send) {
            return ProviderTransportPreference::Callback;
        }
        if !matches!(
            target,
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
}

/// Builder for `SystemMessageDispatcherImpl`.
#[derive(Default)]
pub struct SystemMessageDispatcherBuilder {
    producers: HashMap<SystemMessageEventKind, Box<dyn SystemMessageProducerService>>,
    registry: Option<Arc<dyn BotRegistryCoreService>>,
    delivery: Option<Arc<dyn BotDeliveryPort>>,
    frontend_delivery: Option<Arc<dyn FrontendDeliveryPort>>,
    bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    provider_stream_gray_list: Option<Arc<ProviderStreamGrayList>>,
}

impl SystemMessageDispatcherBuilder {
    /// Set the bot-registry core service.
    pub fn with_registry(mut self, registry: Arc<dyn BotRegistryCoreService>) -> Self {
        self.registry = Some(registry);
        self
    }

    /// Set the bot-delivery port.
    pub fn with_delivery(mut self, delivery: Arc<dyn BotDeliveryPort>) -> Self {
        self.delivery = Some(delivery);
        self
    }

    /// Set the frontend delivery port.
    pub fn with_frontend_delivery(mut self, frontend_delivery: Arc<dyn FrontendDeliveryPort>) -> Self {
        self.frontend_delivery = Some(frontend_delivery);
        self
    }

    /// Set the run-context registry used by HTTP provider callbacks.
    pub fn with_bot_run_context(mut self, bot_run_context: Arc<dyn BotRunContextPort>) -> Self {
        self.bot_run_context = Some(bot_run_context);
        self
    }

    /// Set the optional message repo for persisting system messages to history.
    pub fn with_message_repo(mut self, message_repo: Arc<dyn MessageRepoPort>) -> Self {
        self.message_repo = Some(message_repo);
        self
    }

    /// Set the optional gray list gating SSE transport for provider 2.0 sends.
    pub fn with_provider_stream_gray_list(
        mut self,
        gray_list: Arc<ProviderStreamGrayList>,
    ) -> Self {
        self.provider_stream_gray_list = Some(gray_list);
        self
    }

    /// Register a producer for its declared event kind.
    pub fn register<P: SystemMessageProducerService + 'static>(mut self, producer: P) -> Self {
        let kind = producer.kind();
        self.producers.insert(kind, Box::new(producer));
        self
    }

    /// Build the dispatcher, failing if required dependencies are missing.
    pub fn build(self) -> Result<SystemMessageDispatcherImpl, String> {
        Ok(SystemMessageDispatcherImpl {
            producers: self.producers,
            registry: self.registry.ok_or("registry required")?,
            delivery: self.delivery.ok_or("delivery required")?,
            frontend_delivery: self.frontend_delivery.ok_or("frontend_delivery required")?,
            bot_run_context: self.bot_run_context,
            message_repo: self.message_repo,
            provider_stream_gray_list: self.provider_stream_gray_list,
        })
    }
}

struct PendingSystemMessageDelivery {
    cmd: BotDeliveryCommand,
    recipient_id: String,
    run_id: String,
    record_run_context: bool,
    group_id: String,
    bcs_session_id: Option<String>,
}

#[async_trait]
impl SystemMessageDispatcherService for SystemMessageDispatcherImpl {
    async fn dispatch(
        &self,
        event: SystemMessageEvent,
        group: &Group,
        session_id: &str,
        participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome> {
        let kind = event.kind();
        tracing::info!(group_id = %group.id, event_kind = ?kind, %session_id, "dispatching system message");

        let producer = self.producers.get(&kind).ok_or_else(|| {
            ServiceError::InternalError(format!("No producer registered for kind {:?}", kind))
        })?;

        let messages = producer.produce(&event, group, self.registry.as_ref(), participants).await;

        // Persist system messages to the message store (best-effort). Most
        // system events keep one global record. Manager-worker session context
        // has recipient-specific content, so worker context is persisted under
        // that worker's owner_bot_id while manager context stays global.
        if let Some(ref repo) = self.message_repo {
            let mut persisted_count = 0usize;
            for (msg, owner_bot_id) in history_messages_to_persist(kind, group, &messages) {
                let new_msg = NewMessage {
                    group_id: group.id.clone(),
                    session_id: session_id.to_string(),
                    sender_id: "system".to_string(),
                    sender_type: SenderType::System,
                    message_type: "system".to_string(),
                    content: serde_json::Value::String(msg.message.clone()),
                    client_msg_id: None,
                    owner_bot_id,
                    created_at: now_ms(),
                    run_id: String::new(),
                };
                if let Err(e) = repo.append_message(new_msg).await {
                    tracing::warn!(
                        group_id = %group.id,
                        error = %e,
                        "failed to persist system message to message store"
                    );
                } else {
                    persisted_count += 1;
                }
            }
            tracing::info!(
                group_id = %group.id,
                count = persisted_count,
                "system message persisted"
            );
        }
        let protocol_group = group_context_input(group, session_id);
        let group_type = group_type_wire(group.group_strategy);

        let mut total = 0usize;
        let mut success = 0usize;
        let mut failed = 0usize;
        let mut results = Vec::new();
        let mut commands = Vec::new();
        for msg in &messages {
            for recipient in &msg.recipients {
                total += 1;
                let run_id = uuid::Uuid::new_v4().to_string();
                let target = match self.registry.resolve_delivery_target(recipient).await {
                    Ok(target) => target,
                    Err(error) => {
                        tracing::warn!(
                            %recipient,
                            error = %error,
                            "system message target resolution failed"
                        );
                        results.push(SystemMessageRecipientResult {
                            recipient_id: recipient.clone(),
                            delivered: false,
                            error: Some(error),
                        });
                        continue;
                    }
                };
                let protocol_version = frame_protocol_version(
                    self.registry.get_protocol_version(recipient).await,
                    &target,
                );
                let (frame, delivery_kind) = match msg.delivery_type {
                    DeliveryType::Send => (
                        build_chat_send_frame(
                            &run_id,
                            &group.id,
                            &protocol_group,
                            &msg.message,
                            BCS_SYSTEM_MESSAGE,
                            BCS_SYSTEM_MESSAGE,
                            &[],
                            recipient,
                            &None,
                            &None,
                            false,
                            protocol_version,
                            None,
                            group_type.clone(),
                            Some(session_id),
                        ),
                        BotDeliveryKind::Send,
                    ),
                    DeliveryType::Inject => (
                        build_chat_inject_frame(
                            &run_id,
                            &group.id,
                            &protocol_group,
                            &msg.message,
                            BCS_SYSTEM_MESSAGE,
                            BCS_SYSTEM_MESSAGE,
                            &[],
                            recipient,
                            false,
                            protocol_version,
                            None,
                            group_type.clone(),
                            Some(session_id),
                        ),
                        BotDeliveryKind::Inject,
                    ),
                };
                let provider_transport = self
                    .provider_transport_preference(recipient, &delivery_kind, &target)
                    .await;
                commands.push(PendingSystemMessageDelivery {
                    recipient_id: recipient.clone(),
                    run_id: run_id.clone(),
                    record_run_context: msg.delivery_type == DeliveryType::Send
                        && target.is_http_provider(),
                    group_id: group.id.clone(),
                    bcs_session_id: Some(session_id.to_string()),
                    cmd: BotDeliveryCommand {
                        target,
                        run_id,
                        frame,
                        delivery_kind,
                        provider_transport,
                    },
                });
            }
        }

        let delivery = self.delivery.clone();
        let bot_run_context = self.bot_run_context.clone();
        results.extend(join_all(commands.into_iter().map(|cmd| {
            let recipient = cmd.recipient_id.clone();
            let delivery = delivery.clone();
            let bot_run_context = bot_run_context.clone();
            async move {
                let delivered = match delivery.deliver(cmd.cmd).await {
                    Ok(r) => r.delivered,
                    Err(e) => {
                        tracing::warn!(%recipient, error = %e, "system message delivery failed");
                        false
                    }
                };
                if delivered && cmd.record_run_context {
                    if let Some(run_context) = bot_run_context {
                        run_context
                            .put_context(BotRunContext {
                                run_id: cmd.run_id,
                                bot_id: recipient.clone(),
                                group_id: cmd.group_id,
                                bcs_session_id: cmd.bcs_session_id,
                                deadline_ms: now_ms().saturating_add(300_000),
                                terminal: false,
                            })
                            .await;
                    }
                }
                let error = if delivered {
                    None
                } else {
                    Some(ServiceError::InternalError("delivery failed".to_string()))
                };
                SystemMessageRecipientResult {
                    recipient_id: recipient,
                    delivered,
                    error,
                }
            }
        }))
        .await);

        for r in &results {
            if r.delivered {
                success += 1;
            } else {
                failed += 1;
            }
        }

        // Publish all produced messages to frontend WebSocket clients.
        let mut seen = std::collections::HashSet::new();
        let frontend_content = messages
            .iter()
            .filter(|msg| seen.insert(msg.message.clone()))
            .map(|msg| msg.message.clone())
            .collect::<Vec<_>>()
            .join("\n");
        if !frontend_content.is_empty() {
            let event_json = build_frontend_system_event_frame(&group.id, &frontend_content, session_id);
            let target = FrontendDeliveryTarget::Session { session_id: session_id.to_string() };
            if let Err(e) = self.frontend_delivery.publish(FrontendDeliveryCommand {
                target,
                event_json,
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: None,
                exclude_conn_id: None,
            }).await {
                tracing::warn!(
                    group_id = %group.id,
                    %session_id,
                    error = %e,
                    "system message frontend delivery failed"
                );
            }
        }

        tracing::info!(
            group_id = %group.id,
            event_kind = ?kind,
            total_recipients = total,
            successful = success,
            failed = failed,
            "system message dispatch complete"
        );

        Ok(SystemMessageDispatchOutcome {
            total_recipients: total,
            successful_deliveries: success,
            failed_deliveries: failed,
            recipient_results: results,
        })
    }
}

/// Build the frontend JSON event frame for a system message.
/// Follows the exact format used by `group_flow.rs::publish_group_callback_event`.
fn build_frontend_system_event_frame(
    group_id: &str,
    content: &str,
    session_id: &str,
) -> String {
    let run_id = uuid::Uuid::new_v4().to_string();
    let mut event = serde_json::json!({
        "bcs_group_id": group_id,
        "run_id": run_id,
        "state": "final",
        "message": {
            "role": "system",
            "content": [{"type": "text", "text": content}],
            "timestamp": now_ms(),
        },
    });
    event["bcs_session_id"] = serde_json::Value::String(session_id.to_string());
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "payload": event,
        "group_id": group_id,
        "bot_uuid": BCS_SYSTEM_MESSAGE,
    });
    serde_json::to_string(&frame).unwrap_or_default()
}

fn frame_protocol_version(protocol_version: u32, target: &BotDeliveryTarget) -> u32 {
    if target.is_http_provider() {
        protocol_version.max(3)
    } else {
        protocol_version
    }
}

fn history_messages_to_persist<'a>(
    kind: SystemMessageEventKind,
    group: &Group,
    messages: &'a [SystemGroupMessage],
) -> Vec<(&'a SystemGroupMessage, Option<String>)> {
    if messages.is_empty() {
        return Vec::new();
    }
    if kind != SystemMessageEventKind::SessionContext
        || group.group_strategy != GroupStrategy::ManagerWorker
    {
        return vec![(&messages[0], None)];
    }

    let mut records = Vec::new();
    let mut has_global = false;
    for msg in messages {
        if !has_global && is_manager_context_message(group, msg) {
            records.push((msg, None));
            has_global = true;
        }
        for recipient in msg.recipients.iter().filter(|recipient| {
            participant_has_role(group, recipient, bcs_domain::ParticipantRole::Worker)
        }) {
            records.push((msg, Some(recipient.clone())));
        }
    }

    records
}

fn is_manager_context_message(group: &Group, msg: &SystemGroupMessage) -> bool {
    msg.recipients
        .iter()
        .any(|recipient| participant_has_role(group, recipient, bcs_domain::ParticipantRole::Manager))
}

fn participant_has_role(group: &Group, bot_id: &str, role: bcs_domain::ParticipantRole) -> bool {
    group
        .participants
        .iter()
        .any(|participant| {
            participant.is_bot()
                && participant.bot_uuid == bot_id
                && participant.role == role
        })
}

fn group_context_input(group: &Group, session_id: &str) -> GroupContextInput {
    GroupContextInput {
        session_id: group.id.clone(),
        driver_bot: group.driver_bot.clone(),
        originator: group.originator().to_string(),
        participants: group
            .participants
            .iter()
            .map(|participant| GroupContextParticipant {
                id: participant.bot_uuid.clone(),
                name: participant.bot_name.clone(),
                role: Some(
                    match participant.role {
                        bcs_domain::ParticipantRole::Driver => "driver",
                        bcs_domain::ParticipantRole::Consultant => "consultant",
                        bcs_domain::ParticipantRole::Manager => "manager",
                        bcs_domain::ParticipantRole::Worker => "worker",
                        bcs_domain::ParticipantRole::Observer => "observer",
                    }
                    .to_string(),
                ),
                is_bot: participant.is_bot(),
            })
            .collect(),
        bcs_session_id: Some(session_id.to_string()),
    }
}

fn group_type_wire(strategy: GroupStrategy) -> Option<String> {
    match strategy {
        GroupStrategy::ManagerWorker => Some("manager_worker".to_string()),
        GroupStrategy::StateMachine => Some("state_machine".to_string()),
        GroupStrategy::Chat => None,
    }
}

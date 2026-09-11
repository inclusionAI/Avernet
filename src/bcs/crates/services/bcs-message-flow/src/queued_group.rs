//! Versioned group intent and send-time reconstruction. No wire frames, message
//! bodies, attachment URLs or credentials are stored in a delivery projection.
use crate::BcsMessageFlow;
use crate::group_flow::{
    ContextProjection, apply_outbound_interceptors, apply_session_participant_scope,
    context_projection_for_delivery, frame_protocol_version, request_session_key,
};
use crate::queued_payload::read_bounded_queued_payload;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_protocol::{
    ChannelInfo, ChannelSource, GroupContextInput, GroupContextParticipant, apply_channel_info,
    build_chat_send_frame, build_direct_chat_send_frame,
};
use bcs_service_api::PreparedManagedDelivery;
use bcs_service_api::{
    ActorStatus, BotDeliveryCommand, BotDeliveryKind, BotDeliveryTarget, BotRunTransportOwner,
    DeliveryType, Group, GroupKind, GroupMessage, GroupMessageType, GroupStatus, MessageRole,
    RoutingDecision, RoutingTarget, ServiceError, ServiceResult, WebSendCommand,
};
use bcs_service_api::{ManagedDeliveryPreparationService, ManagedMessageDeliveryService};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ParticipantProjection {
    id: String,
    name: Option<String>,
    role: Option<String>,
    is_bot: bool,
}

#[derive(Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TextProjection {
    Original,
    RoutedMentions,
    ExplicitMentions,
}

/// A routing decision is fixed at admission; current membership/security is
/// still checked at dispatch. This snapshot is not an authorization grant.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueuedGroupProjection {
    version: u32,
    #[serde(default)]
    policy_version: Option<u64>,
    driver_bot: String,
    originator: String,
    participants: Vec<ParticipantProjection>,
    direct_bot: bool,
    group_type: Option<String>,
    mentions: Vec<String>,
    target_tags: Vec<String>,
    sender_name: String,
    sender_owner: Option<String>,
    thinking: Option<String>,
    text_projection: TextProjection,
    #[serde(default)]
    reply_context: Option<bcs_protocol::GroupContext>,
    #[serde(default)]
    forward_hop: Option<u32>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueuedTransportContext {
    pub version: u32,
    pub owner: BotRunTransportOwner,
    pub connection_id: Option<String>,
    pub downstream_session_key: String,
    #[serde(default)]
    pub downstream_run_id: Option<String>,
}

pub struct QueuedGroupPreparation {
    pub flow: std::sync::Weak<BcsMessageFlow>,
    pub deliveries: Arc<dyn ManagedMessageDeliveryService>,
    pub provider_bypass_headers_configured: bool,
}

#[async_trait::async_trait]
impl ManagedDeliveryPreparationService for QueuedGroupPreparation {
    async fn still_valid(&self, prepared: &PreparedManagedDelivery) -> bool {
        let Some(flow) = self.flow.upgrade() else { return false; };
        let Ok(target) = flow.registry.resolve_delivery_target(prepared.command.target_bot_id()).await else { return false; };
        if target != prepared.command.target || !flow.bot_delivery.is_available(&target).await { return false; }
        match prepared.transport_context_json.get("connection_id").and_then(|v| v.as_str()) {
            Some(id) => flow.bot_delivery.connection_identity(&target).await.as_deref() == Some(id),
            None => target.is_http_provider(),
        }
    }
    async fn is_available(&self, bot_id: &str) -> bool {
        let Some(flow) = self.flow.upgrade() else {
            return false;
        };
        match flow.registry.resolve_delivery_target(bot_id).await {
            Ok(target) => flow.bot_delivery.is_available(&target).await,
            Err(_) => false,
        }
    }

    async fn prepare(
        &self,
        delivery: &PersistedMessageDelivery,
    ) -> ServiceResult<PreparedManagedDelivery> {
        let flow = self
            .flow
            .upgrade()
            .ok_or_else(|| invalid("queue owner stopped"))?;
        let limits = match &flow.delivery_policy { Some(p) => p.snapshot.read().await.policy.clone(), None => Default::default() };
        let max_messages = delivery.context_selection_json.as_ref().and_then(|v| v.get("max_messages")).and_then(|v| v.as_u64()).unwrap_or(limits.max_context_messages as u64);
        let contexts = self
            .deliveries
            .bounded_contexts(&delivery.delivery_id, max_messages as usize + 1)
            .await
            .map_err(|_| invalid("queue context lookup failed"))?;
        prepare_queued_group_bounded(
            &flow,
            delivery,
            &contexts.rows,
            contexts.total,
            self.provider_bypass_headers_configured,
        )
        .await
    }

    async fn before_send(
        &self,
        delivery: &PersistedMessageDelivery,
        command: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        let flow = self
            .flow
            .upgrade()
            .ok_or_else(|| invalid("queue owner stopped"))?;
        let transport: QueuedTransportContext = serde_json::from_value(
            delivery
                .transport_context_json
                .clone()
                .ok_or_else(|| invalid("queue send-start transport metadata missing"))?,
        )
        .map_err(|_| invalid("invalid queue transport metadata"))?;
        if transport.version != 1
            || !delivery.state.may_have_been_sent
            || command.target_bot_id() != delivery.target_bot_id
            || Some(command.run_id.as_str()) != delivery.run_id.as_deref()
            || request_session_key(&command.frame).as_deref()
                != Some(transport.downstream_session_key.as_str())
        {
            return Err(invalid("queue send-start scope mismatch"));
        }
        let matches_owner = match (&command.target, &transport.owner) {
            (BotDeliveryTarget::WebSocket { .. }, BotRunTransportOwner::WebSocket) => {
                transport.connection_id.is_some()
            }
            (
                BotDeliveryTarget::HttpProvider {
                    provider_id,
                    provider_bot_ref,
                    ..
                },
                BotRunTransportOwner::HttpProvider {
                    provider_id: original_id,
                    provider_bot_ref: original_ref,
                },
            ) => {
                provider_id == original_id
                    && provider_bot_ref == original_ref
                    && transport.connection_id.is_none()
            }
            _ => false,
        };
        if !matches_owner || !command.provider_bypass_headers.is_empty() {
            return Err(invalid("queue original transport owner mismatch"));
        }
        // Preparation may outlive a Provider mutation or WS reconnect. Resolve
        // through the invalidation-aware store immediately before transport I/O.
        let current_target = flow.registry.resolve_delivery_target(&delivery.target_bot_id).await?;
        if current_target != command.target || !flow.bot_delivery.is_available(&current_target).await {
            return Err(invalid("queue delivery target changed during preparation"));
        }
        if let Some(original_connection) = &transport.connection_id {
            if flow.bot_delivery.connection_identity(&current_target).await.as_ref() != Some(original_connection) {
                return Err(invalid("queue connection changed during preparation"));
            }
        }
        let nonce = delivery
            .request_id
            .as_ref()
            .ok_or_else(|| invalid("queue attempt request identity missing"))?;
        if !matches!(&command.frame, bcs_protocol::BcsFrame::Request(f) if f.id == *nonce && f.method == "chat.send")
        {
            return Err(invalid("queue attempt frame identity mismatch"));
        }
        let run_context = flow
            .bot_run_context
            .as_ref()
            .ok_or_else(|| invalid("queue run correlation unavailable"))?;
        let deadline_ms = delivery
            .run_deadline_at_ms
            .and_then(|n| u64::try_from(n).ok())
            .ok_or_else(|| invalid("queue run deadline missing"))?;
        run_context
            .put_context(bcs_service_api::BotRunContext {
                run_id: command.run_id.clone(),
                bot_id: delivery.target_bot_id.clone(),
                group_id: delivery.group_id.clone(),
                bcs_session_id: Some(delivery.session_id.clone()),
                deadline_ms,
                terminal: false,
            })
            .await;
        if run_context.get_context(&command.run_id).await.is_none() {
            return Err(invalid("queue run correlation write failed"));
        }
        run_context
            .register_active_run(bcs_service_api::ActiveBotRunContext {
                canonical_run_id: command.run_id.clone(),
                downstream_run_id: command.run_id.clone(),
                downstream_session_key: Some(transport.downstream_session_key),
                scope: bcs_service_api::BotRunScope {
                    group_id: delivery.group_id.clone(),
                    session_id: delivery.session_id.clone(),
                    bot_id: delivery.target_bot_id.clone(),
                },
                transport_owner: transport.owner,
                provider_bypass_headers: Vec::new(),
                deadline_ms,
            })
            .await?;
        if !run_context
            .bind_request_alias(&command.run_id, nonce)
            .await?
        {
            return Err(invalid("queue request correlation unavailable"));
        }
        let repo = flow
            .message_repo
            .as_ref()
            .ok_or_else(|| invalid("queue message repository unavailable"))?;
        let source = repo
            .get_message_by_id(&delivery.session_id, &delivery.source_message_id)
            .await
            .map_err(|_| invalid("queue source read failed"))?
            .ok_or_else(|| invalid("queue source missing"))?;
        if let Some(id) = source
            .content
            .get("source_im_message_id")
            .and_then(serde_json::Value::as_str)
        {
            flow.message_tracker
                .cache_channel_source_message_id(&command.run_id, id)
                .await;
        }
        Ok(())
    }

    async fn prepare_abort(
        &self,
        delivery: &PersistedMessageDelivery,
    ) -> ServiceResult<bcs_service_api::BotAbortDeliveryCommand> {
        let flow = self
            .flow
            .upgrade()
            .ok_or_else(|| invalid("queue owner stopped"))?;
        let metadata: QueuedTransportContext = serde_json::from_value(
            delivery
                .transport_context_json
                .clone()
                .ok_or_else(|| invalid("queue original transport is unknown"))?,
        )
        .map_err(|_| invalid("invalid queue transport metadata"))?;
        if metadata.version != 1 {
            return Err(invalid("unsupported queue transport version"));
        }
        // The current Provider API aborts a whole scope. Until a scope barrier
        // accounts for legacy traffic, an exact delivery abort must fail closed.
        if metadata.owner != BotRunTransportOwner::WebSocket {
            return Err(invalid("exact_abort_not_supported"));
        }
        let target = flow
            .registry
            .resolve_delivery_target(&delivery.target_bot_id)
            .await?;
        if !matches!(&target, BotDeliveryTarget::WebSocket { .. })
            || metadata.connection_id.is_none()
            || flow.bot_delivery.connection_identity(&target).await != metadata.connection_id
        {
            return Err(invalid("queue original Bot connection is unavailable"));
        }
        let run_id = delivery
            .run_id
            .as_ref()
            .ok_or_else(|| invalid("queue run identity missing"))?;
        // Active-run indexes intentionally age out at the run deadline. Abort
        // is needed precisely after that deadline, so use durable original
        // scope/accepted alias rather than treating an expired index as proof
        // that the downstream work stopped.
        let downstream_run_id = metadata.downstream_run_id.unwrap_or_else(|| run_id.clone());
        Ok(bcs_service_api::BotAbortDeliveryCommand {
            target,
            command_id: String::new(),
            group_id: delivery.group_id.clone(),
            session_id: metadata.downstream_session_key,
            run_id: Some(downstream_run_id),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 30_000,
        })
    }
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: message.into(),
        request_id: None,
    }
}

impl QueuedGroupProjection {
    pub fn with_reply_context(
        mut self,
        mut context: bcs_protocol::GroupContext,
        forward_hop: Option<u32>,
        strip_mentions: bool,
    ) -> Self {
        context.message.clear();
        self.reply_context = Some(context);
        self.forward_hop = forward_hop;
        self.text_projection = if strip_mentions {
            TextProjection::RoutedMentions
        } else {
            TextProjection::Original
        };
        self
    }
    pub async fn capture(
        flow: &BcsMessageFlow,
        group: &Group,
        command: &WebSendCommand,
        decision: &RoutingDecision,
        target: &RoutingTarget,
        sender_name: String,
        sender_owner: Option<String>,
    ) -> ServiceResult<Self> {
        if command.session_id.as_deref().is_none_or(|s| s.is_empty()) {
            return Err(invalid("queue admission requires a canonical session"));
        }
        if !command.provider_bypass_headers.is_empty() {
            return Err(invalid(
                "queue admission does not retain Provider bypass headers",
            ));
        }
        let participant = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == target.bot_uuid)
            .ok_or_else(|| invalid("queue target is not a session participant"))?;
        let snapshot = crate::protocol_context::group_context_input(group);
        Ok(Self {
            policy_version: None,
            version: 1,
            reply_context: None,
            forward_hop: None,
            driver_bot: snapshot.driver_bot,
            originator: snapshot.originator,
            participants: snapshot
                .participants
                .into_iter()
                .map(|p| ParticipantProjection {
                    id: p.id,
                    name: p.name,
                    role: p.role,
                    is_bot: p.is_bot,
                })
                .collect(),
            direct_bot: context_projection_for_delivery(flow, group, command.session_id.as_deref())
                .await
                == ContextProjection::DirectBot,
            group_type: crate::protocol_context::group_type_wire(group.group_strategy),
            mentions: decision.mentions.clone(),
            target_tags: participant.tags.clone(),
            sender_name,
            sender_owner,
            thinking: command.thinking.clone(),
            text_projection: if group.group_kind == GroupKind::Dm {
                TextProjection::Original
            } else if command.mentions.is_empty() {
                TextProjection::RoutedMentions
            } else {
                TextProjection::ExplicitMentions
            },
        })
    }

    fn decode(row: &PersistedMessageDelivery) -> Result<Self, String> {
        let projection: Self = serde_json::from_value(row.semantic_projection_json.clone())
            .map_err(|_| "invalid queued group projection".to_string())?;
        if projection.version != 1 || row.flow_kind != DeliveryFlowKind::Group {
            return Err("unsupported queued group projection version or flow".into());
        }
        Ok(projection)
    }

    fn project_text(&self, text: &str) -> Result<String, String> {
        let pattern = match self.text_projection {
            TextProjection::Original => return Ok(text.to_string()),
            TextProjection::RoutedMentions => r"@([-\w\p{Unified_Ideograph}:]+)",
            TextProjection::ExplicitMentions => r"@([\w\p{Unified_Ideograph}:]+)",
        };
        let mentions = regex::Regex::new(pattern).map_err(|_| "invalid mention projection")?;
        Ok(mentions.replace_all(text, "$1").into_owned())
    }
}

/// Canonical queued content is the only durable source of attachment payloads.
/// External source attribution is stored once, not on each target delivery.
pub fn queued_inbound_content(command: &WebSendCommand) -> serde_json::Value {
    let mut content = serde_json::json!({"text": command.message, "mentions": command.mentions});
    if let Some(attachments) = &command.attachments {
        content["attachments"] = serde_json::json!(attachments);
    }
    if let Some(id) = &command.source_im_message_id {
        content["source_im_message_id"] = serde_json::json!(id);
    }
    if let Some(identity) = &command.channel_sender_identity {
        content["channel_sender_identity"] = serde_json::json!({
            "channel_type": identity.channel_type, "user_id": identity.user_id,
            "actor_id": identity.actor_id, "display_name": identity.display_name,
        });
    }
    content
}

/// Production protocol preparation used by the managed runtime. In particular,
/// Inject attachments retain their restrictions when included in chat.send.
pub async fn prepare_queued_group(
    flow: &BcsMessageFlow,
    row: &PersistedMessageDelivery,
    contexts: &[PersistedMessageDelivery],
    provider_bypass_headers_configured: bool,
) -> ServiceResult<PreparedManagedDelivery> {
    prepare_queued_group_bounded(flow, row, contexts, contexts.len() as u64, provider_bypass_headers_configured).await
}

async fn prepare_queued_group_bounded(
    flow: &BcsMessageFlow,
    row: &PersistedMessageDelivery,
    contexts: &[PersistedMessageDelivery],
    total: u64,
    provider_bypass_headers_configured: bool,
) -> ServiceResult<PreparedManagedDelivery> {
    let projection = QueuedGroupProjection::decode(row).map_err(|e| invalid(&e))?;
    let repo = flow
        .message_repo
        .as_ref()
        .ok_or_else(|| invalid("queue message repository unavailable"))?;
    let session_management = flow
        .session_management
        .as_ref()
        .ok_or_else(|| invalid("queue session authorization unavailable"))?;
    let session = session_management
        .get(&row.session_id)
        .await
        .map_err(|_| invalid("queue session authorization read failed"))?
        .ok_or_else(|| invalid("queue session no longer exists"))?;
    if session.group_id != row.group_id {
        return Err(invalid("queue session scope mismatch"));
    }
    if session.status != bcs_service_api::SessionStatus::Running {
        return Err(invalid("queue session is no longer running"));
    }
    let mut group = flow
        .group
        .get(&row.group_id)
        .await
        .ok_or_else(|| invalid("queue group no longer exists"))?;
    if group.status != GroupStatus::Active {
        return Err(invalid("queue group is no longer active"));
    }
    apply_session_participant_scope(flow, &mut group, Some(&row.session_id)).await?;
    let source = repo
        .get_message_by_id(&row.session_id, &row.source_message_id)
        .await
        .map_err(|_| invalid("canonical queue message read failed"))?
        .ok_or_else(|| invalid("canonical queue message missing"))?;
    for actor in [&source.sender_id, &row.target_bot_id] {
        if !group.participants.iter().any(|p| &p.bot_uuid == actor) {
            return Err(invalid("queue participant no longer has session access"));
        }
    }
    let bot = flow
        .registry
        .get(&row.target_bot_id)
        .await
        .ok_or_else(|| invalid("queue target no longer exists"))?;
    if bot.status == ActorStatus::Hidden {
        return Err(invalid("queue target is hidden"));
    }
    let limits = match &flow.delivery_policy { Some(p) => p.snapshot.read().await.policy.clone(), None => Default::default() };
    let payload = read_bounded_queued_payload(repo.as_ref(), row, contexts, total, limits.max_context_messages, limits.max_context_bytes, |d, text| {
        QueuedGroupProjection::decode(d)?.project_text(text)
    })
    .await
    .map_err(|e| invalid(&e))?;
    let run_id = row
        .run_id
        .as_ref()
        .ok_or_else(|| invalid("queue send has no run identity"))?;
    let target = RoutingTarget {
        bot_uuid: row.target_bot_id.clone(),
        url: String::new(),
        is_driver: row.target_bot_id == projection.driver_bot,
        delivery_type: DeliveryType::Send,
    };
    let candidate = GroupMessage {
        id: run_id.clone(),
        timestamp: bcs_protocol::now_ms(),
        sender: source.sender_id.clone(),
        content: payload.text,
        message_type: GroupMessageType::Bot,
        bot_name: Some(projection.sender_name.clone()),
        role: MessageRole::User,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: None,
    };
    let authorized = apply_outbound_interceptors(flow, &row.group_id, &candidate, &target)
        .await
        .map_err(|_| invalid("queue outbound policy rejected delivery"))?;
    let delivery_target = flow
        .registry
        .resolve_delivery_target(&row.target_bot_id)
        .await?;
    if delivery_target.is_http_provider() && provider_bypass_headers_configured {
        return Err(invalid(
            "Provider queue cannot enforce with bypass headers configured",
        ));
    }
    let connection_id = match &delivery_target {
        BotDeliveryTarget::WebSocket { .. } => Some(
            flow.bot_delivery
                .connection_identity(&delivery_target)
                .await
                .ok_or_else(|| invalid("queue transport cannot pin current Bot connection"))?,
        ),
        BotDeliveryTarget::HttpProvider { .. } => None,
    };
    let protocol_version = frame_protocol_version(
        flow.registry.get_protocol_version(&row.target_bot_id).await,
        &delivery_target,
    );
    let attachments = (!payload.attachments.is_empty())
        .then(|| payload.attachments.into_iter().map(Into::into).collect());
    let tags = if delivery_target.is_http_provider() {
        projection.target_tags.as_slice()
    } else {
        &[]
    };
    let mut frame = if let Some(mut context) = projection.reply_context {
        if !context.message.is_empty()
            || context.recipient.as_deref() != Some(row.target_bot_id.as_str())
        {
            return Err(invalid("invalid queued reply routing context"));
        }
        context.message = authorized.content.clone();
        let mut frame = crate::bot_event::build_send_frame(
            &row.group_id,
            Some(&row.session_id),
            &source.sender_id,
            &projection.sender_name,
            &authorized.content,
            &context,
            tags,
            protocol_version,
            Some(run_id),
        );
        if let bcs_protocol::BcsFrame::Request(request) = &mut frame {
            if let Some(params) = request.params.as_mut() {
                params["idempotency_key"] = serde_json::json!(row.idempotency_key);
                if let Some(attachments) = &attachments {
                    params["attachments"] = serde_json::json!(attachments);
                }
            }
        }
        if let Some(hop) = projection.forward_hop {
            crate::bot_event::stamp_forward_hop(&mut frame, hop);
        }
        frame
    } else if projection.direct_bot {
        build_direct_chat_send_frame(
            run_id,
            &row.group_id,
            &authorized.content,
            &source.sender_id,
            &projection.sender_name,
            &row.target_bot_id,
            tags,
            &attachments,
            &projection.thinking,
            protocol_version,
            Some(&row.session_id),
        )
    } else {
        let group_context = GroupContextInput {
            session_id: row.group_id.clone(),
            driver_bot: projection.driver_bot,
            originator: projection.originator,
            bcs_session_id: Some(row.session_id.clone()),
            participants: projection
                .participants
                .into_iter()
                .map(|p| GroupContextParticipant {
                    id: p.id,
                    name: p.name,
                    role: p.role,
                    is_bot: p.is_bot,
                })
                .collect(),
        };
        build_chat_send_frame(
            run_id,
            &row.group_id,
            &group_context,
            &authorized.content,
            &source.sender_id,
            &projection.sender_name,
            &projection.mentions,
            &row.target_bot_id,
            tags,
            &attachments,
            &projection.thinking,
            row.target_bot_id == source.sender_id,
            protocol_version,
            projection.sender_owner,
            projection.group_type,
            Some(&row.session_id),
        )
    };
    if let Some(identity) = source.content.get("channel_sender_identity") {
        let field = |key| {
            identity
                .get(key)
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned)
                .ok_or_else(|| invalid("invalid persisted channel sender identity"))
        };
        let source = match field("channel_type")?.as_str() {
            "dingtalk" => ChannelSource::DingTalk,
            "webui" => ChannelSource::WebUi,
            _ => ChannelSource::Api,
        };
        apply_channel_info(
            &mut frame,
            ChannelInfo {
                source,
                user_id: Some(field("user_id")?),
                actor_id: Some(field("actor_id")?),
                actor_name: Some(field("display_name")?),
                thread_id: Some(row.group_id.clone()),
                identity_forwarding: Some(true),
            },
        );
    }
    let owner = match &delivery_target {
        BotDeliveryTarget::WebSocket { .. } => BotRunTransportOwner::WebSocket,
        BotDeliveryTarget::HttpProvider {
            provider_id,
            provider_bot_ref,
            ..
        } => BotRunTransportOwner::HttpProvider {
            provider_id: provider_id.clone(),
            provider_bot_ref: provider_bot_ref.clone(),
        },
    };
    let transport = QueuedTransportContext {
        version: 1,
        owner,
        connection_id,
        downstream_run_id: None,
        downstream_session_key: request_session_key(&frame)
            .ok_or_else(|| invalid("queue send lacks downstream session key"))?,
    };
    let mut transport_json = serde_json::to_value(transport).map_err(|_| invalid("queue transport metadata encoding failed"))?;
    transport_json["context_selection"] = serde_json::to_value(payload.selection).map_err(|_| invalid("queue selection encoding failed"))?;
    Ok(PreparedManagedDelivery {
        command: BotDeliveryCommand {
            target: delivery_target,
            run_id: run_id.clone(),
            frame,
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        },
        transport_context_json: transport_json,
    })
}

use super::*;

pub(super) async fn frame_for_target(
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
    let wire_attachments = cmd.attachments.as_ref().and_then(|attachments| {
        let attachments = attachments
            .iter()
            .filter(|attachment| {
                target.delivery_type == DeliveryType::Send
                    || attachment.attachment_type != bcs_domain::AttachmentType::File
            })
            .cloned()
            .map(WireAttachment::from)
            .collect::<Vec<_>>();
        (!attachments.is_empty()).then_some(attachments)
    });
    let provider_tags = if delivery_target.is_http_provider() {
        group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == target.bot_uuid)
            .map(|participant| participant.tags.as_slice())
            .unwrap_or(&[])
    } else {
        &[]
    };
    let mut frame = match target.delivery_type {
        DeliveryType::Send => {
            if context_projection == ContextProjection::DirectBot {
                build_direct_chat_send_frame(
                    run_id,
                    &cmd.group_id,
                    content,
                    &cmd.from_actor_id,
                    sender_display_name,
                    &target.bot_uuid,
                    provider_tags,
                    &wire_attachments,
                    &cmd.thinking,
                    protocol_version,
                    cmd.session_id.as_deref(),
                )
            } else {
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
                    provider_tags,
                    &wire_attachments,
                    &cmd.thinking,
                    is_self,
                    protocol_version,
                    from_bot_owner,
                    group_type_wire(group.group_strategy),
                    cmd.session_id.as_deref(),
                )
            }
        }
        DeliveryType::Inject => {
            if context_projection == ContextProjection::DirectBot {
                build_direct_chat_inject_frame(
                    run_id,
                    &cmd.group_id,
                    content,
                    &cmd.from_actor_id,
                    sender_display_name,
                    &target.bot_uuid,
                    provider_tags,
                    &wire_attachments,
                    protocol_version,
                    cmd.session_id.as_deref(),
                )
            } else {
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
                    provider_tags,
                    &wire_attachments,
                    is_self,
                    protocol_version,
                    from_bot_owner,
                    group_type_wire(group.group_strategy),
                    cmd.session_id.as_deref(),
                )
            }
        }
    };
    if let Some(identity) = cmd.channel_sender_identity.as_ref() {
        let source = match identity.channel_type.as_str() {
            "dingtalk" => ChannelSource::DingTalk,
            "webui" => ChannelSource::WebUi,
            _ => ChannelSource::Api,
        };
        apply_channel_info(
            &mut frame,
            ChannelInfo {
                source,
                user_id: Some(identity.user_id.clone()),
                actor_id: Some(identity.actor_id.clone()),
                actor_name: Some(identity.display_name.clone()),
                thread_id: Some(cmd.group_id.clone()),
                identity_forwarding: Some(true),
            },
        );
    }
    frame
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ContextProjection {
    Group,
    DirectBot,
}

pub(crate) async fn context_projection_for_delivery(
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

pub(crate) fn frame_protocol_version(protocol_version: u32, target: &BotDeliveryTarget) -> u32 {
    if target.is_http_provider() {
        protocol_version.max(3)
    } else {
        protocol_version
    }
}

pub(super) fn bot_delivery_kind(delivery_type: DeliveryType) -> BotDeliveryKind {
    match delivery_type {
        DeliveryType::Send => BotDeliveryKind::Send,
        DeliveryType::Inject => BotDeliveryKind::Inject,
    }
}

pub(super) fn delivery_metric_kind(delivery_type: DeliveryType) -> DeliveryMetricKind {
    match delivery_type {
        DeliveryType::Send => DeliveryMetricKind::Send,
        DeliveryType::Inject => DeliveryMetricKind::Inject,
    }
}

pub(super) async fn publish_web_user_message(
    flow: &BcsMessageFlow,
    cmd: &WebSendCommand,
) -> Vec<FrontendDeliveryResult> {
    let event_json = build_workbench_user_event(flow, cmd).await;
    let group = flow.group.try_get(&cmd.group_id).await.ok().flatten();
    let visibility_domain = group
        .as_ref()
        .map(|group| match group.group_strategy {
            GroupStrategy::Chat => MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
        })
        .unwrap_or(MessageVisibilityDomain::ManagerWorker);
    let audience = match visibility_domain {
        MessageVisibilityDomain::DirectA2a => Some(MessageAudience::FullOnly),
        MessageVisibilityDomain::Chat => None,
        MessageVisibilityDomain::ManagerWorker => {
            let mut actor_ids = vec![cmd.from_actor_id.clone()];
            if let Some(group) = group.as_ref() {
                actor_ids.extend(
                    group
                        .participants
                        .iter()
                        .filter(|participant| participant.role == ParticipantRole::Manager)
                        .map(|participant| participant.bot_uuid.clone()),
                );
            }
            Some(MessageAudience::directed(actor_ids).unwrap_or(MessageAudience::FullOnly))
        }
        MessageVisibilityDomain::StateMachine => Some(
            MessageAudience::directed([cmd.from_actor_id.clone()])
                .unwrap_or(MessageAudience::FullOnly),
        ),
    };
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: frontend_target_for_web_send(cmd),
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: cmd.sender_conn_id,
            visibility_domain,
            audience,
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

pub(super) async fn publish_group_callback_event(
    flow: &BcsMessageFlow,
    cmd: &GroupCallbackCommand,
) -> Vec<FrontendDeliveryResult> {
    let visibility_domain = frontend_domain_for_group(flow, &cmd.group_id).await;
    let audience =
        (visibility_domain != MessageVisibilityDomain::Chat).then_some(MessageAudience::FullOnly);
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
            visibility_domain,
            audience,
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

pub(super) async fn publish_chat_abort_event(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: Option<&str>,
    aborted_run_ids: &[String],
) -> Vec<FrontendDeliveryResult> {
    let visibility_domain = frontend_domain_for_group(flow, group_id).await;
    let audience =
        (visibility_domain != MessageVisibilityDomain::Chat).then_some(MessageAudience::FullOnly);
    let event_json = build_chat_abort_event(group_id, aborted_run_ids);
    let target = match session_id {
        Some(session_id) => FrontendDeliveryTarget::Session {
            session_id: session_id.to_string(),
        },
        None => FrontendDeliveryTarget::Group {
            group_id: group_id.to_string(),
        },
    };
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target,
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain,
            audience,
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

pub(crate) async fn frontend_domain_for_group(
    flow: &BcsMessageFlow,
    group_id: &str,
) -> MessageVisibilityDomain {
    flow.group
        .try_get(group_id)
        .await
        .ok()
        .flatten()
        .map(|group| match group.group_strategy {
            GroupStrategy::Chat => MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
        })
        .unwrap_or(MessageVisibilityDomain::ManagerWorker)
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
    let mut event = serde_json::json!({
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
    if let Some(attachments) = echo_event_attachments(cmd.attachments.as_deref()) {
        event["message"]["attachments"] = attachments;
    }
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

/// Echo image capabilities so frontends can render them immediately, while
/// exposing only stable metadata for files whose URL is target-bot-only.
fn echo_event_attachments(attachments: Option<&[Attachment]>) -> Option<Value> {
    let attachments = attachments.filter(|items| !items.is_empty())?;
    let sanitized = attachments
        .iter()
        .map(|attachment| match attachment.attachment_type {
            bcs_domain::AttachmentType::Image => serde_json::to_value(attachment).ok(),
            // COSEC: file URLs are bearer capabilities scoped to active chat.send.
            bcs_domain::AttachmentType::File => Some(attachment.stable_metadata()),
        })
        .collect::<Option<Vec<_>>>()?;
    Some(Value::Array(sanitized))
}

fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id
        .filter(|value| !value.is_empty())
        .unwrap_or(group_id)
}

fn delivery_type_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

pub(super) fn route_source_for_web_send(group: &Group, cmd: &WebSendCommand) -> &'static str {
    if group.group_kind == GroupKind::Dm {
        "dm"
    } else if cmd.mentions.is_empty() {
        "routing_policy"
    } else {
        "explicit_mentions"
    }
}

pub(super) fn log_message_received(cmd: &WebSendCommand, mode: MessageLogMode) {
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

pub(super) fn log_routing_digest(
    cmd: &WebSendCommand,
    decision: &RoutingDecision,
    mode: MessageLogMode,
    route_source: &'static str,
) {
    // Log the sender's original text so the digest joins with `message_received`;
    // the @-stripped `cleaned_message` only applies to bot-bound deliveries.
    let content = MessageLogContent::from_text(&cmd.message);
    let targets_summary: Vec<MessageLogTargetSummary> = decision
        .targets
        .iter()
        .map(|target| {
            MessageLogTargetSummary::new(&target.bot_uuid)
                .with_delivery_type(delivery_type_slug(target.delivery_type))
                .with_route_source(route_source)
        })
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

pub(super) fn log_bot_deliver_result(
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

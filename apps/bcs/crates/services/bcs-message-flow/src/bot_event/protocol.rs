use super::*;

pub(super) fn build_default_policy_decision(
    group: &Group,
    sender_bot_id: &str,
    default_delivery: DefaultDelivery,
) -> RoutingDecision {
    let targets = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot() && participant.bot_uuid != sender_bot_id)
        // ManagerWorker: workers are fully excluded from broadcast —
        // even @mentions don't reach them. Workers only receive via
        // bcs_assign_task task dispatch.
        .filter(|p| {
            if group.group_strategy == GroupStrategy::ManagerWorker
                && p.role != group.group_strategy.lead_role()
            {
                return false;
            }
            true
        })
        .map(|participant| {
            let is_driver = participant.bot_uuid == group.driver_bot;
            let delivery_type = match default_delivery {
                DefaultDelivery::SendToDriver => {
                    if is_driver {
                        DeliveryType::Send
                    } else {
                        DeliveryType::Inject
                    }
                }
                DefaultDelivery::InjectObservers => DeliveryType::Inject,
            };
            RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver,
                delivery_type,
            }
        })
        .collect();
    let mentions = match default_delivery {
        DefaultDelivery::SendToDriver => vec![group.driver_bot.clone()],
        DefaultDelivery::InjectObservers => Vec::new(),
    };
    RoutingDecision {
        targets,
        mentions,
        cleaned_message: String::new(),
        hidden_mentions: vec![],
    }
}

pub(super) fn build_sender_route_decision(
    group: &Group,
    sender_bot_id: &str,
    route_targets: &[String],
) -> RoutingDecision {
    let targets = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot() && participant.bot_uuid != sender_bot_id)
        // ManagerWorker: workers are fully excluded from broadcast.
        .filter(|p| {
            if group.group_strategy == GroupStrategy::ManagerWorker
                && p.role != group.group_strategy.lead_role()
            {
                return false;
            }
            true
        })
        .map(|participant| {
            let delivery_type = if route_targets.contains(&participant.bot_uuid) {
                DeliveryType::Send
            } else {
                DeliveryType::Inject
            };
            // The lead role differs by strategy (Driver for Chat, Manager for
            // ManagerWorker). Use role-based classification, not the legacy
            // `driver_bot` field identity (bug #8).
            let is_driver = participant.role == group.group_strategy.lead_role();
            RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver,
                delivery_type,
            }
        })
        .collect();
    RoutingDecision {
        targets,
        mentions: route_targets.to_vec(),
        cleaned_message: String::new(),
        hidden_mentions: vec![],
    }
}

pub(super) fn build_response_directive(
    target: &RoutingTarget,
    source: &RequestSource,
    mode: &ResponseMode,
    reason: Option<&str>,
) -> ResponseDirective {
    let should_respond = target.delivery_type == DeliveryType::Send;
    ResponseDirective {
        action: if should_respond {
            DirectiveAction::Respond
        } else {
            DirectiveAction::Observe
        },
        mode: if should_respond {
            Some(to_wire_response_mode(mode))
        } else {
            None
        },
        reason: reason.map(str::to_string),
        request_source: source.clone(),
        matched_by: None,
    }
}

pub(super) fn to_wire_response_mode(mode: &ResponseMode) -> WireResponseMode {
    match mode {
        ResponseMode::Required => WireResponseMode::Required,
        ResponseMode::Optional => WireResponseMode::Optional,
    }
}

const SENDER_ROUTES_MAX_HOPS: u32 = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum RoutingPath {
    Structured,
    SenderRoutes,
    LegacyMention,
    DefaultPolicy,
}

pub(super) fn determine_routing_path(
    has_structured_meta: bool,
    mode: RoutingMode,
    sender_route_targets: Option<&[String]>,
    hop_count: u32,
) -> RoutingPath {
    if has_structured_meta && mode != RoutingMode::Mention {
        return RoutingPath::Structured;
    }
    if let Some(targets) = sender_route_targets {
        if !targets.is_empty() && hop_count < SENDER_ROUTES_MAX_HOPS {
            return RoutingPath::SenderRoutes;
        }
    }
    if mode != RoutingMode::Structured {
        RoutingPath::LegacyMention
    } else {
        RoutingPath::DefaultPolicy
    }
}

pub(crate) fn build_send_frame(
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
    tags: &[String],
    protocol_version: u32,
    message_id: Option<&str>,
) -> BcsFrame {
    build_bot_relay_frame(
        "chat.send",
        true,
        group_id,
        bcs_session_id,
        from_bot,
        from_bot_name,
        message,
        group_context,
        tags,
        protocol_version,
        message_id,
    )
}

pub(super) fn build_inject_frame(
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
    tags: &[String],
    protocol_version: u32,
    message_id: Option<&str>,
) -> BcsFrame {
    build_bot_relay_frame(
        "chat.inject",
        false,
        group_id,
        bcs_session_id,
        from_bot,
        from_bot_name,
        message,
        group_context,
        tags,
        protocol_version,
        message_id,
    )
}

pub(super) fn build_bot_relay_frame(
    method: &str,
    deliver: bool,
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
    tags: &[String],
    protocol_version: u32,
    message_id: Option<&str>,
) -> BcsFrame {
    let prefixed = format!("[from:{}]{}", from_bot_name, message);
    let text = if protocol_version >= 2 {
        format!(
            "{}\n\n[消息内容]\n{}",
            group_context.format_header(),
            prefixed
        )
    } else {
        prefixed
    };
    let supports_session_field = protocol_version >= 3;
    let wire_group_id = match (bcs_session_id, supports_session_field) {
        (Some(session_id), false) if !session_id.ends_with(":00000000") => session_id,
        _ => group_id,
    };
    let mut params = serde_json::json!({
        "session_key": build_session_key(wire_group_id),
        "bcs_group_id": wire_group_id,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "timestamp": now_ms(),
        },
        "channel": {
            "source": "bcs",
            "actor_id": from_bot,
            "actor_name": from_bot_name,
        },
        "from": from_bot,
        "deliver": deliver,
    });

    if !tags.is_empty() {
        params["tags"] = serde_json::json!(tags);
    }

    if supports_session_field {
        if let Some(session_id) = bcs_session_id {
            params["bcs_session_id"] = Value::String(session_id.to_string());
        }
    }

    if let Ok(context) = serde_json::to_value(group_context) {
        params["session_context"] = context;
    }

    BcsFrame::Request(RequestFrame::new(
        message_id
            .map(str::to_string)
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
        method,
        Some(params),
    ))
}

pub(super) fn build_task_result_frame(
    group: Option<&Group>,
    group_id: &str,
    manager_session_id: &str,
    driver_bot: &str,
    target_bot: &str,
    target_bot_name: &str,
    response_text: &str,
    task_id: &str,
    run_id: &str,
    tags: &[String],
) -> BcsFrame {
    let group_context = GroupContext {
        session_id: manager_session_id.to_string(),
        participants: Vec::new(),
        originator: driver_bot.to_string(),
        from: target_bot_name.to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec![driver_bot.to_string()],
        message: response_text.to_string(),
        response_directive: None,
        recipient: Some(driver_bot.to_string()),
        recipient_name: None,
        recipient_role: Some(task_result_recipient_role(group).to_string()),
        delivery_type: Some("send".to_string()),
        routing_mode: None,
        group_type: Some(task_result_group_type(group)),
        from_bot_id: None,
        from_bot_owner: None,
    };
    let mut params = serde_json::json!({
        "session_key": manager_session_id,
        "bcs_group_id": manager_session_id,
        "bcs_session_id": manager_session_id,
        "task_id": task_id,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": format!("[from:{}] {}", target_bot_name, response_text)}],
            "timestamp": now_ms() / 1000,
        },
        "channel": {
            "source": "api",
            "user_id": target_bot_name,
            "actor_id": target_bot,
            "actor_name": target_bot_name,
            "thread_id": group_id,
        },
        "session_context": group_context,
        "timeout_ms": null,
        "idempotency_key": null,
    });
    if !tags.is_empty() {
        params["tags"] = serde_json::json!(tags);
    }

    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

pub(super) fn task_result_recipient_role(group: Option<&Group>) -> &'static str {
    match group.map(|group| group.group_strategy) {
        Some(GroupStrategy::ManagerWorker) => "manager",
        _ => "driver",
    }
}

pub(super) fn task_result_group_type(group: Option<&Group>) -> String {
    group
        .and_then(|group| group_type_wire(group.group_strategy))
        .unwrap_or_else(|| "task".to_string())
}

pub(crate) fn stamp_forward_hop(frame: &mut BcsFrame, hop_count: u32) {
    if let BcsFrame::Request(request) = frame {
        if let Some(params) = &mut request.params {
            params["_forward_hop"] = serde_json::json!(hop_count);
        }
    }
}

pub(super) fn request_id(frame: &BcsFrame) -> Option<String> {
    match frame {
        BcsFrame::Request(request) => Some(request.id.clone()),
        _ => None,
    }
}

pub(super) fn frame_protocol_version(protocol_version: u32, target: &BotDeliveryTarget) -> u32 {
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

pub(super) fn routing_mode_slug(mode: RoutingMode) -> &'static str {
    match mode {
        RoutingMode::Structured => "structured",
        RoutingMode::Mention => "mention",
        RoutingMode::Hybrid => "hybrid",
    }
}

pub(super) fn log_route_digest(
    cmd: &BotEventCommand,
    decision: &RoutingDecision,
    message_text: &str,
    routing_source: &RequestSource,
) {
    let content = MessageLogContent::from_text(message_text);
    let mode = message_log_mode_for_request_source(routing_source);
    let route_source = request_source_slug(routing_source);
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
        session_id = %effective_message_log_session_id(&cmd.group_id, cmd.bcs_session_id.as_deref()),
        group_id = %cmd.group_id,
        run_id = %cmd.run_id,
        bot_id = %cmd.bot_id,
        from_bot_id = %cmd.bot_id,
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

pub(super) fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id
        .filter(|value| !value.is_empty())
        .unwrap_or(group_id)
}

pub(super) fn delivery_type_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

pub(super) fn request_source_slug(source: &RequestSource) -> &'static str {
    match source {
        RequestSource::StructuredMetadata => "structured_metadata",
        RequestSource::LegacyMention => "legacy_mention",
        RequestSource::DefaultPolicy => "default_policy",
        RequestSource::SenderRoutes => "sender_routes",
    }
}

pub(super) fn message_log_mode_for_request_source(source: &RequestSource) -> MessageLogMode {
    match source {
        RequestSource::StructuredMetadata => MessageLogMode::Structured,
        _ => MessageLogMode::FreeChat,
    }
}

pub(super) fn message_log_mode_for_payload(payload: &Value) -> MessageLogMode {
    if payload.get("routing").is_some() {
        MessageLogMode::Structured
    } else {
        MessageLogMode::FreeChat
    }
}

pub(super) fn message_log_status_for_bot_event(state: &ChatEventState) -> MessageLogStatus {
    match state {
        ChatEventState::Error | ChatEventState::Aborted => MessageLogStatus::Failed,
        ChatEventState::Final => MessageLogStatus::Responded,
        _ => MessageLogStatus::Responded,
    }
}

pub(super) fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

pub(super) fn log_incoming_bot_event(cmd: &BotEventCommand, task_id: Option<&str>) {
    let text = extract_message_text(&cmd.event_payload);
    let content = MessageLogContent::from_text(&text);
    let mode = if task_id.is_some() {
        MessageLogMode::ManagerWorker
    } else {
        message_log_mode_for_payload(&cmd.event_payload)
    };
    let status = message_log_status_for_bot_event(&cmd.state);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::BotEvent.as_str(),
        status = status.as_str(),
        mode = mode.as_str(),
        session_id = %effective_message_log_session_id(&cmd.group_id, cmd.bcs_session_id.as_deref()),
        group_id = %cmd.group_id,
        run_id = %cmd.run_id,
        task_id = %task_id.unwrap_or(""),
        bot_id = %cmd.bot_id,
        from_bot_id = %cmd.bot_id,
        chat_event_type = %cmd.event_type,
        chat_event_state = chat_event_state_slug(&cmd.state),
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "bot_event"
    );
}

pub(super) fn log_relay_deliver_result(
    cmd: &BotEventCommand,
    run_id: &str,
    bot_id: &str,
    delivery_type: DeliveryType,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
    routing_source: &RequestSource,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    let mode = message_log_mode_for_request_source(routing_source);
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = mode.as_str(),
            session_id = %effective_message_log_session_id(&cmd.group_id, cmd.bcs_session_id.as_deref()),
            group_id = %cmd.group_id,
            parent_run_id = %cmd.run_id,
            run_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %cmd.bot_id,
            to_bot_id = %bot_id,
            delivery_type = delivery_type_slug(delivery_type),
            delivered = delivered,
            route_source = request_source_slug(routing_source),
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
            mode = mode.as_str(),
            session_id = %effective_message_log_session_id(&cmd.group_id, cmd.bcs_session_id.as_deref()),
            group_id = %cmd.group_id,
            parent_run_id = %cmd.run_id,
            run_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %cmd.bot_id,
            to_bot_id = %bot_id,
            delivery_type = delivery_type_slug(delivery_type),
            delivered = delivered,
            route_source = request_source_slug(routing_source),
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

pub(super) fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{Participant, ParticipantRole};

    fn manager_worker_group(driver: &str, manager: &str) -> Group {
        let mut g = Group::new(
            "g",
            // legacy `driver_bot` field — distinct from the actual lead so we
            // can verify routing flags use `is_lead_participant`, not field
            // identity.
            driver,
            vec![
                Participant::bot(manager, ParticipantRole::Manager),
                Participant::bot("worker-1", ParticipantRole::Worker),
                Participant::bot(driver, ParticipantRole::Worker),
            ],
        );
        g.group_strategy = GroupStrategy::ManagerWorker;
        g
    }

    #[test]
    fn manager_worker_sender_route_marks_manager_as_driver_not_driver_bot_field() {
        // In a ManagerWorker group, `is_driver` on the routing target must be
        // computed via lead role (Manager), not via the legacy `driver_bot`
        // field. Bug #8: bot_event.rs:701 used `bot_uuid == group.driver_bot`,
        // which mis-identified the lead in ManagerWorker groups.
        let group = manager_worker_group(/* driver_bot field = */ "worker-2", "mgr-1");

        // Sender is a worker, so manager + driver_bot worker must remain in
        // the targets list (manager not filtered, driver_bot worker filtered
        // out because workers are excluded from broadcast for ManagerWorker).
        let decision = build_sender_route_decision(&group, "worker-1", &[]);

        let manager_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "mgr-1")
            .expect("manager must be in routing targets");
        assert!(
            manager_target.is_driver,
            "Manager (lead role) must be flagged as is_driver in ManagerWorker strategy"
        );
    }
}

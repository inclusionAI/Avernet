use bcs_domain::SenderType;
use bcs_protocol::{
    BcsFrame, CoordinationCall, DirectiveAction, EventFrame, GroupContext, RequestFrame,
    RequestSource, ResponseDirective, ResponseMode as WireResponseMode, TOOL_ASSIGN_TASK,
    TOOL_SEND_TASK_MESSAGE, TOOL_TASK_COMPLETE,
    build_recipient_group_context, build_session_key,
};
use bcs_service_api::application::channel::OutboundMessage;
use bcs_service_api::{
    ActorStatus, BotDeliveryCommand, BotDeliveryKind, BotDeliveryResult, BotDeliveryTarget,
    BotEventCommand, BotEventOutcome, BotTerminalEvent, BotTerminalState,
    ChannelOutboundEventKind, ChannelRenderHint,
    ChatEventRouting, ChatEventState, ChatResponseMode,
    DefaultDelivery, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryResult, FrontendDeliveryTarget, Group, GroupKind, GroupStatus, GroupStrategy, MessageDeliveryResult,
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MessageLogTargetSummary, MESSAGE_LOG_SCHEMA_VERSION, message_log_json,
    RouteParticipantOverlay, ResponseMode, RoutingDecision, RoutingMode, RoutingTarget,
    RunFallbackDelivery, ServiceError, ServiceResult, SystemMessageEvent, TaskCompleteCommand,
    TaskDispatchCommand, TaskMessageCommand, backfill_bot_names,
};
use serde_json::Value;
use tracing::{info, warn};

use crate::BcsMessageFlow;
use crate::group_flow::apply_overlay_to_decision;
use crate::protocol_context::{group_context_delivery_type, group_context_input, group_type_wire};
use crate::task_store::{TaskEntry, TaskLedgerStatus, TaskStore};
use crate::MSG_LOG_TARGET;

const COORDINATION_PROCESSED_TTL_MS: u64 = 10 * 60 * 1000;

pub async fn handle_bot_event(
    flow: &BcsMessageFlow,
    cmd: BotEventCommand,
) -> ServiceResult<BotEventOutcome> {
    let mut cmd = cmd;
    let task_id_for_event = flow.task_store.resolve_task_id(&cmd.run_id).await;
    log_incoming_bot_event(&cmd, task_id_for_event.as_deref());

    // Persist streaming chat deltas, collapsing a segment's consecutive deltas
    // into ONE row rather than one row per delta. A segment ends when a
    // non-chat event (tool_call / thinking / approval / final) is persisted for
    // the run, so the chat / tool / chat interleaving matches the BCN plugin.
    //
    // Two producers, routed by what the frame carries:
    // - SSE (raw engine) frames carry an incremental `delta_text`; BCS APPENDS
    //   them itself (self-accumulate) instead of trusting the cumulative
    //   `message.content`, which would otherwise persist growing supersets.
    // - Plugin (WS) frames carry already-sliced per-segment text in
    //   `message.content` and no `delta_text`; keep the legacy REPLACE path.
    //
    // We accumulate BEFORE publishing to the frontend so we can synthesize the
    // segment-cumulative `message.content` the frontend SDK renders from — the
    // raw SSE delta frame only carries `delta_text` and no `message`.
    if cmd.event_type == "agent" {
        match cmd.event_payload.get("stream").and_then(|value| value.as_str()) {
            Some("thinking") if cmd.state == ChatEventState::Delta => {
                normalize_thinking_delta(flow, &mut cmd).await;
            }
            Some("thinking") | None => {}
            Some(_) => flow.message_tracker.clear_thinking_buf(&cmd.run_id).await,
        }
    }

    if cmd.state == ChatEventState::Delta
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
    {
        if !cmd.group_id.is_empty() {
            match extract_delta_text(&cmd.event_payload) {
                Some(delta) if !delta.is_empty() => {
                    flow.message_tracker
                        .append_chat_delta(&cmd.run_id, delta)
                        .await;
                    // Inject the segment-accumulated text as `message` so the
                    // frontend SDK (which reads message.content[].text, not
                    // delta_text) can render the streaming reply.
                    if let Some(acc) = flow.message_tracker.peek_chat_buf(&cmd.run_id).await {
                        inject_synthesized_message(&mut cmd.event_payload, &acc);
                    }
                }
                Some(_) => {}
                None => {
                    let msg_text = extract_message_text(&cmd.event_payload);
                    if !msg_text.is_empty() {
                        persist_streaming_chat(flow, &cmd, msg_text).await;
                    }
                }
            }
        }
    }

    let mut frontend_deliveries = publish_incoming_event(flow, &cmd).await?;
    try_channel_outbound(flow, &cmd).await;
    let mut bot_deliveries = Vec::new();

    // Persist tool call events (identified by payload.stream == "tool", distinguished by payload.data.phase)
    if let Some((data, phase)) = tool_event_phase(&cmd.event_payload) {
        match phase {
            "start" => cache_tool_start(flow, &cmd, data).await,
            "result" => {
                persist_tool_result(flow, &cmd, data).await;
                if let Some(coordination) = maybe_handle_coordination_echo(flow, &cmd, data).await? {
                    bot_deliveries.extend(coordination.bot_deliveries);
                    frontend_deliveries.extend(coordination.frontend_deliveries);
                }
            }
            _ => {}
        }
    }

    // A thinking or approval (HITL) event also ends the run's current chat text
    // segment. Flush any buffered chat deltas as ONE row FIRST so the persisted
    // order is chat → thinking/approval → chat, matching the BCN plugin (which
    // flushes visible reply text before every non-assistant event). Repeated
    // thinking frames are cheap no-ops once the buffer is drained.
    if is_chat_segment_boundary_stream(&cmd.event_payload) {
        flush_chat_segment(flow, &cmd, None).await;
    }
    if is_terminal_state(&cmd.state) {
        flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
    }

    // A terminal error/abort ends the run's open chat segment but never reaches
    // the Final relay path below (which flushes). Flush the buffered partial
    // reply + clear per-run tracking here. NON-TASK ONLY: task runs flush inside
    // handle_task_bot_event, behind its status/target validation, so a duplicate
    // or wrong-owner task terminal cannot append history (see that fn). Doing it
    // here for task runs would bypass that guard. flush is a no-op on empty buf.
    if task_id_for_event.is_none()
        && matches!(cmd.state, ChatEventState::Error | ChatEventState::Aborted)
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
    {
        flush_chat_segment(flow, &cmd, None).await;
        flow.message_tracker.cleanup_run(&cmd.run_id).await;
    }

    if let Some(task_id) = task_id_for_event {
        bot_deliveries.extend(handle_task_bot_event(flow, &cmd, &task_id).await?);
        notify_terminal_observer(flow, &cmd).await;
        return Ok(BotEventOutcome {
            bot_deliveries,
            frontend_deliveries,
            unregistered_run_ids: final_run_ids(&cmd),
            mentions: Vec::new(),
            delivered_count: 0,
            failed_count: 0,
            delivery_results: Vec::new(),
        });
    }

    if matches!(cmd.state, ChatEventState::Final)
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
    {
        let relay = relay_final_chat_event(flow, &cmd).await?;
        let relay_mentions = relay.mentions;
        let relay_delivery_results = relay.delivery_results;
        bot_deliveries.extend(relay.bot_deliveries);
        frontend_deliveries.extend(relay.frontend_deliveries);
        flow.message_tracker.cleanup_run(&cmd.run_id).await;
        notify_terminal_observer(flow, &cmd).await;
        return Ok(BotEventOutcome {
            bot_deliveries,
            frontend_deliveries,
            unregistered_run_ids: final_run_ids(&cmd),
            mentions: relay_mentions,
            delivered_count: relay_delivery_results.iter().filter(|result| result.success).count(),
            failed_count: relay_delivery_results.iter().filter(|result| !result.success).count(),
            delivery_results: relay_delivery_results,
        });
    }

    notify_terminal_observer(flow, &cmd).await;
    Ok(BotEventOutcome {
        bot_deliveries,
        frontend_deliveries,
        unregistered_run_ids: final_run_ids(&cmd),
        mentions: Vec::new(),
        delivered_count: 0,
        failed_count: 0,
        delivery_results: Vec::new(),
    })
}

async fn notify_terminal_observer(flow: &BcsMessageFlow, cmd: &BotEventCommand) {
    if !matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
        return;
    }
    let state = match cmd.state {
        ChatEventState::Final => BotTerminalState::Final,
        ChatEventState::Error => BotTerminalState::Error,
        ChatEventState::Aborted => BotTerminalState::Aborted,
        _ => return,
    };
    flow.bot_terminal_observer
        .observe(BotTerminalEvent {
            run_id: cmd.run_id.clone(),
            bot_uuid: cmd.bot_id.clone(),
            state,
            text: terminal_event_text(&cmd.event_payload),
        })
        .await;
}

fn terminal_event_text(event: &Value) -> String {
    let message = extract_message_text(event);
    if !message.is_empty() {
        return message;
    }
    event
        .get("errorMessage")
        .or_else(|| event.get("error_message"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

async fn try_channel_outbound(flow: &BcsMessageFlow, cmd: &BotEventCommand) {
    let Some(channel) = flow.channel.get().cloned() else {
        return;
    };
    let Some(kind) = channel_event_kind(cmd) else {
        return;
    };

    let (sender_role, sender_label) = match flow.group.get(&cmd.group_id).await {
        Some(group) => group
            .participants
            .into_iter()
            .find(|participant| participant.bot_uuid == cmd.bot_id)
            .map(|participant| {
                (
                    participant.role,
                    participant
                        .bot_name
                        .filter(|name| !name.trim().is_empty())
                        .unwrap_or_else(|| cmd.bot_id.clone()),
                )
            })
            .unwrap_or((bcs_domain::ParticipantRole::Observer, cmd.bot_id.clone())),
        None => (bcs_domain::ParticipantRole::Observer, cmd.bot_id.clone()),
    };
    let text = channel_outbound_text(kind, cmd);
    let raw_payload = if kind == ChannelOutboundEventKind::System {
        serde_json::json!({ "state": channel_terminal_state(&cmd.state) })
    } else {
        cmd.event_payload.clone()
    };
    let render_hint = match kind {
        ChannelOutboundEventKind::Agent => ChannelRenderHint::IgnoreByDefault,
        ChannelOutboundEventKind::ChatDelta
        | ChannelOutboundEventKind::ChatFinal
        | ChannelOutboundEventKind::System => ChannelRenderHint::Render,
    };

    if let Err(error) = channel
        .try_outbound(OutboundMessage {
            group_id: cmd.group_id.clone(),
            bcs_session_id: cmd
                .bcs_session_id
                .clone()
                .unwrap_or_else(|| cmd.group_id.clone()),
            run_id: cmd.run_id.clone(),
            sender_actor_id: cmd.bot_id.clone(),
            sender_role,
            sender_label,
            kind,
            text: (!text.is_empty()).then_some(text),
            raw_payload,
            render_hint,
            source_is_channel: false,
        })
        .await
    {
        warn!(run_id = %cmd.run_id, error = %error, "channel outbound hook failed");
    }
}

fn channel_event_kind(cmd: &BotEventCommand) -> Option<ChannelOutboundEventKind> {
    match (cmd.event_type.as_str(), &cmd.state) {
        ("agent", _) => Some(ChannelOutboundEventKind::Agent),
        ("chat" | "chat.event", ChatEventState::Delta) => Some(ChannelOutboundEventKind::ChatDelta),
        ("chat" | "chat.event", ChatEventState::Final) => Some(ChannelOutboundEventKind::ChatFinal),
        ("chat" | "chat.event", ChatEventState::Error | ChatEventState::Aborted) => {
            Some(ChannelOutboundEventKind::System)
        }
        ("chat" | "chat.event", ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd) => {
            Some(ChannelOutboundEventKind::Agent)
        }
        _ => None,
    }
}

fn channel_outbound_text(kind: ChannelOutboundEventKind, cmd: &BotEventCommand) -> String {
    if kind == ChannelOutboundEventKind::System {
        let message = match cmd.state {
            ChatEventState::Error => "机器人连接或执行失败，请稍后重试。",
            ChatEventState::Aborted => "机器人已中止本次处理，请重新发送。",
            _ => return String::new(),
        };
        return format!("{message} (追踪标识: {})", short_ascii_run_id(&cmd.run_id));
    }
    if kind == ChannelOutboundEventKind::ChatDelta {
        if let Some(delta) = extract_delta_text(&cmd.event_payload) {
            return delta.to_string();
        }
    }
    extract_message_text(&cmd.event_payload)
}

fn channel_terminal_state(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Error => "error",
        ChatEventState::Aborted => "aborted",
        _ => "unknown",
    }
}

fn short_ascii_run_id(run_id: &str) -> String {
    let trace: String = run_id
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_'))
        .take(12)
        .collect();
    if trace.is_empty() {
        "unknown".to_string()
    } else {
        trace
    }
}

struct RelayOutcome {
    bot_deliveries: Vec<BotDeliveryResult>,
    frontend_deliveries: Vec<FrontendDeliveryResult>,
    mentions: Vec<String>,
    delivery_results: Vec<MessageDeliveryResult>,
}

async fn relay_final_chat_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
) -> ServiceResult<RelayOutcome> {
    // A2A direct chat has no group context; skip the broadcast-to-group leg
    // silently. Without this, every A2A→provider final would log a
    // "bot relay skipped: group not found" warn even though there is nothing
    // wrong.
    if cmd.group_id.is_empty() {
        return Ok(RelayOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            delivery_results: Vec::new(),
        });
    }

    if flow.bot_relay_turn_limit > 0 {
        let count = flow.group.message_count(&cmd.group_id).await.unwrap_or(0);
        if count >= flow.bot_relay_turn_limit as usize {
            flow.group
                .update_status(&cmd.group_id, GroupStatus::Inactive)
                .await?;
            let frontend = publish_system_event(
                flow,
                &cmd.group_id,
                "system",
                &format!(
                    "[system] 群聊消息数量已达上限 ({}/{}), 请发送新消息继续讨论",
                    count, flow.bot_relay_turn_limit
                ),
            )
            .await?;
            return Ok(RelayOutcome {
                bot_deliveries: Vec::new(),
                frontend_deliveries: vec![frontend],
                mentions: Vec::new(),
                delivery_results: Vec::new(),
            });
        }
    }

    let mut group = match flow.group.get(&cmd.group_id).await {
        Some(group) => group,
        None => {
            warn!(
                group_id = %cmd.group_id,
                sender = %cmd.bot_id,
                "bot relay skipped: group not found"
            );
            return Ok(RelayOutcome {
                bot_deliveries: Vec::new(),
                frontend_deliveries: Vec::new(),
                mentions: Vec::new(),
                delivery_results: Vec::new(),
            });
        }
    };

    let message_text = extract_message_text(&cmd.event_payload);
    if message_text.is_empty() {
        info!(
            group_id = %cmd.group_id,
            sender = %cmd.bot_id,
            "bot relay skipped: empty message text"
        );
        return Ok(RelayOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            delivery_results: Vec::new(),
        });
    }

    backfill_bot_names(flow.registry.as_ref(), &mut group).await;

    // Session-aware routing: when the bot responded in the context of a
    // specific session (bcs_session_id present), swap the group-level
    // participants for the session-level participants. This ensures
    // per-session add/remove (e.g. human joins session via PATCH) takes
    // effect on outbound routing.
    if let Some(ref bcs_session_id) = cmd.bcs_session_id {
        if let Some(ref session_mgmt) = flow.session_management {
            if let Ok(Some(sess)) = session_mgmt.get(bcs_session_id).await {
                if !sess.participants.is_empty() {
                    group.participants = sess.participants;
                }
            }
        }
    }

    let overlay = build_route_overlay(flow, &group).await;
    let routing_meta: Option<ChatEventRouting> = cmd
        .event_payload
        .get("routing")
        .and_then(|value| serde_json::from_value(value.clone()).ok());
    let routing_policy = group.routing_policy.clone().unwrap_or_default();
    let hop_count = cmd
        .event_payload
        .get("_forward_hop")
        .and_then(|value| value.as_u64())
        .unwrap_or(0) as u32;
    let sender_route_targets = routing_policy
        .sender_routes
        .get(&cmd.bot_id)
        .filter(|targets| !targets.is_empty());
    let path = if group.group_kind == GroupKind::Dm {
        RoutingPath::LegacyMention
    } else {
        determine_routing_path(
            routing_meta.is_some(),
            routing_policy.mode,
            sender_route_targets.map(|targets| targets.as_slice()),
            hop_count,
        )
    };

    let (mut decision, routing_source) = if group.group_kind == GroupKind::Dm {
        (
            flow.routing
                .route_dm_with_overlay(&group, &message_text, &cmd.bot_id, &overlay)
                .await,
            RequestSource::LegacyMention,
        )
    } else {
        match path {
            RoutingPath::Structured => {
                let meta = routing_meta.as_ref().expect("structured path requires metadata");
                let decision = flow
                    .routing
                    .route_structured(&group, meta, &cmd.bot_id, &*flow.registry)
                    .await
                    .map_err(|error| ServiceError::InvalidOperation {
                        message: error.to_string(),
                        request_id: Some(cmd.run_id.clone()),
                    })?;
                (decision, RequestSource::StructuredMetadata)
            }
            RoutingPath::SenderRoutes => {
                let targets = sender_route_targets.expect("sender routes path requires targets");
                (
                    build_sender_route_decision(&group, &cmd.bot_id, targets),
                    RequestSource::SenderRoutes,
                )
            }
            RoutingPath::LegacyMention => {
                let mut decision = flow
                    .routing
                    .route_with_overlay(&group, &message_text, Some(&cmd.bot_id), &overlay)
                    .await;
                if decision.mentions.is_empty()
                    && routing_policy.default_bot_final_delivery == DefaultDelivery::InjectObservers
                {
                    for target in &mut decision.targets {
                        if target.is_driver && target.delivery_type == DeliveryType::Send {
                            target.delivery_type = DeliveryType::Inject;
                        }
                    }
                }
                (decision, RequestSource::LegacyMention)
            }
            RoutingPath::DefaultPolicy => (
                build_default_policy_decision(
                    &group,
                    &cmd.bot_id,
                    routing_policy.default_bot_final_delivery,
                ),
                RequestSource::DefaultPolicy,
            ),
        }
    };
    decision = apply_overlay_to_decision(decision, &overlay);

    log_route_digest(cmd, &decision, &message_text, &routing_source);

    let cleaned = if routing_source == RequestSource::LegacyMention {
        decision.cleaned_message.clone()
    } else {
        message_text
    };
    let sender_display_name = sender_display_name(flow, &cmd.bot_id).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.bot_id).await;
    let routing_mode = routing_meta
        .as_ref()
        .and_then(|meta| meta.mode.clone())
        .unwrap_or(ResponseMode::Required);
    let routing_reason = routing_meta.as_ref().map(|meta| meta.reason.clone());
    let policy_mode = routing_mode_slug(routing_policy.mode).to_string();
    let mut protocol_group = group_context_input(&group);
    if let Some(ref bcs_session_id) = cmd.bcs_session_id {
        protocol_group.session_id = bcs_session_id.clone();
        protocol_group.bcs_session_id = Some(bcs_session_id.clone());
    }
    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();

    // Finalize the run's open chat segment: flush the buffered streaming text
    // as ONE row, using the final frame's complete text. (Final-only runs with
    // no buffered deltas just insert the final text.)
    persist_final_chat(flow, &cmd, cleaned.clone()).await;

    for target in &decision.targets {
        let directive = build_response_directive(
            target,
            &routing_source,
            &routing_mode,
            routing_reason.as_deref(),
        );
        let group_context = build_recipient_group_context(
            &protocol_group,
            &target.bot_uuid,
            &cmd.bot_id,
            &cleaned,
            &decision.mentions,
            group_context_delivery_type(target.delivery_type),
            Some(directive),
            Some(policy_mode.clone()),
            group_type_wire(group.group_strategy),
            from_bot_owner.clone(),
        );
        let outbound_message = bcs_service_api::GroupMessage {
            id: uuid::Uuid::new_v4().to_string(),
            timestamp: now_ms(),
            sender: cmd.bot_id.clone(),
            content: cleaned.clone(),
            message_type: bcs_service_api::GroupMessageType::Bot,
            bot_name: Some(sender_display_name.clone()),
            role: bcs_service_api::MessageRole::Assistant,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
        };
        let outbound_message = match crate::group_flow::apply_outbound_interceptors(
            flow,
            &cmd.group_id,
            &outbound_message,
            target,
        )
        .await
        {
            Ok(message) => message,
            Err(reason) => {
                log_relay_deliver_result(
                    cmd,
                    &cmd.run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(reason.message.as_str()),
                    Some("interceptor"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(reason.message),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(ServiceError::Unauthorized(format!(
                        "outbound blocked by interceptor '{}'",
                        reason.interceptor_id
                    ))),
                });
                continue;
            }
        };

        let delivery_target = match flow.registry.resolve_delivery_target(&target.bot_uuid).await {
            Ok(target) => target,
            Err(error) => {
                let error_text = error.to_string();
                log_relay_deliver_result(
                    cmd,
                    &cmd.run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("resolve_target"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(error_text),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        let protocol_version = frame_protocol_version(
            flow.registry.get_protocol_version(&target.bot_uuid).await,
            &delivery_target,
        );
        let mut frame = match target.delivery_type {
            DeliveryType::Send => build_send_frame(
                &cmd.group_id,
                cmd.bcs_session_id.as_deref(),
                &cmd.bot_id,
                &sender_display_name,
                &outbound_message.content,
                &group_context,
                protocol_version,
                None,
            ),
            DeliveryType::Inject => build_inject_frame(
                &cmd.group_id,
                cmd.bcs_session_id.as_deref(),
                &cmd.bot_id,
                &sender_display_name,
                &outbound_message.content,
                &group_context,
                protocol_version,
                None,
            ),
        };
        if path == RoutingPath::SenderRoutes {
            stamp_forward_hop(&mut frame, hop_count + 1);
        }

        let run_id = request_id(&frame).unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        // FIXME(interceptor-modify): if SecurityInterceptor rewrote
        // outbound_message.id (security gateway task_id), it isn't reaching
        // the bot — frame's request_id is generated upstream from cmd, not
        // from outbound_message. Threading the modified id requires changing
        // build_send_frame / build_inject_frame signatures. Tracked in the
        // Phase-5 follow-up list (Modify semantics completeness).
        let delivery_kind = bot_delivery_kind(target.delivery_type);
        let provider_transport = flow
            .provider_transport_preference(&target.bot_uuid, &delivery_kind, &delivery_target)
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
                log_relay_deliver_result(
                    cmd,
                    &run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string).as_deref(),
                    None,
                    &routing_source,
                );
                flow.record_successful_send_context(
                    target.delivery_type,
                    &result,
                    &run_id,
                    &target.bot_uuid,
                    &cmd.group_id,
                    cmd.bcs_session_id.as_deref(),
                )
                .await;
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: result.delivered,
                    error: result.error.as_ref().map(ToString::to_string),
                });
                bot_deliveries.push(result);
            }
            Err(error) => {
                let error_text = error.to_string();
                log_relay_deliver_result(
                    cmd,
                    &run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("deliver"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(error_text),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    if bot_deliveries.iter().any(|delivery| delivery.delivered) {
        flow.group.increment_message_count(&cmd.group_id).await?;
    }

    if !decision.hidden_mentions.is_empty() {
        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.bcs_session_id.as_deref().unwrap_or(&cmd.group_id);
            for hidden in &decision.hidden_mentions {
                let event = SystemMessageEvent::BotHiddenNotice {
                    group_id: cmd.group_id.clone(),
                    mentioner_bot_id: cmd.bot_id.clone(),
                    hidden_bot_name: hidden.hidden_bot_name.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    Ok(RelayOutcome {
        bot_deliveries,
        frontend_deliveries: Vec::new(),
        mentions: decision.mentions,
        delivery_results,
    })
}

async fn handle_task_bot_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    task_id: &str,
) -> ServiceResult<Vec<BotDeliveryResult>> {
    let Some(entry) = flow.task_store.get(task_id).await else {
        return Ok(Vec::new());
    };
    if entry.status != TaskLedgerStatus::Dispatched || entry.target_bot != cmd.bot_id {
        return Ok(Vec::new());
    }
    if !is_terminal_state(&cmd.state) {
        record_task_response_event(flow, task_id, cmd).await;
        return Ok(Vec::new());
    }

    let response_text = preview_task_response_text(&entry, cmd).await;
    let group = flow.group.get(&entry.group_id).await;

    let target_bot_name = entry
        .target_bot_name
        .as_deref()
        .unwrap_or(entry.target_bot.as_str());
    let manager_result_run_id = uuid::Uuid::new_v4().to_string();
    let frame = build_task_result_frame(
        group.as_ref(),
        &entry.group_id,
        entry.session_id.as_deref().unwrap_or(&entry.group_id),
        &entry.driver_bot,
        &entry.target_bot,
        target_bot_name,
        &response_text,
        &entry.task_id,
        &manager_result_run_id,
    );
    let delivery_target = flow
        .registry
        .resolve_delivery_target(&entry.driver_bot)
        .await?;
    let delivery_kind = BotDeliveryKind::TaskResult;
    let provider_transport = flow
        .provider_transport_preference(&entry.driver_bot, &delivery_kind, &delivery_target)
        .await;
    let result = flow
        .bot_delivery
        .deliver(BotDeliveryCommand {
            target: delivery_target,
            run_id: manager_result_run_id.clone(),
            frame,
            delivery_kind,
            provider_transport,
        })
        .await?;
    if !result.delivered {
        return Ok(vec![result]);
    }

    flow.record_successful_send_context(
        DeliveryType::Send,
        &result,
        &manager_result_run_id,
        &entry.driver_bot,
        &entry.group_id,
        entry.session_id.as_deref(),
    )
    .await;

    record_task_response_event(flow, task_id, cmd).await;

    // Terminal chat for a validated, still-Dispatched task run is persisted only
    // after the task result reaches the driver. If delivery fails, the task stays
    // retryable and no history side effect is committed.
    if matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
        if matches!(cmd.state, ChatEventState::Final) {
            persist_task_final_chat(flow, cmd, response_text.clone()).await;
        } else {
            flush_chat_segment(flow, cmd, None).await;
        }
        flow.message_tracker.cleanup_run(&cmd.run_id).await;
    }

    if let Some(group) = group.as_ref() {
        if group.group_strategy == GroupStrategy::ManagerWorker {
            crate::group_flow::try_persist_group_message(
                flow,
                &entry.group_id,
                Some(entry.session_id.as_deref().unwrap_or(&entry.group_id)),
                &entry.target_bot,
                SenderType::Bot,
                "chat",
                Value::String(response_text.clone()),
                None,
                None,
                &entry.task_id,
            )
            .await;
        }
    }
    flow.task_store.mark_replied(task_id).await;
    if let Some(group) = group.as_ref() {
        crate::task_flow::emit_task_ledger_status(
            flow,
            group,
            &entry.group_id,
            entry.session_id.as_deref(),
            &entry.driver_bot,
        )
        .await;
    }
    Ok(vec![result])
}

async fn preview_task_response_text(entry: &TaskEntry, cmd: &BotEventCommand) -> String {
    let scratch = TaskStore::new();
    scratch.register(entry.clone()).await;
    record_task_response_event_in_store(&scratch, &entry.task_id, cmd).await;
    let attempted_entry = scratch.get(&entry.task_id).await.unwrap_or_else(|| entry.clone());
    task_response_text(&attempted_entry, cmd)
}

fn task_response_text(entry: &TaskEntry, cmd: &BotEventCommand) -> String {
    let response_text = if entry.response_mode == ChatResponseMode::Full {
        extract_message_text(&cmd.event_payload)
    } else if entry.response_content.is_empty() {
        extract_message_text(&cmd.event_payload)
    } else {
        entry.response_content.clone()
    };
    if response_text.is_empty() {
        "[no response]".to_string()
    } else {
        response_text
    }
}

async fn record_task_response_event(flow: &BcsMessageFlow, task_id: &str, cmd: &BotEventCommand) {
    record_task_response_event_in_store(flow.task_store.as_ref(), task_id, cmd).await;
}

async fn record_task_response_event_in_store(
    task_store: &TaskStore,
    task_id: &str,
    cmd: &BotEventCommand,
) {
    if cmd.event_type == "agent"
        && cmd.event_payload.get("stream").and_then(|value| value.as_str()) == Some("tool")
    {
        task_store.record_response_tool_call(task_id).await;
        return;
    }
    match cmd.state {
        ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => {
            task_store.record_response_tool_call(task_id).await;
        }
        ChatEventState::Delta | ChatEventState::Final => {
            let text = extract_message_text(&cmd.event_payload);
            task_store.record_response_text(task_id, &text).await;
        }
        ChatEventState::Error | ChatEventState::Aborted => {}
    }
}

async fn publish_incoming_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
) -> ServiceResult<Vec<FrontendDeliveryResult>> {
    let frontend_event = workbench_event_name(&cmd.event_type, &cmd.state);
    let frame = serde_json::json!({
        "type": "event",
        "event": frontend_event,
        "payload": cmd.event_payload,
        "group_id": cmd.group_id,
        "bot_uuid": cmd.bot_id,
    });
    let event_json = serde_json::to_string(&frame)?;
    let frontend_target = match cmd.bcs_session_id.clone() {
        Some(session_id) => FrontendDeliveryTarget::Session { session_id },
        None => FrontendDeliveryTarget::Group {
            group_id: cmd.group_id.clone(),
        },
    };
    let run_fallback = RunFallbackDelivery {
        run_id: cmd.run_id.clone(),
        session_id: cmd
            .bcs_session_id
            .clone()
            .unwrap_or_else(|| cmd.group_id.clone()),
        event_json: serde_json::to_string(&BcsFrame::Event(EventFrame::new(
            cmd.event_type.clone(),
            Some(cmd.event_payload.clone()),
            None,
        )))?,
    };
    let result = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: frontend_target,
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(run_fallback),
            exclude_conn_id: None,
        })
        .await?;
    Ok(vec![result])
}

fn workbench_event_name<'a>(event_type: &'a str, state: &ChatEventState) -> &'a str {
    match event_type {
        "agent" => "agent",
        "chat" | "chat.event" => match state {
            ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => "agent",
            ChatEventState::Delta
            | ChatEventState::Final
            | ChatEventState::Error
            | ChatEventState::Aborted => "chat",
        },
        _ => event_type,
    }
}

async fn publish_system_event(
    flow: &BcsMessageFlow,
    group_id: &str,
    bot_id: &str,
    text: &str,
) -> ServiceResult<FrontendDeliveryResult> {
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": bot_id,
        "payload": {
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": text,
                }],
            },
        },
    });
    flow.frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: group_id.to_string(),
            },
            event_json: serde_json::to_string(&frame)?,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
        })
        .await
}

async fn build_route_overlay(
    flow: &BcsMessageFlow,
    group: &Group,
) -> Vec<RouteParticipantOverlay> {
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

async fn sender_display_name(flow: &BcsMessageFlow, bot_id: &str) -> String {
    flow.registry
        .get(bot_id)
        .await
        .and_then(|bot| bot.capabilities.name.clone())
        .unwrap_or_else(|| bot_id.to_string())
}

async fn from_bot_owner(flow: &BcsMessageFlow, bot_id: &str) -> Option<String> {
    if bot_id.starts_with("human_") {
        None
    } else {
        flow.registry.get(bot_id).await.and_then(|bot| bot.created_by)
    }
}

fn final_run_ids(cmd: &BotEventCommand) -> Vec<String> {
    if is_terminal_state(&cmd.state) {
        vec![cmd.run_id.clone()]
    } else {
        Vec::new()
    }
}

fn is_terminal_state(state: &ChatEventState) -> bool {
    matches!(
        state,
        ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted
    )
}

pub(crate) fn extract_message_text(event: &Value) -> String {
    if let Some(message) = event.get("message") {
        if let Some(content) = message.get("content") {
            if let Some(arr) = content.as_array() {
                return arr
                    .iter()
                    .filter_map(|block| block.get("text").and_then(|text| text.as_str()))
                    .collect::<Vec<_>>()
                    .join("\n");
            }
            if let Some(text) = content.as_str() {
                return text.to_string();
            }
        }
    }
    String::new()
}

/// Incremental delta text for a single streaming chat frame, if present.
///
/// Only the SSE (raw engine) ingest path sets `delta_text`; plugin (WS) frames
/// omit it. Its presence is what routes a delta frame to self-accumulation vs
/// the legacy cumulative path, so an empty string is still meaningful (a delta
/// frame that happens to carry no new text) and is returned as `Some("")`.
fn extract_delta_text(event: &Value) -> Option<&str> {
    event.get("delta_text").and_then(|value| value.as_str())
}

async fn normalize_thinking_delta(flow: &BcsMessageFlow, cmd: &mut BotEventCommand) {
    if cmd.event_payload.get("stream").and_then(|value| value.as_str()) != Some("thinking") {
        return;
    }
    let Some(delta) = extract_thinking_delta(&cmd.event_payload) else {
        return;
    };
    let accumulated = flow
        .message_tracker
        .append_thinking_delta(&cmd.run_id, delta)
        .await;
    inject_thinking_text(&mut cmd.event_payload, &accumulated);
}

fn extract_thinking_delta(event: &Value) -> Option<&str> {
    event
        .get("data")
        .and_then(|data| data.get("delta"))
        .and_then(|value| value.as_str())
}

fn inject_thinking_text(event: &mut Value, accumulated_text: &str) {
    if let Some(data) = event.get_mut("data").and_then(|value| value.as_object_mut()) {
        data.insert("text".to_string(), Value::String(accumulated_text.to_string()));
    }
}

/// Overwrite the frame's `message` with a synthesized assistant message whose
/// `content` is the segment-accumulated text. The SSE (raw engine) delta frame
/// carries only `delta_text`; the frontend SDK renders `message.content[].text`
/// (segment-cumulative), so we build that shape here from BCS's own accumulator.
/// Matches the wire `MessageContent` layout: `{role, content:[{type,text}], timestamp}`.
fn inject_synthesized_message(event: &mut Value, accumulated_text: &str) {
    if let Some(obj) = event.as_object_mut() {
        obj.insert(
            "message".to_string(),
            serde_json::json!({
                "role": "assistant",
                "content": [{ "type": "text", "text": accumulated_text }],
                "timestamp": bcs_protocol::now_ms(),
            }),
        );
    }
}

/// If `payload` is a tool-call event (stream == "tool"), returns `(data, phase)`.
fn tool_event_phase(payload: &Value) -> Option<(&Value, &str)> {
    if payload.get("stream").and_then(|v| v.as_str()) != Some("tool") {
        return None;
    }
    let data = payload.get("data")?;
    let phase = data.get("phase").and_then(|v| v.as_str())?;
    Some((data, phase))
}

/// True when `payload` is an agent stream event that ends the current chat text
/// segment — `thinking` or `approval` (HITL). Mirrors the BCN plugin, which
/// flushes visible reply text before every non-assistant event. Tool events are
/// handled separately via [`tool_event_phase`].
fn is_chat_segment_boundary_stream(payload: &Value) -> bool {
    matches!(
        payload.get("stream").and_then(|value| value.as_str()),
        Some("thinking") | Some("approval")
    )
}

async fn cache_tool_start(flow: &BcsMessageFlow, cmd: &BotEventCommand, data: &Value) {
    if cmd.group_id.is_empty() {
        return;
    }
    let tool_call_id = data.get("toolCallId").and_then(|v| v.as_str()).unwrap_or("");
    let args = data.get("args").cloned().unwrap_or(Value::Null);
    let session_id = cmd.bcs_session_id.clone().unwrap_or_default();

    flow.message_tracker
        .cache_tool_call_start(
            tool_call_id.to_string(),
            crate::message_tracker::ToolCallStartInfo {
                run_id: cmd.run_id.clone(),
                session_id,
                args,
            },
        )
        .await;
}

async fn persist_tool_result(flow: &BcsMessageFlow, cmd: &BotEventCommand, data: &Value) {
    if cmd.group_id.is_empty() {
        return;
    }

    // A tool_call ends the run's current chat text segment. Flush any buffered
    // chat deltas as ONE row FIRST, so the persisted order is chat → tool_call
    // → (next) chat, matching the BCN plugin path.
    flush_chat_segment(flow, cmd, None).await;

    let tool_call_id = data.get("toolCallId").and_then(|v| v.as_str()).unwrap_or("");
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let is_error = data.get("isError").and_then(|v| v.as_bool()).unwrap_or(false);
    let result = data.get("result").cloned().unwrap_or(Value::Null);

    let start_info = flow.message_tracker.take_tool_call_start(tool_call_id).await;
    let (args, run_id, session_id) = match start_info {
        Some(ref info) => (
            info.args.clone(),
            info.run_id.clone(),
            info.session_id.clone(),
        ),
        None => (
            Value::Null,
            cmd.run_id.clone(),
            cmd.bcs_session_id.clone().unwrap_or_default(),
        ),
    };

    let content = serde_json::json!({
        "tool_call_id": tool_call_id,
        "name": name,
        "args": args,
        "result": result,
        "is_error": is_error,
    });

    crate::group_flow::try_persist_group_message(
        flow,
        &cmd.group_id,
        if session_id.is_empty() { None } else { Some(&session_id) },
        &cmd.bot_id,
        SenderType::Bot,
        "tool_call",
        content,
        None,
        crate::group_flow::manager_worker_self_owner(
            flow,
            &cmd.group_id,
            cmd.bcs_session_id.as_deref(),
            &cmd.bot_id,
        )
        .await,
        &run_id,
    )
    .await;
}

struct CoordinationEchoDispatch {
    bot_deliveries: Vec<BotDeliveryResult>,
    frontend_deliveries: Vec<FrontendDeliveryResult>,
}

async fn maybe_handle_coordination_echo(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    data: &Value,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    if cmd.event_type != "agent" || cmd.group_id.is_empty() {
        return Ok(None);
    }
    if data.get("isError").and_then(|value| value.as_bool()) == Some(true) {
        return Ok(None);
    }

    let Some(result_text) = tool_result_text(data) else {
        return Ok(None);
    };
    let Some(call) = CoordinationCall::from_stdout(&result_text) else {
        return Ok(None);
    };
    if !coordination_tool_name_allowed(data) {
        warn!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool_name = ?data.get("name").and_then(|value| value.as_str()),
            "Ignoring coordination echo from unsupported tool name"
        );
        return Ok(None);
    }

    let tool_call_id = data
        .get("toolCallId")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let Some(tool_call_id) = tool_call_id else {
        warn!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool = %call.tool,
            "Ignoring coordination echo without toolCallId"
        );
        return Ok(None);
    };
    let dedup_key = format!("{}:{}", cmd.run_id, tool_call_id);
    if !flow
        .message_tracker
        .mark_coordination_echo_seen(dedup_key.clone(), now_ms(), COORDINATION_PROCESSED_TTL_MS)
        .await
    {
        info!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool_call_id = %tool_call_id,
            dedup_key = %dedup_key,
            "Skipping duplicate coordination echo"
        );
        return Ok(None);
    }

    dispatch_coordination_call(flow, cmd, &call).await
}

async fn dispatch_coordination_call(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    call: &CoordinationCall,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    match call.tool.as_str() {
        TOOL_ASSIGN_TASK => {
            let Some(target_bot_id) = coordination_argument_str(call, "target_bot") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_assign_task echo without target_bot"
                );
                return Ok(None);
            };
            let Some(message) = coordination_argument_str(call, "message") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_assign_task echo without message"
                );
                return Ok(None);
            };
            let mut payload = serde_json::json!({
                "message": message,
            });
            if let Some(response_mode) = coordination_argument_str(call, "response_mode") {
                payload["response_mode"] = Value::String(response_mode.to_string());
            }
            if let Some(session_id) = cmd.bcs_session_id.as_deref() {
                payload["bcs_session_id"] = Value::String(session_id.to_string());
            }
            match crate::task_flow::handle_task_dispatch(
                flow,
                TaskDispatchCommand {
                    driver_bot_id: cmd.bot_id.clone(),
                    group_id: cmd.group_id.clone(),
                    target_bot_id: target_bot_id.to_string(),
                    target_bot_name: None,
                    payload,
                },
            )
            .await
            {
                Ok(outcome) => Ok(Some(CoordinationEchoDispatch {
                    bot_deliveries: outcome.bot_deliveries,
                    frontend_deliveries: outcome.frontend_deliveries,
                })),
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_assign_task coordination echo"
                    );
                    Ok(None)
                }
            }
        }
        TOOL_SEND_TASK_MESSAGE => {
            let Some(session_id) = cmd.bcs_session_id.as_deref() else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_send_task_message echo without bcs_session_id"
                );
                return Ok(None);
            };
            let Some(message) = coordination_argument_str(call, "message") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_send_task_message echo without message"
                );
                return Ok(None);
            };
            match crate::task_flow::handle_task_message(
                flow,
                TaskMessageCommand {
                    worker_bot_id: cmd.bot_id.clone(),
                    group_id: cmd.group_id.clone(),
                    payload: serde_json::json!({
                        "message": message,
                        "bcs_session_id": session_id,
                    }),
                },
            )
            .await
            {
                Ok(outcome) => Ok(Some(CoordinationEchoDispatch {
                    bot_deliveries: outcome.bot_deliveries,
                    frontend_deliveries: outcome.frontend_deliveries,
                })),
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_send_task_message coordination echo"
                    );
                    Ok(None)
                }
            }
        }
        TOOL_TASK_COMPLETE => {
            let Some(summary) = coordination_argument_str(call, "summary") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_task_complete echo without summary"
                );
                return Ok(None);
            };
            let mut payload = serde_json::json!({
                "group_id": cmd.group_id.as_str(),
                "summary": summary,
                "status": "completed",
            });
            if let Some(session_id) = cmd.bcs_session_id.as_deref() {
                payload["bcs_session_id"] = Value::String(session_id.to_string());
            }
            match crate::task_flow::handle_task_complete(
                flow,
                TaskCompleteCommand {
                    task_id: cmd.group_id.clone(),
                    bot_id: cmd.bot_id.clone(),
                    via_echo: true,
                    payload,
                },
            )
            .await
            {
                Ok(outcome) => {
                    if outcome.blocked {
                        warn!(
                            bot_id = %cmd.bot_id,
                            group_id = %cmd.group_id,
                            run_id = %cmd.run_id,
                            pending = ?outcome.pending,
                            "Task completion coordination echo blocked by pending targets"
                        );
                        return Ok(None);
                    }
                    Ok(Some(CoordinationEchoDispatch {
                        bot_deliveries: Vec::new(),
                        frontend_deliveries: outcome.frontend_deliveries,
                    }))
                }
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_task_complete coordination echo"
                    );
                    Ok(None)
                }
            }
        }
        _ => {
            warn!(
                bot_id = %cmd.bot_id,
                group_id = %cmd.group_id,
                run_id = %cmd.run_id,
                tool = %call.tool,
                "Ignoring coordination echo for unsupported tool"
            );
            Ok(None)
        }
    }
}

fn coordination_tool_name_allowed(data: &Value) -> bool {
    let Some(name) = data.get("name").and_then(|value| value.as_str()) else {
        // Claude Code command_output callbacks do not always carry the source
        // tool name; authenticated event intake plus task-flow role checks
        // still gate the side effect after the coordination magic is parsed.
        return true;
    };
    let name = name.trim();
    if name.is_empty() {
        return true;
    }
    matches!(
        name.to_ascii_lowercase().as_str(),
        "exec" | "bash" | "shell" | "mcporter"
    )
}

fn tool_result_text(data: &Value) -> Option<String> {
    let result = data.get("result")?;
    if let Some(text) = result.as_str().filter(|value| !value.is_empty()) {
        return Some(text.to_string());
    }
    let mut text = String::new();
    let mut found_text = false;
    for block in result
        .get("content")
        .and_then(|value| value.as_array())
        .into_iter()
        .flatten()
    {
        if let Some(block_text) = block.get("text").and_then(|value| value.as_str()) {
            text.push_str(block_text);
            found_text = true;
        }
    }
    found_text.then_some(text)
}

fn coordination_argument_str<'a>(
    call: &'a CoordinationCall,
    key: &str,
) -> Option<&'a str> {
    call.arguments
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

/// Buffer a streaming chat delta in memory — NO DB write.
///
/// Per-token deltas only update an in-memory buffer (latest cumulative text).
/// The buffered text is flushed as a SINGLE INSERT at the next segment boundary
/// ([`flush_chat_segment`] from a tool_call or the run's final), so a high-rate
/// delta stream costs one row, not one write per token.
async fn persist_streaming_chat(flow: &BcsMessageFlow, cmd: &BotEventCommand, msg_text: String) {
    flow.message_tracker
        .buffer_chat_text(&cmd.run_id, msg_text)
        .await;
}

/// Flush the run's buffered chat segment as one INSERT, then clear the buffer.
/// `final_text`, when given, overrides the buffer (the final frame carries the
/// authoritative full text); otherwise the buffered delta text is used. No-op
/// if there is nothing to write.
async fn flush_chat_segment(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    final_text: Option<String>,
) {
    let buffered = flow.message_tracker.take_chat_buf(&cmd.run_id).await;
    let text = match (final_text, buffered) {
        (Some(t), _) => t,         // final frame wins
        (None, Some(t)) => t,      // flush buffered delta text
        (None, None) => return,    // nothing streamed in this segment
    };
    if text.is_empty() || cmd.group_id.is_empty() {
        return;
    }
    crate::group_flow::try_persist_group_message(
        flow,
        &cmd.group_id,
        cmd.bcs_session_id.as_deref(),
        &cmd.bot_id,
        SenderType::Bot,
        "chat",
        Value::String(text),
        None,
        crate::group_flow::manager_worker_self_owner(
            flow,
            &cmd.group_id,
            cmd.bcs_session_id.as_deref(),
            &cmd.bot_id,
        )
        .await,
        &cmd.run_id,
    )
    .await;
}

/// Persist the run's final chat text as a single row.
///
/// - **SSE (delta) mode**: BCS already stitched the run's text from `delta_text`
///   frames and flushed each completed segment at its boundary. The open final
///   segment (if any) is in the buffer, so flush it WITHOUT override — writing
///   the engine's cumulative full text here would duplicate the already-persisted
///   segments. If the buffer is empty (final right after a boundary), nothing is
///   written.
/// - **Legacy plugin mode**: no `delta_text` was seen; the final frame carries
///   the authoritative full text and supersedes any buffered segment text.
async fn persist_final_chat(flow: &BcsMessageFlow, cmd: &BotEventCommand, text: String) {
    if flow.message_tracker.is_chat_delta_mode(&cmd.run_id).await {
        flush_chat_segment(flow, cmd, None).await;
    } else {
        flush_chat_segment(flow, cmd, Some(text)).await;
    }
}

async fn persist_task_final_chat(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    response_text: String,
) {
    persist_final_chat(flow, cmd, response_text).await;
}

fn build_default_policy_decision(
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

fn build_sender_route_decision(
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

fn build_response_directive(
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
        mode: if should_respond { Some(to_wire_response_mode(mode)) } else { None },
        reason: reason.map(str::to_string),
        request_source: source.clone(),
        matched_by: None,
    }
}

fn to_wire_response_mode(mode: &ResponseMode) -> WireResponseMode {
    match mode {
        ResponseMode::Required => WireResponseMode::Required,
        ResponseMode::Optional => WireResponseMode::Optional,
    }
}

const SENDER_ROUTES_MAX_HOPS: u32 = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RoutingPath {
    Structured,
    SenderRoutes,
    LegacyMention,
    DefaultPolicy,
}

fn determine_routing_path(
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

fn build_send_frame(
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
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
        protocol_version,
        message_id,
    )
}

fn build_inject_frame(
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
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
        protocol_version,
        message_id,
    )
}

fn build_bot_relay_frame(
    method: &str,
    deliver: bool,
    group_id: &str,
    bcs_session_id: Option<&str>,
    from_bot: &str,
    from_bot_name: &str,
    message: &str,
    group_context: &GroupContext,
    protocol_version: u32,
    message_id: Option<&str>,
) -> BcsFrame {
    let prefixed = format!("[from:{}]{}", from_bot_name, message);
    let text = if protocol_version >= 2 {
        format!("{}\n\n[消息内容]\n{}", group_context.format_header(), prefixed)
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

fn build_task_result_frame(
    group: Option<&Group>,
    group_id: &str,
    manager_session_id: &str,
    driver_bot: &str,
    target_bot: &str,
    target_bot_name: &str,
    response_text: &str,
    task_id: &str,
    run_id: &str,
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
    let params = serde_json::json!({
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

    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

fn task_result_recipient_role(group: Option<&Group>) -> &'static str {
    match group.map(|group| group.group_strategy) {
        Some(GroupStrategy::ManagerWorker) => "manager",
        _ => "driver",
    }
}

fn task_result_group_type(group: Option<&Group>) -> String {
    group
        .and_then(|group| group_type_wire(group.group_strategy))
        .unwrap_or_else(|| "task".to_string())
}

fn stamp_forward_hop(frame: &mut BcsFrame, hop_count: u32) {
    if let BcsFrame::Request(request) = frame {
        if let Some(params) = &mut request.params {
            params["_forward_hop"] = serde_json::json!(hop_count);
        }
    }
}

fn request_id(frame: &BcsFrame) -> Option<String> {
    match frame {
        BcsFrame::Request(request) => Some(request.id.clone()),
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

fn routing_mode_slug(mode: RoutingMode) -> &'static str {
    match mode {
        RoutingMode::Structured => "structured",
        RoutingMode::Mention => "mention",
        RoutingMode::Hybrid => "hybrid",
    }
}

fn log_route_digest(
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

fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id.filter(|value| !value.is_empty()).unwrap_or(group_id)
}

fn delivery_type_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

fn request_source_slug(source: &RequestSource) -> &'static str {
    match source {
        RequestSource::StructuredMetadata => "structured_metadata",
        RequestSource::LegacyMention => "legacy_mention",
        RequestSource::DefaultPolicy => "default_policy",
        RequestSource::SenderRoutes => "sender_routes",
    }
}

fn message_log_mode_for_request_source(source: &RequestSource) -> MessageLogMode {
    match source {
        RequestSource::StructuredMetadata => MessageLogMode::Structured,
        _ => MessageLogMode::FreeChat,
    }
}

fn message_log_mode_for_payload(payload: &Value) -> MessageLogMode {
    if payload.get("routing").is_some() {
        MessageLogMode::Structured
    } else {
        MessageLogMode::FreeChat
    }
}

fn message_log_status_for_bot_event(state: &ChatEventState) -> MessageLogStatus {
    match state {
        ChatEventState::Error | ChatEventState::Aborted => MessageLogStatus::Failed,
        ChatEventState::Final => MessageLogStatus::Responded,
        _ => MessageLogStatus::Responded,
    }
}

fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

fn log_incoming_bot_event(cmd: &BotEventCommand, task_id: Option<&str>) {
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

fn log_relay_deliver_result(
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

fn now_ms() -> u64 {
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

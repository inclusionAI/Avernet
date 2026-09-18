#[path = "bot_event/relay.rs"]
mod relay;
use relay::*;
#[path = "bot_event/task.rs"]
mod task;
use task::*;
mod task_terminal;
#[path = "bot_event/coordination.rs"]
mod coordination;
use coordination::*;
#[path = "bot_event/protocol.rs"]
mod protocol;
pub(crate) use protocol::{build_send_frame, stamp_forward_hop};
use protocol::*;
#[path = "bot_event/persistence.rs"]
mod persistence;
pub(crate) use persistence::{extract_message_text};
use persistence::*;

use bcs_domain::{
    CoordinationMode, CoordinationSurface, MessageAudience, MessageVisibilityDomain, SenderType,
};
use bcs_service_api::port::{CoordinationContext, CoordinationClaim, CoordinationResult, CoordinationStatus};
use bcs_protocol::{
    BcsFrame, CoordinationCall, DirectiveAction, EventFrame, GroupContext, RequestFrame,
    RequestSource, ResponseDirective, ResponseMode as WireResponseMode, TOOL_ASSIGN_TASK,
    TOOL_SEND_TASK_MESSAGE, TOOL_TASK_COMPLETE, build_recipient_group_context, build_session_key,
};
use bcs_service_api::application::channel::OutboundMessage;
use bcs_service_api::{
    ActorStatus, BotDeliveryCommand, BotDeliveryKind, BotDeliveryResult, BotDeliveryTarget,
    BotEventCommand, BotEventOutcome, BotTerminalEvent, BotTerminalState, ChannelOutboundEventKind,
    ChannelOutboundPurpose, ChannelRenderHint, ChatEventRouting, ChatEventState, ChatResponseMode,
    DefaultDelivery, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryResult, FrontendDeliveryTarget, Group, GroupKind, GroupStatus, GroupStrategy,
    MESSAGE_LOG_SCHEMA_VERSION, MessageDeliveryResult, MessageLogContent, MessageLogEventType,
    MessageLogMode, MessageLogStatus, MessageLogTargetSummary, ParticipantRole, ResponseMode,
    RouteParticipantOverlay, RoutingDecision, RoutingMode, RoutingTarget, RunFallbackDelivery,
    ServiceError, ServiceResult, SystemMessageEvent, TaskCompleteCommand, TaskDispatchCommand,
    TaskMessageCommand, backfill_bot_names, backfill_participant_names, message_log_json,
};
use serde_json::Value;
use tracing::{info, warn};

use crate::BcsMessageFlow;
use crate::MSG_LOG_TARGET;
use crate::group_flow::apply_overlay_to_decision;
use crate::message_tracker::BotEventRunInfo;
use crate::protocol_context::{group_context_delivery_type, group_context_input, group_type_wire};
use crate::task_store::{TaskEntry, TaskLedgerStatus, TaskStore};

const COORDINATION_PROCESSED_TTL_MS: u64 = 10 * 60 * 1000;

pub async fn handle_bot_event(
    flow: &BcsMessageFlow,
    cmd: BotEventCommand,
) -> ServiceResult<BotEventOutcome> {
    let mut cmd = cmd;
    let admission_timing = crate::reply_timing::Timer::new("event.lookup_and_lock");
    let managed = crate::queued_admission::find_managed_run(flow, &cmd).await?;
    let task_lock_id = flow.task_store.resolve_task_id(&cmd.run_id).await;
    // Serialize all events for a managed run, including two simultaneous
    // finals. Holding this guard spans the committed terminal and its
    // best-effort effects, not merely the first state lookup.
    let _event_guard = {
        let lock_id = task_lock_id.map(|id| format!("task:{id}"))
            .or_else(|| managed.as_ref().map(|row| row.delivery_id.clone()))
            .unwrap_or_else(|| format!("run:{}", crate::run_reply::chat_key(&cmd)));
        let lock = {
            let mut locks = flow.delivery_event_locks.lock().await;
            locks.retain(|_, lock| lock.strong_count() > 0);
            let lock = locks.get(&lock_id).and_then(std::sync::Weak::upgrade)
                .unwrap_or_else(|| std::sync::Arc::new(tokio::sync::Mutex::new(())));
            locks.insert(lock_id, std::sync::Arc::downgrade(&lock));
            lock
        };
        lock.lock_owned().await
    };
    let managed = if managed.is_some() { crate::queued_admission::find_managed_run(flow, &cmd).await? } else { None };
    if let Some(row) = &managed {
        use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
        if matches!(row.state.status, Status::Completed | Status::Failed | Status::Cancelled | Status::Expired | Status::RejectedCapacity) {
            if matches!((row.state.status, &cmd.state),
                    (Status::Completed, ChatEventState::Final) | (Status::Failed, ChatEventState::Error) | (Status::Cancelled, ChatEventState::Aborted))
                && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
                && crate::queued_task::intent(row)?.is_some_and(|task| task.leg == crate::queued_task::TaskLeg::Dispatch)
                && row.state.may_have_been_sent {
                cmd.bcs_session_id = Some(row.session_id.clone());
                if let Some(run) = &row.run_id { cmd.run_id = run.clone(); }
                return task_terminal::finish(flow, row, &cmd, true).await;
            }
            return Ok(BotEventOutcome { bot_deliveries: Vec::new(), frontend_deliveries: Vec::new(),
                unregistered_run_ids: Vec::new(), mentions: Vec::new(), delivered_count: 0, failed_count: 0, delivery_results: Vec::new() });
        }
        if !row.state.may_have_been_sent {
            return Err(ServiceError::InvalidOperation { message: "Bot event arrived for an unsent delivery".into(), request_id: None });
        }
        if cmd.state == ChatEventState::Delta {
            if let (Some(service), Some(request_id)) = (&flow.managed_deliveries, &row.request_id) {
                service.accept_run(request_id, &cmd.bot_id, None, chrono::Utc::now().timestamp_millis()).await
                    .map_err(|_| ServiceError::InternalError("managed receipt persistence failed".into()))?;
            }
        }
        cmd.bcs_session_id = Some(row.session_id.clone());
        if let Some(run_id) = &row.run_id { cmd.run_id = run_id.clone(); }
        crate::queued_task::restore(flow, row).await?;
    }
    let managed_terminal = managed.is_some() && is_terminal_state(&cmd.state) && matches!(cmd.event_type.as_str(), "chat" | "chat.event");
    drop(admission_timing);
    let task_id_for_event = flow.task_store.resolve_task_id(&cmd.run_id).await;
    log_incoming_bot_event(&cmd, task_id_for_event.as_deref());

    let error_group = if cmd.state == ChatEventState::Error
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
        && !cmd.group_id.is_empty()
    {
        flow.group.try_get(&cmd.group_id).await?
    } else {
        None
    };
    if managed_terminal && cmd.state == ChatEventState::Error && error_group.is_none() {
        return Err(ServiceError::InvalidOperation {
            message: "cannot persist terminal error without its Group".into(),
            request_id: None,
        });
    }
    let history_error = error_group
        .is_some_and(|group| group.group_strategy != GroupStrategy::StateMachine);
    if history_error {
        let text = error_display_text(&cmd.event_payload);
        inject_synthesized_message(&mut cmd.event_payload, &text);
        cmd.event_payload["errorMessage"] = Value::String(text);
        if task_id_for_event.is_none() {
            if managed_terminal {
                let partial = flow
                    .message_tracker
                    .peek_chat_buf(&crate::run_reply::chat_key(&cmd))
                    .await
                    .unwrap_or_default();
                crate::queued_admission::settle_without_relay(flow, &cmd, &partial, None)
                    .await?;
            } else {
                flush_chat_segment(flow, &cmd, None).await?;
                persist_chat_error(flow, &cmd).await?;
            }
            let frontend_deliveries = publish_incoming_event(flow, &cmd, None).await?;
            try_channel_outbound(flow, &cmd).await;
            flow.complete_send_context(&cmd.run_id).await?;
            flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
            flow.message_tracker.cleanup_run(&cmd.run_id).await;
            flow.message_tracker
                .cleanup_run(&crate::run_reply::chat_key(&cmd))
                .await;
            notify_terminal_observer(flow, &cmd).await;
            return Ok(BotEventOutcome {
                bot_deliveries: Vec::new(),
                frontend_deliveries,
                unregistered_run_ids: final_run_ids(&cmd),
                mentions: Vec::new(),
                delivered_count: 0,
                failed_count: 0,
                delivery_results: Vec::new(),
            });
        }
    }

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
        match cmd
            .event_payload
            .get("stream")
            .and_then(|value| value.as_str())
        {
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
        match extract_delta_text(&cmd.event_payload) {
            Some(delta) if !delta.is_empty() => {
                flow.message_tracker
                    .append_chat_delta(&crate::run_reply::chat_key(&cmd), delta)
                    .await;
                // Inject the segment-accumulated text as `message` so every
                // consumer, including direct A2A runs, sees the same shape.
                if let Some(acc) = flow.message_tracker.peek_chat_buf(&crate::run_reply::chat_key(&cmd)).await {
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

    // Queue reconstruction is a separate payload. Never rewrite the provider's
    // final or replace its frontend/channel projection with queue display text.
    let queue_reply = if cmd.state == ChatEventState::Final && task_id_for_event.is_none()
        && !cmd.group_id.is_empty() && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
        && managed_terminal {
        let text = extract_message_text(&cmd.event_payload);
        Some(crate::run_reply::prepare(flow, &cmd, &text, managed_terminal).await?)
    } else { None };
    // Preserve the existing empty-final fallback for task/direct A2A runs;
    // group finals use the independent queue reconstruction above.
    if (task_id_for_event.is_some() || cmd.group_id.is_empty())
        && cmd.state == ChatEventState::Final
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
        && extract_message_text(&cmd.event_payload).is_empty()
    {
        if let Some(accumulated) = flow.message_tracker.peek_chat_buf(&crate::run_reply::chat_key(&cmd)).await {
            if !accumulated.is_empty() {
                inject_synthesized_message(&mut cmd.event_payload, &accumulated);
            }
        }
    }
    let mut frontend_deliveries = if managed_terminal || history_error {
        Vec::new()
    } else {
        publish_incoming_event(flow, &cmd, task_id_for_event.as_deref()).await?
    };
    if !managed_terminal && !history_error {
        try_channel_outbound(flow, &cmd).await;
    }
    let mut bot_deliveries = Vec::new();

    // Persist tool call events (identified by payload.stream == "tool", distinguished by payload.data.phase)
    if let Some((data, phase)) = tool_event_phase(&cmd.event_payload) {
        match phase {
            "start" => cache_tool_start(flow, &cmd, data).await,
            "result" => {
                persist_tool_result(flow, &cmd, data).await?;
                let coordination = maybe_handle_coordination_echo(flow, &cmd, data).await;
                if let Err(error) = &coordination {
                    warn!(bot_id = %cmd.bot_id, run_id = %cmd.run_id, error = %error,
                        "Coordination event could not be confirmed");
                    if let Some(system_message) = &flow.system_message {
                        if let Some(group) = flow.group.get(&cmd.group_id).await {
                            let event = SystemMessageEvent::GenericNotification {
                                group_id: cmd.group_id.clone(),
                                message: "协同操作未确认完成，请检查任务状态后再处理；系统不会自动重复分发。".into(),
                                receivers: group.participants.iter()
                                    .filter(|p| p.bot_uuid == cmd.bot_id).cloned().collect(),
                            };
                            if let Err(notice_error) = system_message.notify(&cmd.group_id, event,
                                cmd.bcs_session_id.as_deref().unwrap_or(&cmd.group_id), &group.participants).await {
                                warn!(error = %notice_error, "Coordination failure notice could not be delivered");
                            }
                        }
                    }
                }
                if let Some(coordination) = coordination?
                {
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
        flush_chat_segment(flow, &cmd, None).await?;
    }
    if is_terminal_state(&cmd.state) && !history_error {
        if !managed_terminal && matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
            flow.complete_send_context(&cmd.run_id).await?;
        }
        if !managed_terminal { flow.frontend_delivery.unregister_run(&cmd.run_id).await?; }
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
        if managed_terminal {
            let text = flow.message_tracker.peek_chat_buf(&crate::run_reply::chat_key(&cmd)).await.unwrap_or_default();
            crate::queued_admission::settle_without_relay(flow, &cmd, &text, None).await?;
            frontend_deliveries.extend(publish_incoming_event(flow, &cmd, task_id_for_event.as_deref()).await?);
            try_channel_outbound(flow, &cmd).await;
            flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
            flow.complete_send_context(&cmd.run_id).await?;
        } else {
            flush_chat_segment(flow, &cmd, None).await?;
        }
        flow.message_tracker.cleanup_run(&cmd.run_id).await;
        flow.message_tracker.cleanup_run(&crate::run_reply::chat_key(&cmd)).await;
    }

    if let Some(task_id) = task_id_for_event {
        bot_deliveries.extend(handle_task_bot_event(flow, &cmd, &task_id).await?);
        if managed_terminal {
            if managed.as_ref().is_some_and(|row| row.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::Task) {
                let row = crate::queued_admission::find_managed_run(flow, &cmd).await?
                    .ok_or_else(|| crate::queued_task::error("committed task delivery missing"))?;
                return task_terminal::finish(flow, &row, &cmd, false).await;
            }
            frontend_deliveries.extend(publish_incoming_event(flow, &cmd, Some(&task_id)).await?);
            try_channel_outbound(flow, &cmd).await;
            flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
            flow.complete_send_context(&cmd.run_id).await?;
            flow.message_tracker.cleanup_run(&cmd.run_id).await;
            flow.message_tracker.cleanup_run(&crate::run_reply::chat_key(&cmd)).await;
        }
        if history_error
            && flow
                .task_store
                .get(&task_id)
                .await
                .is_some_and(|entry| entry.status != TaskLedgerStatus::Dispatched)
        {
            frontend_deliveries.extend(
                publish_incoming_event(flow, &cmd, Some(&task_id)).await?,
            );
            try_channel_outbound(flow, &cmd).await;
            flow.complete_send_context(&cmd.run_id).await?;
            flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
        }
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
        let relay = {
            let _timing = crate::reply_timing::Timer::new("final.relay_including_terminal");
            relay_final_chat_event(flow, &cmd, queue_reply.as_ref()).await?
        };
        let _timing = crate::reply_timing::Timer::new("final.after_relay");
        if managed_terminal {
            crate::queued_admission::settle_without_relay(flow, &cmd, &extract_message_text(&cmd.event_payload), queue_reply.as_ref()).await?;
            frontend_deliveries.extend(publish_incoming_event(flow, &cmd, task_id_for_event.as_deref()).await?);
            try_channel_outbound(flow, &cmd).await;
            flow.frontend_delivery.unregister_run(&cmd.run_id).await?;
            flow.complete_send_context(&cmd.run_id).await?;
        }
        let relay_mentions = relay.mentions;
        let relay_delivery_results = relay.delivery_results;
        bot_deliveries.extend(relay.bot_deliveries);
        frontend_deliveries.extend(relay.frontend_deliveries);
        flow.message_tracker.cleanup_run(&cmd.run_id).await;
        flow.message_tracker.cleanup_run(&crate::run_reply::chat_key(&cmd)).await;
        notify_terminal_observer(flow, &cmd).await;
        return Ok(BotEventOutcome {
            bot_deliveries,
            frontend_deliveries,
            unregistered_run_ids: final_run_ids(&cmd),
            mentions: relay_mentions,
            delivered_count: relay_delivery_results
                .iter()
                .filter(|result| result.success)
                .count(),
            failed_count: relay_delivery_results
                .iter()
                .filter(|result| !result.success)
                .count(),
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

/// Normalize only user-visible text, never serialize arbitrary provider objects.
pub(crate) fn error_display_text(event: &Value) -> String {
    let message = extract_message_text(event);
    if !message.trim().is_empty() {
        return message;
    }
    for field in ["errorMessage", "error_message"] {
        if let Some(text) = event
            .get(field)
            .and_then(Value::as_str)
            .filter(|text| !text.trim().is_empty())
        {
            return text.to_string();
        }
    }
    "本次回复失败，请稍后重试。".to_string()
}

#[cfg(test)]
mod error_projection_tests {
    use super::error_display_text;
    use serde_json::json;

    #[test]
    fn normalize_display_fields_without_serializing_transport_objects() {
        assert_eq!(
            error_display_text(&json!({"message":{"content":[{"type":"text","text":" human text "}]},"errorMessage":"fallback"})),
            " human text "
        );
        assert_eq!(
            error_display_text(&json!({"message":{"content":"  "},"errorMessage":"fallback"})),
            "fallback"
        );
        assert_eq!(error_display_text(&json!({"error_message":"legacy"})), "legacy");
        assert_eq!(
            error_display_text(&json!({"errorMessage":{"stack":"private"},"request":{"token":"private"}})),
            "本次回复失败，请稍后重试。"
        );
    }
}

async fn persist_chat_error(flow: &BcsMessageFlow, cmd: &BotEventCommand) -> ServiceResult<()> {
    let owner = crate::group_flow::manager_worker_self_owner(
        flow,
        &cmd.group_id,
        cmd.bcs_session_id.as_deref(),
        &cmd.bot_id,
    )
    .await;
    crate::group_flow::try_persist_group_message(
        flow,
        &cmd.group_id,
        cmd.bcs_session_id.as_deref(),
        &cmd.bot_id,
        SenderType::Bot,
        bcs_domain::CHAT_ERROR_MESSAGE_TYPE,
        Value::String(error_display_text(&cmd.event_payload)),
        Some(&format!("chat-error:{}", cmd.run_id)),
        owner,
        &cmd.run_id,
    )
    .await?;
    Ok(())
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

    let (sender_role, sender_label) = resolve_channel_sender(flow, cmd).await;
    let source_im_message_id = flow
        .message_tracker
        .channel_source_message_id(&cmd.run_id)
        .await;
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
            purpose: ChannelOutboundPurpose::Conversation,
            text: (!text.is_empty()).then_some(text),
            raw_payload,
            render_hint,
            source_im_message_id,
            source_is_channel: false,
        })
        .await
    {
        warn!(run_id = %cmd.run_id, error = %error, "channel outbound hook failed");
    }
}

async fn resolve_channel_sender(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
) -> (ParticipantRole, String) {
    if let Some(info) = flow
        .message_tracker
        .channel_sender_info(&cmd.run_id)
        .await
    {
        return info;
    }

    let run_info = resolve_bot_event_run_info(flow, cmd).await;
    let info = match run_info.participant {
        Some(mut participant) => {
            let sender_role = participant.role;
            let label = match participant_display_name(&participant) {
                Some(label) => label,
                None => {
                    backfill_participant_names(
                        flow.registry.as_ref(),
                        std::slice::from_mut(&mut participant),
                    )
                    .await;
                    participant_display_name(&participant).unwrap_or_else(|| cmd.bot_id.clone())
                }
            };
            (sender_role, label)
        }
        None => (ParticipantRole::Observer, cmd.bot_id.clone()),
    };
    flow.message_tracker
        .cache_channel_sender_info(&cmd.run_id, info.clone())
        .await;
    info
}

fn participant_display_name(participant: &bcs_service_api::Participant) -> Option<String> {
    participant
        .bot_name
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty() && *name != participant.bot_uuid)
        .map(str::to_string)
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

async fn persist_streaming_chat(flow: &BcsMessageFlow, cmd: &BotEventCommand, msg_text: String) {
    flow.message_tracker
        .buffer_chat_text(&crate::run_reply::chat_key(&cmd), msg_text)
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
) -> ServiceResult<()> {
    // Do not consume the buffer before durable persistence succeeds.
    let buffered = flow.message_tracker.peek_chat_buf(&crate::run_reply::chat_key(&cmd)).await;
    let text = match (final_text, buffered) {
        (Some(t), _) => t,             // final frame wins
        (None, Some(t)) => t,          // flush buffered delta text
        (None, None) => return Ok(()), // nothing streamed in this segment
    };
    if text.is_empty() || cmd.group_id.is_empty() {
        return Ok(());
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
    .await?;
    flow.message_tracker.take_chat_buf(&crate::run_reply::chat_key(&cmd)).await;
    Ok(())
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
async fn persist_final_chat(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    text: String,
) -> ServiceResult<()> {
    if flow.message_tracker.is_chat_delta_mode(&crate::run_reply::chat_key(&cmd)).await {
        flush_chat_segment(flow, cmd, None).await?;
    } else {
        flush_chat_segment(flow, cmd, Some(text)).await?;
    }
    Ok(())
}

async fn persist_task_final_chat(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    response_text: String,
) -> ServiceResult<()> {
    persist_final_chat(flow, cmd, response_text).await
}

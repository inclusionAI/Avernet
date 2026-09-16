use super::*;

pub(super) async fn publish_incoming_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    task_id: Option<&str>,
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
    let (visibility_domain, audience) = incoming_event_visibility(flow, cmd, task_id).await;
    let result = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: frontend_target,
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(run_fallback),
            exclude_conn_id: None,
            visibility_domain,
            audience,
        })
        .await?;
    Ok(vec![result])
}

pub(super) async fn incoming_event_visibility(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    task_id: Option<&str>,
) -> (MessageVisibilityDomain, Option<MessageAudience>) {
    let run_info = resolve_bot_event_run_info(flow, cmd).await;
    let visibility_domain = run_info.visibility_domain;
    if visibility_domain == MessageVisibilityDomain::Chat {
        return (visibility_domain, None);
    }

    if let Some(task_id) = task_id {
        let audience = match flow.task_store.get(task_id).await {
            Some(task)
                if task.group_id == cmd.group_id
                    && task.target_bot == cmd.bot_id
                    && task.session_id.as_deref() == cmd.bcs_session_id.as_deref() =>
            {
                MessageAudience::directed([task.driver_bot, task.target_bot])
                    .unwrap_or(MessageAudience::FullOnly)
            }
            _ => MessageAudience::FullOnly,
        };
        return (visibility_domain, Some(audience));
    }

    let sender_role = run_info
        .participant
        .filter(|participant| participant.is_bot())
        .map(|participant| participant.role);
    // State-machine node events are consumed by CollaborationRuntime before
    // they reach this generic group-message path. A manager chat event that
    // reaches here is therefore a public group announcement, while non-chat
    // execution events remain full-only.
    let audience = if sender_role == Some(ParticipantRole::Manager)
        && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
    {
        MessageAudience::Public
    } else {
        MessageAudience::FullOnly
    };
    (visibility_domain, Some(audience))
}

pub(super) async fn resolve_bot_event_run_info(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
) -> BotEventRunInfo {
    if let Some(info) = flow
        .message_tracker
        .bot_event_run_info(&cmd.run_id, &cmd.group_id, &cmd.bot_id)
        .await
    {
        return info;
    }

    let group = flow.group.try_get(&cmd.group_id).await.ok().flatten();
    let visibility_domain = group
        .as_ref()
        .map(|group| match group.group_strategy {
            GroupStrategy::Chat => MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
        })
        .unwrap_or(MessageVisibilityDomain::ManagerWorker);
    let mut participant = group
        .as_ref()
        .and_then(|group| group.get_participant(&cmd.bot_id))
        .cloned();
    if let (Some(session_id), Some(session_management)) =
        (cmd.bcs_session_id.as_deref(), flow.session_management.as_ref())
    {
        if let Ok(Some(session)) = session_management.get(session_id).await {
            if session.group_id == cmd.group_id {
                participant = session
                    .participants
                    .into_iter()
                    .find(|candidate| candidate.bot_uuid == cmd.bot_id);
            }
        }
    }
    let info = BotEventRunInfo {
        group_id: cmd.group_id.clone(),
        bot_id: cmd.bot_id.clone(),
        visibility_domain,
        participant,
    };
    flow.message_tracker
        .cache_bot_event_run_info(&cmd.run_id, info.clone())
        .await;
    info
}

pub(super) fn workbench_event_name<'a>(event_type: &'a str, state: &ChatEventState) -> &'a str {
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

pub(super) async fn publish_system_event(
    flow: &BcsMessageFlow,
    group_id: &str,
    bot_id: &str,
    text: &str,
) -> ServiceResult<FrontendDeliveryResult> {
    let visibility_domain = crate::group_flow::frontend_domain_for_group(flow, group_id).await;
    let audience = (visibility_domain != MessageVisibilityDomain::Chat)
        .then_some(MessageAudience::FullOnly);
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
            visibility_domain,
            audience,
        })
        .await
}

pub(super) async fn build_route_overlay(flow: &BcsMessageFlow, group: &Group) -> Vec<RouteParticipantOverlay> {
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

pub(super) async fn sender_display_name(flow: &BcsMessageFlow, bot_id: &str) -> String {
    flow.registry
        .get(bot_id)
        .await
        .and_then(|bot| bot.capabilities.name.clone())
        .unwrap_or_else(|| bot_id.to_string())
}

pub(super) async fn from_bot_owner(flow: &BcsMessageFlow, bot_id: &str) -> Option<String> {
    if bot_id.starts_with("human_") {
        None
    } else {
        flow.registry
            .get(bot_id)
            .await
            .and_then(|bot| bot.created_by)
    }
}

pub(super) fn final_run_ids(cmd: &BotEventCommand) -> Vec<String> {
    if is_terminal_state(&cmd.state) {
        vec![cmd.run_id.clone()]
    } else {
        Vec::new()
    }
}

pub(super) fn is_terminal_state(state: &ChatEventState) -> bool {
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
pub(super) fn extract_delta_text(event: &Value) -> Option<&str> {
    event.get("delta_text").and_then(|value| value.as_str())
}

pub(super) async fn normalize_thinking_delta(flow: &BcsMessageFlow, cmd: &mut BotEventCommand) {
    if cmd
        .event_payload
        .get("stream")
        .and_then(|value| value.as_str())
        != Some("thinking")
    {
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

pub(super) fn extract_thinking_delta(event: &Value) -> Option<&str> {
    event
        .get("data")
        .and_then(|data| data.get("delta"))
        .and_then(|value| value.as_str())
}

pub(super) fn inject_thinking_text(event: &mut Value, accumulated_text: &str) {
    if let Some(data) = event
        .get_mut("data")
        .and_then(|value| value.as_object_mut())
    {
        data.insert(
            "text".to_string(),
            Value::String(accumulated_text.to_string()),
        );
    }
}

/// Overwrite the frame's `message` with a synthesized assistant message whose
/// `content` is the segment-accumulated text. The SSE (raw engine) delta frame
/// carries only `delta_text`; the frontend SDK renders `message.content[].text`
/// (segment-cumulative), so we build that shape here from BCS's own accumulator.
/// Matches the wire `MessageContent` layout: `{role, content:[{type,text}], timestamp}`.
pub(super) fn inject_synthesized_message(event: &mut Value, accumulated_text: &str) {
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
pub(super) fn tool_event_phase(payload: &Value) -> Option<(&Value, &str)> {
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
pub(super) fn is_chat_segment_boundary_stream(payload: &Value) -> bool {
    matches!(
        payload.get("stream").and_then(|value| value.as_str()),
        Some("thinking") | Some("approval")
    )
}

pub(super) async fn cache_tool_start(flow: &BcsMessageFlow, cmd: &BotEventCommand, data: &Value) {
    if cmd.group_id.is_empty() {
        return;
    }
    let tool_call_id = data
        .get("toolCallId")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let name = data
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let args = data.get("args").cloned().unwrap_or(Value::Null);
    let session_id = cmd.bcs_session_id.clone().unwrap_or_default();

    flow.message_tracker
        .cache_tool_call_start(
            tool_call_id.to_string(),
            crate::message_tracker::ToolCallStartInfo {
                run_id: cmd.run_id.clone(),
                session_id,
                name,
                args,
                created_at_ms: now_ms(),
            },
        )
        .await;
}

pub(super) async fn persist_tool_result(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    data: &Value,
) -> ServiceResult<()> {
    if cmd.group_id.is_empty() {
        return Ok(());
    }

    // A tool_call ends the run's current chat text segment. Flush any buffered
    // chat deltas as ONE row FIRST, so the persisted order is chat → tool_call
    // → (next) chat, matching the BCN plugin path.
    flush_chat_segment(flow, cmd, None).await?;

    let tool_call_id = data
        .get("toolCallId")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let is_error = data
        .get("isError")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let result = data.get("result").cloned().unwrap_or(Value::Null);

    let start_info = flow.message_tracker.get_tool_call_start(tool_call_id).await;
    let (args, run_id, session_id, start_name) = match start_info {
        Some(ref info) => (
            info.args.clone(),
            info.run_id.clone(),
            info.session_id.clone(),
            info.name.clone(),
        ),
        None => (
            Value::Null,
            cmd.run_id.clone(),
            cmd.bcs_session_id.clone().unwrap_or_default(),
            String::new(),
        ),
    };
    let name = data
        .get("name")
        .and_then(|v| v.as_str())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(start_name.as_str())
        .to_string();

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
        if session_id.is_empty() {
            None
        } else {
            Some(&session_id)
        },
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
    .await?;
    flow.message_tracker
        .remove_tool_call_start(tool_call_id)
        .await;
    Ok(())
}

use super::*;

pub(super) fn provider_request_from_frame(
    target: &BotDeliveryTarget,
    frame: &BcsFrame,
    timeout_ms: u64,
) -> ServiceResult<ProviderWebhookRequest> {
    let BotDeliveryTarget::HttpProvider {
        provider_id,
        provider_bot_ref,
        ..
    } = target
    else {
        return Err(ServiceError::InvalidOperation {
            message: "provider_request_from_frame requires http provider target".to_string(),
            request_id: None,
        });
    };
    let BcsFrame::Request(request) = frame else {
        return Err(ServiceError::InvalidOperation {
            message: "provider delivery requires request frame".to_string(),
            request_id: None,
        });
    };
    let params = request.params.clone().unwrap_or(Value::Null);
    let bcs_group_id = params
        .get("bcs_group_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let session_id = provider_session_id(&params, &bcs_group_id);

    let callback_timeout_ms = if request.method == "chat.send" {
        params
            .get("timeout_ms")
            .and_then(Value::as_u64)
            .unwrap_or(timeout_ms)
    } else {
        NON_CHAT_PROVIDER_REQUEST_TIMEOUT_MS
    };
    let attachments = params
        .get("attachments")
        .cloned()
        .map(serde_json::from_value::<Vec<Attachment>>)
        .transpose()
        .map_err(|error| ServiceError::InvalidOperation {
            message: format!("provider attachment payload is invalid: {error}"),
            request_id: Some(request.id.clone()),
        })?
        .unwrap_or_default();

    Ok(ProviderWebhookRequest {
        frame_type: "req".to_string(),
        id: request.id.clone(),
        method: request.method.clone(),
        params: None,
        session_id,
        bcn_group_id: bcs_group_id,
        to_bot: ProviderWebhookBotRef {
            provider_id: provider_id.clone(),
            provider_bot_ref: provider_bot_ref.clone(),
            tags: provider_tags_from_params(&params),
        },
        from: provider_sender_from_params(&params),
        message: provider_message_for_method(&request.method, &params),
        attachments,
        before: params.get("before").and_then(Value::as_u64),
        after: params.get("after").and_then(Value::as_u64),
        limit: params.get("limit").and_then(Value::as_u64),
        timeout_ms: callback_timeout_ms,
        extensions: provider_extensions_from_params(&params),
    })
}

pub(super) fn provider_extensions_from_params(params: &Value) -> Option<Value> {
    let extensions = params.get("extensions")?;
    match extensions {
        Value::Object(map) if !map.is_empty() => Some(extensions.clone()),
        _ => None,
    }
}

pub(super) fn provider_tags_from_params(params: &Value) -> Vec<String> {
    params
        .get("tags")
        .and_then(Value::as_array)
        .map(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|tag| !tag.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

pub(super) fn provider_sender_from_params(params: &Value) -> Option<ProviderWebhookSender> {
    let channel = params.get("channel")?;
    let actor_id = channel
        .get("actor_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|actor_id| !actor_id.is_empty());
    let name = channel
        .get("actor_name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .or(actor_id)?;
    Some(ProviderWebhookSender {
        kind: "bot".to_string(),
        name: name.to_string(),
        actor_id: actor_id.map(str::to_string),
    })
}

pub(super) fn provider_message_from_params(params: &Value) -> Option<Value> {
    let mut message = params.get("message")?.clone();
    let Some(sender_json) = provider_sender_json(params) else {
        return Some(message);
    };
    prepend_provider_sender(&mut message, &sender_json);
    Some(message)
}

pub(super) fn provider_message_for_method(method: &str, params: &Value) -> Option<Value> {
    if !matches!(method, "chat.send" | "chat.inject") {
        return params.get("message").cloned();
    }
    provider_message_from_params(params)
}

pub(super) fn provider_sender_json(params: &Value) -> Option<String> {
    let channel = params.get("channel")?;
    if channel.get("identity_forwarding").and_then(Value::as_bool) != Some(true) {
        return None;
    }
    let id = channel
        .get("user_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())?;
    let name = channel
        .get("actor_name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())?;
    // COSEC: serialize untrusted identity fields as JSON rather than interpolating
    // them into a JSON-shaped string.
    Some(serde_json::json!({ "sender": { "id": id, "name": name } }).to_string())
}

pub(super) fn prepend_provider_sender(message: &mut Value, sender_json: &str) {
    if let Some(text) = message.as_str() {
        *message = Value::String(format!("{sender_json}\n\n{text}"));
        return;
    }
    let Some(object) = message.as_object_mut() else {
        return;
    };
    if let Some(text) = object.get("text").and_then(Value::as_str) {
        let combined = format!("{sender_json}\n\n{text}");
        object.insert("text".to_string(), Value::String(combined));
        return;
    }
    if let Some(content) = object.get_mut("content") {
        if let Some(text) = content.as_str() {
            *content = Value::String(format!("{sender_json}\n\n{text}"));
            return;
        }
        if let Some(blocks) = content.as_array_mut() {
            if let Some(text_block) = blocks.iter_mut().find(|block| {
                block.get("type").and_then(Value::as_str) == Some("text")
                    && block.get("text").and_then(Value::as_str).is_some()
            }) {
                if let Some(text) = text_block.get("text").and_then(Value::as_str) {
                    let combined = format!("{sender_json}\n\n{text}");
                    text_block["text"] = Value::String(combined);
                }
                return;
            }
            blocks.insert(
                0,
                serde_json::json!({ "type": "text", "text": sender_json }),
            );
        }
    }
}

pub(super) fn provider_session_id(params: &Value, bcs_group_id: &str) -> String {
    if let Some(session_id) = params.get("bcs_session_id").and_then(Value::as_str) {
        return session_id.to_string();
    }
    if let Some(session_id) = params.get("session_id").and_then(Value::as_str) {
        return session_id.to_string();
    }
    if let Some(session_key) = params.get("session_key").and_then(Value::as_str) {
        if !session_key.starts_with("group:") {
            return session_key.to_string();
        }
    }
    bcs_group_id.to_string()
}

/// Read the per-variant seq from a parsed `StreamEvent` (#4). ping / unknown
/// carry no seq and must never reach the dedupe counter anyway.
pub(super) fn stream_event_seq(event: &StreamEvent) -> Option<u64> {
    match event {
        StreamEvent::Agent(agent) => agent.seq,
        StreamEvent::Chat(chat) => chat.seq,
        StreamEvent::Interaction(interaction) => interaction.seq,
        _ => None,
    }
}

/// The engine-stamped `ts` (ms) of a parsed frame, for SSE-detail lag tracing.
pub(super) fn stream_event_ts(event: &StreamEvent) -> Option<u64> {
    match event {
        StreamEvent::Agent(agent) => agent.ts,
        StreamEvent::Chat(chat) => chat.ts,
        StreamEvent::Interaction(interaction) => interaction.ts,
        StreamEvent::Ping { ts } => *ts,
        _ => None,
    }
}

pub(super) fn ingest_kind_state_slug(kind: &IngestKind) -> &'static str {
    match kind {
        IngestKind::Pipeline { state, .. } | IngestKind::Terminal { state, .. } => {
            chat_event_state_slug(state)
        }
        IngestKind::CloseUnsupported => "close_unsupported",
        IngestKind::Interaction => "interaction",
        IngestKind::Drop => "drop",
    }
}

pub(super) fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Error => "error",
        ChatEventState::Aborted => "aborted",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

/// Build the downstream `event_payload` by FILLING the existing protocol structs
/// (#1) so the wire names match the WS plugin path byte-for-byte. `run_id` and
/// `bcs_group_id` come from the run context (BCN ids), NOT the engine frame.
pub(super) fn build_event_payload(event: &StreamEvent, run_id: &str, group_id: &str, recv_ms: u64) -> Value {
    match event {
        StreamEvent::Agent(agent) => {
            if let bcs_protocol::stream::AgentData::Error { raw } = &agent.data {
                let error_message = raw
                    .get("errorMessage")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                let mut message = raw.get("message").cloned().and_then(|mut raw_message| {
                    if let Some(object) = raw_message.as_object_mut() {
                        object.entry("timestamp").or_insert_with(|| {
                            Value::from(agent.ts.unwrap_or(recv_ms))
                        });
                    }
                    match serde_json::from_value(raw_message) {
                        Ok(parsed) => Some(parsed),
                        Err(error) => {
                            warn!(
                                run_id,
                                %error,
                                "agent error message did not match MessageContent; body omitted"
                            );
                            None
                        }
                    }
                });
                if !message_has_text(&message) {
                    if let Some(text) = error_message
                        .as_deref()
                        .map(str::trim)
                        .filter(|text| !text.is_empty())
                    {
                        message = Some(assistant_text_message(text));
                    }
                }
                return serde_json::to_value(ChatEventPayload {
                    run_id: run_id.to_string(),
                    bcs_group_id: group_id.to_string(),
                    state: WireChatState::Error,
                    message,
                    delta_text: None,
                    usage: None,
                    stop_reason: None,
                    error_message,
                    error_kind: raw
                        .get("errorKind")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    error_code: raw
                        .get("errorCode")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    tool_call_id: None,
                    tool_name: None,
                    args: None,
                    result: None,
                    is_error: None,
                    success: None,
                    routing: None,
                })
                .unwrap_or(Value::Null);
            }
            let stream = match &agent.data {
                bcs_protocol::stream::AgentData::Tool(_) => AgentStream::Tool,
                bcs_protocol::stream::AgentData::Thinking(_) => AgentStream::Thinking,
                bcs_protocol::stream::AgentData::Lifecycle(_) => AgentStream::Lifecycle,
                // Phase has no AgentStream counterpart; fall back to Assistant
                // (the closest "model output" stream) and WARN so the choice is
                // visible. Approval is handled upstream as CloseUnsupported.
                bcs_protocol::stream::AgentData::Phase(_) => {
                    warn!(
                        run_id,
                        "agent phase stream has no AgentStream variant; using assistant"
                    );
                    AgentStream::Assistant
                }
                other => {
                    warn!(
                        run_id,
                        ?other,
                        "unexpected agent data in payload build; using assistant"
                    );
                    AgentStream::Assistant
                }
            };
            // Preserve the raw frame (including provider extensions), then
            // overlay typed tool fields so canonical aliases such as
            // toolName/arguments reach message flow as name/args.
            let mut data = agent
                .raw
                .get("data")
                .cloned()
                .unwrap_or_else(|| agent.raw.clone());
            if let bcs_protocol::stream::AgentData::Tool(tool) = &agent.data {
                if let (Some(raw), Ok(Value::Object(normalized))) =
                    (data.as_object_mut(), serde_json::to_value(tool))
                {
                    raw.extend(normalized);
                }
            }
            let payload = AgentEventPayload {
                run_id: run_id.to_string(),
                bcs_group_id: group_id.to_string(),
                stream,
                ts: agent.ts.unwrap_or(0),
                data,
            };
            let mut payload = serde_json::to_value(payload).unwrap_or(Value::Null);
            if matches!(
                &agent.data,
                bcs_protocol::stream::AgentData::Tool(tool)
                    if matches!(tool.phase, bcs_protocol::stream::ToolPhase::Result)
            ) {
                payload[TASK_INTENT_ELIGIBLE_KEY] = Value::Bool(true);
            }
            payload
        }
        StreamEvent::Chat(chat) => {
            let state = match chat.state {
                ChatState::Delta => WireChatState::Delta,
                ChatState::Final => WireChatState::Final,
                ChatState::Aborted => WireChatState::Aborted,
                ChatState::Error => WireChatState::Error,
            };
            // message is strongly typed (Option<MessageContent>). If the engine
            // frame's message shape doesn't deserialize, WARN rather than
            // silently dropping the body (#1 risk note).
            let mut message = match chat.message.clone() {
                Some(mut raw_message) => {
                    // SSE permits an omitted message timestamp. Normalize a
                    // copy at this boundary, preserving supplied values and
                    // validation of malformed messages in the shared WS type.
                    if let Some(object) = raw_message.as_object_mut() {
                        object.entry("timestamp").or_insert_with(|| {
                            Value::from(stream_event_ts(event).unwrap_or(recv_ms))
                        });
                    }
                    match serde_json::from_value(raw_message) {
                        Ok(parsed) => Some(parsed),
                        Err(error) => {
                            warn!(
                                run_id,
                                %error,
                                "chat message did not match MessageContent; body omitted"
                            );
                            None
                        }
                    }
                }
                None => chat.content.as_deref().map(|content| MessageContent {
                    role: "assistant".to_string(),
                    content: vec![ContentBlock::text(content)],
                    timestamp: chat.ts.unwrap_or(recv_ms),
                }),
            };
            if matches!(chat.state, ChatState::Error) && !message_has_text(&message) {
                if let Some(error_message) = chat
                    .error_message
                    .as_deref()
                    .map(str::trim)
                    .filter(|text| !text.is_empty())
                {
                    message = Some(assistant_text_message(error_message));
                }
            }
            let payload = ChatEventPayload {
                run_id: run_id.to_string(),
                bcs_group_id: group_id.to_string(),
                state,
                message,
                // Forward the frame's incremental delta so BCS can accumulate
                // segments itself instead of re-deriving from cumulative message.
                delta_text: chat.delta_text.clone().or_else(|| {
                    matches!(chat.state, ChatState::Delta)
                        .then(|| chat.content.clone())
                        .flatten()
                }),
                usage: None,
                stop_reason: chat.stop_reason.clone(),
                error_message: chat.error_message.clone(),
                error_kind: chat.error_kind.clone(),
                error_code: chat.error_code.clone(),
                tool_call_id: None,
                tool_name: None,
                args: None,
                result: None,
                is_error: None,
                success: None,
                routing: None,
            };
            serde_json::to_value(payload).unwrap_or(Value::Null)
        }
        _ => Value::Null,
    }
}

pub(super) fn message_has_text(message: &Option<MessageContent>) -> bool {
    message.as_ref().is_some_and(|message| {
        message.content.iter().any(|block| {
            block
                .text
                .as_deref()
                .is_some_and(|text| !text.trim().is_empty())
        })
    })
}

pub(super) fn assistant_text_message(text: &str) -> MessageContent {
    MessageContent {
        role: "assistant".to_string(),
        content: vec![ContentBlock::text(text)],
        timestamp: bcs_protocol::now_ms(),
    }
}

/// Build a chat error terminal payload (#3 synthesized terminal / approval gate).
pub(super) fn build_chat_error_payload(run_id: &str, group_id: &str) -> Value {
    let payload = ChatEventPayload {
        run_id: run_id.to_string(),
        bcs_group_id: group_id.to_string(),
        state: WireChatState::Error,
        message: None,
        delta_text: None,
        usage: None,
        stop_reason: None,
        error_message: None,
        error_kind: None,
        error_code: None,
        tool_call_id: None,
        tool_name: None,
        args: None,
        result: None,
        is_error: None,
        success: None,
        routing: None,
    };
    serde_json::to_value(payload).unwrap_or(Value::Null)
}

/// Synthesize and ingest a chat error terminal to close a run cleanly (#3).
pub(super) async fn ingest_synthesized_error(
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    flow: &Arc<dyn ProviderEventIngestService>,
) {
    warn!(run_id = %bcn_run_id, "sse closed without chat terminal; synthesizing error terminal");
    let payload = build_chat_error_payload(bcn_run_id, group_id);
    ingest(
        bcn_run_id,
        group_id,
        bot_id,
        bcs_session_id,
        "chat.event".to_string(),
        ChatEventState::Error,
        payload,
        flow,
    )
    .await;
}

/// Build the `BotEventCommand` and hand it to the message-flow pipeline.
#[allow(clippy::too_many_arguments)]
pub(super) async fn ingest(
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    event_type: String,
    state: ChatEventState,
    payload: Value,
    flow: &Arc<dyn ProviderEventIngestService>,
) {
    let cmd = BotEventCommand {
        bot_id: bot_id.to_string(),
        run_id: bcn_run_id.to_string(), // BCN run id from run_ctx, NOT engine runId (D9)
        group_id: group_id.to_string(),
        event_type,
        event_payload: payload,
        state,
        bcs_session_id: bcs_session_id.clone(),
    };
    if let Err(error) = flow
        .ingest_provider_event(ProviderEventIngestCommand {
            source: ProviderEventSource::Sse,
            event: cmd,
        })
        .await
    {
        warn!(run_id = %bcn_run_id, %error, "ingest handle_bot_event failed");
    }
}

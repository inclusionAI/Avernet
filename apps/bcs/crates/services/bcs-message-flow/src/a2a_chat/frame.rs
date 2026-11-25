//! Direct chat.send frame construction; preserve the caller's message bytes.
use super::*;

pub(super) fn build_chat_send_frame(
    run_id: &str,
    session_key: &str,
    target_bot_id: &str,
    from_bot_id: &str,
    from_bot_name: &str,
    from_actor_id: &str,
    message: &str,
    tags: &[String],
    caller_wait_mode: Option<&str>,
) -> ServiceResult<BcsFrame> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let params = ChatSendParams {
        session_key: session_key.to_string(),
        bcs_group_id: session_key.to_string(),
        message: MessageContent {
            role: "user".to_string(),
            content: vec![ContentBlock::text(message)],
            timestamp,
        },
        channel: ChannelInfo {
            source: ChannelSource::Api,
            user_id: Some(from_bot_name.to_string()),
            actor_id: Some(from_bot_id.to_string()),
            actor_name: Some(from_bot_name.to_string()),
            thread_id: None,
            identity_forwarding: None,
        },
        session_context: GroupContext {
            session_id: session_key.to_string(),
            participants: vec![target_bot_id.to_string()],
            recipient: Some(target_bot_id.to_string()),
            recipient_name: None,
            recipient_role: None,
            delivery_type: Some("send".to_string()),
            originator: from_actor_id.to_string(),
            from: from_actor_id.to_string(),
            from_bot_id: Some(from_bot_id.to_string()),
            from_bot_owner: None,
            you_are_mentioned: true,
            is_sender: false,
            mentions: vec![target_bot_id.to_string()],
            response_directive: None,
            message: message.to_string(),
            routing_mode: None,
            group_type: None,
        },
        // Downstream execution has its own budget and must not inherit the
        // caller's local polling deadline or the BCS run lifecycle deadline.
        timeout_ms: Some(A2A_DOWNSTREAM_EXECUTION_TIMEOUT_MS),
        idempotency_key: None,
        // V3 keeps the execution session separate from the compatibility
        // `bcs_group_id` field. Direct chats do not have a real group, but
        // their resolved session key is still the trusted run scope that the
        // plugin must echo on uplink events.
        bcs_session_id: Some(session_key.to_string()),
        tags: tags.to_vec(),
        attachments: Vec::new(),
    };
    let mut params = serde_json::to_value(params)?;
    if let Some(wait_mode) = caller_wait_mode.map(str::trim).filter(|mode| !mode.is_empty()) {
        if let Some(obj) = params.as_object_mut() {
            obj.insert(
                "extensions".to_string(),
                serde_json::json!({
                    "caller_wait_mode": wait_mode,
                }),
            );
        }
    }
    Ok(BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    )))
}

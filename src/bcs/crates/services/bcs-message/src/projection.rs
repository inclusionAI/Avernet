use super::*;

pub(super) fn pending_to_group_message(
    pending: PendingGroupMessage,
    bot_name: Option<String>,
) -> GroupMessage {
    let (content, metadata) = match pending.kind {
        PendingGroupMessageKind::Chat { text } => (
            text,
            serde_json::json!({
                "bcs_pending": true,
                "pending_kind": "chat",
            }),
        ),
        PendingGroupMessageKind::ToolCall {
            tool_call_id,
            tool_name,
            tool_args,
        } => (
            String::new(),
            serde_json::json!({
                "bcs_pending": true,
                "pending_kind": "tool_call",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
            }),
        ),
    };
    GroupMessage {
        id: format!("bcs-run:{}:{}", pending.run_id, pending.bot_id),
        timestamp: pending.created_at_ms,
        sender: pending.bot_id,
        content,
        message_type: GroupMessageType::Bot,
        bot_name,
        role: MessageRole::Assistant,
        run_id: pending.run_id,
        history_meta: None,
        metadata: Some(metadata),
        attachments: None,
    }
}

pub(super) fn merge_history_window(
    mut durable: Vec<GroupMessage>,
    pending: Vec<GroupMessage>,
    limit: u32,
    durable_next_before: Option<u64>,
) -> (Vec<GroupMessage>, Option<u64>) {
    durable.extend(pending);
    // Stable sort preserves the repository's established order for durable
    // rows that share a timestamp; pending rows were appended after them.
    durable.sort_by(|left, right| right.timestamp.cmp(&left.timestamp));
    let truncated = durable.len() > limit as usize;
    durable.truncate(limit as usize);
    let next_before = if truncated || durable_next_before.is_some() {
        durable.last().map(|message| message.timestamp)
    } else {
        None
    };
    (durable, next_before)
}

pub(super) fn build_tool_call_metadata(content: &serde_json::Value) -> Option<serde_json::Value> {
    let obj = match content.as_object() {
        Some(o) => o,
        None => return None,
    };
    Some(serde_json::json!({
        "tool_call_id": obj.get("tool_call_id").cloned().unwrap_or(serde_json::Value::Null),
        "tool_name": obj.get("name").cloned().unwrap_or(serde_json::Value::Null),
        "arguments": obj.get("args").cloned().unwrap_or(serde_json::Value::Null),
        "is_error": obj.get("is_error").unwrap_or(&serde_json::Value::Bool(false)),
        "result": extract_tool_result_text(obj.get("result").unwrap_or(&serde_json::Value::Null)),
    }))
}

pub(super) fn extract_tool_result_text(result: &serde_json::Value) -> String {
    if let Some(content) = result.get("content") {
        if let Some(arr) = content.as_array() {
            return arr
                .iter()
                .filter_map(|block| block.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<_>>()
                .join("\n");
        }
        if let Some(text) = content.as_str() {
            return text.to_string();
        }
    }
    if let Some(text) = result.as_str() {
        return text.to_string();
    }
    result.to_string()
}

/// Extract display text + attachment views (without url) from persisted content.
/// - `Value::String(s)`                         -> (s, None)
/// - `Value::Object{ text, attachments }`       -> (text, attachments mapped; url=None)
/// - other                                      -> (content.to_string(), None)
pub(super) fn extract_text_and_attachments(
    content: &serde_json::Value,
) -> (String, Option<Vec<MessageAttachment>>) {
    if let Some(s) = content.as_str() {
        return (s.to_string(), None);
    }
    if let Some(obj) = content.as_object() {
        let text = obj
            .get("text")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        let attachments = obj
            .get("attachments")
            .and_then(serde_json::Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(parse_message_attachment)
                    .collect::<Vec<_>>()
            })
            .filter(|v| !v.is_empty());
        return (text, attachments);
    }
    (content.to_string(), None)
}

/// Map one stable_metadata JSON object to a `MessageAttachment` (url=None).
/// Returns None if `attachment_id` or `file_name` is missing.
pub(super) fn parse_message_attachment(v: &serde_json::Value) -> Option<MessageAttachment> {
    let obj = v.as_object()?;
    Some(MessageAttachment {
        attachment_id: obj.get("attachment_id")?.as_str()?.to_string(),
        attachment_type: obj
            .get("type")
            .and_then(|t| serde_json::from_value(t.clone()).ok())
            .unwrap_or(bcs_domain::AttachmentType::Image),
        file_name: obj.get("file_name")?.as_str()?.to_string(),
        mime_type: obj
            .get("mime_type")
            .and_then(|v| v.as_str())
            .map(String::from),
        size: obj.get("size").and_then(|v| v.as_u64()),
        sha256: obj.get("sha256").and_then(|v| v.as_str()).map(String::from),
        url: None,
        expires_at: None,
    })
}

pub(super) fn persisted_to_group_message(
    pm: bcs_domain::PersistedMessage,
    bot_name: Option<String>,
) -> GroupMessage {
    if let Some(mut message) = bcs_domain::state_machine_history::project_state_machine_message(&pm) {
        message.bot_name = message.bot_name.or(bot_name);
        return message;
    }
    let uses_stable_client_message_id = pm.message_type == SESSION_OPENING_MESSAGE_TYPE;
    let message_id = if uses_stable_client_message_id {
        pm.client_msg_id
            .clone()
            .unwrap_or_else(|| pm.message_id.clone())
    } else {
        pm.message_id.clone()
    };
    let is_persisted_bcs_ui = pm.message_type == SESSION_OPENING_MESSAGE_TYPE;
    let bcs_bot_name = is_persisted_bcs_ui.then(|| {
        pm.content
            .get("bot_name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(BCS_SESSION_OPENING_MESSAGE_SENDER_NAME)
            .to_string()
    });
    let (role, metadata, content_str, attachments) = match pm.message_type.as_str() {
        bcs_domain::CHAT_ERROR_MESSAGE_TYPE => (
            MessageRole::Assistant,
            Some(serde_json::json!({"terminal_state": "error", "is_error": true})),
            pm.content.as_str().unwrap_or_default().to_string(),
            None,
        ),
        "chat" | "text" | "system" => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let (text, attachments) = extract_text_and_attachments(&pm.content);
            (role, None, text, attachments)
        }
        SESSION_OPENING_MESSAGE_TYPE => {
            let text = pm
                .content
                .get("text")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            let metadata = pm.content.get("metadata").cloned();
            (MessageRole::Assistant, metadata, text, None)
        }
        "tool_call" => {
            let metadata = build_tool_call_metadata(&pm.content);
            let text = pm
                .content
                .get("result")
                .map(|r| extract_tool_result_text(r))
                .unwrap_or_else(|| pm.content.to_string());
            (MessageRole::ToolResult, metadata, text, None)
        }
        _ => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let text = pm.content.as_str().unwrap_or("").to_string();
            (role, None, text, None)
        }
    };

    GroupMessage {
        id: message_id,
        timestamp: pm.created_at,
        sender: pm.sender_id,
        content: content_str,
        message_type: GroupMessageType::Bot,
        bot_name: bcs_bot_name.or(bot_name),
        role,
        run_id: pm.run_id,
        history_meta: None,
        metadata,
        attachments,
    }
}

/// Mint a share_url into each attachment of a single message. Failures per
/// attachment (file deleted / not owned by session / storage error) leave
/// `url: None` and do NOT abort the batch or bubble up.
pub(super) async fn enrich_message_attachments(
    svc: &Arc<dyn SessionFileService>,
    session_id: &str,
    ttl: u64,
    msg: &mut GroupMessage,
) {
    let Some(atts) = msg.attachments.as_mut() else {
        return;
    };
    for att in atts.iter_mut() {
        match svc
            .share_mint_for_history(session_id, &att.attachment_id, ttl)
            .await
        {
            Ok(minted) => {
                att.url = Some(minted.share_url);
                att.expires_at = Some(minted.expires_at);
            }
            Err(_) => {
                att.url = None;
                att.expires_at = None;
            }
        }
    }
}

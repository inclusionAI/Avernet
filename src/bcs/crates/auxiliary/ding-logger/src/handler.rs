use std::collections::HashSet;

use anyhow::{Context, Result};
use chrono::Utc;
use serde_json::Value;
use tracing::info;

use crate::dedup::DedupStore;

/// Extracted fields from a DingTalk message frame.
#[derive(Debug)]
pub struct MessageRecord {
    pub group_id: String,
    pub message_id: String,
    pub sender_staff_id: String,
    pub sender_nick: String,
    pub content: String,
}

/// Process a parsed DingTalk Stream frame.
///
/// Returns Ok(()) in all cases — errors are logged and swallowed so the
/// caller can continue processing the next frame.
pub fn handle_frame(
    frame: &Value,
    group_ids: &HashSet<String>,
    dedup: &DedupStore,
) -> Result<()> {
    let frame_type = frame.get("type").and_then(|v| v.as_str()).unwrap_or("");
    if frame_type != "EVENT" && frame_type != "CALLBACK" {
        return Ok(());
    }

    let data_field = frame.get("data").context("missing data field")?;
    let data = parse_stream_data(data_field)?;

    let event_type = frame
        .pointer("/headers/eventType")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let is_scene_group = event_type.starts_with("chat");

    let record = if is_scene_group {
        match parse_event_record(&data)? {
            Some(r) => r,
            None => return Ok(()),
        }
    } else {
        parse_callback_record(&data)?
    };

    // Whitelist filter
    if !group_ids.contains(&record.group_id) {
        return Ok(());
    }

    // Dedup
    if !record.message_id.is_empty() && dedup.is_duplicate(&record.message_id) {
        return Ok(());
    }

    // Serialize as a single JSON object in the log message body.
    // BCS logging routes `ding_group_message` target to its own file via logging.outputs.
    let payload = serde_json::json!({
        "timestamp": Utc::now().to_rfc3339(),
        "group_id": record.group_id,
        "message_id": record.message_id,
        "sender_staff_id": record.sender_staff_id,
        "sender_nick": record.sender_nick,
        "content": record.content,
    });
    info!(target: "ding_group_message", "{}", payload);

    Ok(())
}

fn parse_event_record(data: &Value) -> Result<Option<MessageRecord>> {
    let Some(msg_content_str) = data.get("msgContent").and_then(|v| v.as_str()) else {
        return Ok(None); // non-message chat event (member change, rename, etc.)
    };
    let msg_content: Value =
        serde_json::from_str(msg_content_str).context("failed to parse msgContent")?;

    let content = msg_content
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    Ok(Some(MessageRecord {
        group_id: data
            .get("openConversationId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        message_id: data
            .get("openMsgId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        sender_staff_id: data
            .get("senderUserId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        sender_nick: data
            .get("nickName")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        content,
    }))
}

fn parse_callback_record(data: &Value) -> Result<MessageRecord> {
    let content = data
        .get("text")
        .and_then(|t| t.get("content"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    Ok(MessageRecord {
        group_id: data
            .get("conversationId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        message_id: data
            .get("msgId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        sender_staff_id: data
            .get("senderStaffId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        sender_nick: data
            .get("senderNick")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        content,
    })
}

fn parse_stream_data(data_field: &Value) -> Result<Value> {
    match data_field {
        Value::String(s) => {
            if let Ok(decoded) = base64_decode(s) {
                if let Ok(parsed) = serde_json::from_str(&decoded) {
                    return Ok(parsed);
                }
            }
            serde_json::from_str(s).context("failed to parse data string")
        }
        v => Ok(v.clone()),
    }
}

fn base64_decode(s: &str) -> Result<String> {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    let bytes = STANDARD.decode(s).context("base64 decode failed")?;
    String::from_utf8(bytes).context("invalid UTF-8 in base64 data")
}

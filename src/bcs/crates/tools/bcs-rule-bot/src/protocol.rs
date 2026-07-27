use bcs_protocol::{
    BcsFrame, ChatEventPayload, ChatEventState, ContentBlock, ErrorShape, EventFrame,
    MessageContent, ResponseFrame,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoryMessage {
    pub id: String,
    pub role: String,
    pub content: String,
    pub timestamp: u64,
}

#[derive(Debug, Deserialize)]
pub struct ChatHistoryParams {
    pub session_key: String,
    #[serde(default = "default_history_limit")]
    pub limit: usize,
    #[serde(default)]
    pub before: Option<u64>,
    #[serde(default)]
    pub after: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct SessionDeleteParams {
    pub bcs_group_id: String,
}

#[derive(Debug, Serialize)]
pub struct TaskDispatchParams<'a> {
    pub group_id: &'a str,
    pub target_bot: &'a str,
    pub message: &'a str,
}

#[derive(Debug, Serialize)]
pub struct TaskCompleteParams<'a> {
    pub group_id: &'a str,
    pub summary: &'a str,
    pub status: &'static str,
}

#[derive(Debug, Deserialize)]
pub struct TaskDispatchResponse {
    pub task_id: String,
}

fn default_history_limit() -> usize {
    50
}

pub fn message_text(message: &MessageContent) -> String {
    message
        .content
        .iter()
        .filter(|block| block.block_type == "text")
        .filter_map(|block| block.text.as_deref())
        .collect()
}

pub fn ok_response(id: impl Into<String>, payload: Value) -> BcsFrame {
    BcsFrame::Response(ResponseFrame::ok(id, payload))
}

pub fn error_response(
    id: impl Into<String>,
    code: impl Into<String>,
    message: impl Into<String>,
    retryable: bool,
) -> BcsFrame {
    BcsFrame::Response(ResponseFrame::err(
        id,
        ErrorShape {
            code: code.into(),
            message: message.into(),
            details: None,
            retryable,
            retry_after_ms: None,
        },
    ))
}

pub fn final_chat_event(
    run_id: impl Into<String>,
    group_id: impl Into<String>,
    text: impl Into<String>,
) -> BcsFrame {
    let payload = ChatEventPayload {
        run_id: run_id.into(),
        bcs_group_id: group_id.into(),
        state: ChatEventState::Final,
        message: Some(MessageContent {
            role: "assistant".to_string(),
            content: vec![ContentBlock::text(text)],
            timestamp: bcs_protocol::now_ms(),
        }),
        delta_text: None,
        usage: None,
        stop_reason: Some("complete".to_string()),
        error_message: None,
        error_kind: None,
        tool_call_id: None,
        tool_name: None,
        args: None,
        result: None,
        is_error: None,
        success: None,
        routing: None,
    };
    BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::to_value(payload).unwrap_or(Value::Null)),
        Some(0),
    ))
}

pub fn error_chat_event(
    run_id: impl Into<String>,
    group_id: impl Into<String>,
    error_kind: impl Into<String>,
    message: impl Into<String>,
) -> BcsFrame {
    let payload = ChatEventPayload {
        run_id: run_id.into(),
        bcs_group_id: group_id.into(),
        state: ChatEventState::Error,
        message: None,
        delta_text: None,
        usage: None,
        stop_reason: Some("error".to_string()),
        error_message: Some(message.into()),
        error_kind: Some(error_kind.into()),
        tool_call_id: None,
        tool_name: None,
        args: None,
        result: None,
        is_error: Some(true),
        success: Some(false),
        routing: None,
    };
    BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::to_value(payload).unwrap_or(Value::Null)),
        Some(0),
    ))
}

pub fn history_response(
    id: &str,
    session_key: &str,
    messages: &[HistoryMessage],
    params: &ChatHistoryParams,
) -> BcsFrame {
    let mut filtered = messages
        .iter()
        .filter(|message| {
            params
                .before
                .is_none_or(|before| message.timestamp < before)
                && params.after.is_none_or(|after| message.timestamp > after)
        })
        .cloned()
        .collect::<Vec<_>>();
    filtered.sort_by_key(|message| std::cmp::Reverse(message.timestamp));
    let limit = params.limit.clamp(1, 1000);
    let has_more = filtered.len() > limit;
    filtered.truncate(limit);

    ok_response(
        id,
        json!({
            "session_key": session_key,
            "session_id": session_key,
            "messages": filtered,
            "has_more": has_more
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn joins_text_blocks_only() {
        let message = MessageContent {
            role: "user".to_string(),
            content: vec![
                ContentBlock::text("你"),
                ContentBlock::image("https://example.test/image"),
                ContentBlock::text("好"),
            ],
            timestamp: 0,
        };

        assert_eq!(message_text(&message), "你好");
    }
}

use serde::{Deserialize, Serialize};

pub const MSG_LOG_TARGET: &str = "bcs_message";
pub const MESSAGE_LOG_SCHEMA_VERSION: u16 = 1;
pub const MESSAGE_LOG_CONTENT_MAX_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageLogMode {
    FreeChat,
    ManagerWorker,
    Structured,
    StateMachine,
}

impl MessageLogMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::FreeChat => "free_chat",
            Self::ManagerWorker => "manager_worker",
            Self::Structured => "structured",
            Self::StateMachine => "state_machine",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageLogEventType {
    MessageReceived,
    RouteDecided,
    BotDeliverResult,
    BotAccept,
    BotReject,
    BotEvent,
    TaskDispatchCreated,
    TaskComplete,
    NodeDispatch,
    NodeResult,
    Transition,
    RunComplete,
    RunFailed,
    Timeout,
}

impl MessageLogEventType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::MessageReceived => "message_received",
            Self::RouteDecided => "route_decided",
            Self::BotDeliverResult => "bot_deliver_result",
            Self::BotAccept => "bot_accept",
            Self::BotReject => "bot_reject",
            Self::BotEvent => "bot_event",
            Self::TaskDispatchCreated => "task_dispatch_created",
            Self::TaskComplete => "task_complete",
            Self::NodeDispatch => "node_dispatch",
            Self::NodeResult => "node_result",
            Self::Transition => "transition",
            Self::RunComplete => "run_complete",
            Self::RunFailed => "run_failed",
            Self::Timeout => "timeout",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageLogStatus {
    Received,
    Routed,
    Delivered,
    Accepted,
    Rejected,
    Responded,
    Completed,
    Failed,
    Timeout,
    Skipped,
}

impl MessageLogStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Received => "received",
            Self::Routed => "routed",
            Self::Delivered => "delivered",
            Self::Accepted => "accepted",
            Self::Rejected => "rejected",
            Self::Responded => "responded",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Timeout => "timeout",
            Self::Skipped => "skipped",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MessageLogContent {
    pub content: String,
    pub content_length: usize,
    pub content_truncated: bool,
    pub content_truncated_bytes: usize,
}

impl MessageLogContent {
    pub fn from_text(text: &str) -> Self {
        Self::from_text_with_max_bytes(text, MESSAGE_LOG_CONTENT_MAX_BYTES)
    }

    pub fn from_text_with_max_bytes(text: &str, max_bytes: usize) -> Self {
        let (content, content_truncated, content_truncated_bytes) =
            truncate_utf8_to_bytes(text, max_bytes);
        Self {
            content: content.to_string(),
            content_length: text.len(),
            content_truncated,
            content_truncated_bytes,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MessageLogTargetSummary {
    pub bot_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route_source: Option<String>,
}

impl MessageLogTargetSummary {
    pub fn new(bot_id: impl Into<String>) -> Self {
        Self {
            bot_id: bot_id.into(),
            delivery_type: None,
            route_source: None,
        }
    }

    pub fn with_delivery_type(mut self, delivery_type: impl Into<String>) -> Self {
        self.delivery_type = Some(delivery_type.into());
        self
    }

    pub fn with_route_source(mut self, route_source: impl Into<String>) -> Self {
        self.route_source = Some(route_source.into());
        self
    }
}

pub fn message_log_json<T: Serialize>(value: &T) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

fn truncate_utf8_to_bytes(text: &str, max_bytes: usize) -> (&str, bool, usize) {
    if text.len() <= max_bytes {
        return (text, false, 0);
    }

    let mut cutoff = 0usize;
    for (index, ch) in text.char_indices() {
        let next = index + ch.len_utf8();
        if next > max_bytes {
            break;
        }
        cutoff = next;
    }

    (&text[..cutoff], true, text.len() - cutoff)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_under_limit_is_not_truncated() {
        let content = MessageLogContent::from_text_with_max_bytes("hello", 10);

        assert_eq!(content.content, "hello");
        assert_eq!(content.content_length, 5);
        assert!(!content.content_truncated);
        assert_eq!(content.content_truncated_bytes, 0);
    }

    #[test]
    fn content_truncation_is_utf8_safe() {
        let content = MessageLogContent::from_text_with_max_bytes("abc你好", 5);

        assert_eq!(content.content, "abc");
        assert_eq!(content.content_length, "abc你好".len());
        assert!(content.content_truncated);
        assert_eq!(content.content_truncated_bytes, "你好".len());
    }

    #[test]
    fn content_can_truncate_to_empty_string() {
        let content = MessageLogContent::from_text_with_max_bytes("你", 1);

        assert_eq!(content.content, "");
        assert_eq!(content.content_length, "你".len());
        assert!(content.content_truncated);
        assert_eq!(content.content_truncated_bytes, "你".len());
    }

    #[test]
    fn target_summary_serializes_optional_fields() {
        let target = MessageLogTargetSummary::new("bot-1")
            .with_delivery_type("websocket")
            .with_route_source("explicit");

        let json = message_log_json(&target);

        assert!(json.contains("\"bot_id\":\"bot-1\""));
        assert!(json.contains("\"delivery_type\":\"websocket\""));
        assert!(json.contains("\"route_source\":\"explicit\""));
    }
}

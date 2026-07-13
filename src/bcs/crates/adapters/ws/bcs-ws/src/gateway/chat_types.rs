//! Chat method types.
//!
//! Parameter and result types for chat.send, chat.history, chat.abort.

use serde::{Deserialize, Serialize};
use bcs_protocol::Attachment;

// ============================================================================
// chat.history
// ============================================================================

/// Parameters for chat.history method.
#[derive(Debug, Clone, Deserialize)]
pub struct ChatHistoryParams {
    /// Session key to get history for.
    pub session_key: String,

    /// Maximum number of messages to return.
    #[serde(default)]
    pub limit: Option<usize>,

    /// Return only messages older than this timestamp.
    #[serde(default)]
    pub before: Option<u64>,
}

/// Result of chat.history method.
#[derive(Debug, Clone, Serialize)]
pub struct ChatHistoryResult {
    /// Session key.
    pub session_key: String,

    /// Session ID (if resolved).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,

    /// Messages in the session.
    pub messages: Vec<serde_json::Value>,

    /// Thinking level for the session.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking_level: Option<String>,
}

// ============================================================================
// chat.send
// ============================================================================

/// Parameters for chat.send method.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatSendParams {
    /// Session key to send message to.
    pub session_key: String,

    /// Message content.
    pub message: String,

    /// Sender identifier.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,

    /// Thinking mode override.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thinking: Option<String>,

    /// Whether to deliver the message (default true).
    #[serde(default = "default_deliver")]
    pub deliver: bool,

    /// Attachments.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attachments: Option<Vec<Attachment>>,

    /// Timeout in milliseconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,

    /// Idempotency key for deduplication.
    #[serde(alias = "idempotency_key")]
    pub idempotency_key: String,
}

fn default_deliver() -> bool {
    true
}

/// Result of chat.send method.
#[derive(Debug, Clone, Serialize)]
pub struct ChatSendResult {
    /// Run ID for tracking the chat execution.
    pub run_id: String,

    /// Status of the send request.
    pub status: ChatSendStatus,
}

/// Status of chat.send request.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ChatSendStatus {
    /// Chat has started.
    Started,

    /// Chat is already in flight (idempotent return).
    InFlight,

    /// Chat completed successfully.
    Ok,

    /// Chat failed with error.
    Error,
}

// ============================================================================
// chat.abort
// ============================================================================

/// Parameters for chat.abort method.
#[derive(Debug, Clone, Deserialize)]
pub struct ChatAbortParams {
    /// Session key to abort chats for.
    pub session_key: String,

    /// Specific run ID to abort (optional, aborts all if not provided).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
}

/// Result of chat.abort method.
#[derive(Debug, Clone, Serialize)]
pub struct ChatAbortResult {
    /// Whether the abort was successful.
    pub ok: bool,

    /// Whether any runs were actually aborted.
    pub aborted: bool,

    /// List of run IDs that were aborted.
    pub run_ids: Vec<String>,
}

// ============================================================================
// Chat Event
// ============================================================================

/// Chat event pushed to clients during execution.
#[derive(Debug, Clone, Serialize)]
pub struct ChatEvent {
    /// Run ID for this chat execution.
    pub run_id: String,

    /// Session key.
    pub session_key: String,

    /// Sequence number for ordering.
    pub seq: u64,

    /// Event state.
    pub state: ChatEventState,

    /// Message content (for delta/final states).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<serde_json::Value>,

    /// Error message (for error state).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,

    /// Token usage information.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<serde_json::Value>,

    /// Stop reason (for final state).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_reason: Option<String>,
}

/// State of a chat event.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatEventState {
    /// Delta/partial content.
    Delta,

    /// Final complete message.
    Final,

    /// Chat was aborted.
    Aborted,

    /// Chat failed with error.
    Error,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chat_send_params_deserialization_camel_case() {
        let json = r#"{
            "session_key": "test-session",
            "message": "Hello",
            "idempotency_key": "run-1"
        }"#;

        let params: ChatSendParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.message, "Hello");
        assert!(params.deliver); // default
        assert_eq!(params.idempotency_key, "run-1");
    }

    #[test]
    fn test_chat_send_params_deserialization_snake_case() {
        // Also support snake_case
        let json = r#"{
            "session_key": "test-session",
            "message": "Hello",
            "idempotency_key": "run-1"
        }"#;

        let params: ChatSendParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.message, "Hello");
    }

    #[test]
    fn test_chat_event_serialization() {
        let event = ChatEvent {
            run_id: "run-1".to_string(),
            session_key: "test".to_string(),
            seq: 1,
            state: ChatEventState::Delta,
            message: Some(serde_json::json!({"content": "Hello"})),
            error_message: None,
            usage: None,
            stop_reason: None,
        };

        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains(r#""state":"delta""#));
        assert!(json.contains(r#""run_id""#)); // snake_case output
    }

    #[test]
    fn test_chat_history_params_deserialization() {
        let json = r#"{
            "session_key": "test-session",
            "limit": 100
        }"#;

        let params: ChatHistoryParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.limit, Some(100));
        assert_eq!(params.before, None);
    }

    #[test]
    fn test_chat_history_params_snake_case() {
        let json = r#"{
            "session_key": "test-session",
            "limit": 50
        }"#;

        let params: ChatHistoryParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.limit, Some(50));
        assert_eq!(params.before, None);
    }

    #[test]
    fn test_chat_history_params_default_limit() {
        let json = r#"{
            "session_key": "test-session"
        }"#;

        let params: ChatHistoryParams = serde_json::from_str(json).unwrap();
        assert!(params.limit.is_none());
        assert!(params.before.is_none());
    }

    #[test]
    fn test_chat_history_params_before() {
        let json = r#"{
            "session_key": "test-session",
            "before": 12345
        }"#;

        let params: ChatHistoryParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.before, Some(12345));
    }

    #[test]
    fn test_chat_history_result_serialization() {
        let result = ChatHistoryResult {
            session_key: "test-session".to_string(),
            session_id: Some("session-123".to_string()),
            messages: vec![
                serde_json::json!({"id": "msg-1", "content": "Hello"}),
            ],
            thinking_level: Some("high".to_string()),
        };

        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains(r#""session_key""#));
        assert!(json.contains(r#""session_id""#));
        assert!(json.contains(r#""thinking_level""#));
    }

    #[test]
    fn test_chat_history_result_skip_none_fields() {
        let result = ChatHistoryResult {
            session_key: "test".to_string(),
            session_id: None,
            messages: vec![],
            thinking_level: None,
        };

        let json = serde_json::to_string(&result).unwrap();
        assert!(!json.contains("session_id"));
        assert!(!json.contains("thinking_level"));
    }

    #[test]
    fn test_chat_send_params_all_fields() {
        let json = r#"{
            "session_key": "test-session",
            "message": "Hello world",
            "from": "user-1",
            "thinking": "high",
            "deliver": false,
            "attachments": [{
                "attachment_id": "att-1",
                "type": "image",
                "file_name": "image.png",
                "mime_type": "image/png",
                "size": 4,
                "sha256": "abcd",
                "url": "https://example.com/image.png",
                "expires_at": 123
            }],
            "timeout_ms": 30000,
            "idempotency_key": "run-unique"
        }"#;

        let params: ChatSendParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.message, "Hello world");
        assert_eq!(params.from, Some("user-1".to_string()));
        assert_eq!(params.thinking, Some("high".to_string()));
        assert!(!params.deliver);
        assert!(params.attachments.is_some());
        assert_eq!(params.timeout_ms, Some(30000));
        assert_eq!(params.idempotency_key, "run-unique");
    }

    #[test]
    fn test_chat_send_result_serialization() {
        let result = ChatSendResult {
            run_id: "run-123".to_string(),
            status: ChatSendStatus::Started,
        };

        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains(r#""run_id""#));
        assert!(json.contains(r#""status":"started""#));
    }

    #[test]
    fn test_chat_send_status_variants() {
        let statuses = vec![
            (ChatSendStatus::Started, "started"),
            (ChatSendStatus::InFlight, "in_flight"),
            (ChatSendStatus::Ok, "ok"),
            (ChatSendStatus::Error, "error"),
        ];

        for (status, expected) in statuses {
            let json = serde_json::to_string(&status).unwrap();
            assert!(json.contains(expected));
        }
    }

    #[test]
    fn test_chat_abort_params_deserialization() {
        let json = r#"{
            "session_key": "test-session",
            "run_id": "run-123"
        }"#;

        let params: ChatAbortParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert_eq!(params.run_id, Some("run-123".to_string()));
    }

    #[test]
    fn test_chat_abort_params_without_run_id() {
        let json = r#"{
            "session_key": "test-session"
        }"#;

        let params: ChatAbortParams = serde_json::from_str(json).unwrap();
        assert_eq!(params.session_key, "test-session");
        assert!(params.run_id.is_none());
    }

    #[test]
    fn test_chat_abort_result_serialization() {
        let result = ChatAbortResult {
            ok: true,
            aborted: true,
            run_ids: vec!["run-1".to_string(), "run-2".to_string()],
        };

        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains(r#""ok":true"#));
        assert!(json.contains(r#""aborted":true"#));
        assert!(json.contains(r#""run_ids""#));
    }

    #[test]
    fn test_chat_abort_result_not_found() {
        let result = ChatAbortResult {
            ok: true,
            aborted: false,
            run_ids: vec![],
        };

        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains(r#""aborted":false"#));
    }

    #[test]
    fn test_chat_event_all_states() {
        let states = vec![
            (ChatEventState::Delta, "delta"),
            (ChatEventState::Final, "final"),
            (ChatEventState::Aborted, "aborted"),
            (ChatEventState::Error, "error"),
        ];

        for (state, expected) in states {
            let event = ChatEvent {
                run_id: "run".to_string(),
                session_key: "session".to_string(),
                seq: 1,
                state,
                message: None,
                error_message: None,
                usage: None,
                stop_reason: None,
            };

            let json = serde_json::to_string(&event).unwrap();
            assert!(json.contains(&format!(r#""state":"{}""#, expected)));
        }
    }

    #[test]
    fn test_chat_event_with_usage() {
        let event = ChatEvent {
            run_id: "run-1".to_string(),
            session_key: "session".to_string(),
            seq: 5,
            state: ChatEventState::Final,
            message: Some(serde_json::json!({"content": "Done"})),
            error_message: None,
            usage: Some(serde_json::json!({
                "promptTokens": 100,
                "completionTokens": 200,
                "totalTokens": 300
            })),
            stop_reason: Some("complete".to_string()),
        };

        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains(r#""usage""#));
        assert!(json.contains(r#""stop_reason""#));
    }

    #[test]
    fn test_chat_event_skip_none_fields() {
        let event = ChatEvent {
            run_id: "run".to_string(),
            session_key: "session".to_string(),
            seq: 1,
            state: ChatEventState::Delta,
            message: None,
            error_message: None,
            usage: None,
            stop_reason: None,
        };

        let json = serde_json::to_string(&event).unwrap();
        assert!(!json.contains("message"));
        assert!(!json.contains("error_message"));
        assert!(!json.contains("usage"));
        assert!(!json.contains("stop_reason"));
    }

    #[test]
    fn test_chat_event_error_state() {
        let event = ChatEvent {
            run_id: "run-1".to_string(),
            session_key: "session".to_string(),
            seq: 1,
            state: ChatEventState::Error,
            message: None,
            error_message: Some("Something went wrong".to_string()),
            usage: None,
            stop_reason: None,
        };

        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains(r#""state":"error""#));
        assert!(json.contains(r#""error_message":"Something went wrong""#));
    }

    #[test]
    fn test_chat_event_aborted_state() {
        let event = ChatEvent {
            run_id: "run-1".to_string(),
            session_key: "session".to_string(),
            seq: 3,
            state: ChatEventState::Aborted,
            message: Some(serde_json::json!({"content": "Partial content"})),
            error_message: None,
            usage: None,
            stop_reason: Some("aborted".to_string()),
        };

        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains(r#""state":"aborted""#));
        assert!(json.contains(r#""stop_reason":"aborted""#));
    }

    #[test]
    fn test_chat_send_params_serialization() {
        let params = ChatSendParams {
            session_key: "test".to_string(),
            message: "Hello".to_string(),
            from: Some("user".to_string()),
            thinking: None,
            deliver: true,
            attachments: None,
            timeout_ms: None,
            idempotency_key: "run-1".to_string(),
        };

        let json = serde_json::to_string(&params).unwrap();
        assert!(json.contains(r#""session_key""#));
        assert!(json.contains(r#""idempotency_key""#));
    }
}

use async_trait::async_trait;
use serde_json::Value;

/// In-memory message content that has not reached the durable message store.
///
/// The port is intentionally read-only. Durable history remains authoritative;
/// callers may merge this best-effort snapshot into one history response.
#[derive(Debug, Clone, PartialEq)]
pub struct PendingGroupMessage {
    pub run_id: String,
    pub bot_id: String,
    pub session_id: Option<String>,
    pub created_at_ms: u64,
    pub kind: PendingGroupMessageKind,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PendingGroupMessageKind {
    Chat {
        text: String,
    },
    ToolCall {
        tool_call_id: String,
        tool_name: String,
        tool_args: Value,
    },
}

#[async_trait]
pub trait PendingGroupMessagePort: Send + Sync {
    async fn list_pending(
        &self,
        group_id: &str,
        session_id: Option<&str>,
    ) -> Vec<PendingGroupMessage>;
}

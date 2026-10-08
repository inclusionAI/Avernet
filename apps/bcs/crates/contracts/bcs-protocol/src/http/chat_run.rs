//! HTTP DTOs for async chat-run endpoints.
//!
//! Shape mirrors the BCS HTTP endpoints:
//! - `POST /bots/{id}/chat-async` -> [`ChatRunSubmitResponse`]
//! - `GET  /chat/runs/{run_id}` -> [`ChatRunStatusResponse`]
//! - `POST /chat/runs/{run_id}/cancel` -> [`ChatRunCancelResponse`]

use serde::{Deserialize, Serialize};

/// HTTP chat schema version understood by this client/server contract.
pub const BCS_CHAT_VERSION: &str = "2";
pub const BCS_CHAT_VERSION_HEADER: &str = "X-BCS-CHAT-VERSION";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRunState {
    Pending,
    Submitted,
    Running,
    Completed,
    Failed,
    Cancelled,
    #[serde(other)]
    Unknown,
}

impl ChatRunState {
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Submitted => "submitted",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatRunSubmitResponse {
    pub run_id: String,
    pub bot_uuid: String,
    pub session_id: String,
    /// Submission status. Currently always `"pending"`.
    #[serde(default)]
    pub status: Option<String>,
    /// Unix epoch milliseconds when the run will be auto-expired if still
    /// unfinished.
    #[serde(default)]
    pub expires_at_ms: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatRunResponseContent {
    #[serde(default)]
    pub content: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatRunStatusResponse {
    pub run_id: String,
    pub bot_uuid: String,
    pub from_bot_id: String,
    pub session_id: String,
    pub state: ChatRunState,
    pub response: ChatRunResponseContent,
    #[serde(default)]
    pub error_message: Option<String>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    #[serde(default)]
    pub completed_at_ms: Option<u64>,
    pub expires_at_ms: u64,
    pub version: u64,
    #[serde(default)]
    pub content_truncated: bool,
    pub is_terminal: bool,
}

impl ChatRunStatusResponse {
    pub fn is_terminal(&self) -> bool {
        self.is_terminal || self.state.is_terminal()
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatRunCancelResponse {
    pub run_id: String,
    pub cancelled: bool,
    pub state: ChatRunState,
    pub response: ChatRunResponseContent,
    #[serde(default)]
    pub error_message: Option<String>,
    pub version: u64,
    #[serde(default)]
    pub content_truncated: bool,
}

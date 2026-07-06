//! Group message-history use-case contracts.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{GroupMessage, Participant};

use super::{group_management::GroupUseCaseError, principal::CallerContext};

/// Request for loading group message history.
#[derive(Debug, Clone)]
pub struct GroupHistoryCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub view_bot_id: Option<String>,
    pub limit: u64,
    pub before: Option<u64>,
}

/// Response payload for group message history.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupHistoryResult {
    pub group_id: String,
    pub messages: Vec<GroupMessage>,
    pub limit: u64,
    pub before: Option<u64>,
    pub next_before: Option<u64>,
}

/// Request for loading session message history.
#[derive(Debug, Clone)]
pub struct SessionHistoryCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub session_id: String,
    pub session_participants: Vec<Participant>,
    pub view_bot_id: Option<String>,
    pub limit: u64,
    pub before: Option<u64>,
}

/// Response payload for session message history.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionHistoryResult {
    pub session_id: String,
    pub messages: Vec<GroupMessage>,
    pub limit: u64,
    pub before: Option<u64>,
    pub next_before: Option<u64>,
}

/// Group message-history application service.
#[async_trait]
pub trait GroupMessageHistoryService: Send + Sync {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError>;

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError>;
}

//! Group message-history use-case contracts.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{GroupMessage, Participant};

use super::{group_management::GroupUseCaseError, principal::CallerContext};

/// Optional capabilities for one history read.
///
/// The default intentionally preserves the durable-only response used by
/// clients deployed before pending in-memory recovery was introduced.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MessageHistoryOptions {
    pub include_pending: bool,
}

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

    async fn get_history_with_options(
        &self,
        cmd: GroupHistoryCommand,
        _options: MessageHistoryOptions,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        self.get_history(cmd).await
    }

    async fn get_session_history_with_options(
        &self,
        cmd: SessionHistoryCommand,
        _options: MessageHistoryOptions,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.get_session_history(cmd).await
    }

    /// Batched group activity keys for the recent-activity list sort (design
    /// D1/D4): the max persisted message `created_at` per group id, in epoch
    /// milliseconds. The implementation's store owns `env` isolation, so the
    /// caller cannot read another environment's message log. Group ids with no
    /// persisted messages are absent from the map.
    ///
    /// Default returns an empty map so every existing implementation keeps
    /// compiling: implementations without the persisted message log (for
    /// example the legacy bot-delivery fallback) degrade to the `created_at`
    /// fallback (D1/D6) rather than sourcing activity from delivery internals.
    async fn last_activity_by_group_ids(
        &self,
        group_ids: &[String],
    ) -> Result<std::collections::HashMap<String, u64>, GroupUseCaseError> {
        let _ = group_ids;
        Ok(Default::default())
    }

    /// Batched session activity keys for the recent-activity list sort (design
    /// D1/D4): the max persisted message `created_at` per session id, in epoch
    /// milliseconds, under the same store-owned `env` isolation as
    /// `last_activity_by_group_ids`. Session ids with no persisted messages are
    /// absent from the map. Default returns an empty map.
    async fn last_activity_by_session_ids(
        &self,
        session_ids: &[String],
    ) -> Result<std::collections::HashMap<String, u64>, GroupUseCaseError> {
        let _ = session_ids;
        Ok(Default::default())
    }

    /// Per-session message activity keys within ONE group, in ONE aggregate
    /// round trip (design D5 revised — the bounded-read session activity sort):
    /// the max persisted message `created_at` per session id in epoch
    /// milliseconds, under the same store-owned `env` isolation as
    /// `last_activity_by_group_ids`. Session ids with no persisted message in
    /// this group are absent from the map, so callers fall back to the
    /// session's own `created_at`. Default returns an empty map so
    /// implementations without the persisted message log degrade to the
    /// `created_at` fallback ordering (D6) and keep compiling.
    async fn last_activity_in_group(
        &self,
        group_id: &str,
    ) -> Result<std::collections::HashMap<String, u64>, GroupUseCaseError> {
        let _ = group_id;
        Ok(Default::default())
    }
}

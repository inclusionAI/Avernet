//! Message history repository port.
//!
//! Persistence contract for session-level chat message history.
//! Implementations: MemoryMessageRepo (local dev/test), MySqlMessageStore (production).

use async_trait::async_trait;
use bcs_domain::{
    HumanMessageView, MessageOwnerFilter, MessagePage, MessageQuery, NewMessage, PersistedMessage,
};

use crate::types::ServiceResult;

use super::AppendEventRecord;

#[derive(Debug, Clone)]
pub struct AppendMessageWithEvent {
    pub message_id: String,
    pub message: NewMessage,
    pub event: AppendEventRecord,
}

/// Errors specific to message repository operations.
#[derive(Debug, thiserror::Error)]
pub enum MessageRepoError {
    #[error("duplicate message: message_id={message_id}, session_seq={session_seq}")]
    DuplicateMessage {
        message_id: String,
        session_seq: i64,
    },

    #[error("session not found: {0}")]
    SessionNotFound(String),

    #[error("invalid session sequence: {0}")]
    InvalidSequence(String),

    #[error("storage error: {0}")]
    StorageError(String),
}

/// Message history persistence port.
#[async_trait]
pub trait MessageRepoPort: Send + Sync + 'static {
    /// Resolve the ordinary Chat join window in physical sequence positions.
    /// Supplemental StateMachine projections do not consume a slot; missing
    /// legacy positions still do. The anchor is fixed before paging starts.
    async fn resolve_history_window_start(&self, _session: &str, _anchor: i64, _limit: u64) -> Result<i64, MessageRepoError> {
        Err(MessageRepoError::StorageError("history window resolution is not configured".into()))
    }

    /// Internal reconstruction read: exact env/session/sender/run, chat only,
    /// ordered by session_seq. Includes legacy string bodies, excludes summaries
    /// and tool records. Implementations must not substitute a limited history page.
    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let _ = (session, sender, run);
        Err(MessageRepoError::StorageError("run text reconstruction unavailable".into()))
    }
    /// Internal canonical payload read, not public history projection. SQL
    /// implementations batch IDs (including attachments) rather than N+1 reads.
    async fn get_messages_by_ids(&self, session_id: &str, ids: &[String]) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let mut messages = Vec::new();
        for id in ids { if let Some(message) = self.get_message_by_id(session_id, id).await? { messages.push(message); } }
        Ok(messages)
    }
    /// Canonical StateMachine records by physical ID or stored legacy client key.
    /// Scope to this environment/session; do not apply audience, time or page filters.
    /// Return earliest session_seq first; published chat results are excluded.
    async fn get_state_machine_messages_by_keys(
        &self, session_id: &str, keys: &[String],
    ) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let _ = (session_id, keys);
        Err(MessageRepoError::StorageError("StateMachine canonical history lookup unavailable".into()))
    }

    /// The same store instance owns canonical message/delivery transactions.
    /// None denotes a legacy implementation that cannot host durable queues.
    fn delivery_repository(self: std::sync::Arc<Self>) -> Option<std::sync::Arc<dyn super::message_delivery::MessageDeliveryRepoPort>> {
        None
    }
    /// Append a message to a session. Allocates `session_seq` atomically.
    /// chat_error retries are unique per environment/group/session/sender/run,
    /// including concurrent writes; run_id is required. SQL uses a scoped
    /// deterministic primary key, memory repositories serialize the same rule.
    async fn append_message(&self, msg: NewMessage) -> Result<PersistedMessage, MessageRepoError>;

    /// Append using a caller-owned stable logical message id. Concurrent/repeated
    /// writes of that id must return one stored message without allocating extra
    /// sequence numbers. Return the original content; callers must validate it
    /// before treating an idempotency collision as their own successful write.
    /// IDs must be nonempty and at most 256 bytes. A matching legacy client key
    /// may return a prior logical message with its original generated message id.
    async fn append_message_with_id(&self, _message_id: String, _msg: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        Err(MessageRepoError::StorageError("stable message-id persistence is not configured".into()))
    }

    async fn append_message_with_event(
        &self,
        command: AppendMessageWithEvent,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let _ = command;
        Err(MessageRepoError::StorageError(
            "Eventful message persistence is not configured".to_string(),
        ))
    }

    /// Query messages with cursor-based pagination and optional filters.
    async fn query_messages(&self, query: MessageQuery) -> Result<MessagePage, MessageRepoError>;

    /// StateMachine panel/prompt/output history, without consulting workflow data.
    /// Filter Session/environment, audience and prompt display before LIMIT;
    /// published chat results remain in ordinary history. Full/Bot hides prompts.
    /// Newest first by (created_at, session_seq), exclusive composite cursor,
    /// bounded to 1..=1000 rows plus one lookahead. Preserve legacy stored IDs.
    async fn list_state_machine_history(
        &self, group_id: &str, session_id: &str, human_view: Option<HumanMessageView>,
        before: Option<(u64, i64)>, limit: u32,
    ) -> Result<MessagePage, MessageRepoError> {
        let _ = (group_id, session_id, human_view, before, limit);
        Err(MessageRepoError::StorageError("StateMachine history reads are not configured".into()))
    }

    /// Get a single message by its global unique id.
    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError>;

    /// Get the current max session_seq for a session (0 if no messages).
    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError>;

    /// Direct-read session history with the full legacy visibility predicates
    /// plus cursor-based pagination (replaces V1's offset/limit + total path).
    ///
    /// Sort order is the legacy `created_at DESC, session_seq DESC` (newest
    /// first); `before` is an exclusive composite `(created_at, session_seq)`
    /// cursor for the next page so messages sharing a `created_at` at a page
    /// boundary are not permanently skipped (VYQHI).
    ///
    /// VSN7A/VHxMU/VUlao — fix the V1 message-history regressions in one
    /// place:
    /// - Full 3-state [`MessageOwnerFilter`] (`Any` / `IsNull` /
    ///   `Eq(owner)`). Callers (the V1 session facade) reuse the legacy
    ///   `bcs-message` visibility helper so the ManagerWorker public-only
    ///   (`IsNull`) case is now expressible, fixing VUlai.
    /// - `visible_from_seq` (spec §5.2 new-participant join cutoff).
    /// - `env` isolation on read (VUlao): the store filters by its own
    ///   configured `env` so a dev/session store cannot leak another env's
    ///   messages. There is no `env` parameter because the store owns its env
    ///   (store-per-env architecture, matching the existing INSERT behavior).
    /// - Cursor pagination with `has_more` instead of a separate `COUNT(*)`
    ///   estimate (VHxMU); `next_cursor` is the last returned message's
    ///   `(created_at, session_seq)` when `has_more` is true.
    ///
    /// Default returns an empty page so noop/test impls keep compiling; real
    /// impls (memory + mysql) override this.
    async fn list_session_history(
        &self,
        session_id: &str,
        owner_filter: MessageOwnerFilter,
        visible_from_seq: Option<i64>,
        human_view: Option<HumanMessageView>,
        before: Option<(u64, i64)>,
        limit: u32,
    ) -> ServiceResult<MessagePage> {
        let _ = (
            session_id,
            owner_filter,
            visible_from_seq,
            human_view,
            before,
            limit,
        );
        Ok(MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }
}

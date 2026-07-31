//! Message history repository port.
//!
//! Persistence contract for session-level chat message history.
//! Implementations: MemoryMessageRepo (local dev/test), MySqlMessageStore (production).

use async_trait::async_trait;
use bcs_domain::{MessagePage, MessageQuery, NewMessage, PersistedMessage};

use crate::types::ServiceResult;

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
    /// Append a message to a session. Allocates `session_seq` atomically.
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError>;

    /// Query messages with cursor-based pagination and optional filters.
    async fn query_messages(
        &self,
        query: MessageQuery,
    ) -> Result<MessagePage, MessageRepoError>;

    /// Get a single message by its global unique id.
    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError>;

    /// Get the current max session_seq for a session (0 if no messages).
    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError>;

    /// List messages for a session ordered by `session_seq` ASCENDING, with
    /// offset/limit pagination, plus the total count for that session.
    ///
    /// Used by the V1 message history endpoint which presents chronological
    /// (oldest-first) history with a `total` field. This is a NEW method that
    /// does NOT replace [`MessageRepoPort::query_messages`] (compat): the old
    /// cursor-based DESC query is unchanged.
    ///
    /// VSN7A/VHxMU — message-history visibility predicates. Both filters are
    /// applied BEFORE pagination so pages stay consistent, and `total` is the
    /// filtered count (matches the same filters), not the raw session count:
    /// - `visible_from_seq`: when set, only messages with
    ///   `session_seq >= visible_from_seq` are returned (spec §5.2 new-participant
    ///   cutoff so a late joiner cannot read pre-join history).
    /// - `owner_bot_id`: when set, only messages whose `owner_bot_id` equals
    ///   the given bot are returned (ManagerWorker worker self-message view).
    ///   `None` means "any owner" (no owner filtering). NOTE: the public-only
    ///   (`owner_bot_id IS NULL`) isolation for ManagerWorker non-worker
    ///   viewers is intentionally NOT expressible with `Option<&str>`; the V1
    ///   session facade documents that case as deferred to the group history
    ///   path (`bcs-message`'s `MessageService`) which owns the full
    ///   `MessageOwnerFilter` enum.
    ///
    /// Default returns an empty page so noop/test impls keep compiling; real
    /// impls (memory + mysql) override this.
    async fn list_session_messages_by_seq(
        &self,
        session_id: &str,
        offset: u64,
        limit: u64,
        visible_from_seq: Option<i64>,
        owner_bot_id: Option<&str>,
    ) -> ServiceResult<(Vec<PersistedMessage>, u64)> {
        let _ = (session_id, offset, limit, visible_from_seq, owner_bot_id);
        Ok((Vec::new(), 0))
    }
}
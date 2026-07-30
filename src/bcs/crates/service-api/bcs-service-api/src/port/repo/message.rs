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
    /// Returns `(messages, total)` where `total` is the number of messages in
    /// the session BEFORE pagination is applied.
    ///
    /// Default returns an empty page so noop/test impls keep compiling; real
    /// impls (memory + mysql) override this.
    async fn list_session_messages_by_seq(
        &self,
        session_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<(Vec<PersistedMessage>, u64)> {
        let _ = (session_id, offset, limit);
        Ok((Vec::new(), 0))
    }
}
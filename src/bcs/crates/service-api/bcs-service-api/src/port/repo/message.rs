//! Message history repository port.
//!
//! Persistence contract for session-level chat message history.
//! Implementations: MemoryMessageRepo (local dev/test), MySqlMessageStore (production).

use async_trait::async_trait;
use bcs_domain::{MessageOwnerFilter, MessagePage, MessageQuery, NewMessage, PersistedMessage};

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
        before: Option<(u64, i64)>,
        limit: u32,
    ) -> ServiceResult<MessagePage> {
        let _ = (session_id, owner_filter, visible_from_seq, before, limit);
        Ok(MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }
}
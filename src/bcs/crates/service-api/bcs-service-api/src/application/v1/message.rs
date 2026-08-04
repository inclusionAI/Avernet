use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageSenderKind {
    Bot,
    Human,
    System,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionMessageKind {
    Text,
    System,
}

/// A single message in a session transcript.
///
/// Note: the identifier field is `id` (not `message_id`) per the V1 contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionMessage {
    pub id: String,
    pub session_seq: i64,
    pub sender_id: String,
    pub sender_type: MessageSenderKind,
    pub kind: SessionMessageKind,
    pub content: String,
    pub created_at: u64,
}

#[derive(Debug, Clone)]
pub struct ListSessionMessages {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    /// Opaque composite cursor for cursor-based pagination (VYQHI). Encoded
    /// by the V1 facade as `"created_at:session_seq"` (e.g. `"1234567890:42"`)
    /// so messages sharing a `created_at` at a page boundary are not skipped.
    /// Omit (or pass `None`) on the first page; on subsequent pages pass the
    /// previous response's `next_cursor`.
    pub before: Option<String>,
    pub limit: u64,
    /// Optional viewer identity for message history visibility scoping. Omit
    /// for the authenticated User's `human_<id>` Participant view, explicitly
    /// pass that same Human Actor ID, or pass an exact-`created_by` owned Bot.
    /// The selected Actor must be a Session Participant.
    pub view_bot_id: Option<String>,
}

/// Cursor-based session message history page returned by the V1
/// `list_session_messages` operation.
///
/// Replaces the legacy `Page<SessionMessage>` (`items/total/offset/limit`)
/// with the legacy direct-read shape: `messages` in `created_at DESC,
/// session_seq DESC` order, an opaque composite `next_cursor`
/// (`"created_at:session_seq"`, present only when `has_more` is true — pass it
/// back as the `before` query param to fetch the next page), and `has_more`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionMessagePage {
    pub messages: Vec<SessionMessage>,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

/// Transport-independent session message use cases for BCN OpenAPI v1.
///
/// Delivery adapters translate HTTP requests into these queries. The trait is
/// object-safe so an `Arc<dyn SessionMessageService>` can be shared across
/// routes.
#[async_trait]
pub trait SessionMessageService: Send + Sync {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<SessionMessagePage, ApplicationError>;
}

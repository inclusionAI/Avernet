use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::group::Page;
use super::{ApplicationError, Principal};

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
    pub principal: Principal,
    pub session_id: String,
    pub offset: u64,
    pub limit: u64,
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
    ) -> Result<Page<SessionMessage>, ApplicationError>;
}

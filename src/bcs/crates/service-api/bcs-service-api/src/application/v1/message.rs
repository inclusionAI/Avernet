use async_trait::async_trait;

use super::{ApplicationError, AuthenticatedCaller};
use crate::core::GroupMessage;

#[derive(Debug, Clone)]
pub struct ListSessionMessages {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    /// Exclusive millisecond timestamp bound used by the existing legacy
    /// Session-history capability. Omit on the first page.
    pub before: Option<u64>,
    pub limit: u64,
    /// Optional viewer identity for message history visibility scoping. Omit
    /// for the authenticated User's `human_<id>` Participant view, explicitly
    /// pass that same Human Actor ID, or pass an exact-`created_by` owned Bot.
    /// The selected Actor must be a Session Participant.
    pub view_bot_id: Option<String>,
}

/// Transport-independent session message use cases for BCN OpenAPI v1.
///
/// Delivery adapters translate HTTP requests into these queries. The trait is
/// object-safe so an `Arc<dyn SessionMessageService>` can be shared across
/// routes.
#[async_trait]
pub trait SessionMessageService: Send + Sync {
    async fn list(&self, query: ListSessionMessages)
    -> Result<Vec<GroupMessage>, ApplicationError>;
}

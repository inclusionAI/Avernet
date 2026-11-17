use async_trait::async_trait;

use super::{ApplicationError, AuthenticatedCaller};
use crate::MessageHistoryOptions;
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

/// Send-into-Session use case (BCN OpenAPI v1): the authenticated caller
/// speaks into an existing Session's collaboration flow.
#[derive(Debug, Clone)]
pub struct SendSessionMessage {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    /// Non-empty message body. Adapters reject empty input before the
    /// command crosses this boundary.
    pub message: String,
}

/// Observable outcome of one accepted send-into-Session. Mirrors what
/// the OpenAPI v1 envelope projects; transportneutral so tests can
/// assert without HTTP types.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct SendSessionMessageOutcome {
    pub session_id: String,
    pub group_id: String,
    /// The actor identity that spoke (an authenticated Human speaks as
    /// its own `human_<user.id>` actor; a Bot caller speaks as itself).
    pub sender: String,
    /// Recipients that admitted the message, following the existing
    /// group-chat routing semantics.
    pub delivered_count: u64,
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

    async fn list_with_options(
        &self,
        query: ListSessionMessages,
        _options: MessageHistoryOptions,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        self.list(query).await
    }

    /// Send a message into an existing Session's collaboration flow.
    /// Defaults to a fail-closed internal error so Noop test doubles keep
    /// compiling; production wiring overrides it in `bcs-app-session`.
    async fn send(
        &self,
        _command: SendSessionMessage,
    ) -> Result<SendSessionMessageOutcome, ApplicationError> {
        Err(ApplicationError::internal(
            "session message send is not configured",
        ))
    }
}

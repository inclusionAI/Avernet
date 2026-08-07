//! Core system-message contracts.

use async_trait::async_trait;
use bcs_domain::{Group, Participant};

use crate::{BotRegistryCoreService, ServiceError, ServiceResult};

pub use bcs_domain::{SystemGroupMessage, SystemMessageEvent, SystemMessageEventKind};

pub const BCS_SYSTEM_MESSAGE: &str = "bcs-system-message";

#[async_trait]
pub trait SystemMessageProducerService: Send + Sync {
    fn kind(&self) -> SystemMessageEventKind;

    /// Produce messages for a system-message event.
    ///
    /// Returns `(bot_messages, user_message)`:
    /// - `bot_messages`: per-bot delivery messages (semantics unchanged). The
    ///   dispatcher persists one history record per recipient with
    ///   `owner_bot_id = recipient` and delivers each via `chat.send`/`chat.inject`.
    /// - `user_message`: single user-facing text used ONLY for the frontend
    ///   WebSocket session broadcast (NOT persisted); `None` when the event has
    ///   no user-facing content.
    ///
    /// Producers must return `None` (never `Some("")`) when the event has no
    /// non-empty user-facing text, so the dispatcher's non-empty check and the
    /// producer responsibility stay aligned. An empty `bot_messages` does NOT
    /// block a non-empty `user_message` (e.g. last bot leaving).
    async fn produce(
        &self,
        event: &SystemMessageEvent,
        group: &Group,
        registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> (Vec<SystemGroupMessage>, Option<String>);
}

#[derive(Debug)]
pub struct SystemMessageRecipientResult {
    pub recipient_id: String,
    pub delivered: bool,
    pub error: Option<ServiceError>,
}

#[derive(Debug)]
pub struct SystemMessageDispatchOutcome {
    pub total_recipients: usize,
    pub successful_deliveries: usize,
    pub failed_deliveries: usize,
    pub recipient_results: Vec<SystemMessageRecipientResult>,
}

#[async_trait]
pub trait SystemMessageDispatcherService: Send + Sync {
    async fn dispatch(
        &self,
        event: SystemMessageEvent,
        group: &Group,
        session_id: &str,
        participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome>;
}
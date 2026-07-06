//! Core system-message contracts.

use async_trait::async_trait;
use bcs_domain::{Group, Participant};

use crate::{BotRegistryCoreService, ServiceError, ServiceResult};

pub use bcs_domain::{SystemGroupMessage, SystemMessageEvent, SystemMessageEventKind};

pub const BCS_SYSTEM_MESSAGE: &str = "bcs-system-message";

#[async_trait]
pub trait SystemMessageProducerService: Send + Sync {
    fn kind(&self) -> SystemMessageEventKind;

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        group: &Group,
        registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> Vec<SystemGroupMessage>;
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
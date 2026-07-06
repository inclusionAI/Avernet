//! Producer for `SystemMessageEventKind::GenericNotification`.
//!
//! Broadcasts the notification message to all bot participants in the group.

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, Participant, SystemMessageEvent, SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService};

/// Produces system messages for generic notifications.
pub struct GenericNotificationMessageProducer;

#[async_trait]
impl SystemMessageProducerService for GenericNotificationMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::GenericNotification
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        let SystemMessageEvent::GenericNotification {
            message, receivers, ..
        } = event
        else {
            return vec![];
        };
        let recipients: Vec<String> = if receivers.is_empty() {
            participants
                .iter()
                .filter(|p| p.is_bot())
                .map(|p| p.bot_uuid.clone())
                .collect()
        } else {
            receivers.iter().map(|p| p.bot_uuid.clone()).collect()
        };
        if recipients.is_empty() {
            return vec![];
        }
        vec![SystemGroupMessage {
            recipients,
            message: message.clone(),
            delivery_type: DeliveryType::Inject,
        }]
    }
}
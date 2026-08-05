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
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::GenericNotification {
            message, receivers, ..
        } = event
        else {
            return (vec![], None);
        };
        let user_message = if message.trim().is_empty() {
            None
        } else {
            Some(message.clone())
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
        let bot_messages = if recipients.is_empty() {
            vec![]
        } else {
            vec![SystemGroupMessage {
                recipients,
                message: message.clone(),
                delivery_type: DeliveryType::Inject,
            }]
        };
        (bot_messages, user_message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{Group, Participant, ParticipantRole};
    use bcs_test_support::NoopBotRegistryCoreService;

    fn group_with(bot: &str) -> Group {
        Group::new("g1", bot, vec![Participant::bot(bot, ParticipantRole::Driver)])
    }

    #[tokio::test]
    async fn generic_emits_user_message_equal_to_event_message() {
        let group = group_with("bot-a");
        let event = SystemMessageEvent::GenericNotification {
            group_id: "g1".into(),
            message: "维护开始".into(),
            receivers: vec![],
        };
        let (messages, user_message) = GenericNotificationMessageProducer
            .produce(&event, &group, &NoopBotRegistryCoreService, &group.participants)
            .await;
        assert_eq!(messages.len(), 1);
        assert_eq!(user_message.as_deref(), Some("维护开始"));
    }

    #[tokio::test]
    async fn generic_empty_message_yields_none_user_message() {
        let group = group_with("bot-a");
        let event = SystemMessageEvent::GenericNotification {
            group_id: "g1".into(),
            message: String::new(),
            receivers: vec![],
        };
        let (messages, user_message) = GenericNotificationMessageProducer
            .produce(&event, &group, &NoopBotRegistryCoreService, &group.participants)
            .await;
        assert_eq!(messages.len(), 1);
        assert_eq!(user_message, None, "empty message → None, never Some(\"\")");
    }

    #[tokio::test]
    async fn generic_no_recipients_still_emits_user_message() {
        let group = Group::new("g1", "bot-a", vec![]);
        let event = SystemMessageEvent::GenericNotification {
            group_id: "g1".into(),
            message: "维护开始".into(),
            receivers: vec![],
        };
        let (messages, user_message) = GenericNotificationMessageProducer
            .produce(&event, &group, &NoopBotRegistryCoreService, &group.participants)
            .await;
        assert!(messages.is_empty());
        assert_eq!(user_message.as_deref(), Some("维护开始"));
    }
}
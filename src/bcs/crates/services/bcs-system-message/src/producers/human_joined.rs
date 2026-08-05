//! Producer for `SystemMessageEventKind::HumanJoined`.
//!
//! When a human actor joins a group/session via invite link, this producer
//! generates a short notification delivered to all other bot participants
//! as `Inject` (no `Send` — bots observe silently).

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, Participant, PersistMode, SystemMessageEvent, SystemMessageEventKind,
    SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService};

pub struct HumanJoinedMessageProducer;

impl HumanJoinedMessageProducer {
    pub fn new() -> Self {
        Self
    }
}

#[async_trait]
impl SystemMessageProducerService for HumanJoinedMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::HumanJoined
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::HumanJoined { actor, .. } = event else {
            return (vec![], None);
        };

        let display_name = actor
            .bot_name
            .as_deref()
            .unwrap_or(&actor.bot_uuid);

        let message = format!("{}({}) 已加入协作群", display_name, actor.bot_uuid);
        let user_message = Some(message.clone());

        let recipients: Vec<String> = participants
            .iter()
            .filter(|p| p.is_bot() && p.bot_uuid != actor.bot_uuid)
            .map(|p| p.bot_uuid.clone())
            .collect();

        // Identical text for every recipient: persist a single public record
        // (owner = None) that human viewers also read in history.
        let bot_messages = vec![SystemGroupMessage {
            recipients,
            message,
            delivery_type: DeliveryType::Inject,
            persist: PersistMode::Public,
        }];
        (bot_messages, user_message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ParticipantRole};
    use bcs_test_support::NoopBotRegistryCoreService;

    #[tokio::test]
    async fn human_joined_emits_user_message() {
        let group = Group::new("g1", "bot-1", vec![Participant::bot("bot-1", ParticipantRole::Driver)]);
        let actor = Participant {
            bot_uuid: "human_42".into(),
            bot_name: Some("Alice".into()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: None,
        };
        let event = SystemMessageEvent::HumanJoined { group_id: "g1".into(), actor };

        let (messages, user_message) = HumanJoinedMessageProducer::new()
            .produce(&event, &group, &NoopBotRegistryCoreService, &group.participants)
            .await;

        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].recipients, vec!["bot-1".to_string()]);
        assert_eq!(user_message.as_deref(), Some("Alice(human_42) 已加入协作群"));
    }

    #[tokio::test]
    async fn human_joined_emits_user_message_even_with_no_bot_recipients() {
        let group = Group::new("g1", "bot-1", vec![]);
        let actor = Participant {
            bot_uuid: "human_42".into(),
            bot_name: Some("Alice".into()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: None,
        };
        let event = SystemMessageEvent::HumanJoined { group_id: "g1".into(), actor };

        let (messages, user_message) = HumanJoinedMessageProducer::new()
            .produce(&event, &group, &NoopBotRegistryCoreService, &group.participants)
            .await;

        // No bot recipients, but the notice is still a public history record
        // for human viewers (persisted with owner = None by the dispatcher).
        assert_eq!(messages.len(), 1);
        assert!(messages[0].recipients.is_empty());
        assert_eq!(messages[0].persist, PersistMode::Public);
        assert_eq!(user_message.as_deref(), Some("Alice(human_42) 已加入协作群"));
    }
}
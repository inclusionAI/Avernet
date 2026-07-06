//! Producer for `SystemMessageEventKind::BotLeft`.
//!
//! When a participant leaves a group this producer generates a short
//! notification delivered to the remaining group members.

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, Participant, SystemMessageEvent, SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService};

/// Produces system messages when a participant leaves a group.
pub struct BotLeftMessageProducer;

#[async_trait]
impl SystemMessageProducerService for BotLeftMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::BotLeft
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        _group: &Group,
        registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        let SystemMessageEvent::BotLeft { actor, .. } = event else {
            return vec![];
        };

        let left_id = actor.bot_uuid.clone();
        let registered = registry.get(&left_id).await;
        let name = registered
            .as_ref()
            .and_then(|b| b.capabilities.name.clone())
            .unwrap_or_else(|| left_id.clone());
        let message = format!("{}({}) 已退出协作群", name, left_id);

        let recipients: Vec<String> = participants
            .iter()
            .filter(|p| p.bot_uuid != left_id)
            .filter(|p| p.is_bot())
            .map(|p| p.bot_uuid.clone())
            .collect();
        if recipients.is_empty() {
            return vec![];
        }

        vec![SystemGroupMessage {
            recipients,
            message,
            delivery_type: DeliveryType::Inject,
        }]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, Participant, ParticipantMode, ParticipantRole};
    use bcs_test_support::NoopBotRegistryCoreService;

    #[test]
    fn bot_left_kind_matches() {
        let producer = BotLeftMessageProducer;
        assert_eq!(producer.kind(), SystemMessageEventKind::BotLeft);
    }

    #[tokio::test]
    async fn bot_left_produces_leave_message_for_other_bots() {
        let registry = NoopBotRegistryCoreService;
        let group = Group {
            id: "g1".into(),
            label: None,
            status: bcs_domain::GroupStatus::Active,
            driver_bot: "bot-2".into(),
            originator: Some("bot-2".into()),
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "bot-1".into(),
                    bot_name: Some("测试Bot".into()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::Auto),
                },
                Participant {
                    bot_uuid: "bot-2".into(),
                    bot_name: Some("其他Bot".into()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::Auto),
                },
            ],
            messages: vec![],
            workspace: Default::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_domain::GroupKind::Normal,
            dm_pair_key: None,
            group_strategy: bcs_domain::GroupStrategy::Chat,
            service_spec: None,
            version: 0,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        };

        let event = SystemMessageEvent::BotLeft {
            group_id: "g1".into(),
            actor: Participant {
                bot_uuid: "bot-1".into(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: None,
            },
        };

        let messages = BotLeftMessageProducer
            .produce(&event, &group, &registry, &group.participants)
            .await;

        assert_eq!(messages.len(), 1);
        // Noop registry returns None, so name falls back to bot_uuid
        assert!(messages[0].message.contains("已退出协作群"));
        assert!(messages[0].message.contains("bot-1"));
        assert_eq!(messages[0].recipients, vec!["bot-2"]);
        assert_eq!(messages[0].delivery_type, DeliveryType::Inject);
    }
}

//! Producer for `SystemMessageEventKind::BotHiddenNotice`.

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, Participant, PersistMode, SystemMessageEvent, SystemMessageEventKind,
    SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService};

pub struct BotHiddenNoticeProducer;

#[async_trait]
impl SystemMessageProducerService for BotHiddenNoticeProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::BotHiddenNotice
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::BotHiddenNotice {
            mentioner_bot_id,
            hidden_bot_name,
            ..
        } = event
        else {
            return (vec![], None);
        };

        let message = format!("{} 已设置为「不可协作」", hidden_bot_name);
        let user_message = Some(message.clone());
        // The notice text is identical for every recipient, so persist a
        // single public record (owner = None) with the mentioner's message;
        // the others' copy is delivered but not persisted again.
        let mut messages = vec![SystemGroupMessage {
            recipients: vec![mentioner_bot_id.clone()],
            message: message.clone(),
            delivery_type: DeliveryType::Send,
            persist: PersistMode::Public,
        }];

        let others: Vec<String> = participants
            .iter()
            .filter(|p| p.bot_uuid != *mentioner_bot_id)
            .filter(|p| p.is_bot())
            .map(|p| p.bot_uuid.clone())
            .collect();
        if !others.is_empty() {
            messages.push(SystemGroupMessage {
                recipients: others,
                message,
                delivery_type: DeliveryType::Inject,
                persist: PersistMode::Skip,
            });
        }

        (messages, user_message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, Participant, ParticipantMode, ParticipantRole};
    use bcs_test_support::NoopBotRegistryCoreService;

    #[test]
    fn kind_matches() {
        let producer = BotHiddenNoticeProducer;
        assert_eq!(producer.kind(), SystemMessageEventKind::BotHiddenNotice);
    }

    #[tokio::test]
    async fn produces_notice_for_mentioner_only() {
        let registry = NoopBotRegistryCoreService;
        let group = Group {
            id: "g1".into(),
            label: None,
            status: bcs_domain::GroupStatus::Active,
            driver_bot: "bot-driver".into(),
            originator: Some("bot-driver".into()),
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "bot-driver".into(),
                    bot_name: Some("Driver".into()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::Auto),
                },
                Participant {
                    bot_uuid: "bot-hidden".into(),
                    bot_name: Some("HiddenBot".into()),
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

        let event = SystemMessageEvent::BotHiddenNotice {
            group_id: "g1".into(),
            mentioner_bot_id: "bot-driver".into(),
            hidden_bot_name: "HiddenBot".into(),
        };

        let (messages, user_message) = BotHiddenNoticeProducer
            .produce(&event, &group, &registry, &group.participants)
            .await;

        assert_eq!(messages.len(), 2);
        // First message: Send to mentioner; carries the single public record.
        assert_eq!(messages[0].recipients, vec!["bot-driver"]);
        assert!(messages[0].message.contains("HiddenBot"));
        assert!(messages[0].message.contains("不可协作"));
        assert_eq!(messages[0].delivery_type, DeliveryType::Send);
        assert_eq!(messages[0].persist, PersistMode::Public);
        // Second message: Inject to other bots; identical text, not persisted.
        assert_eq!(messages[1].recipients, vec!["bot-hidden"]);
        assert_eq!(messages[1].delivery_type, DeliveryType::Inject);
        assert_eq!(messages[1].persist, PersistMode::Skip);
        assert_eq!(user_message.as_deref(), Some("HiddenBot 已设置为「不可协作」"));
    }

    #[tokio::test]
    async fn bot_hidden_emits_user_message_with_only_mentioner() {
        let registry = NoopBotRegistryCoreService;
        let group = Group {
            id: "g1".into(),
            label: None,
            status: bcs_domain::GroupStatus::Active,
            driver_bot: "bot-driver".into(),
            originator: Some("bot-driver".into()),
            routing_policy: None,
            context: None,
            participants: vec![Participant {
                bot_uuid: "bot-driver".into(),
                bot_name: Some("Driver".into()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            }],
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
        let event = SystemMessageEvent::BotHiddenNotice {
            group_id: "g1".into(),
            mentioner_bot_id: "bot-driver".into(),
            hidden_bot_name: "HiddenBot".into(),
        };
        let (messages, user_message) = BotHiddenNoticeProducer
            .produce(&event, &group, &registry, &group.participants)
            .await;
        // Only mentioner present → 1 Send to mentioner, no Inject to others.
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].recipients, vec!["bot-driver"]);
        assert_eq!(user_message.as_deref(), Some("HiddenBot 已设置为「不可协作」"));
    }
}

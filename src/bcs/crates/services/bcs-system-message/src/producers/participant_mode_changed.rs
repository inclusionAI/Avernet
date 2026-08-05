//! Producer for `SystemMessageEventKind::ParticipantModeChanged`.
//!
//! Generates a notification when a participant's collaboration mode changes.
//! Human join events (from=None, actor_kind=Human) produce a join message;
//! Bot join events with from=None should be handled by `BotJoined` instead.

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, DeliveryType, Group, Participant, ParticipantMode, SystemMessageEvent,
    SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService};

/// Produces system messages when a participant's mode changes.
pub struct ParticipantModeChangedMessageProducer;

#[async_trait]
impl SystemMessageProducerService for ParticipantModeChangedMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::ParticipantModeChanged
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::ParticipantModeChanged {
            actor_id: _,
            actor_name,
            actor_kind,
            from,
            to,
            ..
        } = event
        else {
            return (vec![], None);
        };

        if *actor_kind == ActorKind::Bot && from.is_none() {
            tracing::warn!(
                "ParticipantModeChanged with from=None for Bot; BotJoined should handle this"
            );
            return (vec![], None);
        }

        let content = match (*actor_kind, *to) {
            (ActorKind::Human, ParticipantMode::Present) => {
                format!("用户 {} 已加入协作群", actor_name)
            }
            (ActorKind::Human, ParticipantMode::Absent) => {
                format!("用户 {} 已退出协作群", actor_name)
            }
            (ActorKind::Bot, ParticipantMode::Muted) => {
                format!("Bot {} 已切换成禁言模式", actor_name)
            }
            (ActorKind::Bot, ParticipantMode::Auto) => {
                format!("Bot {} 已切换成自动发言模式", actor_name)
            }
            _ => format!("{} 的状态变成了 {:?}", actor_name, to),
        };
        let user_message = Some(content.clone());

        let recipients: Vec<String> = participants
            .iter()
            .filter(|p| p.is_bot())
            .map(|p| p.bot_uuid.clone())
            .collect();

        let bot_messages = if recipients.is_empty() {
            vec![]
        } else {
            vec![SystemGroupMessage {
                recipients,
                message: content,
                delivery_type: DeliveryType::Inject,
            }]
        };
        (bot_messages, user_message)
    }
}
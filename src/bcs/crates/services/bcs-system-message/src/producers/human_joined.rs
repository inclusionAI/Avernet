//! Producer for `SystemMessageEventKind::HumanJoined`.
//!
//! When a human actor joins a group/session via invite link, this producer
//! generates a short notification delivered to all other bot participants
//! as `Inject` (no `Send` — bots observe silently).

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, Participant, SystemMessageEvent, SystemMessageEventKind,
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
    ) -> Vec<SystemGroupMessage> {
        let SystemMessageEvent::HumanJoined { actor, .. } = event else {
            return vec![];
        };

        let display_name = actor
            .bot_name
            .as_deref()
            .unwrap_or(&actor.bot_uuid);

        let message = format!("{}({}) 已加入协作群", display_name, actor.bot_uuid);

        let recipients: Vec<String> = participants
            .iter()
            .filter(|p| p.is_bot() && p.bot_uuid != actor.bot_uuid)
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
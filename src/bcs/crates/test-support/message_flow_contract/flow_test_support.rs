//! Composition fixture wiring the message-flow test doubles together. Items are
//! re-exported from `message_flow_contract_support`, keeping the public paths used
//! by the message-flow contract test binaries unchanged.
#![allow(dead_code)]

use std::sync::Arc;

use bcs_service_api::{ActorKind, Group, GroupCoreService, Participant, ParticipantRole};

use super::delivery_recordings::{RecordingBotDelivery, RecordingFrontendDelivery};
use super::group_core_fakes::FakeGroupCoreService;
use super::registry_routing_fakes::{FakeRegistryService, FakeRoutingCoreService};

pub struct FlowTestSupport {
    pub group: Arc<FakeGroupCoreService>,
    pub routing: Arc<FakeRoutingCoreService>,
    pub registry: Arc<FakeRegistryService>,
    pub bot_delivery: Arc<RecordingBotDelivery>,
    pub frontend_delivery: Arc<RecordingFrontendDelivery>,
}

impl FlowTestSupport {
    pub async fn new_group_with_driver_and_observer() -> Self {
        let group = Arc::new(FakeGroupCoreService::default());
        let routing = Arc::new(FakeRoutingCoreService::default());
        let registry = Arc::new(FakeRegistryService::default());
        let bot_delivery = Arc::new(RecordingBotDelivery::default());
        let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());

        registry.insert_named_actor("human_1", "Human One").await;
        registry.insert_named_actor("bot-driver", "Driver").await;
        registry
            .insert_named_actor("bot-observer", "Observer")
            .await;

        let session = Group::new(
            "group-1",
            "bot-driver",
            vec![
                bot_participant("bot-driver", "Driver", ParticipantRole::Driver),
                bot_participant("bot-observer", "Observer", ParticipantRole::Observer),
                Participant {
                    bot_uuid: "human_1".to_string(),
                    bot_name: Some("Human One".to_string()),
                    kind: None,
                    role: ParticipantRole::Observer,
                    actor_kind: ActorKind::Human,
                    mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
            ],
        );
        group.upsert(session).await.unwrap();
        group.increment_message_count("group-1").await.unwrap();

        Self {
            group,
            routing,
            registry,
            bot_delivery,
            frontend_delivery,
        }
    }
}

fn bot_participant(id: &str, name: &str, role: ParticipantRole) -> Participant {
    let mut participant = Participant::bot(id, role);
    participant.bot_name = Some(name.to_string());
    participant
}

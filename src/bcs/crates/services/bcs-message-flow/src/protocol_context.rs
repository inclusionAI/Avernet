use bcs_protocol::{GroupContextDeliveryType, GroupContextInput, GroupContextParticipant};
use bcs_service_api::{DeliveryType, Group, GroupStrategy, ParticipantRole};

pub(crate) fn group_context_input(group: &Group) -> GroupContextInput {
    GroupContextInput {
        session_id: group.id.clone(),
        driver_bot: group.driver_bot.clone(),
        originator: group.originator().to_string(),
        participants: group
            .participants
            .iter()
            .map(|participant| GroupContextParticipant {
                id: participant.bot_uuid.clone(),
                name: participant.bot_name.clone(),
                role: Some(participant_role_slug(participant.role).to_string()),
                is_bot: participant.is_bot(),
            })
            .collect(),
        bcs_session_id: None,
    }
}

pub(crate) fn group_type_wire(strategy: GroupStrategy) -> Option<String> {
    match strategy {
        GroupStrategy::ManagerWorker => Some("manager_worker".to_string()),
        GroupStrategy::StateMachine => Some("state_machine".to_string()),
        GroupStrategy::Chat => None,
    }
}

pub(crate) fn group_context_delivery_type(delivery_type: DeliveryType) -> GroupContextDeliveryType {
    match delivery_type {
        DeliveryType::Send => GroupContextDeliveryType::Send,
        DeliveryType::Inject => GroupContextDeliveryType::Inject,
    }
}

fn participant_role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

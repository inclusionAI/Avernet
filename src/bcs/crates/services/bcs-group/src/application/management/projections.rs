//! Legacy response projections for the Group application service.

use super::*;

pub(crate) fn group_to_detail(group: DomainGroup) -> GroupDetailResult {
    group_to_detail_with_context(group, 0)
}

pub(crate) fn group_to_detail_with_context(group: DomainGroup, context_injected: u64) -> GroupDetailResult {
    let message_count = group.messages.len();
    GroupDetailResult {
        group_id: group.id,
        label: group.label,
        status: group.status,
        driver_bot_id: group.driver_bot,
        context: group.context,
        opening_message: group.opening_message,
        participants: group
            .participants
            .into_iter()
            .map(|participant| GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
                tags: participant.tags,
                message_view_scope: participant.message_view_scope,
            })
            .collect(),
        message_count,
        workspace: group.workspace,
        service_group_uuid: group.service_group_uuid,
        service_mode: group.service_mode,
        group_kind: group.group_kind,
        dm_pair_key: group.dm_pair_key,
        group_strategy: group.group_strategy,
        created_at: group.created_at,
        updated_at: group.updated_at,
        chat_url: None,
        context_injected,
        service_spec: group.service_spec.clone(),
        latest_running_session_id: None,
        initial_run: None,
        originator: group.originator,
        visibility: group.visibility.clone(),
        human_mention_notify_mode: group.human_mention_notify_mode,
    }
}

pub(crate) fn group_to_list_entry(group: DomainGroup) -> GroupListEntry {
    let participant_count = group.participants.len();
    let message_count = group.messages.len();
    GroupListEntry {
        group_id: group.id,
        label: group.label,
        driver_bot_id: group.driver_bot,
        originator: group.originator.clone(),
        context: group.context,
        participants: group
            .participants
            .into_iter()
            .map(|participant| GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
                tags: participant.tags,
                message_view_scope: participant.message_view_scope,
            })
            .collect(),
        participant_count,
        message_count,
        created_at: group.created_at,
        updated_at: group.updated_at,
        group_kind: group.group_kind,
        group_strategy: group.group_strategy,
        visibility: group.visibility.clone(),
        human_mention_notify_mode: group.human_mention_notify_mode,
    }
}

pub(crate) fn participant_role_to_wire(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

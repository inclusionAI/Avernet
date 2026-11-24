//! Legacy response projections for the Group application service.

use super::*;

pub(crate) fn group_to_detail(group: DomainGroup) -> GroupDetailResult {
    group_to_detail_with_context(group, 0)
}

pub(crate) fn group_to_detail_with_context(group: DomainGroup, context_injected: u64) -> GroupDetailResult {
    let message_count = group.messages.len();
    GroupDetailResult {
        catalog_labels:Default::default(),
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
        service_group_uuid: group.service_group_uuid,
        service_mode: group.service_mode,
        group_kind: group.group_kind,
        dm_pair_key: group.dm_pair_key,
        group_strategy: group.group_strategy,
        created_at: group.created_at,
        updated_at: group.updated_at,
        chat_url: None,
        context_injected,
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

impl GroupManagement {
    pub(super) async fn with_detail_catalog_labels(&self,mut detail:GroupDetailResult)->Result<GroupDetailResult,GroupUseCaseError> {
        if detail.group_kind==GroupKind::Normal && matches!(detail.group_strategy,GroupStrategy::ManagerWorker|GroupStrategy::StateMachine) {
            let snapshot = self.group.read_detail_snapshot(&detail.group_id).await?
                .ok_or_else(|| ServiceError::GroupNotFound(detail.group_id.clone()))?;
            let mut group = snapshot.group;
            let names = detail.participants.iter().map(|p| (p.bot_uuid.as_str(), p.bot_name.clone()))
                .collect::<std::collections::HashMap<_, _>>();
            for participant in &mut group.participants {
                if participant.bot_name.is_none() {
                    participant.bot_name = names.get(participant.bot_uuid.as_str()).cloned().flatten();
                }
            }
            backfill_bot_names(self.registry.as_ref(), &mut group).await;
            let mut coherent = group_to_detail_with_context(group, detail.context_injected);
            coherent.catalog_labels = snapshot.catalog_labels;
            // These are operation/session outputs, not persisted Group fields.
            coherent.chat_url = detail.chat_url;
            coherent.latest_running_session_id = detail.latest_running_session_id;
            coherent.initial_run = detail.initial_run;
            detail = coherent;
        }
        Ok(detail)
    }
}

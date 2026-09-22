//! In-memory Group eventful mutation application.
//!
//! Shared by the eventful mutation primitive so versioned updates match the
//! direct Patch semantics of the MySQL-backed store.
use super::*;

pub(super) fn apply_memory_group_mutation(
    group: &mut DomainGroup,
    mutation: &GroupEventfulMutation,
    mutated_at_ms: u64,
) -> ServiceResult<()> {
    match mutation {
        GroupEventfulMutation::PatchMutableFields(patch) => {
            if let Some(label) = &patch.label {
                group.label = Some(label.clone());
            }
            if let Some(context) = &patch.context {
                group.context = Some(context.clone());
            }
            if let Some(opening_message) = &patch.opening_message {
                group.opening_message = opening_message.clone();
            }
            if let Some(visibility) = &patch.visibility {
                group.visibility = visibility.clone();
            }
            if let Some(delivery) = patch.default_bot_final_delivery {
                group
                    .routing_policy
                    .get_or_insert_with(Default::default)
                    .default_bot_final_delivery = delivery;
            }
            if let Some(mode) = patch.human_mention_notify_mode {
                group.human_mention_notify_mode = mode;
            }
        }
        GroupEventfulMutation::UpdateStatus(status) => group.status = *status,
        GroupEventfulMutation::AddParticipant {
            participant,
            actor_is_public,
        } => {
            if group
                .participants
                .iter()
                .any(|existing| existing.bot_uuid == participant.bot_uuid)
            {
                return Err(ServiceError::Conflict(format!(
                    "Participant '{}' is already in Group '{}'",
                    participant.bot_uuid, group.id
                )));
            }
            if participant.is_bot() && group.visibility == "public" && !actor_is_public {
                return Err(ServiceError::ExistNonPublicBots {
                    bots: vec![(participant.bot_uuid.clone(), participant.bot_name.clone())],
                });
            }
            group.participants.push(participant.clone());
        }
        GroupEventfulMutation::RemoveParticipant { actor_id } => {
            let initial_len = group.participants.len();
            group
                .participants
                .retain(|participant| participant.bot_uuid != *actor_id);
            if group.participants.len() == initial_len {
                return Err(ServiceError::ParticipantNotFound(actor_id.clone()));
            }
        }
        GroupEventfulMutation::UpdateParticipantMode { actor_id, mode } => {
            let participant = group
                .participants
                .iter_mut()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            if participant.effective_mode() == *mode {
                return Err(ServiceError::Conflict(format!(
                    "Participant '{actor_id}' mode is already {mode:?}"
                )));
            }
            participant.mode = Some(*mode);
        }
        GroupEventfulMutation::UpdateParticipantMessageViewScope {
            actor_id,
            message_view_scope,
            mode,
        } => {
            let participant = group
                .participants
                .iter_mut()
                .find(|participant| participant.bot_uuid == *actor_id)
                .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.clone()))?;
            if !message_view_scope.is_valid_for(participant.actor_kind) {
                return Err(ServiceError::InvalidOperation {
                    message: "Bot participants must use full message_view_scope".to_string(),
                    request_id: None,
                });
            }
            let scope_changed = participant.message_view_scope != *message_view_scope;
            let mode_changed = mode.is_some_and(|mode| participant.effective_mode() != mode);
            if !scope_changed && !mode_changed {
                return Err(ServiceError::Conflict(format!(
                    "Participant '{actor_id}' mode and message_view_scope are unchanged"
                )));
            }
            participant.message_view_scope = *message_view_scope;
            if let Some(mode) = mode {
                participant.mode = Some(*mode);
            }
        }
        GroupEventfulMutation::UpdateRoutingPolicy(policy) => {
            group.routing_policy = Some(policy.clone());
        }
        GroupEventfulMutation::UpdateServiceSpec(service_spec) => {
            group.service_spec = service_spec.clone();
        }
        GroupEventfulMutation::Delete => {}
    }
    group.version = group
        .version
        .checked_add(1)
        .ok_or_else(|| ServiceError::Conflict(format!("Group '{}' version overflow", group.id)))?;
    group.updated_at = mutated_at_ms;
    Ok(())
}

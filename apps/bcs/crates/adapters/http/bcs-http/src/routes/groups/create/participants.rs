//! Create caller resolution, DM target parsing and participant mapping/validation.

use super::*;

pub(crate) async fn resolve_group_create_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<Option<String>, HttpAdapterError> {
    if let Some(caller) = state.bot_uuid_from_headers(headers).await {
        validate_container_header(state, headers, &caller)?;
        return Ok(Some(caller));
    }

    Ok(state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{}", staff_no)))
}

pub(crate) fn dm_target_actor_id(req: &CreateGroupRequest) -> Result<&str, HttpAdapterError> {
    if let Some(target_actor_id) = req.target_actor_id.as_deref() {
        if target_actor_id.trim().is_empty() {
            return Err(HttpAdapterError::BadRequest(
                "target_actor_id must not be empty for DM group creation".to_string(),
            ));
        }
        if req.participants.len() > 1 {
            return Err(HttpAdapterError::BadRequest(
                "DM group creation accepts at most one participant target".to_string(),
            ));
        }
        if let Some(participant) = req.participants.first() {
            if participant.bot_uuid != target_actor_id {
                return Err(HttpAdapterError::BadRequest(
                    "participants[0].bot_uuid must match target_actor_id for DM group creation"
                        .to_string(),
                ));
            }
        }
        return Ok(target_actor_id);
    }

    match req.participants.as_slice() {
        [participant] if !participant.bot_uuid.trim().is_empty() => Ok(participant.bot_uuid.as_str()),
        [..] if req.participants.is_empty() => Err(HttpAdapterError::BadRequest(
            "target_actor_id is required for DM group creation".to_string(),
        )),
        [_] => Err(HttpAdapterError::BadRequest(
            "participants[0].bot_uuid must not be empty for DM group creation".to_string(),
        )),
        _ => Err(HttpAdapterError::BadRequest(
            "DM group creation requires exactly one participant target when target_actor_id is omitted"
                .to_string(),
        )),
    }
}

pub(crate) fn group_create_participants(
    req: &CreateGroupRequest,
    collaboration_definition: Option<&CollaborationDefinition>,
    driver_bot: &str,
    state_machine_group: bool,
) -> Result<Vec<GroupCreateParticipantCommand>, HttpAdapterError> {
    if !req.participants.is_empty() {
        if state_machine_group
            && req
                .participants
                .iter()
                .any(|participant| participant.role.is_some())
        {
            return Err(HttpAdapterError::BadRequest(
                "state-machine group participants.role is inferred by BCS and must not be provided"
                    .to_string(),
            ));
        }
        if req.participant_bindings.is_empty() {
            validate_definition_has_legacy_bot_ids(collaboration_definition)?;
        }
        validate_participant_binding_members(req, collaboration_definition)?;
        return Ok(req
            .participants
            .iter()
            .map(|participant| GroupCreateParticipantCommand {
                bot_id: participant.bot_uuid.clone(),
                role: if state_machine_group {
                    Some(
                        inferred_participant_role_wire(&participant.bot_uuid, driver_bot)
                            .to_string(),
                    )
                } else {
                    participant.role.clone()
                },
                tags: normalize_participant_tags(&participant.tags),
                message_view_scope: participant.message_view_scope,
            })
            .collect());
    }

    if !req.participant_bindings.is_empty() {
        validate_participant_binding_members(req, collaboration_definition)?;
        return group_create_participants_from_runtime_bindings(req, driver_bot);
    }

    let Some(definition) = collaboration_definition else {
        return Ok(Vec::new());
    };

    let mut participants = Vec::new();
    let mut has_driver_bot = false;

    for (binding_id, binding) in &definition.participants {
        if binding.bcs_participant_role.is_some() {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must not define bcs_participant_role",
                binding_id
            )));
        }
        let Some(bot_id) = binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        else {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must define bot_id",
                binding_id
            )));
        };

        if bot_id == driver_bot {
            has_driver_bot = true;
        }

        let role = inferred_participant_role_wire(bot_id, driver_bot).to_string();

        if let Some(index) = participants
            .iter()
            .position(|participant: &GroupCreateParticipantCommand| participant.bot_id == bot_id)
        {
            participants[index].role = Some(role);
            continue;
        }

        participants.push(GroupCreateParticipantCommand {
            bot_id: bot_id.to_string(),
            role: Some(role),
            tags: Vec::new(),
            message_view_scope: None,
        });
    }

    if participants.is_empty() {
        return Err(HttpAdapterError::BadRequest(
            "collaboration_definition_yaml participants must contain at least one bot_id"
                .to_string(),
        ));
    }
    if !has_driver_bot {
        return Err(HttpAdapterError::BadRequest(
            "driver_bot must appear in collaboration_definition_yaml participants".to_string(),
        ));
    }

    Ok(participants)
}

pub(crate) fn group_create_participants_from_runtime_bindings(
    req: &CreateGroupRequest,
    driver_bot: &str,
) -> Result<Vec<GroupCreateParticipantCommand>, HttpAdapterError> {
    let mut participants = Vec::new();
    let mut seen = HashSet::new();
    let mut has_driver_bot = false;
    for (binding_id, binding) in &req.participant_bindings {
        validate_runtime_binding_wire(binding_id, binding)?;
        for raw_bot_id in &binding.bot_ids {
            let bot_id = raw_bot_id.trim();
            if bot_id == driver_bot {
                has_driver_bot = true;
            }
            if !seen.insert(bot_id.to_string()) {
                continue;
            }
            participants.push(GroupCreateParticipantCommand {
                bot_id: bot_id.to_string(),
                role: Some(inferred_participant_role_wire(bot_id, driver_bot).to_string()),
                tags: Vec::new(),
                message_view_scope: None,
            });
        }
    }
    if participants.is_empty() {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings must resolve to at least one bot participant".to_string(),
        ));
    }
    if !has_driver_bot {
        return Err(HttpAdapterError::BadRequest(
            "driver_bot must appear in participant_bindings".to_string(),
        ));
    }
    Ok(participants)
}

pub(crate) fn normalize_participant_tags(tags: &[String]) -> Vec<String> {
    tags.iter()
        .map(|tag| tag.trim().to_string())
        .filter(|tag| !tag.is_empty())
        .collect()
}

pub(crate) fn validate_participant_binding_members(
    req: &CreateGroupRequest,
    collaboration_definition: Option<&CollaborationDefinition>,
) -> Result<(), HttpAdapterError> {
    if req.participant_bindings.is_empty() {
        return Ok(());
    }
    let Some(definition) = collaboration_definition else {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings requires collaboration_definition_yaml".to_string(),
        ));
    };
    for binding_id in req.participant_bindings.keys() {
        if !definition.participants.contains_key(binding_id) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings contains undeclared slot: {binding_id}"
            )));
        }
    }
    let explicit_participants = req
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    for (binding_id, binding) in &req.participant_bindings {
        validate_runtime_binding_wire(binding_id, binding)?;
        if !explicit_participants.is_empty() {
            for bot_id in &binding.bot_ids {
                if !explicit_participants.contains(bot_id.trim()) {
                    return Err(HttpAdapterError::BadRequest(format!(
                        "participant_bindings.{binding_id} bot_id is not in participants: {}",
                        bot_id.trim()
                    )));
                }
            }
        }
    }
    Ok(())
}

pub(crate) fn validate_state_machine_runtime_bindings_before_create(
    collaboration_definition: Option<&CollaborationDefinition>,
    participants: &[GroupCreateParticipantCommand],
    participant_bindings: &BTreeMap<String, bcs_protocol::ParticipantBindingInfo>,
) -> Result<(), HttpAdapterError> {
    if participant_bindings.is_empty() {
        return Ok(());
    }
    let Some(definition) = collaboration_definition else {
        return Ok(());
    };
    let referenced_slots = referenced_participant_slots(definition)?;
    let group_participants = participants
        .iter()
        .map(|participant| participant.bot_id.as_str())
        .collect::<HashSet<_>>();
    let mut resolved_slots = HashSet::new();

    for (slot, definition_binding) in &definition.participants {
        let bot_ids = if let Some(runtime_binding) = participant_bindings.get(slot) {
            participant_binding_bot_ids(slot, runtime_binding)?
        } else if let Some(bot_id) = definition_binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        {
            vec![bot_id.to_string()]
        } else if definition_binding.required || referenced_slots.contains(slot) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant slot {slot} has no runtime binding or legacy bot_id"
            )));
        } else {
            continue;
        };

        for bot_id in &bot_ids {
            if !group_participants.contains(bot_id.as_str()) {
                return Err(HttpAdapterError::BadRequest(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                )));
            }
        }
        if referenced_slots.contains(slot) && bot_ids.len() != 1 {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant slot {slot} is assigned to a node and must resolve to exactly one bot in the current runtime"
            )));
        }
        resolved_slots.insert(slot.clone());
    }

    for slot in &referenced_slots {
        if !resolved_slots.contains(slot) {
            return Err(HttpAdapterError::BadRequest(format!(
                "node assignee binding has no resolved participant slot: {slot}"
            )));
        }
    }
    Ok(())
}

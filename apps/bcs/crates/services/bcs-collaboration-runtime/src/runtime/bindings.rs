use super::*;
pub(super) fn resolve_participant_bindings(
    group: &Group,
    compiled: &CompiledStateMachine,
    group_binding: Option<&GroupRuntimeBinding>,
) -> Result<BTreeMap<String, ResolvedParticipantBinding>, CollaborationRuntimeError> {
    let definition = &compiled.definition;
    let runtime_bindings = group_binding
        .map(|binding| &binding.participant_bindings)
        .filter(|bindings| !bindings.is_empty());
    if let Some(bindings) = runtime_bindings {
        for binding_key in bindings.keys() {
            if !definition.participants.contains_key(binding_key) {
                return invalid_participant_binding(format!(
                    "participant_bindings contains undeclared slot: {binding_key}"
                ));
            }
        }
    }

    let referenced_slots = referenced_participant_slots(compiled)?;
    let group_participants = group
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    let mut resolved = BTreeMap::new();
    for (slot, definition_binding) in &definition.participants {
        let (source, binding_source, bot_ids) = if let Some(runtime_binding) =
            runtime_bindings.and_then(|bindings| bindings.get(slot))
        {
            (
                "group_runtime_binding".to_string(),
                Some(runtime_binding.source.clone()),
                runtime_binding_bot_ids(slot, runtime_binding)?,
            )
        } else if let Some(bot_id) = definition_binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        {
            (
                "definition_legacy_bot_id".to_string(),
                None,
                vec![bot_id.to_string()],
            )
        } else if definition_binding.required || referenced_slots.contains(slot) {
            return invalid_participant_binding(format!(
                "participant slot {slot} has no runtime binding or legacy bot_id"
            ));
        } else {
            continue;
        };

        for bot_id in &bot_ids {
            if !group_participants.contains(bot_id.as_str()) {
                return invalid_participant_binding(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                ));
            }
        }
        if referenced_slots.contains(slot) && bot_ids.len() != 1 {
            return invalid_participant_binding(format!(
                "participant slot {slot} is assigned to a node and must resolve to exactly one bot in the current runtime"
            ));
        }
        let participants = bot_ids
            .iter()
            .map(|bot_id| ResolvedParticipant {
                bot_id: bot_id.clone(),
                bcs_participant_role: inferred_participant_role(group, bot_id),
            })
            .collect();
        resolved.insert(
            slot.clone(),
            ResolvedParticipantBinding {
                source,
                binding_source,
                bot_ids,
                participants,
                extensions: Default::default(),
            },
        );
    }

    for slot in &referenced_slots {
        if !resolved.contains_key(slot) {
            return invalid_participant_binding(format!(
                "node assignee binding has no resolved participant slot: {slot}"
            ));
        }
    }
    Ok(resolved)
}

pub(super) fn validate_runtime_participant_bindings(
    definition: &CollaborationDefinition,
    group: &Group,
    participant_bindings: &BTreeMap<String, RuntimeParticipantBinding>,
) -> Result<(), CollaborationRuntimeError> {
    let group_participants = group
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    for (slot, binding) in participant_bindings {
        if !definition.participants.contains_key(slot) {
            return invalid_participant_binding(format!(
                "participant_bindings contains undeclared slot: {slot}"
            ));
        }
        for bot_id in runtime_binding_bot_ids(slot, binding)? {
            if !group_participants.contains(bot_id.as_str()) {
                return invalid_participant_binding(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                ));
            }
        }
    }
    Ok(())
}

fn referenced_participant_slots(
    compiled: &CompiledStateMachine,
) -> Result<HashSet<String>, CollaborationRuntimeError> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let mut slots = HashSet::new();
    for node in state_machine.nodes.values() {
        if let Some(StateMachineAssignee::BotBinding { binding }) = &node.assignee {
            slots.insert(binding.clone());
        }
    }
    Ok(slots)
}

fn runtime_binding_bot_ids(
    slot: &str,
    binding: &RuntimeParticipantBinding,
) -> Result<Vec<String>, CollaborationRuntimeError> {
    if binding.source != "manual" {
        return invalid_participant_binding(format!(
            "participant slot {slot} binding source is unsupported: {}",
            binding.source
        ));
    }
    let mut seen = HashSet::new();
    let mut bot_ids = Vec::new();
    for raw_bot_id in &binding.bot_ids {
        let bot_id = raw_bot_id.trim();
        if bot_id.is_empty() {
            return invalid_participant_binding(format!(
                "participant slot {slot} contains empty bot_id"
            ));
        }
        if !seen.insert(bot_id.to_string()) {
            return invalid_participant_binding(format!(
                "participant slot {slot} contains duplicate bot_id: {bot_id}"
            ));
        }
        bot_ids.push(bot_id.to_string());
    }
    if bot_ids.is_empty() {
        return invalid_participant_binding(format!(
            "participant slot {slot} binding bot_ids must not be empty"
        ));
    }
    Ok(bot_ids)
}

fn inferred_participant_role(group: &Group, bot_id: &str) -> ParticipantRole {
    if bot_id == group.driver_bot {
        ParticipantRole::Driver
    } else {
        ParticipantRole::Consultant
    }
}

pub(super) fn session_state_machine_permission_view(
    session_id: &str,
    group: &Group,
    caller_bot_id: &str,
    allowed: bool,
    reason_code: &str,
    message: &str,
    active_run_id: Option<String>,
) -> SessionStateMachinePermissionView {
    SessionStateMachinePermissionView {
        session_id: session_id.to_string(),
        group_id: group.id.clone(),
        caller_bot_id: caller_bot_id.to_string(),
        allowed,
        reason_code: reason_code.to_string(),
        message: message.to_string(),
        policy_version: SESSION_STATE_MACHINE_POLICY_VERSION.to_string(),
        group_strategy: group_strategy_slug(group.group_strategy).to_string(),
        group_owner_bot_id: group.driver_bot.clone(),
        active_run_id,
    }
}

fn group_strategy_slug(strategy: GroupStrategy) -> &'static str {
    match strategy {
        GroupStrategy::Chat => "chat",
        GroupStrategy::ManagerWorker => "manager_worker",
        GroupStrategy::StateMachine => "state_machine",
    }
}

fn invalid_participant_binding<T>(
    message: impl Into<String>,
) -> Result<T, CollaborationRuntimeError> {
    Err(CollaborationRuntimeError::InvalidParticipantBinding(
        message.into(),
    ))
}

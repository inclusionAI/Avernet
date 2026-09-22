//! Authoring collaboration-definition parsing and runtime-binding validation.

use super::*;

pub(crate) fn parse_authoring_collaboration_definition_yaml(
    yaml: &str,
) -> Result<CollaborationDefinition, HttpAdapterError> {
    reject_authoring_yaml_identity(yaml)?;
    serde_yaml::from_str(yaml).map_err(|error| {
        HttpAdapterError::BadRequest(format!("invalid collaboration_definition_yaml: {error}"))
    })
}

pub(crate) fn reject_authoring_yaml_identity(yaml: &str) -> Result<(), HttpAdapterError> {
    if yaml.as_bytes().len() > MAX_COLLABORATION_DEFINITION_YAML_BYTES {
        return Err(HttpAdapterError::BadRequest(format!(
            "collaboration_definition_yaml exceeds {} bytes",
            MAX_COLLABORATION_DEFINITION_YAML_BYTES
        )));
    }
    let value: serde_yaml::Value = serde_yaml::from_str(yaml).map_err(|error| {
        HttpAdapterError::BadRequest(format!("invalid collaboration_definition_yaml: {error}"))
    })?;
    let Some(mapping) = value.as_mapping() else {
        return Ok(());
    };
    for key in mapping.keys() {
        if matches!(key.as_str(), Some("id" | "version")) {
            return Err(HttpAdapterError::BadRequest(
                "collaboration_definition_yaml must not contain top-level id or version"
                    .to_string(),
            ));
        }
    }
    Ok(())
}

pub(crate) fn referenced_participant_slots(
    definition: &CollaborationDefinition,
) -> Result<HashSet<String>, HttpAdapterError> {
    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(HttpAdapterError::BadRequest(
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

pub(crate) fn validate_definition_has_legacy_bot_ids(
    collaboration_definition: Option<&CollaborationDefinition>,
) -> Result<(), HttpAdapterError> {
    let Some(definition) = collaboration_definition else {
        return Ok(());
    };
    for (binding_id, binding) in &definition.participants {
        if binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
            .is_none()
        {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must define bot_id or be provided via participant_bindings",
                binding_id
            )));
        }
    }
    Ok(())
}

pub(crate) fn participant_binding_bot_ids(
    binding_id: &str,
    binding: &bcs_protocol::ParticipantBindingInfo,
) -> Result<Vec<String>, HttpAdapterError> {
    validate_runtime_binding_wire(binding_id, binding)?;
    Ok(binding
        .bot_ids
        .iter()
        .map(|bot_id| bot_id.trim().to_string())
        .collect())
}

pub(crate) fn validate_runtime_binding_wire(
    binding_id: &str,
    binding: &bcs_protocol::ParticipantBindingInfo,
) -> Result<(), HttpAdapterError> {
    if binding.source != "manual" {
        return Err(HttpAdapterError::BadRequest(format!(
            "participant_bindings.{binding_id}.source must be manual"
        )));
    }
    let mut seen = HashSet::new();
    for bot_id in &binding.bot_ids {
        let bot_id = bot_id.trim();
        if bot_id.is_empty() {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings.{binding_id}.bot_ids must not contain empty bot_id"
            )));
        }
        if !seen.insert(bot_id.to_string()) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings.{binding_id}.bot_ids contains duplicate bot_id: {bot_id}"
            )));
        }
    }
    if binding.bot_ids.is_empty() {
        return Err(HttpAdapterError::BadRequest(format!(
            "participant_bindings.{binding_id}.bot_ids must not be empty"
        )));
    }
    Ok(())
}

pub(crate) fn runtime_participant_bindings_from_request(
    req: &CreateGroupRequest,
) -> BTreeMap<String, RuntimeParticipantBinding> {
    req.participant_bindings
        .iter()
        .map(|(binding_id, binding)| {
            (
                binding_id.clone(),
                RuntimeParticipantBinding {
                    source: binding.source.clone(),
                    bot_ids: binding.bot_ids.clone(),
                    extensions: Default::default(),
                },
            )
        })
        .collect()
}

pub(crate) fn inferred_participant_role_wire(bot_id: &str, driver_bot: &str) -> &'static str {
    if bot_id == driver_bot {
        "driver"
    } else {
        "consultant"
    }
}

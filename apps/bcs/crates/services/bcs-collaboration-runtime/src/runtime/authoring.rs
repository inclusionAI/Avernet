use super::*;
pub(super) fn definition_yaml_for_record(
    record: &CollaborationDefinitionRecord,
) -> (Option<String>, DefinitionYamlSource) {
    if let Some(source_yaml) = record
        .yaml_text
        .as_deref()
        .filter(|source_yaml| !source_yaml.is_empty())
    {
        return (
            Some(source_yaml.to_string()),
            DefinitionYamlSource::Original,
        );
    }
    match generated_authoring_yaml(&record.definition) {
        Ok(generated) => (Some(generated), DefinitionYamlSource::GeneratedNormalized),
        Err(_) => (None, DefinitionYamlSource::Unavailable),
    }
}

fn generated_authoring_yaml(
    definition: &CollaborationDefinition,
) -> Result<String, serde_yaml::Error> {
    let mut value = serde_yaml::to_value(definition)?;
    if let Some(mapping) = value.as_mapping_mut() {
        mapping.shift_remove("id");
        mapping.shift_remove("version");
        remove_string_value(mapping, "api_version", "bcs.collaboration/v1");
        mapping.shift_remove("requires");
        cleanup_metadata_authoring_yaml(mapping);
        cleanup_participants_authoring_yaml(mapping);
        cleanup_state_machine_authoring_yaml(mapping);
        remove_empty_mapping(mapping, "extensions");
    }
    serde_yaml::to_string(&value)
}

fn cleanup_metadata_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let remove_metadata = if let Some(metadata) = mapping
        .get_mut("metadata")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        remove_empty_mapping(metadata, "labels");
        remove_empty_mapping(metadata, "extensions");
        metadata.is_empty()
    } else {
        false
    };
    if remove_metadata {
        mapping.shift_remove("metadata");
    }
}

fn cleanup_participants_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    if let Some(participants) = mapping
        .get_mut("participants")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        for participant in participants.values_mut() {
            if let Some(participant) = participant.as_mapping_mut() {
                participant.shift_remove("bcs_participant_role");
                remove_bool_value(participant, "required", false);
                remove_empty_mapping(participant, "extensions");
            }
        }
    }
    remove_empty_mapping(mapping, "participants");
}

fn cleanup_state_machine_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(runtime) = mapping
        .get_mut("runtime")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    let Some(state_machine) = runtime
        .get_mut("state_machine")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    remove_i64_value(state_machine, "version", 1);
    remove_string_value(state_machine, "graph_mode", "acyclic");
    remove_default_projection(state_machine);
    remove_default_state_machine_defaults(state_machine);
    remove_empty_mapping(state_machine, "variables");
    remove_empty_mapping(state_machine, "events");
    remove_empty_mapping(state_machine, "extensions");
    cleanup_state_machine_nodes_authoring_yaml(state_machine);
}

fn remove_default_projection(mapping: &mut serde_yaml::Mapping) {
    let Some(projection) = mapping
        .get("projection")
        .and_then(serde_yaml::Value::as_mapping)
    else {
        return;
    };
    if projection.len() == 1
        && matches!(
            projection.get("default_visibility"),
            Some(serde_yaml::Value::String(value)) if value == "private"
        )
    {
        mapping.shift_remove("projection");
    }
}

fn remove_default_state_machine_defaults(mapping: &mut serde_yaml::Mapping) {
    let remove_defaults = if let Some(defaults) = mapping
        .get_mut("defaults")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        remove_i64_value(defaults, "max_attempts", 1);
        defaults.is_empty()
    } else {
        false
    };
    if remove_defaults {
        mapping.shift_remove("defaults");
    }
}

fn cleanup_state_machine_nodes_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(nodes) = mapping
        .get_mut("nodes")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    for node in nodes.values_mut() {
        let Some(node) = node.as_mapping_mut() else {
            continue;
        };
        remove_bool_value(node, "final_output", false);
        remove_empty_mapping(node, "extensions");
        cleanup_node_transitions_authoring_yaml(node);
        cleanup_judge_authoring_yaml(node);
    }
}

fn cleanup_node_transitions_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(transitions) = mapping
        .get_mut("transitions")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    for transition in transitions.values_mut() {
        let Some(transition) = transition.as_mapping_mut() else {
            continue;
        };
        remove_empty_sequence(transition, "targets");
    }
    remove_empty_mapping(mapping, "transitions");
}

fn cleanup_judge_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(judge) = mapping
        .get_mut("judge")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    remove_empty_mapping(judge, "extensions");
}

fn remove_empty_mapping(mapping: &mut serde_yaml::Mapping, key: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Mapping(value)) if value.is_empty()
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_empty_sequence(mapping: &mut serde_yaml::Mapping, key: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Sequence(value)) if value.is_empty()
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_bool_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: bool) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Bool(value)) if *value == expected
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_string_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::String(value)) if value == expected
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_i64_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: i64) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Number(value)) if value.as_i64() == Some(expected)
    ) {
        mapping.shift_remove(key);
    }
}

pub(super) fn parse_authoring_definition_yaml(
    yaml: &str,
) -> Result<CollaborationDefinition, CollaborationRuntimeError> {
    reject_authoring_yaml_identity(yaml)?;
    validate_v2_definition_input(serde_yaml::from_str(yaml)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
    serde_yaml::from_str(yaml)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))
}

pub(super) fn validate_v2_definition_input(raw: serde_yaml::Value) -> Result<(), CollaborationRuntimeError> {
    crate::validation::validate_v2_runtime_input(&raw).map_err(|error| {
        CollaborationRuntimeError::InvalidDefinition(format!("{} {}: {}", error.code, error.path, error.message))
    })
}

pub(super) fn reject_authoring_yaml_identity(yaml: &str) -> Result<(), CollaborationRuntimeError> {
    if yaml.as_bytes().len() > MAX_COLLABORATION_DEFINITION_YAML_BYTES {
        return Err(CollaborationRuntimeError::InvalidDefinition(format!(
            "collaboration definition YAML exceeds {} bytes",
            MAX_COLLABORATION_DEFINITION_YAML_BYTES
        )));
    }
    let value: serde_yaml::Value = serde_yaml::from_str(yaml)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
    let Some(mapping) = value.as_mapping() else {
        return Ok(());
    };
    for key in mapping.keys() {
        if matches!(key.as_str(), Some("id" | "version")) {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "collaboration definition YAML must not contain top-level id or version"
                    .to_string(),
            ));
        }
    }
    Ok(())
}

pub(super) fn normalized_definition_json(
    definition: &CollaborationDefinition,
) -> Result<String, CollaborationRuntimeError> {
    serde_json::to_string(definition)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))
}

use std::collections::BTreeSet;

use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition, StateMachineAssignee};
use bcs_service_api::{
    CollaborationDefinitionParticipantSlot, CollaborationDefinitionValidationDiagnostic,
    CollaborationDefinitionValidationOutcome, CollaborationDefinitionValidationSummary,
    MAX_COLLABORATION_DEFINITION_YAML_BYTES, ValidateCollaborationDefinitionYamlCommand,
};
use serde_yaml::{Mapping, Value};

use crate::{CompiledStateMachine, reject_explicit_participant_roles, validate_definition};

pub fn validate_authoring_definition_yaml(
    cmd: ValidateCollaborationDefinitionYamlCommand,
) -> CollaborationDefinitionValidationOutcome {
    if cmd.definition_yaml.len() > MAX_COLLABORATION_DEFINITION_YAML_BYTES {
        return invalid_outcome(diagnostic(
            "SIZE_LIMIT",
            "$",
            format!(
                "collaboration definition YAML exceeds {} bytes",
                MAX_COLLABORATION_DEFINITION_YAML_BYTES
            ),
        ));
    }

    // Layer 1: validate the authoring YAML shape before deserialization.
    let raw: Value = match serde_yaml::from_str(&cmd.definition_yaml) {
        Ok(value) => value,
        Err(error) => {
            let message = error.to_string();
            let code = if message.to_ascii_lowercase().contains("duplicate") {
                "DUPLICATE_KEY"
            } else {
                "YAML_PARSE"
            };
            return invalid_outcome(diagnostic(code, "$", message));
        }
    };
    let Some(top_level) = raw.as_mapping() else {
        return invalid_outcome(diagnostic("TYPE", "$", "must be a mapping"));
    };
    for field in ["id", "version"] {
        if mapping_contains(top_level, field) {
            return invalid_outcome(diagnostic(
                "FORBIDDEN_AUTHORING_FIELD",
                format!("$.{field}"),
                "must be omitted from authoring YAML; BCS supplies this value",
            ));
        }
    }
    if let Err(error) = validate_authoring_shape(top_level) {
        return invalid_outcome(error);
    }

    // Layer 2: validate the definition against the current runtime contract.
    let definition: CollaborationDefinition = match serde_yaml::from_str(&cmd.definition_yaml) {
        Ok(definition) => definition,
        Err(error) => {
            return invalid_outcome(diagnostic("INVALID_DEFINITION", "$", error.to_string()));
        }
    };
    if definition.name.trim().is_empty() {
        return invalid_outcome(diagnostic(
            "REQUIRED",
            "$.name",
            "must be a non-empty string",
        ));
    }
    if definition.participants.is_empty() {
        return invalid_outcome(diagnostic(
            "REQUIRED",
            "$.participants",
            "must not be empty",
        ));
    }
    if let Err(error) = reject_explicit_participant_roles(&definition) {
        return invalid_outcome(diagnostic(
            "INVALID_PARTICIPANT",
            "$.participants",
            error.to_string(),
        ));
    }

    let compiled = match validate_definition(definition) {
        Ok(compiled) => compiled,
        Err(error) => {
            return invalid_outcome(diagnostic("INVALID_DEFINITION", "$", error.to_string()));
        }
    };
    // Layer 3: validate capabilities selected by this BCS deployment.
    let mut outcome = valid_outcome(&compiled);
    if !cmd.judge_available && compiled.definition.uses_judge() {
        outcome.valid = false;
        outcome.errors.push(diagnostic(
            "UNAVAILABLE_FEATURE",
            "$.runtime.state_machine.nodes",
            "state-machine judge requires llm.type to select an LLM provider",
        ));
        outcome.definition = None;
        return outcome;
    }
    outcome
}

fn valid_outcome(compiled: &CompiledStateMachine) -> CollaborationDefinitionValidationOutcome {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => unreachable!("validate_definition accepted a non-state-machine runtime"),
    };
    let assigned = state_machine
        .nodes
        .values()
        .filter_map(|node| match &node.assignee {
            Some(StateMachineAssignee::BotBinding { binding }) => Some(binding.clone()),
            _ => None,
        })
        .collect::<BTreeSet<_>>();
    let final_nodes = state_machine
        .nodes
        .iter()
        .filter_map(|(node_id, node)| node.final_output.then_some(node_id.clone()))
        .collect::<Vec<_>>();
    let participants = compiled
        .definition
        .participants
        .iter()
        .map(
            |(binding, participant)| CollaborationDefinitionParticipantSlot {
                binding: binding.clone(),
                display_name: participant.display_name.clone(),
                description: participant.description.clone(),
                required: participant.required,
                assigned: assigned.contains(binding),
            },
        )
        .collect::<Vec<_>>();
    CollaborationDefinitionValidationOutcome {
        valid: true,
        errors: Vec::new(),
        warnings: Vec::new(),
        summary: CollaborationDefinitionValidationSummary {
            participants: participants.len(),
            nodes: state_machine.nodes.len(),
            initial_nodes: compiled.initial_nodes.clone(),
            final_output_node: match final_nodes.as_slice() {
                [node_id] => Some(node_id.clone()),
                _ => None,
            },
        },
        participants,
        definition: Some(compiled.definition.clone()),
    }
}

fn invalid_outcome(
    error: CollaborationDefinitionValidationDiagnostic,
) -> CollaborationDefinitionValidationOutcome {
    CollaborationDefinitionValidationOutcome {
        valid: false,
        errors: vec![error],
        warnings: Vec::new(),
        summary: CollaborationDefinitionValidationSummary::default(),
        participants: Vec::new(),
        definition: None,
    }
}

fn diagnostic(
    code: impl Into<String>,
    path: impl Into<String>,
    message: impl Into<String>,
) -> CollaborationDefinitionValidationDiagnostic {
    CollaborationDefinitionValidationDiagnostic {
        code: code.into(),
        path: path.into(),
        message: message.into(),
        hint: None,
    }
}

fn validate_authoring_shape(
    top_level: &Mapping,
) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    if mapping_contains(top_level, "api_version") {
        return Err(diagnostic(
            "FORBIDDEN_AUTHORING_FIELD",
            "$.api_version",
            "must be omitted from authoring YAML; BCS supplies this value",
        ));
    }
    ensure_allowed_keys(
        top_level,
        &["name", "metadata", "participants", "runtime"],
        "$",
    )?;
    if let Some(metadata) = mapping_get(top_level, "metadata").and_then(Value::as_mapping) {
        ensure_allowed_keys(
            metadata,
            &["description", "labels", "extensions"],
            "$.metadata",
        )?;
    }
    if let Some(participants) = mapping_get(top_level, "participants").and_then(Value::as_mapping) {
        for (binding, participant) in participants {
            let binding = yaml_key(binding, "$.participants")?;
            if !valid_identifier(binding) {
                return Err(diagnostic(
                    "FORMAT",
                    format!("$.participants.{binding}"),
                    "binding id has an invalid format",
                ));
            }
            if let Some(participant) = participant.as_mapping() {
                ensure_allowed_keys(
                    participant,
                    &["display_name", "description", "required", "extensions"],
                    &format!("$.participants.{binding}"),
                )?;
            }
        }
    }
    let Some(runtime) = mapping_get(top_level, "runtime").and_then(Value::as_mapping) else {
        return Ok(());
    };
    ensure_allowed_keys(runtime, &["kind", "state_machine"], "$.runtime")?;
    let Some(machine) = mapping_get(runtime, "state_machine").and_then(Value::as_mapping) else {
        return Ok(());
    };
    ensure_allowed_keys(
        machine,
        &[
            "version",
            "graph_mode",
            "projection",
            "defaults",
            "human_input_channel",
            "nodes",
            "extensions",
            "initial_node",
            "input_schema",
            "variables",
            "events",
        ],
        "$.runtime.state_machine",
    )?;
    if let Some(projection) = mapping_get(machine, "projection").and_then(Value::as_mapping) {
        ensure_allowed_keys(
            projection,
            &["default_visibility"],
            "$.runtime.state_machine.projection",
        )?;
    }
    if let Some(defaults) = mapping_get(machine, "defaults").and_then(Value::as_mapping) {
        ensure_allowed_keys(
            defaults,
            &["node_timeout_ms", "max_attempts"],
            "$.runtime.state_machine.defaults",
        )?;
    }
    if let Some(channel) =
        mapping_get(machine, "human_input_channel").and_then(Value::as_mapping)
    {
        ensure_allowed_keys(
            channel,
            &["channel_type", "fixed_group"],
            "$.runtime.state_machine.human_input_channel",
        )?;
        if let Some(fixed_group) =
            mapping_get(channel, "fixed_group").and_then(Value::as_mapping)
        {
            ensure_allowed_keys(
                fixed_group,
                &["conversation_type", "conversation_id"],
                "$.runtime.state_machine.human_input_channel.fixed_group",
            )?;
        }
    }
    let Some(nodes) = mapping_get(machine, "nodes").and_then(Value::as_mapping) else {
        return Ok(());
    };
    for (node_id, node) in nodes {
        let node_id = yaml_key(node_id, "$.runtime.state_machine.nodes")?;
        if !valid_identifier(node_id) {
            return Err(diagnostic(
                "FORMAT",
                format!("$.runtime.state_machine.nodes.{node_id}"),
                "node id has an invalid format",
            ));
        }
        let Some(node) = node.as_mapping() else {
            continue;
        };
        let node_path = format!("$.runtime.state_machine.nodes.{node_id}");
        ensure_allowed_keys(
            node,
            &[
                "kind",
                "display_name",
                "assignee",
                "notification",
                "instruction",
                "node_timeout_ms",
                "max_attempts",
                "transitions",
                "visibility",
                "final_output",
                "extensions",
                "judge",
                "output_contract",
                "action",
            ],
            &node_path,
        )?;
        if let Some(assignee) = mapping_get(node, "assignee").and_then(Value::as_mapping) {
            ensure_allowed_keys(
                assignee,
                &["type", "binding", "actor"],
                &format!("{node_path}.assignee"),
            )?;
        }
        if let Some(notification) = mapping_get(node, "notification").and_then(Value::as_mapping) {
            ensure_allowed_keys(
                notification,
                &["mode"],
                &format!("{node_path}.notification"),
            )?;
        }
        if let Some(transitions) = mapping_get(node, "transitions").and_then(Value::as_mapping) {
            for (outcome, transition) in transitions {
                let outcome = yaml_key(outcome, &format!("{node_path}.transitions"))?;
                if !valid_identifier(outcome) {
                    return Err(diagnostic(
                        "FORMAT",
                        format!("{node_path}.transitions.{outcome}"),
                        "transition outcome has an invalid format",
                    ));
                }
                let Some(transition) = transition.as_mapping() else {
                    continue;
                };
                ensure_allowed_keys(
                    transition,
                    &["targets", "guard"],
                    &format!("{node_path}.transitions.{outcome}"),
                )?;
            }
        }
    }
    Ok(())
}

fn ensure_allowed_keys(
    mapping: &Mapping,
    allowed: &[&str],
    path: &str,
) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    for key in mapping.keys() {
        let key = yaml_key(key, path)?;
        if !allowed.contains(&key) {
            return Err(diagnostic(
                "UNKNOWN_KEY",
                format!("{path}.{key}"),
                "unsupported or misspelled field",
            ));
        }
    }
    Ok(())
}

fn yaml_key<'a>(
    value: &'a Value,
    path: &str,
) -> Result<&'a str, CollaborationDefinitionValidationDiagnostic> {
    value
        .as_str()
        .ok_or_else(|| diagnostic("TYPE", path, "mapping keys must be strings"))
}

fn mapping_get<'a>(mapping: &'a Mapping, key: &str) -> Option<&'a Value> {
    mapping.get(Value::String(key.to_string()))
}

fn mapping_contains(mapping: &Mapping, key: &str) -> bool {
    mapping.contains_key(Value::String(key.to_string()))
}

fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(first) if first.is_ascii_alphabetic())
        && chars.all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
}

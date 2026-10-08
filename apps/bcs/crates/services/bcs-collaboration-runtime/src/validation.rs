use std::collections::BTreeSet;

use bcs_config_api::FixedLoopLimits;
use bcs_domain::{CollaborationDefinition, CollaborationRuntimeDefinition, StateMachineAssignee};
use bcs_service_api::{
    CollaborationDefinitionGraphEdge, CollaborationDefinitionGraphNode,
    CollaborationDefinitionGraphPreview, CollaborationDefinitionParticipantSlot,
    CollaborationDefinitionValidationDiagnostic, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary, MAX_COLLABORATION_DEFINITION_YAML_BYTES,
    ValidateCollaborationDefinitionYamlCommand,
};
use serde_yaml::{Mapping, Value};

use crate::definition::{DefinitionGraphProjection, project_definition_graph};
use crate::fixed_loop::compile_authoring_definition_with_instrumentation;
use crate::{CompiledStateMachine, reject_explicit_participant_roles};

pub fn validate_authoring_definition_yaml(
    cmd: ValidateCollaborationDefinitionYamlCommand,
) -> CollaborationDefinitionValidationOutcome {
    validate_authoring_definition_yaml_with_limits(cmd, &FixedLoopLimits::default())
}

pub fn validate_authoring_definition_yaml_with_limits(
    cmd: ValidateCollaborationDefinitionYamlCommand,
    limits: &FixedLoopLimits,
) -> CollaborationDefinitionValidationOutcome {
    validate_authoring_definition_yaml_with_instrumentation(cmd, limits, None, false)
}

pub(crate) fn validate_authoring_definition_yaml_with_instrumentation(
    cmd: ValidateCollaborationDefinitionYamlCommand,
    limits: &FixedLoopLimits,
    hook: Option<&dyn bcs_service_api::StateMachineLoopInstrumentationHook>,
    loop_execution_enabled: bool,
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

    let compiled = match compile_authoring_definition_with_instrumentation(definition, limits, hook) {
        Ok(compiled) => compiled,
        Err(error) => return invalid_outcome(error),
    };
    // Layer 3: validate capabilities selected by this BCS deployment.
    let mut outcome = valid_outcome(&compiled.execution);
    outcome.definition = Some(compiled.definition.clone());
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
    let projection = match project_definition_graph(&compiled.execution) {
        Ok(projection) => projection,
        Err(error) => {
            return invalid_outcome(diagnostic("INVALID_DEFINITION", "$", error.to_string()));
        }
    };
    let mut graph = graph_preview(projection);
    graph.loops = crate::loop_graph::loop_graph_descriptors(&compiled.definition);
    if let Some(plan) = &compiled.plan {
        if !loop_execution_enabled {
            outcome.warnings.push(diagnostic(
                "VALIDATION_ONLY_FEATURE",
                "$.runtime.state_machine.version",
                "fixed-loop validation and preview are available; version 2 execution is not enabled",
            ));
        }
        graph.graph_mode = bcs_domain::StateMachineGraphMode::Hierarchical;
        graph.execution_graph_mode = Some(bcs_domain::StateMachineGraphMode::Acyclic);
        for node in &mut graph.nodes {
            let meta = &plan.node_metadata[&node.node_id];
            node.execution = crate::definition::node_execution_metadata(meta);
        }
        let edge_metadata: std::collections::BTreeMap<_, _> = plan.edge_metadata.iter()
            .map(|edge| ((edge.source_execution_node_id.as_str(), edge.outcome.as_str(), edge.target_execution_node_id.as_str()), edge))
            .collect();
        for edge in &mut graph.edges {
            edge.loop_route = edge_metadata[&(edge.source.as_str(), edge.outcome.as_str(), edge.target.as_str())].loop_route.clone();
        }
    }
    outcome.graph = Some(graph);
    outcome
}

fn graph_preview(projection: DefinitionGraphProjection) -> CollaborationDefinitionGraphPreview {
    CollaborationDefinitionGraphPreview {
        loops: Default::default(),
        graph_mode: projection.graph_mode,
        execution_graph_mode: None,
        nodes: projection
            .nodes
            .into_iter()
            .map(|node| CollaborationDefinitionGraphNode {
                node_id: node.node_id,
                display_name: node.display_name,
                kind: node.kind,
                assignee: node.assignee,
                final_output: node.final_output,
                judge: node.judge,
                execution: None,
            })
            .collect(),
        edges: projection
            .edges
            .into_iter()
            .map(|edge| CollaborationDefinitionGraphEdge {
                display_name: edge.display_name,
                source: edge.source,
                target: edge.target,
                outcome: edge.outcome,
                loop_route: None,
            })
            .collect(),
    }
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
        graph: None,
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
        graph: None,
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
    validate_runtime_shape(top_level)
}

pub(crate) fn validate_v2_runtime_input(raw: &Value) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    if raw["runtime"]["state_machine"]["version"].as_i64() != Some(2) {
        return Ok(());
    }
    let top_level = raw.as_mapping().ok_or_else(|| diagnostic("TYPE", "$", "must be a mapping"))?;
    validate_runtime_shape(top_level)
}

fn validate_runtime_shape(top_level: &Mapping) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
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
    validate_nodes_shape(nodes, "$.runtime.state_machine.nodes", false)
}

fn validate_nodes_shape(
    nodes: &Mapping,
    nodes_path: &str,
    inside_loop: bool,
) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    for (node_id, node) in nodes {
        let node_id = yaml_key(node_id, nodes_path)?;
        if !valid_identifier(node_id) {
            return Err(diagnostic(
                "FORMAT",
                format!("{nodes_path}.{node_id}"),
                "node id has an invalid format",
            ));
        }
        let Some(node) = node.as_mapping() else {
            continue;
        };
        let node_path = format!("{nodes_path}.{node_id}");
        let is_loop = mapping_get(node, "kind").and_then(Value::as_str) == Some("loop");
        if is_loop {
            if inside_loop {
                return Err(diagnostic("INVALID_DEFINITION", &node_path, "nested loops are unsupported"));
            }
            ensure_allowed_keys(node, &["kind", "display_name", "loop", "transitions", "extensions"], &node_path)?;
            validate_loop_shape(node, &node_path)?;
        } else {
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
        }
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
        if inside_loop {
            if let Some(judge) = mapping_get(node, "judge").and_then(Value::as_mapping) {
                ensure_allowed_keys(judge, &["type", "criteria", "outcomes", "extensions"], &format!("{node_path}.judge"))?;
            }
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
                    &["targets", "guard", "display_name"],
                    &format!("{node_path}.transitions.{outcome}"),
                )?;
                validate_optional_display_name(transition, "display_name", &format!("{node_path}.transitions.{outcome}"))?;
            }
        }
    }
    Ok(())
}

fn validate_loop_shape(
    node: &Mapping,
    path: &str,
) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    let loop_path = format!("{path}.loop");
    let body = mapping_get(node, "loop").and_then(Value::as_mapping)
        .ok_or_else(|| diagnostic("INVALID_DEFINITION", &loop_path, "loop must be a mapping"))?;
    let fields = ["mode", "max_iterations", "entry_node", "result_node", "continue_outcomes", "break_outcomes", "exhausted_outcome", "nodes"];
    let allowed: Vec<_> = fields.into_iter().chain(["continue_display_name"]).collect();
    ensure_allowed_keys(body, &allowed, &loop_path)?;
    validate_optional_display_name(body, "continue_display_name", &loop_path)?;
    for field in fields {
        if !mapping_contains(body, field) {
            return Err(diagnostic("INVALID_DEFINITION", format!("{loop_path}.{field}"), "field is required"));
        }
    }
    for field in ["continue_outcomes", "break_outcomes"] {
        let values = mapping_get(body, field).and_then(Value::as_sequence)
            .ok_or_else(|| diagnostic("INVALID_DEFINITION", format!("{loop_path}.{field}"), "must be a list"))?;
        if field == "continue_outcomes" && values.is_empty() {
            return Err(diagnostic("INVALID_DEFINITION", format!("{loop_path}.{field}"), "must not be empty"));
        }
    }
    let nodes = mapping_get(body, "nodes").and_then(Value::as_mapping)
        .ok_or_else(|| diagnostic("INVALID_DEFINITION", format!("{loop_path}.nodes"), "must be a mapping"))?;
    validate_nodes_shape(nodes, &format!("{loop_path}.nodes"), true)
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

fn validate_optional_display_name(
    mapping: &Mapping,
    field: &str,
    path: &str,
) -> Result<(), CollaborationDefinitionValidationDiagnostic> {
    if let Some(value) = mapping_get(mapping, field) {
        if value.as_str().is_none_or(|name| name.trim().is_empty()) {
            return Err(diagnostic("INVALID_DEFINITION", format!("{path}.{field}"), "must be a nonblank string"));
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

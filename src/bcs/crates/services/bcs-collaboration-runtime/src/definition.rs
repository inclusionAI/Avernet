use std::collections::{BTreeMap, BTreeSet, VecDeque};

use bcs_domain::{
    CollaborationDefinition, CollaborationRequirements, CollaborationRuntimeDefinition,
    StateMachineAssignee, StateMachineDefinition, StateMachineGraphMode, StateMachineNodeKind,
};
use bcs_service_api::CollaborationRuntimeError;

#[derive(Debug, Clone)]
pub struct CompiledStateMachine {
    pub definition: CollaborationDefinition,
    pub upstreams: BTreeMap<String, Vec<String>>,
    pub initial_nodes: Vec<String>,
}

pub fn validate_definition(
    mut definition: CollaborationDefinition,
) -> Result<CompiledStateMachine, CollaborationRuntimeError> {
    if definition.api_version != "bcs.collaboration/v1" {
        return invalid(format!("unsupported api_version: {}", definition.api_version));
    }

    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => return invalid("runtime.kind must be state_machine"),
    };
    let effective_requires = infer_effective_requires(&definition, state_machine);
    validate_requires(&effective_requires)?;
    definition.requires = Some(effective_requires);

    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => return invalid("runtime.kind must be state_machine"),
    };
    for (binding_id, participant) in &definition.participants {
        if participant.bot_id.as_deref().is_some_and(|bot_id| bot_id.trim().is_empty()) {
            return invalid(format!("participant {binding_id} bot_id must not be empty"));
        }
    }
    if state_machine.version != 1 {
        return invalid(format!(
            "unsupported state_machine.version: {}",
            state_machine.version
        ));
    }
    if state_machine.graph_mode != StateMachineGraphMode::Acyclic {
        return invalid("MVP only supports state_machine.graph_mode.acyclic");
    }
    if state_machine.initial_node.is_some() {
        return invalid("state_machine.initial_node is not supported in MVP");
    }
    if !state_machine.variables.is_empty() {
        return invalid("state_machine.variables is not supported in MVP");
    }
    if !state_machine.events.is_empty() {
        return invalid("state_machine.events is not supported in MVP");
    }
    if state_machine.nodes.is_empty() {
        return invalid("state_machine.nodes must not be empty");
    }

    let mut final_output_count = 0;
    let mut upstreams: BTreeMap<String, Vec<String>> = state_machine
        .nodes
        .keys()
        .map(|node_id| (node_id.clone(), Vec::new()))
        .collect();

    for (node_id, node) in &state_machine.nodes {
        if node.kind != StateMachineNodeKind::BotTask {
            return invalid(format!("node {node_id} kind is not supported in MVP"));
        }
        if node.action.is_some() {
            return invalid(format!("node {node_id} action is not supported in MVP"));
        }
        if let Some(judge) = &node.judge {
            if judge.judge_type.as_deref().unwrap_or("llm") != "llm" {
                return invalid(format!("node {node_id} only judge.type llm is supported"));
            }
            if judge.criteria.is_empty() {
                return invalid(format!("node {node_id} judge.criteria must not be empty"));
            }
            if judge.outcomes.is_empty() {
                return invalid(format!("node {node_id} judge.outcomes must not be empty"));
            }
            for outcome in &judge.outcomes {
                if !node.transitions.contains_key(outcome) {
                    return invalid(format!(
                        "node {node_id} judge outcome has no transition: {outcome}"
                    ));
                }
            }
        }
        if node.output_contract.is_some() {
            return invalid(format!(
                "node {node_id} output_contract is not supported in MVP"
            ));
        }
        if node.instruction.as_deref().map(str::trim).unwrap_or("").is_empty() {
            return invalid(format!("node {node_id} instruction must not be empty"));
        }
        match &node.assignee {
            Some(StateMachineAssignee::BotBinding { binding }) => {
                definition.participants.get(binding).ok_or_else(|| {
                    CollaborationRuntimeError::InvalidDefinition(format!(
                        "node {node_id} assignee binding not found: {binding}"
                    ))
                })?;
            }
            Some(StateMachineAssignee::RuntimeActor { .. }) => {
                return invalid(format!("node {node_id} runtime_actor assignee is not supported in MVP"));
            }
            None => return invalid(format!("node {node_id} assignee is required")),
        }
        if node.final_output {
            final_output_count += 1;
        }
        for (outcome, transition) in &node.transitions {
            if let Some(judge) = &node.judge {
                if !judge.outcomes.iter().any(|allowed| allowed == outcome) {
                    return invalid(format!(
                        "node {node_id} transition outcome is not declared by judge: {outcome}"
                    ));
                }
            } else if outcome != "complete" {
                return invalid(format!(
                    "node {node_id} only transitions.complete is supported in MVP"
                ));
            }
            if transition.guard.is_some() {
                return invalid(format!(
                    "node {node_id} guarded transitions are not supported in MVP"
                ));
            }
            for target in &transition.targets {
                if !state_machine.nodes.contains_key(target) {
                    return invalid(format!(
                        "node {node_id} transitions.complete target not found: {target}"
                    ));
                }
                if let Some(target_upstreams) = upstreams.get_mut(target) {
                    target_upstreams.push(node_id.clone());
                }
            }
        }
    }
    if final_output_count > 1 {
        return invalid("state_machine may have at most one final_output node");
    }

    let initial_nodes = upstreams
        .iter()
        .filter_map(|(node_id, upstream)| {
            if upstream.is_empty() {
                Some(node_id.clone())
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    if initial_nodes.is_empty() {
        return invalid("state_machine must have at least one zero in-degree node");
    }
    ensure_acyclic(&state_machine.nodes, &upstreams)?;

    Ok(CompiledStateMachine {
        definition,
        upstreams,
        initial_nodes,
    })
}

pub fn reject_explicit_participant_roles(
    definition: &CollaborationDefinition,
) -> Result<(), CollaborationRuntimeError> {
    for (binding_id, participant) in &definition.participants {
        if participant.bcs_participant_role.is_some() {
            return invalid(format!(
                "participant {binding_id} must not define bcs_participant_role"
            ));
        }
    }
    Ok(())
}

fn infer_effective_requires(
    definition: &CollaborationDefinition,
    state_machine: &StateMachineDefinition,
) -> CollaborationRequirements {
    let mut server_features = BTreeSet::new();
    let mut bot_runtime_features = BTreeSet::new();

    server_features.insert("state_machine.graph_mode.acyclic".to_string());
    server_features.insert("state_machine.node.kind.bot_task".to_string());
    server_features.insert("state_machine.transitions.complete".to_string());
    bot_runtime_features.insert("delivery.chat_send_task_compat".to_string());

    server_features.insert(graph_mode_feature(state_machine.graph_mode).to_string());
    if state_machine.initial_node.is_some() {
        server_features.insert("state_machine.initial_node".to_string());
    }
    if !state_machine.variables.is_empty() {
        server_features.insert("state_machine.variables".to_string());
    }
    if !state_machine.events.is_empty() {
        server_features.insert("state_machine.events".to_string());
    }

    for node in state_machine.nodes.values() {
        server_features.insert(node_kind_feature(node.kind).to_string());
        if node.output_contract.is_some() {
            let contract_type = node
                .output_contract
                .as_ref()
                .and_then(|contract| contract.contract_type.as_deref())
                .unwrap_or("json");
            server_features.insert(format!("state_machine.output_contract.{contract_type}"));
        }
        if node.action.is_some() {
            server_features.insert("state_machine.node.action".to_string());
        }
        if node.judge.is_some() {
            server_features.insert("state_machine.node.judge".to_string());
            server_features.insert("state_machine.outcome_transitions".to_string());
        }
        for (outcome, transition) in &node.transitions {
            if outcome != "complete" {
                server_features.insert("state_machine.outcome_transitions".to_string());
            }
            if transition.guard.is_some() {
                server_features.insert("state_machine.guarded_transitions".to_string());
            }
        }
    }

    if let Some(explicit) = &definition.requires {
        server_features.extend(explicit.server_features.iter().cloned());
        bot_runtime_features.extend(explicit.bot_runtime_features.iter().cloned());
    }

    CollaborationRequirements {
        server_features: server_features.into_iter().collect(),
        bot_runtime_features: bot_runtime_features.into_iter().collect(),
    }
}

fn graph_mode_feature(graph_mode: StateMachineGraphMode) -> &'static str {
    match graph_mode {
        StateMachineGraphMode::Acyclic => "state_machine.graph_mode.acyclic",
        StateMachineGraphMode::Cyclic => "state_machine.graph_mode.cyclic",
        StateMachineGraphMode::EventDriven => "state_machine.graph_mode.event_driven",
        StateMachineGraphMode::Hierarchical => "state_machine.graph_mode.hierarchical",
    }
}

fn node_kind_feature(kind: StateMachineNodeKind) -> &'static str {
    match kind {
        StateMachineNodeKind::BotTask => "state_machine.node.kind.bot_task",
        StateMachineNodeKind::GroupChat => "state_machine.node.kind.group_chat",
        StateMachineNodeKind::HumanInput => "state_machine.node.kind.human_input",
        StateMachineNodeKind::ToolAction => "state_machine.node.kind.tool_action",
        StateMachineNodeKind::SubStateMachine => "state_machine.node.kind.sub_state_machine",
    }
}

fn validate_requires(requires: &CollaborationRequirements) -> Result<(), CollaborationRuntimeError> {
    for feature in &requires.server_features {
        if !matches!(
            feature.as_str(),
            "state_machine.graph_mode.acyclic"
                | "state_machine.node.kind.bot_task"
                | "state_machine.transitions.complete"
                | "state_machine.node.judge"
                | "state_machine.outcome_transitions"
        ) {
            return invalid(format!("unsupported server feature: {feature}"));
        }
    }
    for feature in &requires.bot_runtime_features {
        if feature != "delivery.chat_send_task_compat" {
            return invalid(format!("unsupported bot runtime feature: {feature}"));
        }
    }
    Ok(())
}

fn ensure_acyclic(
    nodes: &BTreeMap<String, bcs_domain::StateMachineNodeDefinition>,
    upstreams: &BTreeMap<String, Vec<String>>,
) -> Result<(), CollaborationRuntimeError> {
    let mut in_degree = upstreams
        .iter()
        .map(|(node_id, upstream)| (node_id.clone(), upstream.len()))
        .collect::<BTreeMap<_, _>>();
    let mut queue = in_degree
        .iter()
        .filter_map(|(node_id, degree)| {
            if *degree == 0 {
                Some(node_id.clone())
            } else {
                None
            }
        })
        .collect::<VecDeque<_>>();
    let mut visited = 0;

    while let Some(node_id) = queue.pop_front() {
        visited += 1;
        let Some(node) = nodes.get(&node_id) else {
            continue;
        };
        let targets = node
            .transitions
            .values()
            .flat_map(|transition| transition.targets.iter());
        for target in targets {
            if let Some(degree) = in_degree.get_mut(target) {
                *degree = degree.saturating_sub(1);
                if *degree == 0 {
                    queue.push_back(target.clone());
                }
            }
        }
    }

    if visited != nodes.len() {
        return invalid("state_machine complete-transition graph must be acyclic");
    }
    Ok(())
}

fn invalid<T>(message: impl Into<String>) -> Result<T, CollaborationRuntimeError> {
    Err(CollaborationRuntimeError::InvalidDefinition(message.into()))
}

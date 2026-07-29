use std::collections::{BTreeMap, BTreeSet, VecDeque};

use bcs_domain::{
    CollaborationDefinition, CollaborationRequirements, CollaborationRuntimeDefinition,
    HumanInputNotificationMode, StateMachineAssignee, StateMachineDefinition,
    StateMachineGraphMode, StateMachineNodeKind,
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
        return invalid(format!(
            "unsupported api_version: {}",
            definition.api_version
        ));
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
        if participant
            .bot_id
            .as_deref()
            .is_some_and(|bot_id| bot_id.trim().is_empty())
        {
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
        return invalid("current runtime only supports state_machine.graph_mode.acyclic");
    }
    if state_machine.initial_node.is_some() {
        return invalid("state_machine.initial_node is not supported by the current runtime");
    }
    if state_machine.input_schema.is_some() {
        return invalid("state_machine.input_schema is not supported by the current runtime");
    }
    if !state_machine.variables.is_empty() {
        return invalid("state_machine.variables is not supported by the current runtime");
    }
    if !state_machine.events.is_empty() {
        return invalid("state_machine.events is not supported by the current runtime");
    }
    if state_machine.nodes.is_empty() {
        return invalid("state_machine.nodes must not be empty");
    }
    if let Some(channel) = &state_machine.human_input_channel {
        if channel.channel_type.trim().is_empty() {
            return invalid("state_machine.human_input_channel.channel_type must not be empty");
        }
        if let Some(fixed_group) = &channel.fixed_group
            && fixed_group.conversation_id.trim().is_empty()
        {
            return invalid(
                "state_machine.human_input_channel.fixed_group.conversation_id must not be empty",
            );
        }
    }

    let mut terminal_nodes = Vec::new();
    let mut assigned_bindings = BTreeSet::new();
    let mut upstreams: BTreeMap<String, Vec<String>> = state_machine
        .nodes
        .keys()
        .map(|node_id| (node_id.clone(), Vec::new()))
        .collect();

    for (node_id, node) in &state_machine.nodes {
        if !matches!(
            node.kind,
            StateMachineNodeKind::BotTask | StateMachineNodeKind::HumanInput
        ) {
            return invalid(format!("node {node_id} kind is not supported in MVP"));
        }
        if node.action.is_some() {
            return invalid(format!(
                "node {node_id} action is not supported by the current runtime"
            ));
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
            let mut unique_outcomes = BTreeSet::new();
            for outcome in &judge.outcomes {
                if outcome.trim().is_empty() {
                    return invalid(format!("node {node_id} judge outcome must not be empty"));
                }
                if !unique_outcomes.insert(outcome) {
                    return invalid(format!(
                        "node {node_id} has duplicate judge outcome: {outcome}"
                    ));
                }
                if !node.transitions.contains_key(outcome) {
                    return invalid(format!(
                        "node {node_id} judge outcome has no transition: {outcome}"
                    ));
                }
            }
        }
        if node.output_contract.is_some() {
            return invalid(format!(
                "node {node_id} output_contract is not supported by the current runtime"
            ));
        }
        if node.display_name.trim().is_empty() {
            return invalid(format!("node {node_id} display_name must not be empty"));
        }
        if node
            .instruction
            .as_deref()
            .map(str::trim)
            .unwrap_or("")
            .is_empty()
        {
            return invalid(format!("node {node_id} instruction must not be empty"));
        }
        match node.kind {
            StateMachineNodeKind::BotTask => match &node.assignee {
                Some(StateMachineAssignee::BotBinding { binding }) => {
                    definition.participants.get(binding).ok_or_else(|| {
                        CollaborationRuntimeError::InvalidDefinition(format!(
                            "node {node_id} assignee binding not found: {binding}"
                        ))
                    })?;
                    assigned_bindings.insert(binding.as_str());
                }
                Some(StateMachineAssignee::RuntimeActor { .. }) => {
                    return invalid(format!(
                        "node {node_id} runtime_actor assignee is not supported in MVP"
                    ));
                }
                None => return invalid(format!("node {node_id} assignee is required")),
            },
            StateMachineNodeKind::HumanInput => {
                match &node.assignee {
                    Some(StateMachineAssignee::RuntimeActor { actor })
                        if !actor.trim().is_empty() => {}
                    Some(StateMachineAssignee::RuntimeActor { .. }) => {
                        return invalid(format!(
                            "human_input node {node_id} assignee actor must not be empty"
                        ));
                    }
                    Some(StateMachineAssignee::BotBinding { .. }) => {
                        return invalid(format!(
                            "human_input node {node_id} assignee must be runtime_actor"
                        ));
                    }
                    None => {
                        return invalid(format!(
                            "human_input node {node_id} runtime_actor assignee is required"
                        ));
                    }
                }
                let notification = node.notification.as_ref().ok_or_else(|| {
                    CollaborationRuntimeError::InvalidDefinition(format!(
                        "human_input node {node_id} notification is required"
                    ))
                })?;
                let channel = state_machine.human_input_channel.as_ref().ok_or_else(|| {
                    CollaborationRuntimeError::InvalidDefinition(format!(
                        "human_input node {node_id} requires state_machine.human_input_channel"
                    ))
                })?;
                if notification.mode == HumanInputNotificationMode::FixedGroup
                    && channel.fixed_group.is_none()
                {
                    return invalid(format!(
                        "human_input node {node_id} fixed_group notification requires state_machine.human_input_channel.fixed_group"
                    ));
                }
                if node.max_attempts.is_some() {
                    return invalid(format!(
                        "human_input node {node_id} must not define max_attempts"
                    ));
                }
                match node.node_timeout_ms {
                    Some(timeout_ms) if timeout_ms > 0 => {}
                    Some(_) => {
                        return invalid(format!(
                            "human_input node {node_id} node_timeout_ms must be greater than zero"
                        ));
                    }
                    None => {
                        return invalid(format!(
                            "human_input node {node_id} node_timeout_ms is required"
                        ));
                    }
                }
                if node.final_output {
                    return invalid(format!(
                        "human_input node {node_id} must not be final_output"
                    ));
                }
            }
            _ => unreachable!("unsupported node kinds were rejected above"),
        }
        let has_transition_target = node
            .transitions
            .values()
            .any(|transition| !transition.targets.is_empty());
        let is_terminal_human =
            node.kind == StateMachineNodeKind::HumanInput && !has_transition_target;
        if node.final_output {
            terminal_nodes.push(node_id.clone());
            if !node.transitions.is_empty() {
                return invalid(format!(
                    "node {node_id} is final_output and must not define transitions"
                ));
            }
        } else if is_terminal_human {
            terminal_nodes.push(node_id.clone());
        } else if !has_transition_target {
            return invalid(format!(
                "node {node_id} is not final_output and must define a transition target"
            ));
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
                    "node {node_id} without a judge only supports transitions.complete"
                ));
            }
            if transition.guard.is_some() {
                return invalid(format!(
                    "node {node_id} guarded transitions are not supported by the current runtime"
                ));
            }
            for target in &transition.targets {
                if !state_machine.nodes.contains_key(target) {
                    return invalid(format!(
                        "node {node_id} transition target not found: {target}"
                    ));
                }
                if let Some(target_upstreams) = upstreams.get_mut(target) {
                    target_upstreams.push(node_id.clone());
                }
            }
        }
    }
    if terminal_nodes.len() != 1 {
        return invalid(
            "state_machine must have exactly one terminal node (final_output or terminal human_input)",
        );
    }
    for (binding, participant) in &definition.participants {
        if participant.required && !assigned_bindings.contains(binding.as_str()) {
            return invalid(format!(
                "required participant {binding} must be assigned to at least one node"
            ));
        }
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
    ensure_acyclic(&state_machine.nodes, &upstreams)?;
    if initial_nodes.len() != 1 {
        return invalid("state_machine must have exactly one zero in-degree entry node");
    }
    ensure_human_inputs_are_ordered(&state_machine.nodes)?;
    let final_node = terminal_nodes[0].as_str();
    ensure_all_nodes_on_entry_to_final_paths(
        state_machine,
        &upstreams,
        &initial_nodes[0],
        final_node,
    )?;

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

fn validate_requires(
    requires: &CollaborationRequirements,
) -> Result<(), CollaborationRuntimeError> {
    for feature in &requires.server_features {
        if !matches!(
            feature.as_str(),
            "state_machine.graph_mode.acyclic"
                | "state_machine.node.kind.bot_task"
                | "state_machine.node.kind.human_input"
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
        return invalid("state_machine transition graph must be acyclic");
    }
    Ok(())
}

fn ensure_all_nodes_on_entry_to_final_paths(
    state_machine: &StateMachineDefinition,
    upstreams: &BTreeMap<String, Vec<String>>,
    entry: &str,
    final_node: &str,
) -> Result<(), CollaborationRuntimeError> {
    let mut reachable = BTreeSet::new();
    let mut queue = VecDeque::from([entry]);
    while let Some(node_id) = queue.pop_front() {
        if !reachable.insert(node_id) {
            continue;
        }
        if let Some(node) = state_machine.nodes.get(node_id) {
            for target in node
                .transitions
                .values()
                .flat_map(|transition| &transition.targets)
            {
                queue.push_back(target);
            }
        }
    }
    if let Some(node_id) = state_machine
        .nodes
        .keys()
        .find(|node_id| !reachable.contains(node_id.as_str()))
    {
        return invalid(format!(
            "state_machine node {node_id} is not reachable from entry node {entry}"
        ));
    }

    let mut can_reach_final = BTreeSet::new();
    let mut queue = VecDeque::from([final_node]);
    while let Some(node_id) = queue.pop_front() {
        if !can_reach_final.insert(node_id) {
            continue;
        }
        for upstream in upstreams.get(node_id).into_iter().flatten() {
            queue.push_back(upstream);
        }
    }
    if let Some(node_id) = state_machine
        .nodes
        .keys()
        .find(|node_id| !can_reach_final.contains(node_id.as_str()))
    {
        return invalid(format!(
            "state_machine node {node_id} cannot reach final_output node {final_node}"
        ));
    }
    Ok(())
}

fn ensure_human_inputs_are_ordered(
    nodes: &BTreeMap<String, bcs_domain::StateMachineNodeDefinition>,
) -> Result<(), CollaborationRuntimeError> {
    let human_nodes = nodes
        .iter()
        .filter_map(|(node_id, node)| {
            (node.kind == StateMachineNodeKind::HumanInput).then_some(node_id.as_str())
        })
        .collect::<Vec<_>>();

    for (index, left) in human_nodes.iter().enumerate() {
        for right in human_nodes.iter().skip(index + 1) {
            if !node_reaches(nodes, left, right) && !node_reaches(nodes, right, left) {
                return invalid(format!(
                    "human_input nodes {left} and {right} may wait concurrently; MVP requires HumanInput nodes to have an explicit dependency order"
                ));
            }
        }
    }
    Ok(())
}

fn node_reaches(
    nodes: &BTreeMap<String, bcs_domain::StateMachineNodeDefinition>,
    start: &str,
    target: &str,
) -> bool {
    let mut queue = VecDeque::from([start]);
    let mut visited = BTreeSet::new();
    while let Some(node_id) = queue.pop_front() {
        if !visited.insert(node_id) {
            continue;
        }
        let Some(node) = nodes.get(node_id) else {
            continue;
        };
        for next in node
            .transitions
            .values()
            .flat_map(|transition| transition.targets.iter())
        {
            if next == target {
                return true;
            }
            queue.push_back(next);
        }
    }
    false
}

fn invalid<T>(message: impl Into<String>) -> Result<T, CollaborationRuntimeError> {
    Err(CollaborationRuntimeError::InvalidDefinition(message.into()))
}

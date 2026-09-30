//! Load immutable plans without invoking authoring compilation or current limits.

use std::collections::{BTreeMap, BTreeSet};

use bcs_domain::{
    CollaborationRuntimeDefinition, CompiledArtifactProjection, CompiledNodeMetadata,
    StateMachineDefinition, StateMachineExecutionPlan, StateMachineGraphMode,
    StateMachineLoopRouteKind, StateMachineNodeKind,
};
use bcs_service_api::{CollaborationRuntimeError, ServiceError};
use bcs_service_api::port::repo::{StateMachineExecutionPlanSnapshot, StateMachineRunSnapshot};

use crate::definition::validate_definition;
use crate::fixed_loop::{CompiledAuthoringDefinition, execution_plan_content_hash};

pub fn snapshot_execution_plan(
    plan: StateMachineExecutionPlan,
) -> Result<StateMachineExecutionPlanSnapshot, CollaborationRuntimeError> {
    let content_hash = execution_plan_content_hash(&plan)
        .map_err(|error| corrupt(format!("cannot hash plan: {error}")))?;
    Ok(StateMachineExecutionPlanSnapshot {
        compiler_version: plan.compiler_version.clone(),
        content_hash,
        plan,
    })
}

pub fn load_state_machine_snapshot(
    snapshot: StateMachineRunSnapshot,
) -> Result<CompiledAuthoringDefinition, CollaborationRuntimeError> {
    let definition = snapshot.definition;
    let authoring = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(machine) => machine,
        _ => return Err(corrupt("runtime is not a state machine")),
    };
    if authoring.version == 1 && snapshot.execution_plan.is_none() {
        let execution = validate_definition(definition)?;
        return Ok(CompiledAuthoringDefinition {
            definition: execution.definition.clone(), execution, plan: None,
        });
    }
    if authoring.version != 2 || authoring.graph_mode != StateMachineGraphMode::Hierarchical {
        return Err(corrupt("unexpected authoring version, graph mode or plan"));
    }
    let stored = snapshot.execution_plan.ok_or_else(|| corrupt("missing execution plan"))?;
    // This is the reader's compatibility list, independent of which compiler is current.
    if stored.compiler_version != "bcs.fixed-loop.compiler/v1"
        || stored.compiler_version != stored.plan.compiler_version
    {
        return Err(corrupt("unsupported or inconsistent compiler version"));
    }
    if stored.content_hash != execution_plan_content_hash(&stored.plan)
        .map_err(|error| corrupt(format!("cannot hash plan: {error}")))?
    {
        return Err(corrupt("execution plan hash mismatch"));
    }
    validate_plan_mapping(authoring, &stored.plan)?;
    let mut executable = definition.clone();
    executable.runtime = CollaborationRuntimeDefinition::StateMachine(stored.plan.state_machine.clone());
    if let Some(requires) = &mut executable.requires {
        requires.server_features.retain(|feature| !matches!(feature.as_str(),
            "state_machine.version.2" | "state_machine.graph_mode.hierarchical"
            | "state_machine.node.kind.loop" | "state_machine.loop.mode.fixed"
            | "state_machine.loop.previous_result"));
    }
    let mut execution = validate_definition(executable)
        .map_err(|error| corrupt(format!("invalid persisted DAG: {error}")))?;
    execution.execution_plan = Some(stored.plan.clone());
    Ok(CompiledAuthoringDefinition { definition, execution, plan: Some(stored.plan) })
}

fn validate_plan_mapping(
    authoring: &StateMachineDefinition,
    plan: &StateMachineExecutionPlan,
) -> Result<(), CollaborationRuntimeError> {
    if plan.state_machine.version != 1 || plan.state_machine.graph_mode != StateMachineGraphMode::Acyclic
        || plan.node_metadata.len() != plan.state_machine.nodes.len()
    {
        return Err(corrupt("invalid executable graph or incomplete node metadata"));
    }
    let mut logical_nodes = BTreeMap::new();
    for (id, node) in &plan.state_machine.nodes {
        let meta = plan.node_metadata.get(id).ok_or_else(|| corrupt("missing node metadata"))?;
        if meta.execution_node_id != *id {
            return Err(corrupt("execution node ID mismatch"));
        }
        let original = if let Some(loop_id) = &meta.loop_id {
            let body = authoring.nodes.get(loop_id).and_then(|node| node.loop_definition.as_ref())
                .ok_or_else(|| corrupt("unknown logical Loop"))?;
            let iteration = meta.iteration.ok_or_else(|| corrupt("missing iteration"))?;
            if iteration == 0 || iteration > body.max_iterations
                || meta.max_iterations != Some(body.max_iterations)
                || meta.is_loop_entry != (meta.definition_node_id == body.entry_node)
                || meta.is_loop_result != (meta.definition_node_id == body.result_node)
            {
                return Err(corrupt("invalid iteration or entry/result metadata"));
            }
            body.nodes.get(&meta.definition_node_id)
        } else {
            if meta.definition_node_id != *id || meta.iteration.is_some() || meta.max_iterations.is_some()
                || meta.is_loop_entry || meta.is_loop_result || meta.previous_result_node_id.is_some()
            {
                return Err(corrupt("invalid ordinary node metadata"));
            }
            authoring.nodes.get(id)
        }.ok_or_else(|| corrupt("unknown definition node"))?;
        if original.kind == StateMachineNodeKind::Loop {
            return Err(corrupt("Loop cannot be executable"));
        }
        let mut expected = original.clone();
        expected.transitions = node.transitions.clone();
        if serde_json::to_value(expected).map_err(|error| corrupt(error.to_string()))?
            != serde_json::to_value(node).map_err(|error| corrupt(error.to_string()))?
        {
            return Err(corrupt("executable node differs from its snapshotted definition"));
        }
        let mut expected_outcomes = BTreeMap::new();
        if meta.is_loop_result {
            let parent = &authoring.nodes[meta.loop_id.as_ref().expect("validated Loop")];
            let body = parent.loop_definition.as_ref().expect("validated body");
            for outcome in &body.continue_outcomes {
                let count = if meta.iteration < meta.max_iterations { 1 } else {
                    parent.transitions.get(&body.exhausted_outcome)
                        .ok_or_else(|| corrupt("missing exhausted targets"))?.targets.len()
                };
                expected_outcomes.insert(outcome, count);
            }
            for outcome in &body.break_outcomes {
                let count = parent.transitions.get(outcome)
                    .ok_or_else(|| corrupt("missing break targets"))?.targets.len();
                expected_outcomes.insert(outcome, count);
            }
        } else {
            expected_outcomes.extend(original.transitions.iter().map(|(outcome, edge)| (outcome, edge.targets.len())));
        }
        let actual_outcomes: BTreeMap<_, _> = node.transitions.iter()
            .map(|(outcome, edge)| (outcome, edge.targets.len())).collect();
        if actual_outcomes != expected_outcomes {
            return Err(corrupt("missing or extra transition targets"));
        }
        if logical_nodes.insert((meta.loop_id.clone(), meta.iteration, meta.definition_node_id.clone()), id).is_some() {
            return Err(corrupt("duplicate logical node/iteration mapping"));
        }
    }
    let expected_count = authoring.nodes.values().try_fold(0usize, |count, node| {
        let added = match &node.loop_definition {
            Some(body) => body.nodes.len().checked_mul(body.max_iterations as usize),
            None => Some(1),
        };
        added.and_then(|added| count.checked_add(added))
    }).ok_or_else(|| corrupt("node count overflow"))?;
    if expected_count != logical_nodes.len() {
        return Err(corrupt("incomplete iteration mapping"));
    }
    for meta in plan.node_metadata.values() {
        let previous = if meta.is_loop_entry && meta.iteration.is_some_and(|iteration| iteration > 1) {
            let body = &authoring.nodes[meta.loop_id.as_ref().expect("validated Loop")].loop_definition
                .as_ref().expect("validated body");
            Some(*logical_nodes.get(&(meta.loop_id.clone(), meta.iteration.map(|n| n - 1), body.result_node.clone()))
                .ok_or_else(|| corrupt("missing previous iteration result"))?)
        } else { None };
        if meta.previous_result_node_id.as_ref() != previous {
            return Err(corrupt("previous result mapping mismatch"));
        }
    }
    let graph_edges: BTreeSet<_> = plan.state_machine.nodes.iter().flat_map(|(id, node)| {
        node.transitions.iter().flat_map(move |(outcome, transition)| {
            transition.targets.iter().map(move |target| (id, outcome, target))
        })
    }).collect();
    let mut metadata_edges = BTreeSet::new();
    for edge in &plan.edge_metadata {
        let key = (&edge.source_execution_node_id, &edge.outcome, &edge.target_execution_node_id);
        if !graph_edges.contains(&key) || !metadata_edges.insert(key) {
            return Err(corrupt("unknown or duplicate edge metadata"));
        }
        let source = plan.node_metadata.get(&edge.source_execution_node_id)
            .ok_or_else(|| corrupt("unknown edge source"))?;
        let target = plan.node_metadata.get(&edge.target_execution_node_id)
            .ok_or_else(|| corrupt("unknown edge target"))?;
        if source.is_loop_result {
            let parent = &authoring.nodes[source.loop_id.as_ref().expect("validated Loop")];
            let body = parent.loop_definition.as_ref().expect("validated body");
            let route = edge.loop_route.as_ref().ok_or_else(|| corrupt("missing result route"))?;
            if body.continue_outcomes.contains(&edge.outcome) && source.iteration < source.max_iterations {
                if route.kind != StateMachineLoopRouteKind::Continue || route.logical_outcome != edge.outcome
                    || edge.artifact_projection != CompiledArtifactProjection::ControlOnly
                    || target.loop_id != source.loop_id || target.iteration != source.iteration.map(|n| n + 1)
                    || !target.is_loop_entry
                {
                    return Err(corrupt("invalid continue route"));
                }
            } else {
                let (kind, outcome) = if body.continue_outcomes.contains(&edge.outcome) {
                    (StateMachineLoopRouteKind::Exhausted, &body.exhausted_outcome)
                } else if body.break_outcomes.contains(&edge.outcome) {
                    (StateMachineLoopRouteKind::Break, &edge.outcome)
                } else { return Err(corrupt("unknown result outcome")); };
                if route.kind != kind || &route.logical_outcome != outcome
                    || edge.artifact_projection != CompiledArtifactProjection::Artifact
                    || !parent.transitions.get(outcome).is_some_and(|transition| {
                        transition.targets.iter().any(|id| matches_outer_target(id, target))
                    })
                {
                    return Err(corrupt("invalid break/exhausted route"));
                }
            }
        } else if edge.loop_route.is_some() || edge.artifact_projection != CompiledArtifactProjection::Artifact {
            return Err(corrupt("ordinary edge cannot carry a Loop route or ControlOnly projection"));
        } else {
            let matches = match &source.loop_id {
                Some(loop_id) => {
                    let body = authoring.nodes[loop_id].loop_definition.as_ref().expect("validated body");
                    body.nodes[&source.definition_node_id].transitions.get(&edge.outcome).is_some_and(|transition| {
                        target.loop_id.as_ref() == Some(loop_id) && target.iteration == source.iteration
                            && transition.targets.contains(&target.definition_node_id)
                    })
                }
                None => authoring.nodes[&source.definition_node_id].transitions.get(&edge.outcome).is_some_and(|transition| {
                    transition.targets.iter().any(|id| matches_outer_target(id, target))
                }),
            };
            if !matches {
                return Err(corrupt("ordinary edge differs from the snapshotted definition"));
            }
        }
    }
    if graph_edges != metadata_edges {
        return Err(corrupt("incomplete edge metadata"));
    }
    Ok(())
}

fn matches_outer_target(id: &str, target: &CompiledNodeMetadata) -> bool {
    match &target.loop_id {
        Some(loop_id) => loop_id == id && target.iteration == Some(1) && target.is_loop_entry,
        None => target.definition_node_id == id,
    }
}

fn corrupt(message: impl std::fmt::Display) -> CollaborationRuntimeError {
    CollaborationRuntimeError::Internal(ServiceError::InternalError(format!(
        "corrupt state-machine snapshot: {message}"
    )))
}

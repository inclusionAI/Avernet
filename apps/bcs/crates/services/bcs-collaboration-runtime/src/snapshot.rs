//! Load immutable plans without invoking authoring compilation or current limits.

use std::collections::{BTreeMap, BTreeSet};

use bcs_domain::{
    CollaborationRuntimeDefinition, CompiledArtifactProjection, CompiledNodeMetadata,
    StateMachineDefinition, StateMachineExecutionPlan, StateMachineGraphMode,
    StateMachineLoopFrame, StateMachineLoopRouteKind, StateMachineNodeDefinition,
    StateMachineNodeKind,
};
use bcs_service_api::{CollaborationRuntimeError, ServiceError};
use bcs_service_api::port::repo::{StateMachineExecutionPlanSnapshot, StateMachineRunSnapshot};

use crate::definition::{node_loop_path, validate_definition};
use crate::fixed_loop::{
    CompiledAuthoringDefinition, execution_plan_content_hash, FIXED_LOOP_COMPILER_VERSION,
    NESTED_FIXED_LOOP_COMPILER_VERSION,
};

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
    if stored.compiler_version != stored.plan.compiler_version {
        return Err(corrupt("unsupported or inconsistent compiler version"));
    }
    if stored.content_hash != execution_plan_content_hash(&stored.plan)
        .map_err(|error| corrupt(format!("cannot hash plan: {error}")))?
    {
        return Err(corrupt("execution plan hash mismatch"));
    }
    let nesting_feature = definition.requires.as_ref().is_some_and(|requires|
        requires.server_features.iter().any(|feature| feature == "state_machine.loop.nesting"));
    match stored.compiler_version.as_str() {
        FIXED_LOOP_COMPILER_VERSION => validate_v1_plan_mapping(authoring, &stored.plan)?,
        NESTED_FIXED_LOOP_COMPILER_VERSION if nesting_feature =>
            validate_v2_plan_mapping(authoring, &stored.plan)?,
        NESTED_FIXED_LOOP_COMPILER_VERSION =>
            return Err(corrupt("compiler v2 snapshot is missing state_machine.loop.nesting")),
        _ => return Err(corrupt("unsupported or inconsistent compiler version")),
    }
    let mut executable = definition.clone();
    executable.runtime = CollaborationRuntimeDefinition::StateMachine(stored.plan.state_machine.clone());
    if let Some(requires) = &mut executable.requires {
        requires.server_features.retain(|feature| !matches!(feature.as_str(),
            "state_machine.version.2" | "state_machine.graph_mode.hierarchical"
            | "state_machine.node.kind.loop" | "state_machine.loop.mode.fixed"
            | "state_machine.loop.previous_result" | "state_machine.loop.nesting"));
    }
    let mut execution = validate_definition(executable)
        .map_err(|error| corrupt(format!("invalid persisted DAG: {error}")))?;
    execution.execution_plan = Some(stored.plan.clone());
    Ok(CompiledAuthoringDefinition { definition, execution, plan: Some(stored.plan) })
}

fn validate_v1_plan_mapping(
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
        if meta.loop_path.is_some() {
            return Err(corrupt("compiler v1 metadata must omit loop_path"));
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

type LogicalNodeKey = (Vec<StateMachineLoopFrame>, String);

struct ResolvedLoop<'a> {
    node: &'a StateMachineNodeDefinition,
    body: &'a bcs_domain::FixedLoopDefinition,
    parent_scope: &'a BTreeMap<String, StateMachineNodeDefinition>,
}

struct ResolvedLeaf<'a> {
    node: &'a StateMachineNodeDefinition,
    scope: &'a BTreeMap<String, StateMachineNodeDefinition>,
    nearest_loop: Option<ResolvedLoop<'a>>,
}

fn validate_v2_plan_mapping(
    authoring: &StateMachineDefinition,
    plan: &StateMachineExecutionPlan,
) -> Result<(), CollaborationRuntimeError> {
    if plan.compiler_version != NESTED_FIXED_LOOP_COMPILER_VERSION
        || plan.state_machine.version != 1
        || plan.state_machine.graph_mode != StateMachineGraphMode::Acyclic
        || plan.node_metadata.len() != plan.state_machine.nodes.len()
    {
        return Err(corrupt("invalid compiler v2 executable graph or incomplete node metadata"));
    }
    let mut expected = BTreeSet::new();
    collect_expected_nodes(&authoring.nodes, &[], &mut expected)?;
    if !expected.iter().any(|(path, _)| path.len() >= 2) {
        return Err(corrupt("compiler v2 snapshot has no nested Loop"));
    }
    let mut logical_nodes = BTreeMap::new();
    for (id, node) in &plan.state_machine.nodes {
        let meta = plan.node_metadata.get(id).ok_or_else(|| corrupt("missing node metadata"))?;
        if meta.execution_node_id != *id {
            return Err(corrupt("execution node ID mismatch"));
        }
        let path = node_loop_path(plan, meta).map_err(|error| corrupt(error.to_string()))?;
        let resolved = resolve_leaf(authoring, &path, &meta.definition_node_id)?;
        if resolved.node.kind == StateMachineNodeKind::Loop {
            return Err(corrupt("Loop cannot be executable"));
        }
        let (is_entry, is_result) = match &resolved.nearest_loop {
            Some(parent) => (
                meta.definition_node_id == parent.body.entry_node,
                meta.definition_node_id == parent.body.result_node,
            ),
            None => {
                if meta.definition_node_id != *id || meta.loop_id.is_some()
                    || meta.iteration.is_some() || meta.max_iterations.is_some()
                    || meta.loop_path.is_some() || meta.previous_result_node_id.is_some()
                {
                    return Err(corrupt("invalid ordinary compiler v2 node metadata"));
                }
                (false, false)
            }
        };
        if meta.is_loop_entry != is_entry || meta.is_loop_result != is_result {
            return Err(corrupt("invalid compiler v2 entry/result metadata"));
        }
        let mut expected_node = resolved.node.clone();
        expected_node.transitions = node.transitions.clone();
        if serde_json::to_value(expected_node).map_err(|error| corrupt(error.to_string()))?
            != serde_json::to_value(node).map_err(|error| corrupt(error.to_string()))?
        {
            return Err(corrupt("executable node differs from its snapshotted definition"));
        }
        let key = (path, meta.definition_node_id.clone());
        if logical_nodes.insert(key, id).is_some() {
            return Err(corrupt("duplicate compiler v2 logical node mapping"));
        }
    }
    if logical_nodes.keys().cloned().collect::<BTreeSet<_>>() != expected {
        return Err(corrupt("incomplete compiler v2 logical node mapping"));
    }
    for (key, id) in &logical_nodes {
        let meta = &plan.node_metadata[*id];
        let resolved = resolve_leaf(authoring, &key.0, &key.1)?;
        let previous = if meta.is_loop_entry {
            let frame = key.0.last().ok_or_else(|| corrupt("Loop entry has no path"))?;
            if frame.iteration > 1 {
                let mut previous_path = key.0.clone();
                previous_path.last_mut().expect("Loop frame").iteration -= 1;
                let result_id = &resolved.nearest_loop.as_ref().expect("Loop entry").body.result_node;
                Some(*logical_nodes.get(&(previous_path, result_id.clone()))
                    .ok_or_else(|| corrupt("missing previous compiler v2 result"))?)
            } else { None }
        } else { None };
        if meta.previous_result_node_id.as_ref() != previous {
            return Err(corrupt("compiler v2 previous result mapping mismatch"));
        }
        let expected_outcomes = expected_outcome_counts(&resolved, &key.0, meta)?;
        let actual_outcomes: BTreeMap<_, _> = plan.state_machine.nodes[*id].transitions.iter()
            .map(|(outcome, transition)| (outcome.clone(), transition.targets.len())).collect();
        if actual_outcomes != expected_outcomes {
            return Err(corrupt("compiler v2 transition outcome mapping mismatch"));
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
            return Err(corrupt("unknown or duplicate compiler v2 edge metadata"));
        }
        let source_meta = plan.node_metadata.get(&edge.source_execution_node_id)
            .ok_or_else(|| corrupt("unknown compiler v2 edge source"))?;
        let source_path = node_loop_path(plan, source_meta).map_err(|error| corrupt(error.to_string()))?;
        let resolved = resolve_leaf(authoring, &source_path, &source_meta.definition_node_id)?;
        let expected_targets = expected_edge_targets(&resolved, &source_path, source_meta, &edge.outcome)?;
        if !expected_targets.iter().any(|target| {
            logical_nodes.get(target).is_some_and(|id| id.as_str() == edge.target_execution_node_id)
        }) {
            return Err(corrupt("compiler v2 edge crosses its logical scope"));
        }
        if source_meta.is_loop_result {
            let parent = resolved.nearest_loop.as_ref().expect("Loop result");
            let frame = source_path.last().expect("Loop path");
            let (kind, logical_outcome, projection) = if parent.body.continue_outcomes.contains(&edge.outcome) {
                if frame.iteration < frame.max_iterations {
                    (StateMachineLoopRouteKind::Continue, edge.outcome.as_str(), CompiledArtifactProjection::ControlOnly)
                } else {
                    (StateMachineLoopRouteKind::Exhausted, parent.body.exhausted_outcome.as_str(), CompiledArtifactProjection::Artifact)
                }
            } else if parent.body.break_outcomes.contains(&edge.outcome) {
                (StateMachineLoopRouteKind::Break, edge.outcome.as_str(), CompiledArtifactProjection::Artifact)
            } else {
                return Err(corrupt("unknown compiler v2 result outcome"));
            };
            let route = edge.loop_route.as_ref().ok_or_else(|| corrupt("missing compiler v2 result route"))?;
            if route.kind != kind || route.logical_outcome != logical_outcome
                || edge.artifact_projection != projection
            {
                return Err(corrupt("invalid compiler v2 result route"));
            }
        } else if edge.loop_route.is_some()
            || edge.artifact_projection != CompiledArtifactProjection::Artifact
        {
            return Err(corrupt("ordinary compiler v2 edge has Loop control metadata"));
        }
    }
    if graph_edges != metadata_edges {
        return Err(corrupt("incomplete compiler v2 edge metadata"));
    }
    Ok(())
}

fn collect_expected_nodes(
    scope: &BTreeMap<String, StateMachineNodeDefinition>,
    frames: &[StateMachineLoopFrame],
    expected: &mut BTreeSet<LogicalNodeKey>,
) -> Result<(), CollaborationRuntimeError> {
    for (id, node) in scope {
        if node.kind == StateMachineNodeKind::Loop {
            let body = node.loop_definition.as_ref().ok_or_else(|| corrupt("Loop definition is missing"))?;
            for iteration in 1..=body.max_iterations {
                let mut path = frames.to_vec();
                path.push(StateMachineLoopFrame {
                    loop_id: id.clone(), iteration, max_iterations: body.max_iterations,
                });
                collect_expected_nodes(&body.nodes, &path, expected)?;
            }
        } else {
            if node.loop_definition.is_some() || !expected.insert((frames.to_vec(), id.clone())) {
                return Err(corrupt("invalid or duplicate authoring leaf"));
            }
        }
    }
    Ok(())
}

fn resolve_leaf<'a>(
    authoring: &'a StateMachineDefinition,
    path: &[StateMachineLoopFrame],
    definition_node_id: &str,
) -> Result<ResolvedLeaf<'a>, CollaborationRuntimeError> {
    let mut scope = &authoring.nodes;
    let mut nearest_loop = None;
    for frame in path {
        let loop_node = scope.get(&frame.loop_id).ok_or_else(|| corrupt("unknown Loop path"))?;
        if loop_node.kind != StateMachineNodeKind::Loop {
            return Err(corrupt("Loop path resolves to an executable node"));
        }
        let body = loop_node.loop_definition.as_ref().ok_or_else(|| corrupt("Loop body is missing"))?;
        if frame.max_iterations != body.max_iterations
            || frame.iteration == 0 || frame.iteration > frame.max_iterations
        {
            return Err(corrupt("Loop path iteration does not match authoring"));
        }
        nearest_loop = Some(ResolvedLoop { node: loop_node, body, parent_scope: scope });
        scope = &body.nodes;
    }
    let node = scope.get(definition_node_id).ok_or_else(|| corrupt("unknown definition leaf"))?;
    Ok(ResolvedLeaf { node, scope, nearest_loop })
}

fn expected_outcome_counts(
    resolved: &ResolvedLeaf<'_>,
    path: &[StateMachineLoopFrame],
    meta: &CompiledNodeMetadata,
) -> Result<BTreeMap<String, usize>, CollaborationRuntimeError> {
    if !meta.is_loop_result {
        return Ok(resolved.node.transitions.iter()
            .map(|(outcome, transition)| (outcome.clone(), transition.targets.len())).collect());
    }
    let parent = resolved.nearest_loop.as_ref().ok_or_else(|| corrupt("Loop result has no parent"))?;
    let frame = path.last().ok_or_else(|| corrupt("Loop result has no path"))?;
    let mut outcomes = BTreeMap::new();
    for outcome in &parent.body.continue_outcomes {
        let count = if frame.iteration < frame.max_iterations { 1 } else {
            parent.node.transitions.get(&parent.body.exhausted_outcome)
                .ok_or_else(|| corrupt("missing exhausted transition"))?.targets.len()
        };
        outcomes.insert(outcome.clone(), count);
    }
    for outcome in &parent.body.break_outcomes {
        let count = parent.node.transitions.get(outcome)
            .ok_or_else(|| corrupt("missing break transition"))?.targets.len();
        outcomes.insert(outcome.clone(), count);
    }
    Ok(outcomes)
}

fn expected_edge_targets(
    resolved: &ResolvedLeaf<'_>,
    path: &[StateMachineLoopFrame],
    meta: &CompiledNodeMetadata,
    outcome: &str,
) -> Result<Vec<LogicalNodeKey>, CollaborationRuntimeError> {
    if !meta.is_loop_result {
        let transition = resolved.node.transitions.get(outcome)
            .ok_or_else(|| corrupt("unknown ordinary transition outcome"))?;
        return transition.targets.iter().map(|target|
            logical_target(resolved.scope, path, target)).collect();
    }
    let parent = resolved.nearest_loop.as_ref().ok_or_else(|| corrupt("Loop result has no parent"))?;
    let frame = path.last().ok_or_else(|| corrupt("Loop result has no path"))?;
    if parent.body.continue_outcomes.iter().any(|value| value == outcome)
        && frame.iteration < frame.max_iterations
    {
        let mut next_path = path.to_vec();
        next_path.last_mut().expect("Loop frame").iteration += 1;
        return Ok(vec![(next_path, parent.body.entry_node.clone())]);
    }
    let logical_outcome = if parent.body.continue_outcomes.iter().any(|value| value == outcome) {
        &parent.body.exhausted_outcome
    } else if parent.body.break_outcomes.iter().any(|value| value == outcome) {
        outcome
    } else {
        return Err(corrupt("unknown Loop result outcome"));
    };
    let transition = parent.node.transitions.get(logical_outcome)
        .ok_or_else(|| corrupt("missing Loop exit transition"))?;
    let parent_path = &path[..path.len() - 1];
    transition.targets.iter().map(|target|
        logical_target(parent.parent_scope, parent_path, target)).collect()
}

fn logical_target(
    scope: &BTreeMap<String, StateMachineNodeDefinition>,
    frames: &[StateMachineLoopFrame],
    target: &str,
) -> Result<LogicalNodeKey, CollaborationRuntimeError> {
    let node = scope.get(target).ok_or_else(|| corrupt("transition target is outside its scope"))?;
    if node.kind == StateMachineNodeKind::Loop {
        let body = node.loop_definition.as_ref().ok_or_else(|| corrupt("target Loop body is missing"))?;
        let mut path = frames.to_vec();
        path.push(StateMachineLoopFrame {
            loop_id: target.into(), iteration: 1, max_iterations: body.max_iterations,
        });
        Ok((path, body.entry_node.clone()))
    } else {
        Ok((frames.to_vec(), target.into()))
    }
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

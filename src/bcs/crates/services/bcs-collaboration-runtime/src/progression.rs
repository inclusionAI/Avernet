//! Derive unfinished DAG work from immutable outcomes, without edge checkpoints.

use std::collections::{BTreeMap, BTreeSet};

use bcs_domain::{CollaborationRuntimeDefinition, StateMachineNodeRun, StateMachineNodeStatus};
use bcs_service_api::{CollaborationRuntimeError, ServiceError};

use crate::definition::CompiledStateMachine;

pub(crate) struct Progression {
    pub skip: Vec<String>,
    pub ready: Vec<String>,
    pub done: bool,
}

pub(crate) fn derive(
    compiled: &CompiledStateMachine,
    run_id: &str,
    nodes: &[StateMachineNodeRun],
) -> Result<Progression, CollaborationRuntimeError> {
    let CollaborationRuntimeDefinition::StateMachine(machine) = &compiled.definition.runtime else {
        return Err(invalid("missing state-machine snapshot"));
    };
    let by_id: BTreeMap<_, _> = nodes.iter().map(|node| (node.node_id.as_str(), node)).collect();
    if nodes.len() != machine.nodes.len() || by_id.len() != nodes.len()
        || nodes.iter().any(|node| node.run_id != run_id || !machine.nodes.contains_key(&node.node_id))
    {
        return Err(invalid("Node Runs do not match the immutable snapshot"));
    }
    for node in nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Completed) {
        let definition = &machine.nodes[&node.node_id];
        let Some(outcome) = node.outcome.as_deref() else { return Err(invalid("completed node has no outcome")); };
        let valid_outcome = definition.judge.as_ref().map_or(outcome == "complete", |judge| {
            judge.outcomes.iter().any(|declared| declared == outcome)
        });
        if !valid_outcome || node.artifact_text.is_none() || node.completed_at.is_none() {
            return Err(invalid("completed node has invalid or incomplete result"));
        }
    }

    // Unfinished nodes retain every potential route. Completed nodes retain
    // only their selected route. This protects a future Loop break and joins
    // reachable through another branch. Iterating all nodes below also repairs
    // descendants of an already Skipped intermediate node.
    let mut reachable = BTreeSet::new();
    let mut stack = compiled.initial_nodes.clone();
    while let Some(id) = stack.pop() {
        if by_id[id.as_str()].status == StateMachineNodeStatus::Skipped || !reachable.insert(id.clone()) {
            continue;
        }
        let node = by_id[id.as_str()];
        for (outcome, transition) in &machine.nodes[&id].transitions {
            if node.status != StateMachineNodeStatus::Completed || node.outcome.as_ref() == Some(outcome) {
                stack.extend(transition.targets.iter().cloned());
            }
        }
    }
    let mut skip = Vec::new();
    let mut ready = Vec::new();
    for node in nodes.iter().filter(|node| is_unstarted(node.status)) {
        if !reachable.contains(&node.node_id) {
            skip.push(node.node_id.clone());
            continue;
        }
        let upstreams = compiled.upstreams.get(&node.node_id).map(Vec::as_slice).unwrap_or(&[]);
        // An initial Pending node may belong to an interrupted startup whose
        // opening was never saved. Only an already committed retry is evidence
        // that the initial node previously passed the startup barrier.
        let selected = (upstreams.is_empty() && node.status == StateMachineNodeStatus::RetryScheduled && node.attempt > 0)
            || upstreams.iter().any(|id| {
                let source = by_id[id.as_str()];
                source.status == StateMachineNodeStatus::Completed
                    && source.outcome.as_ref().and_then(|outcome| machine.nodes[id].transitions.get(outcome))
                        .is_some_and(|transition| transition.targets.contains(&node.node_id))
            });
        if selected && upstreams.iter().all(|id| matches!(
            by_id[id.as_str()].status, StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped
        )) {
            ready.push(node.node_id.clone());
        }
    }
    let done = nodes.iter().all(|node| matches!(node.status, StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped));
    Ok(Progression { skip, ready, done })
}

pub(crate) fn is_unstarted(status: StateMachineNodeStatus) -> bool {
    matches!(status, StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready | StateMachineNodeStatus::RetryScheduled)
}

fn invalid(message: &str) -> CollaborationRuntimeError {
    CollaborationRuntimeError::Internal(ServiceError::InternalError(format!("invalid progression state: {message}")))
}

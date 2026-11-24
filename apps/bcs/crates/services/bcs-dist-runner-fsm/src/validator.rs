//! Shared publication gate for strict-FSM workflows (plan T5).
//!
//! [`FsmValidator`] implements the shared [`FsmValidatorCoreService`] trait
//! from the service-api core contract, so the strict group directory and the
//! runner reuse one publication gate instead of re-deriving node whitelists.
//! The check is pure: no store, network, clock or randomness. Rejections
//! happen before any Task exists (the D0 decision threshold: reject, do not
//! accept-then-suspend).

use bcs_dist_model::{NodeId, SessionSpec};
use bcs_domain::collaboration::{StateMachineDefinition, StateMachineNodeKind};
use bcs_service_api::core::dist_runner::{
    FsmCapabilities, FsmValidatorCoreService, RunnerError, ValidatedWorkflow,
};
use std::collections::{BTreeMap, BTreeSet};

/// Concrete shared validator used by both the runner and the group directory.
pub struct FsmValidator;

/// Executor capability profile frozen at the D0 decision threshold: BotTask
/// DAGs with a single designated final output, bounded retry and bounded
/// fan-out; Judge/Loop/HumanInput are outside the admitted set.
pub fn d0_capabilities() -> FsmCapabilities {
    FsmCapabilities {
        dispatchable_node_kinds: vec![StateMachineNodeKind::BotTask],
        max_attempts: 3,
        max_fan_out: 8,
    }
}

fn invalid(code: impl Into<String>) -> RunnerError {
    RunnerError::InvalidWorkflow { code: code.into() }
}

/// Forward edges along the single admitted outcome (`complete`); the final
/// node has none by construction.
fn complete_targets(
    workflow: &StateMachineDefinition,
) -> BTreeMap<String, Vec<String>> {
    workflow
        .nodes
        .iter()
        .map(|(id, node)| {
            let mut targets: Vec<String> = node
                .transitions
                .get("complete")
                .map(|transition| transition.targets.clone())
                .unwrap_or_default();
            targets.sort();
            (id.clone(), targets)
        })
        .collect()
}

/// Depth-first cycle detection over the `complete` edges (mirrors the final
/// node rules of the collaboration runtime without embedding its IO).
fn ensure_acyclic(nodes: &BTreeMap<String, Vec<String>>) -> Result<(), RunnerError> {
    const VISITING: u8 = 1;
    const DONE: u8 = 2;
    let mut marks: BTreeMap<String, u8> = nodes.keys().map(|id| (id.clone(), 0)).collect();
    for root in nodes.keys() {
        if marks[root] != 0 {
            continue;
        }
        let mut stack = vec![(root.clone(), false)];
        while let Some((node, expanded)) = stack.pop() {
            if expanded {
                marks.insert(node, DONE);
                continue;
            }
            match marks[&node] {
                DONE => continue,
                VISITING => {
                    return Err(invalid(format!("cycle detected through node {node}")));
                }
                _ => {
                    marks.insert(node.clone(), VISITING);
                    stack.push((node.clone(), true));
                    for target in &nodes[&node] {
                        stack.push((target.clone(), false));
                    }
                }
            }
        }
    }
    Ok(())
}

impl FsmValidatorCoreService for FsmValidator {
    fn validate(
        &self,
        frozen: &SessionSpec,
        workflow: &StateMachineDefinition,
        supported: &FsmCapabilities,
    ) -> Result<ValidatedWorkflow, RunnerError> {
        if workflow.human_input_channel.is_some() {
            return Err(invalid(
                "human_input_channel declares an interactive surface; the strict \
                 FSM plane rejects it at validate() time",
            ));
        }
        if workflow.nodes.is_empty() {
            return Err(invalid("workflow declares no nodes"));
        }

        let mut upstreams: BTreeMap<String, BTreeSet<String>> =
            workflow.nodes.keys().map(|id| (id.clone(), BTreeSet::new())).collect();
        let mut final_nodes: Vec<String> = Vec::new();
        for (id, node) in &workflow.nodes {
            if node.judge.is_some() {
                return Err(invalid(format!(
                    "node {id} declares a judge policy; Judge is rejected at validate() time, \
                     never accepted-then-suspended"
                )));
            }
            if node.loop_definition.is_some() {
                return Err(invalid(format!(
                    "node {id} declares a fixed loop body; Loop is rejected at validate() time"
                )));
            }
            if !supported.dispatchable_node_kinds.contains(&node.kind) {
                // D0 threshold: bot_task only. judge/loop are additionally
                // caught by the policy checks above.
                return Err(invalid(format!(
                    "node {id} has unsupported node kind {:?}; the strict FSM plane dispatches \
                     bot_task nodes only (human_input/loop/judge rejected)",
                    node.kind
                )));
            }
            if node.final_output {
                if !node.transitions.is_empty() {
                    return Err(invalid(format!(
                        "node {id} is final_output and must not declare transitions"
                    )));
                }
                final_nodes.push(id.clone());
                continue;
            }
            if node.transitions.is_empty() {
                return Err(invalid(format!(
                    "node {id} is not final_output and must declare a transition"
                )));
            }
            for (outcome, transition) in &node.transitions {
                if outcome != "complete" {
                    return Err(invalid(format!(
                        "node {id} transition {outcome}: only the complete outcome is admitted \
                         for judge-less nodes"
                    )));
                }
                if transition.guard.is_some() {
                    return Err(invalid(format!(
                        "node {id} transition {outcome}: guarded transitions are not admitted"
                    )));
                }
                if transition.targets.len() > supported.max_fan_out as usize {
                    return Err(invalid(format!(
                        "node {id} fans out to {} targets; the admitted bound is {}",
                        transition.targets.len(),
                        supported.max_fan_out
                    )));
                }
                if transition.targets.is_empty() {
                    return Err(invalid(format!(
                        "node {id} transition {outcome} has no target"
                    )));
                }
                for target in &transition.targets {
                    if !workflow.nodes.contains_key(target) {
                        return Err(invalid(format!(
                            "node {id} transition target {target} not found"
                        )));
                    }
                    upstreams
                        .entry(target.clone())
                        .or_default()
                        .insert(id.clone());
                }
            }
        }
        if final_nodes.len() != 1 {
            return Err(invalid(format!(
                "workflow must designate exactly one final_output node; found {}",
                final_nodes.len()
            )));
        }
        let final_node = final_nodes[0].clone();
        let entries: Vec<String> = upstreams
            .iter()
            .filter(|(_, upstream)| upstream.is_empty())
            .map(|(id, _)| id.clone())
            .collect();
        if entries.len() != 1 {
            return Err(invalid(format!(
                "workflow must have exactly one zero in-degree entry node; found {}",
                entries.len()
            )));
        }
        let entry = entries[0].clone();
        let edges = complete_targets(workflow);
        ensure_acyclic(&edges)?;

        // Every node runs between the single entry and the single final
        // output, so a quiet DAG at final-node success is structural.
        let mut reachable: BTreeSet<String> = BTreeSet::new();
        let mut stack = vec![entry.clone()];
        while let Some(node) = stack.pop() {
            if !reachable.insert(node.clone()) {
                continue;
            }
            for target in &edges[&node] {
                stack.push(target.clone());
            }
        }
        for id in workflow.nodes.keys() {
            if !reachable.contains(id) {
                return Err(invalid(format!(
                    "node {id} is not reachable from the entry node {entry}"
                )));
            }
        }
        let mut back_edges: BTreeMap<String, Vec<String>> =
            workflow.nodes.keys().map(|id| (id.clone(), Vec::new())).collect();
        for (id, targets) in &edges {
            for target in targets {
                back_edges.entry(target.clone()).or_default().push(id.clone());
            }
        }
        let mut final_bound: BTreeSet<String> = BTreeSet::new();
        stack = vec![final_node.clone()];
        while let Some(node) = stack.pop() {
            if !final_bound.insert(node.clone()) {
                continue;
            }
            for upstream in &back_edges[&node] {
                stack.push(upstream.clone());
            }
        }
        for id in workflow.nodes.keys() {
            if !final_bound.contains(id) {
                return Err(invalid(format!(
                    "node {id} has no path to the designated final_output node {final_node}"
                )));
            }
        }

        // Participant bindings are per node in the frozen spec and must
        // cover every dispatchable node exactly once.
        let mut bound: BTreeMap<String, ()> = BTreeMap::new();
        for participant in &frozen.participants {
            if bound.insert(participant.node.0.clone(), ()).is_some() {
                return Err(invalid(format!(
                    "node {} has more than one participant binding",
                    participant.node.0
                )));
            }
        }
        for id in workflow.nodes.keys() {
            if !bound.contains_key(id) {
                return Err(invalid(format!(
                    "node {id} has no participant binding in the frozen session spec"
                )));
            }
        }

        let ceiling = supported.max_attempts.max(1);
        let mut max_attempts = BTreeMap::new();
        for (id, node) in &workflow.nodes {
            let asked = node.max_attempts.unwrap_or(workflow.defaults.max_attempts);
            if asked < 1 {
                return Err(invalid(format!("node {id} max_attempts must be at least 1")));
            }
            max_attempts.insert(id.clone(), (asked as u32).min(ceiling));
        }
        let mut node_timeout_millis = BTreeMap::new();
        for (id, node) in &workflow.nodes {
            let millis = node
                .node_timeout_ms
                .or(workflow.defaults.node_timeout_ms)
                .unwrap_or(frozen.limits.task_timeout_millis);
            if millis == 0 {
                return Err(invalid(format!("node {id} has no positive dispatch timeout")));
            }
            node_timeout_millis.insert(id.clone(), millis);
        }

        Ok(ValidatedWorkflow {
            pinned: frozen.workflow.clone(),
            entry: NodeId(entry),
            final_node: NodeId(final_node),
            max_attempts,
            node_timeout_millis,
        })
    }
}

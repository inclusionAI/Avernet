use bcn_runtime_model::*;
use bcs_domain::{StateMachineAssignee, StateMachineNodeKind};
use std::collections::{BTreeMap, BTreeSet};

pub(crate) fn normalize(spec: &SessionSpec) -> BTreeMap<String, NormalizedNode> {
    let d = spec.definition.as_ref().expect("validated definition");
    let mut graph = BTreeMap::new();
    for (id, n) in &d.nodes {
        let prompt = n.instruction.clone().unwrap_or_default();
        let kind = match &n.assignee {
            Some(StateMachineAssignee::BotBinding { binding }) => NormalizedNodeKind::BotTask {
                binding_key: binding.clone(),
                prompt,
            },
            _ => NormalizedNodeKind::HumanInput { prompt },
        };
        let branches: BTreeMap<_, _> = n
            .transitions
            .iter()
            .map(|(key, t)| (key.clone(), t.targets.clone()))
            .collect();
        let successors = branches
            .values()
            .flatten()
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let timeout_ms = n.node_timeout_ms.or(d.defaults.node_timeout_ms).unwrap_or(
            if n.kind == StateMachineNodeKind::HumanInput {
                spec.policy.human_timeout_ms
            } else {
                spec.policy.execution_timeout_ms
            },
        );
        let successors = if let Some(judge) = &n.judge {
            let judge_id = format!("@judge:{id}");
            graph.insert(
                judge_id.clone(),
                NormalizedNode {
                    id: judge_id.clone(),
                    kind: NormalizedNodeKind::Judge {
                        prompt: judge.criteria.join("\n"),
                        branches,
                    },
                    predecessors: vec![],
                    successors,
                    timeout_ms: spec.policy.execution_timeout_ms,
                },
            );
            vec![judge_id]
        } else {
            successors
        };
        graph.insert(
            id.clone(),
            NormalizedNode {
                id: id.clone(),
                kind,
                predecessors: vec![],
                successors,
                timeout_ms,
            },
        );
    }
    let edges: Vec<_> = graph
        .iter()
        .flat_map(|(id, n)| {
            n.successors
                .iter()
                .map(move |target| (id.clone(), target.clone()))
        })
        .collect();
    for (id, target) in edges {
        graph
            .get_mut(&target)
            .expect("validated edge")
            .predecessors
            .push(id);
    }
    graph
}

/// Potential reachability keeps unresolved branches alive; completed Judge nodes
/// retain only their persisted selection. Joins require all predecessors settled.
pub(crate) fn derive(
    graph: &BTreeMap<String, NormalizedNode>,
    task: &TaskExecution,
) -> (Vec<String>, Vec<String>) {
    let mut reachable = BTreeSet::new();
    let mut stack: Vec<_> = graph
        .iter()
        .filter(|(_, n)| n.predecessors.is_empty())
        .map(|(id, _)| id.clone())
        .collect();
    while let Some(id) = stack.pop() {
        if task.nodes[&id].phase == NodePhase::Skipped || !reachable.insert(id.clone()) {
            continue;
        }
        stack.extend(selected_targets(&graph[&id], &task.nodes[&id]));
    }
    let skip: Vec<_> = task
        .nodes
        .iter()
        .filter(|(id, n)| n.phase == NodePhase::Pending && !reachable.contains(*id))
        .map(|(id, _)| id.clone())
        .collect();
    let ready = task
        .nodes
        .iter()
        .filter(|(id, n)| {
            if n.phase != NodePhase::Pending || !reachable.contains(*id) {
                return false;
            }
            let p = &graph[*id].predecessors;
            let settled = p.iter().all(|id| {
                matches!(
                    task.nodes[id].phase,
                    NodePhase::Completed | NodePhase::Skipped
                ) || skip.contains(id)
            });
            let selected = p.is_empty()
                || p.iter().any(|up| {
                    task.nodes[up].phase == NodePhase::Completed
                        && selected_targets(&graph[up], &task.nodes[up]).contains(*id)
                });
            settled && selected
        })
        .map(|(id, _)| id.clone())
        .collect();
    (skip, ready)
}
fn selected_targets(n: &NormalizedNode, e: &NodeExecution) -> Vec<String> {
    if let NormalizedNodeKind::Judge { branches, .. } = &n.kind {
        if e.phase == NodePhase::Completed {
            return e
                .selected_branch
                .as_ref()
                .and_then(|key| branches.get(key))
                .cloned()
                .unwrap_or_default();
        }
    }
    n.successors.clone()
}

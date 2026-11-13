//! Admission validation shared independently of reducer execution.
use bcn_runtime_model::*;
use bcs_domain::{StateMachineAssignee, StateMachineGraphMode, StateMachineNodeKind};
use std::collections::{BTreeMap, BTreeSet};

pub fn validate_common(spec: &SessionSpec, kind: RunnerKind) -> RuntimeResult<()> {
    spec.session_id
        .scope
        .validate()
        .map_err(|_| RuntimeError::AccessDenied)?;
    if kind == RunnerKind::DirectA2a && spec.live_output_nodes.is_some() {
        return invalid("Direct projection cannot select graph nodes");
    }
    if spec.kind != kind {
        return invalid("runner kind mismatch");
    }
    let target_scope = match &spec.target {
        ExecutionTarget::Agent { agent_id } => &agent_id.scope,
        ExecutionTarget::Group { group_id } => &group_id.scope,
    };
    if target_scope != &spec.session_id.scope
        || spec
            .bindings
            .values()
            .any(|b| b.target.scope != spec.session_id.scope)
    {
        return Err(RuntimeError::AccessDenied);
    }
    validate_policy(&spec.policy)
}
fn validate_policy(policy: &ExecutionPolicy) -> RuntimeResult<()> {
    if policy.execution_timeout_ms == 0
        || policy.human_timeout_ms == 0
        || policy.max_pending_tasks == 0
    {
        return invalid("positive timeouts and queue limit required");
    }
    if policy.max_pending_tasks > 16
        || policy.max_graph_nodes > 32
        || policy.max_agents > 5
    {
        return invalid("policy exceeds MVP limits");
    }
    Ok(())
}

pub fn validate_definition(spec: &SessionSpec) -> RuntimeResult<()> {
    validate_common(spec, RunnerKind::StructuredGroup)?;
    if !matches!(spec.target, ExecutionTarget::Group { .. }) {
        return invalid("structured runner needs group target");
    }
    let d = spec
        .definition
        .as_ref()
        .ok_or_else(|| RuntimeError::InvalidInput("missing definition".into()))?;
    let targets = spec.bindings.iter().map(|(key,binding)|(key.clone(),binding.target.clone())).collect();
    validate_graph(d, &spec.policy, &targets, spec.live_output_nodes.as_deref())
}
/// Validate all executable graph semantics before resolving external agent routes.
/// Binding targets are management identities only; no transport is selected here.
pub fn validate_graph(
    d: &StateMachineDefinition, policy: &ExecutionPolicy,
    bindings: &BTreeMap<String, AgentId>, live_output_nodes: Option<&[String]>,
) -> RuntimeResult<()> {
    validate_policy(policy)?;
    if let Some(selected) = live_output_nodes {
        let unique: BTreeSet<_> = selected.iter().collect();
        if unique.len() != selected.len() || selected.iter().any(|id|
            !d.nodes.get(id).is_some_and(|n| n.final_output && n.kind == StateMachineNodeKind::BotTask)) {
            return invalid("live output selection requires distinct final BotTask nodes in this frozen graph");
        }
    }
    if d.version != 1 || d.graph_mode != StateMachineGraphMode::Acyclic {
        return unsupported("only v1 acyclic definitions are supported");
    }
    if d.input_schema.is_some()
        || !d.variables.is_empty()
        || !d.events.is_empty()
        || d.human_input_channel.is_some()
        || !d.extensions.is_empty()
    {
        return unsupported("schema, variables, events, channels and extensions are unsupported");
    }
    if d.defaults.max_attempts != 1 {
        return unsupported("automatic retries are unsupported");
    }
    if d.nodes.is_empty() || d.nodes.len() > policy.max_graph_nodes as usize {
        return Err(RuntimeError::CapacityExceeded);
    }
    let mut predecessors: BTreeMap<String, BTreeSet<String>> = d
        .nodes
        .keys()
        .map(|id| (id.clone(), BTreeSet::new()))
        .collect();
    let mut agents = BTreeSet::new();
    let mut terminals = Vec::new();
    for (id, n) in &d.nodes {
        if id.is_empty()
            || id.starts_with('@')
            || n.display_name.trim().is_empty()
            || n.instruction.as_deref().unwrap_or("").trim().is_empty()
        {
            return invalid("node id, display_name and instruction are required; @ is reserved");
        }
        if n.loop_definition.is_some()
            || n.action.is_some()
            || n.notification.is_some()
            || n.output_contract.is_some()
            || !n.extensions.is_empty()
        {
            return unsupported(
                "loop, action, notification, output_contract and extensions are unsupported",
            );
        }
        if n.max_attempts.is_some_and(|a| a != 1) {
            return unsupported("automatic retries are unsupported");
        }
        let human = match n.kind {
            StateMachineNodeKind::BotTask => {
                let Some(StateMachineAssignee::BotBinding { binding }) = &n.assignee else {
                    return invalid("bot_task requires bot_binding");
                };
                let b = bindings
                    .get(binding)
                    .ok_or_else(|| RuntimeError::InvalidInput("missing frozen binding".into()))?;
                agents.insert(b.clone());
                false
            }
            StateMachineNodeKind::HumanInput => {
                if n.assignee.is_some() || n.max_attempts.is_some() || n.final_output {
                    return invalid("human_input cannot have assignee, retries or final_output");
                }
                if n.node_timeout_ms.is_none() {
                    return invalid("human_input requires timeout");
                }
                true
            }
            _ => return unsupported("unsupported node kind"),
        };
        if n.node_timeout_ms
            .or(d.defaults.node_timeout_ms)
            .is_some_and(|v| v == 0)
        {
            return invalid("node timeout must be positive");
        }
        if let Some(j) = &n.judge {
            if !policy.judge_available {
                return unsupported("Judge unavailable");
            }
            if j.judge_type.as_deref().unwrap_or("llm") != "llm" || !j.extensions.is_empty() {
                return unsupported("unsupported Judge policy");
            }
            let outcomes: BTreeSet<_> = j.outcomes.iter().collect();
            if j.criteria.is_empty()
                || j.criteria.iter().any(|v| v.trim().is_empty())
                || outcomes.is_empty()
                || outcomes.len() != j.outcomes.len()
                || outcomes.iter().any(|v| v.trim().is_empty())
                || outcomes != n.transitions.keys().collect()
            {
                return invalid("Judge outcomes must uniquely match transitions");
            }
        } else if n.transitions.keys().any(|key| key != "complete") {
            return invalid("nodes without Judge only support complete");
        }
        let has_target = n.transitions.values().any(|t| !t.targets.is_empty());
        if n.final_output || (human && !has_target) {
            terminals.push(id.clone());
        }
        if n.final_output && !n.transitions.is_empty() {
            return invalid("final_output cannot have transitions");
        }
        if !n.final_output && !human && !has_target {
            return invalid("non-final bot_task requires transition");
        }
        for t in n.transitions.values() {
            if t.guard.is_some() {
                return unsupported("guards unsupported");
            }
            if t.display_name.as_ref().is_some_and(|v| v.trim().is_empty()) {
                return invalid("blank transition display_name");
            }
            let unique: BTreeSet<_> = t.targets.iter().collect();
            if unique.len() != t.targets.len() {
                return invalid("duplicate transition target");
            }
            for target in &t.targets {
                predecessors
                    .get_mut(target)
                    .ok_or_else(|| RuntimeError::InvalidInput("missing transition target".into()))?
                    .insert(id.clone());
            }
        }
    }
    if agents.len() > policy.max_agents as usize {
        return Err(RuntimeError::CapacityExceeded);
    }
    let roots: Vec<_> = predecessors
        .iter()
        .filter(|(_, p)| p.is_empty())
        .map(|(id, _)| id.clone())
        .collect();
    if roots.len() != 1 || terminals.len() != 1 {
        return invalid("exactly one entry and terminal required");
    }
    if d.initial_node.as_ref().is_some_and(|id| id != &roots[0]) {
        return invalid("initial_node must be graph entry");
    }
    let mut resolved = BTreeSet::new();
    loop {
        let next: Vec<_> = predecessors
            .iter()
            .filter(|(id, p)| !resolved.contains(*id) && p.iter().all(|id| resolved.contains(id)))
            .map(|(id, _)| id.clone())
            .collect();
        if next.is_empty() {
            break;
        }
        resolved.extend(next);
    }
    if resolved.len() != d.nodes.len() {
        return invalid("cyclic graph");
    }
    let mut to_final = BTreeSet::from([terminals[0].clone()]);
    loop {
        let old = to_final.len();
        for id in to_final.clone() {
            to_final.extend(predecessors[&id].iter().cloned());
        }
        if to_final.len() == old {
            break;
        }
    }
    if to_final.len() != d.nodes.len() {
        return invalid("every node must reach the terminal");
    }
    Ok(())
}
pub(crate) fn invalid<T>(message: &str) -> RuntimeResult<T> {
    Err(RuntimeError::InvalidInput(message.into()))
}
fn unsupported<T>(message: &str) -> RuntimeResult<T> {
    Err(RuntimeError::Unsupported(message.into()))
}

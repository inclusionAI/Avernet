use crate::{
    binding, cancel_agent, freeze_node, id, initialize, operation, reduce, status, timer,
    validation, wait_input,
};
use bcn_runtime_api::core::SessionRunner;
use bcn_runtime_model::*;
use std::collections::BTreeMap;

#[derive(Debug, Default)]
pub struct DirectA2aRunner;
impl SessionRunner for DirectA2aRunner {
    fn kind(&self) -> RunnerKind {
        RunnerKind::DirectA2a
    }
    fn validate(&self, spec: &SessionSpec) -> RuntimeResult<()> {
        validation::validate_common(spec, self.kind())?;
        let ExecutionTarget::Agent { agent_id } = &spec.target else {
            return validation::invalid("direct runner requires agent target");
        };
        if spec.definition.is_some()
            || spec.bindings.len() != 1
            || spec
                .bindings
                .values()
                .next()
                .is_none_or(|b| &b.target != agent_id)
        {
            return validation::invalid(
                "direct runner requires exactly one matching binding and no definition",
            );
        }
        Ok(())
    }
    fn initialize(&self, spec: &SessionSpec) -> RuntimeResult<RunnerState> {
        self.validate(spec)?;
        let binding_key = spec
            .bindings
            .keys()
            .next()
            .expect("validated binding")
            .clone();
        Ok(initialize(
            spec,
            BTreeMap::from([(
                "direct".into(),
                NormalizedNode {
                    id: "direct".into(),
                    kind: NormalizedNodeKind::BotTask {
                        binding_key,
                        prompt: String::new(),
                    },
                    predecessors: vec![],
                    successors: vec![],
                    timeout_ms: spec.policy.execution_timeout_ms,
                },
            )]),
        ))
    }
    fn reduce(&self, state: &RunnerState, input: &SessionInput) -> RuntimeResult<Transition> {
        reduce(self.kind(), state, input)
    }
}

pub(crate) fn send(
    t: &mut Transition,
    task: &mut TaskExecution,
    node: &str,
    mut message: Message,
    at_ms: u64,
) -> RuntimeResult<()> {
    let binding = binding(&t.next_state, node)?;
    let n = task.nodes.get_mut(node).unwrap();
    n.attempt += 1;
    n.phase = NodePhase::Running;
    let dispatch_id = DispatchId::new(
        task.task_id.scope.clone(),
        id(&task.task_id, node, n.attempt, "send_dispatch"),
    );
    n.dispatch_id = Some(dispatch_id.clone());
    if t.next_state.kind == RunnerKind::DirectA2a && n.handle.is_none() {
        n.handle = t.next_state.direct_context.clone();
    }
    let handle = n.handle.clone();
    message.task_id = Some(task.task_id.clone());
    message.context_id = Some(t.next_state.session_id.clone());
    let request = AgentSendRequest {
        dispatch_id,
        operation_id: operation(task, node, "send"),
        binding,
        message,
        handle,
        expires_at_ms: Some(at_ms.saturating_add(t.next_state.graph[node].timeout_ms)),
    };
    t.effects.push(EffectIntent::SendAgent {
        task_id: task.task_id.clone(),
        node_id: if t.next_state.kind == RunnerKind::DirectA2a {
            None
        } else {
            Some(node.into())
        },
        request,
    });
    t.timer_changes.push(TimerChange::Upsert(Deadline {
        timer_id: timer(task, node, false),
        at_ms: at_ms.saturating_add(t.next_state.graph[node].timeout_ms),
    }));
    Ok(())
}
fn check_handle(t: &Transition, node: &str, handle: &ExecutionHandle) -> RuntimeResult<()> {
    let b = binding(&t.next_state, node)?;
    if b.target != handle.target || b.binding_version != handle.binding_version {
        return Err(RuntimeError::AccessDenied);
    }
    Ok(())
}
pub(crate) fn apply_agent(
    t: &mut Transition,
    task: &mut TaskExecution,
    node: &str,
    event: &AgentEvent,
    at_ms: u64,
) -> RuntimeResult<()> {
    match event {
        AgentEvent::Accepted { handle } => {
            check_handle(t, node, handle)?;
            task.nodes.get_mut(node).unwrap().handle = Some(handle.clone());
            if t.next_state.kind == RunnerKind::DirectA2a {
                t.next_state.direct_context = Some(handle.clone());
            }
            if task.cancel_requested {
                cancel_agent(t, task, node)?;
            }
        }
        AgentEvent::Completed { output } => {
            freeze_node(t, task, node, TaskStatus::Completed, output.clone(), None)
        }
        AgentEvent::Failed { error } => freeze_node(
            t,
            task,
            node,
            TaskStatus::Failed,
            vec![],
            Some(error.clone()),
        ),
        AgentEvent::FailedWithOutput { error, output } => freeze_node(
            t, task, node, TaskStatus::Failed, output.clone(), Some(error.clone()),
        ),
        // The application commits TaskFailure from this validated durable input.
        // Observation loss is never authoritative remote termination.
        AgentEvent::ObservationFailed { error, .. } => return Err(error.clone()),
        AgentEvent::Aborted {
            affected_remote_task_ids,
        } => {
            if task.nodes[node]
                .handle
                .as_ref()
                .and_then(|h| h.remote_task_id.as_ref())
                .is_some_and(|remote| affected_remote_task_ids.contains(remote))
            {
                freeze_node(t, task, node, TaskStatus::Cancelled, vec![], None);
            }
        }
        AgentEvent::InputRequired { handle, prompt } => {
            check_handle(t, node, handle)?;
            task.nodes.get_mut(node).unwrap().handle = Some(handle.clone());
            if t.next_state.kind == RunnerKind::DirectA2a {
                t.next_state.direct_context = Some(handle.clone());
            }
            if task.cancel_requested {
                cancel_agent(t, task, node)?;
            } else {
                wait_input(
                    t,
                    task,
                    node,
                    prompt
                        .iter()
                        .filter_map(|p| {
                            if let MessagePart::Text { text } = p {
                                Some(text.as_str())
                            } else {
                                None
                            }
                        })
                        .collect::<Vec<_>>()
                        .join("\n"),
                    at_ms,
                );
            }
        }
        AgentEvent::AuthRequired { handle } => {
            check_handle(t, node, handle)?;
            task.nodes.get_mut(node).unwrap().handle = Some(handle.clone());
            status(t, task, TaskStatus::AuthRequired);
        }
        AgentEvent::Snapshot { snapshot } => {
            check_handle(t, node, &snapshot.handle)?;
            task.nodes.get_mut(node).unwrap().handle = Some(snapshot.handle.clone());
            if t.next_state.kind == RunnerKind::DirectA2a {
                t.next_state.direct_context = Some(snapshot.handle.clone());
            }
            if snapshot.status.is_terminal() {
                freeze_node(
                    t,
                    task,
                    node,
                    snapshot.status,
                    snapshot.output.clone(),
                    snapshot.error.clone(),
                );
            } else if snapshot.status == TaskStatus::InputRequired {
                apply_agent(
                    t,
                    task,
                    node,
                    &AgentEvent::InputRequired {
                        handle: snapshot.handle.clone(),
                        prompt: snapshot.input_prompt.clone().unwrap_or_else(||snapshot.output.clone()),
                    },
                    at_ms,
                )?;
            } else if snapshot.status == TaskStatus::AuthRequired {
                status(t, task, TaskStatus::AuthRequired);
            }
        }
        // Token projection belongs to Connector/EventHub. Never mutate a frozen intent.
        AgentEvent::Delta { .. } => {}
    }
    Ok(())
}

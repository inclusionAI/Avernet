//! Pure deterministic core implementations of `SessionRunner`.
mod direct;
mod graph;
mod structured;
pub mod validation;
use bcn_runtime_model::*;
pub use direct::DirectA2aRunner;
use direct::{apply_agent, send};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
pub use structured::StructuredGroupRunner;

fn initialize(spec: &SessionSpec, graph: BTreeMap<String, NormalizedNode>) -> RunnerState {
    RunnerState {
        schema_version: CHECKPOINT_SCHEMA_VERSION,
        kind: spec.kind,
        session_id: spec.session_id.clone(),
        target: spec.target.clone(),
        current_task: None,
        pending_tasks: vec![],
        direct_context: None,
        context_blocked: false,
        graph,
        bindings: spec.bindings.clone(),
        policy: spec.policy.clone(),
    }
}
/// Length-delimited SHA-256 identifiers stay bounded and storage-key safe even
/// when user node/task identifiers are long or contain punctuation.
fn id(task: &TaskId, node: &str, attempt: u32, purpose: &str) -> String {
    let mut hash = Sha256::new();
    for part in [&task.value, node, &attempt.to_string(), purpose] {
        hash.update((part.len() as u64).to_be_bytes());
        hash.update(part.as_bytes());
    }
    format!("{:x}", hash.finalize())
}
fn operation(task: &TaskExecution, node: &str, purpose: &str) -> OperationId {
    OperationId::new(
        task.task_id.scope.clone(),
        id(&task.task_id, node, task.nodes[node].attempt, purpose),
    )
}
fn timer(task: &TaskExecution, node: &str, human: bool) -> TimerId {
    TimerId::new(
        task.task_id.scope.clone(),
        id(
            &task.task_id,
            node,
            task.nodes[node].attempt,
            if human {
                "human_timer"
            } else {
                "execution_timer"
            },
        ),
    )
}
fn event(t: &mut Transition, task: &TaskExecution, purpose: &str, kind: DomainEventKind) {
    // Event purpose includes node/attempt or new status; each hard event is stable.
    t.events.push(DomainEvent {
        event_id: EventId::new(
            task.task_id.scope.clone(),
            id(&task.task_id, "", 0, purpose),
        ),
        task_id: task.task_id.clone(),
        kind,
    });
}
fn status(t: &mut Transition, task: &mut TaskExecution, value: TaskStatus) {
    if task.status != value {
        task.status = value;
        let position: Vec<_> = task
            .nodes
            .iter()
            .map(|(id, n)| (id, n.attempt, n.phase))
            .collect();
        event(
            t,
            task,
            &format!("status:{value:?}:{position:?}"),
            DomainEventKind::TaskStatusChanged { status: value },
        );
    }
}
fn node_execution() -> NodeExecution {
    NodeExecution {
        phase: NodePhase::Pending,
        attempt: 0,
        dispatch_id: None,
        handle: None,
        interaction_id: None,
        selected_branch: None,
        result_intent: None,
        saved_result: None,
    }
}
fn cancel_timers(t: &mut Transition, task: &TaskExecution, node: &str) {
    t.timer_changes
        .push(TimerChange::Cancel(timer(task, node, false)));
    t.timer_changes
        .push(TimerChange::Cancel(timer(task, node, true)));
}
fn task_matches(task: &TaskExecution, task_id: &TaskId) -> bool {
    &task.task_id == task_id
}

fn reduce(
    kind: RunnerKind,
    state: &RunnerState,
    input: &SessionInput,
) -> RuntimeResult<Transition> {
    if state.schema_version != CHECKPOINT_SCHEMA_VERSION {
        return Err(RuntimeError::IncompatibleSchema(state.schema_version));
    }
    if state.kind != kind {
        return validation::invalid("runner kind mismatch");
    }
    if input.event_id.scope != state.session_id.scope {
        return Err(RuntimeError::AccessDenied);
    }
    let mut t = Transition {
        next_state: state.clone(),
        effects: vec![],
        events: vec![],
        timer_changes: vec![],
    };
    if let SessionInputKind::Submit { task } = &input.kind {
        if state.context_blocked {
            return Err(RuntimeError::ContextBlocked);
        }
        if task.terminal_intent.is_some() {
            return validation::invalid("submitted task cannot supply terminal intent");
        }
        validate_message(&task.message, &state.session_id, &task.task_id)?;
        if task.task_id.scope != state.session_id.scope {
            return Err(RuntimeError::AccessDenied);
        }
        if state
            .current_task
            .as_ref()
            .is_some_and(|v| v.task_id == task.task_id)
            || state
                .pending_tasks
                .iter()
                .any(|v| v.task_id == task.task_id)
        {
            return Ok(t);
        }
        if state
            .current_task
            .as_ref()
            .is_some_and(|v| !v.status.is_terminal())
        {
            if state.pending_tasks.len() >= state.policy.max_pending_tasks as usize {
                return Err(RuntimeError::CapacityExceeded);
            }
            t.next_state.pending_tasks.push(task.clone());
        } else {
            start(&mut t, task.clone(), input.at_ms)?;
        }
        return Ok(t);
    }
    // Queued cancellation bypasses the current execution and never dispatches.
    if let SessionInputKind::Cancel { command } = &input.kind {
        if let Some(index) = t
            .next_state
            .pending_tasks
            .iter()
            .position(|q| q.task_id == command.task_id)
        {
            if t.next_state.pending_tasks[index].terminal_intent.is_none() {
                let task_id = t.next_state.pending_tasks[index].task_id.clone();
                let intent = ResultIntent {
                    result_id: ResultId::new(
                        task_id.scope.clone(),
                        id(&task_id, "", 0, "queued_cancel_result"),
                    ),
                    task_id: task_id.clone(),
                    node_id: None,
                    attempt: 0,
                    status: TaskStatus::Cancelled,
                    output: vec![],
                    error: None,
                };
                t.next_state.pending_tasks[index].terminal_intent = Some(intent.clone());
                t.effects.push(EffectIntent::SaveResult { intent });
                queued_status(&mut t, &task_id, TaskStatus::PendingResult);
            }
            return Ok(t);
        }
    }
    if let SessionInputKind::ResultSaved { result } = &input.kind {
        if let Some(index) = t
            .next_state
            .pending_tasks
            .iter()
            .position(|q| q.task_id == result.intent.task_id && q.terminal_intent.is_some())
        {
            if t.next_state.pending_tasks[index].terminal_intent.as_ref() != Some(&result.intent) {
                return Err(RuntimeError::Conflict(
                    "saved queued result differs from immutable intent".into(),
                ));
            }
            t.next_state.pending_tasks.remove(index);
            queued_status(&mut t, &result.intent.task_id, result.intent.status);
            return Ok(t);
        }
    }
    let Some(mut task) = t.next_state.current_task.take() else {
        return Ok(t);
    };
    if task.status.is_terminal() {
        t.next_state.current_task = Some(task);
        return Ok(t);
    }
    match &input.kind {
        SessionInputKind::Agent {
            task_id,
            node_id,
            dispatch_id,
            event: agent_event,
        } if task_matches(&task, task_id) => {
            let node = node_id.as_deref().unwrap_or("direct");
            if task.nodes.get(node).is_some_and(|n| {
                n.dispatch_id.as_ref() == Some(dispatch_id)
                    && matches!(n.phase, NodePhase::Running | NodePhase::WaitingInput)
            }) {
                apply_agent(&mut t, &mut task, node, agent_event, input.at_ms)?;
            }
        }
        SessionInputKind::HumanReply {
            task_id,
            interaction_id,
            caller,
            message,
        } if task_matches(&task, task_id) => {
            if caller.scope != state.session_id.scope || caller.principal.trim().is_empty() {
                return Err(RuntimeError::AccessDenied);
            }
            validate_message(message, &state.session_id, task_id)?;
            let node = task
                .nodes
                .iter()
                .find(|(_, n)| {
                    n.phase == NodePhase::WaitingInput
                        && n.interaction_id.as_ref() == Some(interaction_id)
                })
                .map(|(id, _)| id.clone());
            if let Some(node) = node {
                if !task.cancel_requested {
                    cancel_timers(&mut t, &task, &node);
                    task.nodes.get_mut(&node).unwrap().interaction_id = None;
                    if matches!(
                        state.graph[&node].kind,
                        NormalizedNodeKind::HumanInput { .. }
                    ) {
                        freeze_node(
                            &mut t,
                            &mut task,
                            &node,
                            TaskStatus::Completed,
                            message.parts.clone(),
                            None,
                        );
                    } else {
                        let binding = binding(&t.next_state, &node)?;
                        if !binding.capabilities.input_continuation {
                            return Err(RuntimeError::Unsupported(
                                "input continuation unavailable".into(),
                            ));
                        }
                        send(&mut t, &mut task, &node, message.clone(), input.at_ms)?;
                        status(&mut t, &mut task, TaskStatus::Working);
                    }
                }
            }
        }
        SessionInputKind::JudgeDecided {
            task_id,
            node_id,
            branch,
        } if task_matches(&task, task_id) => {
            if task
                .nodes
                .get(node_id)
                .is_some_and(|n| n.phase == NodePhase::WaitingJudge)
            {
                let NormalizedNodeKind::Judge { branches, .. } = &state.graph[node_id].kind else {
                    return validation::invalid("not a Judge node");
                };
                if !branches.contains_key(branch) {
                    return validation::invalid("invalid Judge branch");
                }
                task.nodes.get_mut(node_id).unwrap().selected_branch = Some(branch.clone());
                freeze_node(
                    &mut t,
                    &mut task,
                    node_id,
                    TaskStatus::Completed,
                    vec![],
                    None,
                );
            }
        }
        SessionInputKind::ResultSaved { result } if task_matches(&task, &result.intent.task_id) => {
            apply_saved(&mut t, &mut task, result)?
        }
        SessionInputKind::Cancel { command } if task_matches(&task, &command.task_id) => {
            if !task.cancel_requested && task.result_intent.is_none() {
                task.cancel_requested = true;
                cancel_all(&mut t, &mut task)?;
            }
        }
        SessionInputKind::Cancellation {
            task_id,
            node_id,
            dispatch_id,
            operation_id,
            outcome,
        } if task_matches(&task, task_id) => {
            let node = node_id.as_deref().unwrap_or("direct");
            if task.cancel_requested
                && task.nodes.get(node).is_some_and(|n| {
                    matches!(n.phase, NodePhase::Running | NodePhase::WaitingInput)
                })
            {
                let expected_dispatch = DispatchId::new(
                    task.task_id.scope.clone(),
                    id(
                        &task.task_id,
                        node,
                        task.nodes[node].attempt,
                        "cancel_dispatch",
                    ),
                );
                if operation_id == &operation(&task, node, "cancel")
                    && dispatch_id == &expected_dispatch
                {
                    match outcome {
                        CancelOutcome::Confirmed {
                            affected_remote_task_ids,
                        } => {
                            let confirmed = task.nodes[node]
                                .handle
                                .as_ref()
                                .and_then(|h| h.remote_task_id.as_ref())
                                .is_some_and(|remote| affected_remote_task_ids.contains(remote));
                            if confirmed {
                                freeze_node(
                                    &mut t,
                                    &mut task,
                                    node,
                                    TaskStatus::Cancelled,
                                    vec![],
                                    None,
                                );
                            } else {
                                freeze_node(
                                    &mut t,
                                    &mut task,
                                    node,
                                    TaskStatus::Failed,
                                    vec![],
                                    Some(RuntimeError::CancellationUnknown),
                                );
                            }
                        }
                        CancelOutcome::Unknown => freeze_node(
                            &mut t,
                            &mut task,
                            node,
                            TaskStatus::Failed,
                            vec![],
                            Some(RuntimeError::CancellationUnknown),
                        ),
                        // AlreadyTerminal proves no cancellation; await authoritative terminal output.
                        CancelOutcome::AlreadyTerminal => {}
                    }
                }
            }
        }
        SessionInputKind::TimerFired { timer_id } => {
            let node = task
                .nodes
                .iter()
                .find(|(node, n)| match n.phase {
                    // Cancellation may race an already-enqueued timer. Only the
                    // deadline for the current wait phase remains authoritative.
                    NodePhase::WaitingInput => timer(&task, node, true) == *timer_id,
                    NodePhase::Running | NodePhase::WaitingJudge => {
                        timer(&task, node, false) == *timer_id
                    }
                    _ => false,
                })
                .map(|(id, _)| id.clone());
            if let Some(node) = node {
                if task.nodes[&node].handle.is_some() {
                    cancel_agent(&mut t, &task, &node)?;
                }
                // A timeout is failure evidence, never proof the remote work stopped.
                let error = if matches!(state.graph[&node].kind, NormalizedNodeKind::BotTask { .. })
                {
                    RuntimeError::ExecutionUnknown
                } else {
                    RuntimeError::Unavailable("deadline exceeded".into())
                };
                freeze_node(
                    &mut t,
                    &mut task,
                    &node,
                    TaskStatus::Failed,
                    vec![],
                    Some(error),
                );
            }
        }
        SessionInputKind::EffectFailed {
            operation_id,
            error,
        } => {
            let node = task
                .nodes
                .iter()
                .find(|(id, n)| {
                    matches!(
                        n.phase,
                        NodePhase::Running | NodePhase::WaitingJudge | NodePhase::WaitingInput
                    ) && ["send", "judge", "cancel"]
                        .iter()
                        .any(|purpose| &operation(&task, id, purpose) == operation_id)
                })
                .map(|(id, _)| id.clone());
            if let Some(node) = node {
                freeze_node(
                    &mut t,
                    &mut task,
                    &node,
                    TaskStatus::Failed,
                    vec![],
                    Some(error.clone()),
                );
            }
        }
        _ => {}
    }
    if t.next_state.context_blocked {
        for index in 0..t.next_state.pending_tasks.len() {
            if t.next_state.pending_tasks[index].terminal_intent.is_none() {
                let task_id = t.next_state.pending_tasks[index].task_id.clone();
                let intent = ResultIntent {
                    result_id: ResultId::new(
                        task_id.scope.clone(),
                        id(&task_id, "", 0, "blocked_result"),
                    ),
                    task_id: task_id.clone(),
                    node_id: None,
                    attempt: 0,
                    status: TaskStatus::Failed,
                    output: vec![],
                    error: Some(RuntimeError::ContextBlocked),
                };
                t.next_state.pending_tasks[index].terminal_intent = Some(intent.clone());
                t.effects.push(EffectIntent::SaveResult { intent });
                queued_status(&mut t, &task_id, TaskStatus::PendingResult);
            }
        }
    }
    advance(&mut t, &mut task, input.at_ms)?;
    let finished = task.status.is_terminal();
    t.next_state.current_task = Some(task);
    if finished && !t.next_state.context_blocked {
        if let Some(index) = t
            .next_state
            .pending_tasks
            .iter()
            .position(|q| q.terminal_intent.is_none())
        {
            let next = t.next_state.pending_tasks.remove(index);
            start(&mut t, next, input.at_ms)?;
        }
    }
    Ok(t)
}
fn queued_status(t: &mut Transition, task_id: &TaskId, value: TaskStatus) {
    t.events.push(DomainEvent {
        event_id: EventId::new(
            task_id.scope.clone(),
            id(task_id, "", 0, &format!("queued:{value:?}")),
        ),
        task_id: task_id.clone(),
        kind: DomainEventKind::TaskStatusChanged { status: value },
    });
}
fn validate_message(message: &Message, session: &SessionId, task: &TaskId) -> RuntimeResult<()> {
    if message.message_id.scope != session.scope
        || message.context_id.as_ref().is_some_and(|id| id != session)
        || message.task_id.as_ref().is_some_and(|id| id != task)
    {
        return Err(RuntimeError::AccessDenied);
    }
    Ok(())
}
fn start(t: &mut Transition, queued: QueuedTask, at_ms: u64) -> RuntimeResult<()> {
    let mut task = TaskExecution {
        task_id: queued.task_id,
        message: queued.message,
        status: TaskStatus::Submitted,
        nodes: t
            .next_state
            .graph
            .keys()
            .map(|id| (id.clone(), node_execution()))
            .collect(),
        result_intent: None,
        cancel_requested: false,
    };
    status(t, &mut task, TaskStatus::Working);
    advance(t, &mut task, at_ms)?;
    t.next_state.current_task = Some(task);
    Ok(())
}
fn binding(state: &RunnerState, node: &str) -> RuntimeResult<FrozenTargetBinding> {
    let NormalizedNodeKind::BotTask { binding_key, .. } = &state.graph[node].kind else {
        return validation::invalid("node has no agent binding");
    };
    state
        .bindings
        .get(binding_key)
        .cloned()
        .ok_or_else(|| RuntimeError::InvalidInput("missing binding".into()))
}
fn input_parts(state: &RunnerState, task: &TaskExecution) -> Vec<MessagePart> {
    task.nodes
        .iter()
        .filter(|(id, _)| !matches!(state.graph[*id].kind, NormalizedNodeKind::Judge { .. }))
        .filter_map(|(_, n)| n.saved_result.as_ref())
        .flat_map(|r| r.intent.output.clone())
        .collect()
}
fn advance(t: &mut Transition, task: &mut TaskExecution, at_ms: u64) -> RuntimeResult<()> {
    if task.status.is_terminal() || task.result_intent.is_some() {
        return Ok(());
    }
    let failed = task.nodes.values().any(|n| n.phase == NodePhase::Failed);
    if (failed || task.nodes.values().any(|n| n.phase == NodePhase::Cancelled))
        && !task.cancel_requested
    {
        task.cancel_requested = true;
        cancel_all(t, task)?;
    }
    if task.cancel_requested {
        if task.nodes.values().all(|n| {
            matches!(
                n.phase,
                NodePhase::Completed
                    | NodePhase::Failed
                    | NodePhase::Skipped
                    | NodePhase::Cancelled
            )
        }) {
            finish_task(
                t,
                task,
                if failed {
                    TaskStatus::Failed
                } else {
                    TaskStatus::Cancelled
                },
            );
        }
        return Ok(());
    }
    if task.status == TaskStatus::InputRequired
        && !task
            .nodes
            .values()
            .any(|n| n.phase == NodePhase::WaitingInput)
    {
        status(t, task, TaskStatus::Working);
    }
    let (skip, ready) = graph::derive(&t.next_state.graph, task);
    for node in skip {
        task.nodes.get_mut(&node).unwrap().phase = NodePhase::Skipped;
    }
    for node in ready {
        let definition = t.next_state.graph[&node].clone();
        match definition.kind {
            NormalizedNodeKind::BotTask { prompt, .. } => {
                let mut message = task.message.clone();
                if !prompt.is_empty() {
                    message.parts.push(MessagePart::Text { text: prompt });
                }
                message.parts.extend(input_parts(&t.next_state, task));
                message.message_id = MessageId::new(
                    task.task_id.scope.clone(),
                    id(&task.task_id, &node, 1, "message"),
                );
                send(t, task, &node, message, at_ms)?;
            }
            NormalizedNodeKind::HumanInput { prompt } => {
                task.nodes.get_mut(&node).unwrap().attempt = 1;
                wait_input(t, task, &node, prompt, at_ms);
            }
            NormalizedNodeKind::Judge { prompt, branches } => {
                let n = task.nodes.get_mut(&node).unwrap();
                n.phase = NodePhase::WaitingJudge;
                n.attempt = 1;
                let inputs = task
                    .nodes
                    .iter()
                    .filter(|(id, _)| {
                        !matches!(
                            t.next_state.graph[*id].kind,
                            NormalizedNodeKind::Judge { .. }
                        )
                    })
                    .filter_map(|(id, n)| n.saved_result.clone().map(|r| (id.clone(), r)))
                    .collect();
                t.effects.push(EffectIntent::Judge {
                    request: JudgeRequest {
                        operation_id: operation(task, &node, "judge"),
                        task_id: task.task_id.clone(),
                        node_id: node.clone(),
                        prompt,
                        choices: branches.keys().cloned().collect(),
                        inputs,
                    },
                });
                t.timer_changes.push(TimerChange::Upsert(Deadline {
                    timer_id: timer(task, &node, false),
                    at_ms: at_ms.saturating_add(definition.timeout_ms),
                }));
            }
        }
    }
    if task
        .nodes
        .values()
        .all(|n| matches!(n.phase, NodePhase::Completed | NodePhase::Skipped))
    {
        finish_task(t, task, TaskStatus::Completed);
    }
    Ok(())
}
fn wait_input(
    t: &mut Transition,
    task: &mut TaskExecution,
    node: &str,
    prompt: String,
    at_ms: u64,
) {
    cancel_timers(t, task, node);
    let interaction_id = InteractionId::new(
        task.task_id.scope.clone(),
        id(&task.task_id, node, task.nodes[node].attempt, "interaction"),
    );
    let n = task.nodes.get_mut(node).unwrap();
    n.phase = NodePhase::WaitingInput;
    n.interaction_id = Some(interaction_id.clone());
    status(t, task, TaskStatus::InputRequired);
    event(
        t,
        task,
        &format!("input:{node}:{}", task.nodes[node].attempt),
        DomainEventKind::InputRequired {
            interaction_id,
            prompt,
        },
    );
    let timeout = if matches!(
        t.next_state.graph[node].kind,
        NormalizedNodeKind::HumanInput { .. }
    ) {
        t.next_state.graph[node].timeout_ms
    } else {
        t.next_state.policy.human_timeout_ms
    };
    t.timer_changes.push(TimerChange::Upsert(Deadline {
        timer_id: timer(task, node, true),
        at_ms: at_ms.saturating_add(timeout),
    }));
}
fn freeze_node(
    t: &mut Transition,
    task: &mut TaskExecution,
    node: &str,
    result_status: TaskStatus,
    output: Vec<MessagePart>,
    error: Option<RuntimeError>,
) {
    if task.nodes[node].phase == NodePhase::PendingResult || task.nodes[node].saved_result.is_some()
    {
        return;
    }
    if t.next_state.kind == RunnerKind::DirectA2a
        && matches!(
            error,
            Some(RuntimeError::ExecutionUnknown | RuntimeError::CancellationUnknown)
        )
    {
        t.next_state.context_blocked = true;
    }
    let intent = ResultIntent {
        result_id: ResultId::new(
            task.task_id.scope.clone(),
            id(&task.task_id, node, task.nodes[node].attempt, "result"),
        ),
        task_id: task.task_id.clone(),
        node_id: if t.next_state.kind == RunnerKind::DirectA2a {
            None
        } else {
            Some(node.into())
        },
        attempt: task.nodes[node].attempt,
        status: result_status,
        output,
        error,
    };
    let n = task.nodes.get_mut(node).unwrap();
    n.phase = NodePhase::PendingResult;
    n.result_intent = Some(intent.clone());
    n.interaction_id = None;
    if t.next_state.kind == RunnerKind::DirectA2a {
        task.result_intent = Some(intent.clone());
        status(t, task, TaskStatus::PendingResult);
    }
    t.effects.push(EffectIntent::SaveResult { intent });
}
fn apply_saved(
    t: &mut Transition,
    task: &mut TaskExecution,
    result: &SavedResult,
) -> RuntimeResult<()> {
    if let Some(intent) = &task.result_intent {
        if result.intent.result_id == intent.result_id {
            if &result.intent != intent {
                return Err(RuntimeError::Conflict(
                    "saved result differs from immutable intent".into(),
                ));
            }
            if t.next_state.kind == RunnerKind::DirectA2a {
                let n = task.nodes.get_mut("direct").unwrap();
                n.saved_result = Some(result.clone());
                n.phase = phase(result.intent.status);
                cancel_timers(t, task, "direct");
                // Only the remote context is reused for a subsequent independent task.
                if let Some(handle) = &mut t.next_state.direct_context {
                    handle.remote_task_id = None;
                }
            }
            status(t, task, result.intent.status);
            return Ok(());
        }
    }
    let Some(node) = result.intent.node_id.as_ref() else {
        return Ok(());
    };
    let Some(n) = task.nodes.get(node) else {
        return Ok(());
    };
    if n.saved_result.is_some() || n.phase != NodePhase::PendingResult {
        return Ok(());
    }
    if n.result_intent.as_ref() != Some(&result.intent) {
        return Err(RuntimeError::Conflict(
            "saved result differs from immutable intent".into(),
        ));
    }
    let n = task.nodes.get_mut(node).unwrap();
    n.saved_result = Some(result.clone());
    n.phase = phase(result.intent.status);
    cancel_timers(t, task, node);
    if result.intent.status == TaskStatus::Completed {
        if let Some(branch) = task.nodes[node].selected_branch.clone() {
            event(
                t,
                task,
                &format!("branch:{node}"),
                DomainEventKind::BranchSelected {
                    node_id: node.clone(),
                    branch,
                },
            );
        } else {
            event(
                t,
                task,
                &format!("node:{node}"),
                DomainEventKind::NodeCompleted {
                    node_id: node.clone(),
                    result_id: result.intent.result_id.clone(),
                },
            );
        }
    }
    Ok(())
}
fn phase(status: TaskStatus) -> NodePhase {
    match status {
        TaskStatus::Completed => NodePhase::Completed,
        TaskStatus::Cancelled => NodePhase::Cancelled,
        _ => NodePhase::Failed,
    }
}
fn finish_task(t: &mut Transition, task: &mut TaskExecution, result_status: TaskStatus) {
    if t.next_state.kind == RunnerKind::DirectA2a || task.result_intent.is_some() {
        return;
    }
    let error = task
        .nodes
        .values()
        .filter_map(|n| n.saved_result.as_ref())
        .find_map(|r| r.intent.error.clone());
    let intent = ResultIntent {
        result_id: ResultId::new(
            task.task_id.scope.clone(),
            id(&task.task_id, "", 0, "task_result"),
        ),
        task_id: task.task_id.clone(),
        node_id: None,
        attempt: 0,
        status: result_status,
        output: if result_status == TaskStatus::Completed {
            t.next_state
                .graph
                .iter()
                .filter(|(_, n)| n.successors.is_empty())
                // Normalization inserts a Judge after its source business node.
                // A terminal Judge keeps that source as the output identity.
                .filter_map(|(id, n)| match n.kind {
                    NormalizedNodeKind::Judge { .. } => n.predecessors.first(),
                    _ => Some(id),
                })
                .filter_map(|id| task.nodes[id].saved_result.as_ref())
                .flat_map(|r| r.intent.output.clone())
                .collect()
        } else {
            input_parts(&t.next_state, task)
        },
        error,
    };
    task.result_intent = Some(intent.clone());
    status(t, task, TaskStatus::PendingResult);
    t.effects.push(EffectIntent::SaveResult { intent });
}
fn cancel_all(t: &mut Transition, task: &mut TaskExecution) -> RuntimeResult<()> {
    let ids: Vec<_> = task.nodes.keys().cloned().collect();
    for node in ids {
        match task.nodes[&node].phase {
            NodePhase::Pending => {
                task.nodes.get_mut(&node).unwrap().phase = NodePhase::Skipped;
            }
            NodePhase::WaitingJudge => {
                freeze_node(t, task, &node, TaskStatus::Cancelled, vec![], None)
            }
            NodePhase::Running | NodePhase::WaitingInput => {
                if matches!(
                    t.next_state.graph[&node].kind,
                    NormalizedNodeKind::HumanInput { .. }
                ) {
                    freeze_node(t, task, &node, TaskStatus::Cancelled, vec![], None);
                } else if task.nodes[&node].handle.is_some() {
                    cancel_agent(t, task, &node)?;
                }
                // No downstream handle yet: preserve running dispatch until evidence arrives.
            }
            _ => {}
        }
    }
    Ok(())
}
fn cancel_agent(t: &mut Transition, task: &TaskExecution, node: &str) -> RuntimeResult<()> {
    let binding = binding(&t.next_state, node)?;
    let Some(handle) = task.nodes[node].handle.clone() else {
        return Ok(());
    };
    if binding.capabilities.cancellation == CancellationSupport::Unsupported {
        return Ok(());
    }
    let request = AgentCancelRequest {
        operation_id: operation(task, node, "cancel"),
        handle,
        exclusive_context: binding.capabilities.cancellation
            == CancellationSupport::ExclusiveContext,
    };
    t.effects.push(EffectIntent::CancelAgent {
        task_id: task.task_id.clone(),
        node_id: if t.next_state.kind == RunnerKind::DirectA2a {
            None
        } else {
            Some(node.into())
        },
        dispatch_id: DispatchId::new(
            task.task_id.scope.clone(),
            id(
                &task.task_id,
                node,
                task.nodes[node].attempt,
                "cancel_dispatch",
            ),
        ),
        binding,
        request,
    });
    Ok(())
}

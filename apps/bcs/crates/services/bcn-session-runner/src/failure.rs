//! Explicit application-budget failure through the same immutable result barrier.
use crate::{cancel_agent, cancel_timers, id, queued_status, status};
use bcn_runtime_model::*;
pub(crate) fn fail(
    mut t: Transition,
    task_id: &TaskId,
    error: &RuntimeError,
    output: &[MessagePart],
) -> RuntimeResult<Transition> {
    if task_id.scope != t.next_state.session_id.scope {
        return Err(RuntimeError::AccessDenied);
    }
    let intent = ResultIntent {
        result_id: ResultId::new(
            task_id.scope.clone(),
            id(task_id, "", 0, "application_failure"),
        ),
        task_id: task_id.clone(),
        node_id: None,
        attempt: 0,
        status: TaskStatus::Failed,
        output: output.to_vec(),
        error: Some(error.clone()),
    };
    if let Some(mut task) = t.next_state.current_task.take() {
        if task.task_id == *task_id {
            if task.result_intent.is_some() {
                t.next_state.current_task = Some(task);
                return Ok(t);
            }
            let nodes: Vec<_> = task.nodes.keys().cloned().collect();
            let uncertain = task
                .nodes
                .values()
                .any(|n| matches!(n.phase, NodePhase::Running | NodePhase::WaitingInput));
            if uncertain && t.next_state.kind == RunnerKind::DirectA2a {
                t.next_state.context_blocked = true;
            }
            for node in nodes {
                if task.nodes[&node].handle.is_some()
                    && matches!(
                        task.nodes[&node].phase,
                        NodePhase::Running | NodePhase::WaitingInput
                    )
                {
                    cancel_agent(&mut t, &task, &node)?;
                }
                cancel_timers(&mut t, &task, &node);
                if !matches!(
                    task.nodes[&node].phase,
                    NodePhase::Completed
                        | NodePhase::Failed
                        | NodePhase::Cancelled
                        | NodePhase::Skipped
                ) {
                    task.nodes.get_mut(&node).unwrap().phase = NodePhase::PendingResult;
                }
            }
            task.cancel_requested = true;
            task.result_intent = Some(intent.clone());
            status(&mut t, &mut task, TaskStatus::PendingResult);
            t.next_state.current_task = Some(task);
            t.effects.push(EffectIntent::SaveResult { intent });
            return Ok(t);
        }
        t.next_state.current_task = Some(task);
    }
    if let Some(queued) = t
        .next_state
        .pending_tasks
        .iter_mut()
        .find(|q| q.task_id == *task_id)
    {
        if queued.terminal_intent.is_some() {
            return Ok(t);
        }
        queued.terminal_intent = Some(intent.clone());
        queued.message.parts.clear();
    } else {
        if t.next_state.pending_tasks.len() >= t.next_state.policy.max_pending_tasks as usize {
            return Err(RuntimeError::CapacityExceeded);
        }
        t.next_state.pending_tasks.push(QueuedTask {
            task_id: task_id.clone(),
            message: Message {
                message_id: MessageId::new(
                    task_id.scope.clone(),
                    id(task_id, "", 0, "failed_message"),
                ),
                context_id: Some(t.next_state.session_id.clone()),
                task_id: Some(task_id.clone()),
                parts: vec![],
            },
            admitted_at_ms: 0,
            terminal_intent: Some(intent.clone()),
        });
    }
    queued_status(&mut t, task_id, TaskStatus::PendingResult);
    t.effects.push(EffectIntent::SaveResult { intent });
    Ok(t)
}

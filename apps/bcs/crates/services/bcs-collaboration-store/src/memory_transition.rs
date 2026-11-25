use super::*;
pub(super) fn apply_memory_state_machine_transition(
    inner: &mut StoreInner,
    transition: &StateMachineEventfulTransition,
) -> ServiceResult<bool> {
    match transition {
        StateMachineEventfulTransition::AcceptHistory(command) => history::accept_memory(inner, command),
        StateMachineEventfulTransition::FinishJudge(command) => judge::finish_memory(inner, command),
        StateMachineEventfulTransition::StartRun {
            run_id,
            started_at_ms,
            ..
        } => {
            let run = inner.runs.get_mut(run_id).ok_or_else(|| {
                ServiceError::InternalError(format!("state machine run not found: {run_id}"))
            })?;
            if run.status != StateMachineRunStatus::Pending {
                return Ok(false);
            }
            run.status = StateMachineRunStatus::Running;
            run.updated_at = *started_at_ms;
            Ok(true)
        }
        StateMachineEventfulTransition::StartBotNode {
            run_id,
            node_id,
            attempt,
            delivery_request_id,
            started_at_ms,
            ..
        } => {
            if !run_is_running(inner, run_id)? {
                return Ok(false);
            }
            let node = node_mut(inner, run_id, node_id)?;
            if !matches!(
                node.status,
                StateMachineNodeStatus::Pending
                    | StateMachineNodeStatus::Ready
                    | StateMachineNodeStatus::RetryScheduled
            ) || node.attempt != *attempt
            {
                return Ok(false);
            }
            node.status = StateMachineNodeStatus::Running;
            node.delivery_request_id = Some(delivery_request_id.clone());
            node.bot_delivery_run_id = None;
            node.outcome = None;
            node.responded_by = None;
            node.artifact_text = None;
            node.error = None;
            node.started_at = Some(*started_at_ms);
            node.completed_at = None;
            node.timeout_deadline_ms = node
                .node_timeout_ms
                .map(|timeout_ms| started_at_ms.saturating_add(timeout_ms));
            Ok(true)
        }
        StateMachineEventfulTransition::StartHumanNode { command, .. } => {
            if !run_is_running(inner, &command.run_id)? {
                return Ok(false);
            }
            let node = node_mut(inner, &command.run_id, &command.node_id)?;
            if !matches!(
                node.status,
                StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready
            ) || node.attempt != command.attempt
            {
                return Ok(false);
            }
            node.status = StateMachineNodeStatus::Running;
            node.assignee_bot_id = None;
            node.delivery_request_id = None;
            node.bot_delivery_run_id = None;
            node.outcome = None;
            node.responded_by = None;
            node.artifact_text = None;
            node.error = None;
            node.started_at = Some(command.started_at_ms);
            node.completed_at = None;
            node.timeout_deadline_ms = Some(command.timeout_deadline_ms);
            Ok(true)
        }
        StateMachineEventfulTransition::CompleteNode {
            run_id,
            node_id,
            attempt,
            outcome,
            artifact_text,
            responded_by,
            completed_at_ms,
            ..
        } => {
            if !run_is_running(inner, run_id)? {
                return Ok(false);
            }
            if judge::is_judging(inner, run_id, node_id, *attempt) { return Ok(false); }
            let node = node_mut(inner, run_id, node_id)?;
            if node.status != StateMachineNodeStatus::Running || node.attempt != *attempt {
                return Ok(false);
            }
            node.status = StateMachineNodeStatus::Completed;
            node.outcome = Some(outcome.clone());
            node.responded_by = responded_by.clone();
            node.artifact_text = Some(artifact_text.clone());
            node.error = None;
            node.completed_at = Some(*completed_at_ms);
            node.timeout_deadline_ms = None;
            Ok(true)
        }
        StateMachineEventfulTransition::ScheduleNodeRetry {
            run_id,
            node_id,
            failed_attempt,
            next_attempt,
            ..
        } => {
            if !run_is_running(inner, run_id)? {
                return Ok(false);
            }
            let key = (run_id.clone(), node_id.clone());
            if inner.node_failure_actions.get(&key) == Some(&StateMachineFailureAction::FailRun) { return Ok(false); }
            let node = node_mut(inner, run_id, node_id)?;
            if node.status != StateMachineNodeStatus::Failed || node.attempt != *failed_attempt {
                return Ok(false);
            }
            node.status = StateMachineNodeStatus::RetryScheduled;
            node.attempt = *next_attempt;
            node.delivery_request_id = None;
            node.bot_delivery_run_id = None;
            node.artifact_text = None;
            node.error = None;
            node.started_at = None;
            node.completed_at = None;
            node.timeout_deadline_ms = None;
            inner.node_failure_actions.remove(&key);
            Ok(true)
        }
        StateMachineEventfulTransition::CompleteRun {
            run_id,
            output,
            completed_at_ms,
            ..
        } => {
            let run = inner.runs.get_mut(run_id).ok_or_else(|| {
                ServiceError::InternalError(format!("state machine run not found: {run_id}"))
            })?;
            if run.status != StateMachineRunStatus::Running {
                return Ok(false);
            }
            run.status = StateMachineRunStatus::Completed;
            run.output = output.clone();
            run.error = None;
            run.updated_at = *completed_at_ms;
            run.completed_at = Some(*completed_at_ms);
            Ok(true)
        }
    }
}

pub(super) fn node_mut<'a>(
    inner: &'a mut StoreInner,
    run_id: &str,
    node_id: &str,
) -> ServiceResult<&'a mut StateMachineNodeRun> {
    inner
        .nodes
        .get_mut(&(run_id.to_string(), node_id.to_string()))
        .ok_or_else(|| {
            ServiceError::InternalError(format!("state machine node not found: {run_id}/{node_id}"))
        })
}

pub(super) fn run_is_running(inner: &StoreInner, run_id: &str) -> ServiceResult<bool> {
    let run = inner.runs.get(run_id).ok_or_else(|| {
        ServiceError::InternalError(format!("state machine run not found: {run_id}"))
    })?;
    Ok(run.status == StateMachineRunStatus::Running)
}

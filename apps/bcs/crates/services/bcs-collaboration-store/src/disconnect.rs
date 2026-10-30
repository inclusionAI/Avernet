//! Connection-loss candidates and attempt-scoped failure CAS.
use super::*;
use bcs_service_api::port::repo::StateMachineRunReason;

const ERROR: &str = "state-machine worker WebSocket disconnected during execution";

impl MemoryCollaborationStore {
    pub(super) async fn disconnect_candidates(&self, bot: &str, at: u64, after: Option<(&str, &str)>, limit: usize) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        let mut nodes: Vec<_> = inner.nodes.values().filter(|n| n.status == StateMachineNodeStatus::Running
            && n.assignee_bot_id.as_deref() == Some(bot) && n.artifact_text.is_none()
            && n.started_at.is_some_and(|started| started <= at)
            && after.is_none_or(|(r, id)| (n.run_id.as_str(), n.node_id.as_str()) > (r, id))
            && inner.runs.get(&n.run_id).is_some_and(|run| run.status == StateMachineRunStatus::Running))
            .cloned().collect();
        nodes.sort_by(|a, b| (&a.run_id, &a.node_id).cmp(&(&b.run_id, &b.node_id)));
        nodes.truncate(limit.min(64));
        Ok(nodes)
    }
    pub(super) async fn disconnect_fail(&self, expected: &StateMachineNodeRun, at: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, &expected.run_id)? { return Ok(false); }
        let n = node_mut(&mut inner, &expected.run_id, &expected.node_id)?;
        if n.status != StateMachineNodeStatus::Running || n.attempt != expected.attempt || n.artifact_text.is_some()
            || n.assignee_bot_id != expected.assignee_bot_id || n.delivery_request_id != expected.delivery_request_id
            || n.started_at != expected.started_at || !n.started_at.is_some_and(|v| v <= at) || n.delivery_request_id.is_none() { return Ok(false); }
        n.status = StateMachineNodeStatus::Failed; n.error = Some(ERROR.into()); n.completed_at = Some(at); n.timeout_deadline_ms = None;
        inner.failure_reasons.insert((expected.run_id.clone(), expected.node_id.clone(), expected.attempt), StateMachineRunReason::NodeFailed);
        inner.node_failure_actions.insert((expected.run_id.clone(), expected.node_id.clone()), StateMachineFailureAction::FailRun);
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn disconnect_candidates(&self, bot: &str, at: u64, after: Option<(&str, &str)>, limit: usize) -> ServiceResult<Vec<StateMachineNodeRun>> {
        if limit == 0 { return Ok(Vec::new()); }
        let (run, node) = after.unwrap_or(("", ""));
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT {SM_NODE_SELECT_COLS} FROM bcs_state_machine_node_runs n WHERE n.env = ? AND n.assignee_bot_id = ? AND n.status = 'running' AND n.artifact_text IS NULL AND n.started_at_ms <= ? AND n.record_status = 'active' AND (n.run_id > ? OR (n.run_id = ? AND n.node_id > ?)) AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = n.env AND r.run_id = n.run_id AND r.status = 'running' AND r.record_status = 'active') ORDER BY n.run_id, n.node_id LIMIT ?"),
            vec![self.env.as_str().into(), bot.into(), at.into(), run.into(), run.into(), node.into(), (limit.min(64) as u64).into()])).await
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;
        rows.into_iter().map(row_to_state_machine_node_run).collect()
    }
    pub(super) async fn disconnect_fail(&self, n: &StateMachineNodeRun, at: u64) -> ServiceResult<bool> {
        let (Some(bot), Some(request), Some(started)) = (&n.assignee_bot_id, &n.delivery_request_id, n.started_at) else { return Ok(false); };
        if started > at { return Ok(false); }
        let metadata = failure_metadata::step(&self.env, &n.run_id, &n.node_id, n.attempt, StateMachineRunReason::NodeFailed, at)?;
        let statement = DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs SET status = 'failed', error_message = ?, completed_at_ms = ?, failure_action = 'fail_run', timeout_deadline_ms = NULL, {} WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND artifact_text IS NULL AND assignee_bot_id = ? AND delivery_request_id = ? AND started_at_ms = ? AND record_status = 'active' AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id AND r.status = 'running' AND r.record_status = 'active')", self.flavor.set_modified_now()),
            vec![ERROR.into(), at.into(), self.env.as_str().into(), n.run_id.as_str().into(), n.node_id.as_str().into(), n.attempt.into(), bot.as_str().into(), request.as_str().into(), started.into()]);
        match self.db.transaction(vec![DbTransactionStep::ExecuteChecked { statement, expected_affected_rows: 1 }, metadata]).await {
            Ok(_) => Ok(true), Err(DbError::ConditionFailed { expected: 1, actual: 0 }) => Ok(false),
            Err(e) => Err(ServiceError::InternalError(e.to_string())),
        }
    }
}

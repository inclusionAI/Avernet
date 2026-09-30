//! Conditional convergence for absent original facts. No work is reconstructed.
use super::*;
use bcs_service_api::{FailStateMachineStartup as Command, StateMachineMissingStartupFact as Missing,
    StateMachineStartupFailure as Failure, STATE_MACHINE_PREPARATION_GRACE_MS as GRACE};

fn key(run: &str) -> String { format!("smrun:{run}:startup-failure") }
fn dispatch_key(run: &str, node: &str, attempt: i32) -> String { format!("smnode:{run}:{node}:{attempt}:dispatch") }
fn error(e: impl std::fmt::Display) -> ServiceError { ServiceError::InternalError(format!("missing facts recovery: {e}")) }
fn failure(run: &StateMachineRun, c: &Command) -> ServiceResult<Failure> {
    if c.failed_at_ms > i64::MAX as u64 || c.preparation_error.as_ref().is_some_and(|e| e.trim().is_empty()) {
        return Err(error("invalid startup failure"));
    }
    Ok(Failure { run_id: run.run_id.clone(), group_id: run.group_id.clone(), session_id: run.session_id.clone(),
        session_activation_count: run.session_activation_count, run_created_at_ms: run.created_at, failed_at_ms: c.failed_at_ms,
        missing: c.missing, error: c.preparation_error.clone().unwrap_or_else(|| format!(
            "state-machine startup failed: original {} missing after preparation grace; no work replayed",
            match c.missing { Missing::Snapshot => "snapshot", Missing::Opening => "opening payload" })) })
}
const DISPATCH_ERROR: &str = "state-machine dispatch failed: original payload missing at deadline; delivery may have started, attempt not resent";

impl MemoryCollaborationStore {
    pub(super) async fn fail_startup_gap(&self, c: Command) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(run) = inner.runs.get(&c.run_id) else { return Ok(false); };
        let fact = failure(run, &c)?;
        if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running)
            || (c.preparation_error.is_none() && c.failed_at_ms < run.created_at.saturating_add(GRACE))
            || match c.missing { Missing::Snapshot => inner.run_snapshots.contains_key(&c.run_id), Missing::Opening => inner.openings.contains_key(&c.run_id) }
        { return Ok(false); }
        let run = inner.runs.get_mut(&c.run_id).expect("checked Run");
        run.status = StateMachineRunStatus::Failed; run.output = None; run.error = Some(fact.error.clone());
        run.updated_at = c.failed_at_ms; run.completed_at = Some(c.failed_at_ms);
        inner.startup_failures.insert(c.run_id, fact); Ok(true)
    }
    pub(super) async fn startup_failure(&self, run: &str) -> ServiceResult<Option<Failure>> {
        Ok(self.inner.read().await.startup_failures.get(run).cloned())
    }
    pub(super) async fn fail_dispatch_gap(&self, run: &str, node: &str, attempt: i32, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(r) = inner.runs.get(run) else { return Ok(false); };
        if r.status != StateMachineRunStatus::Running || inner.dispatches.contains_key(&dispatch_key(run, node, attempt)) { return Ok(false); }
        let created = r.created_at;
        let Some(n) = inner.nodes.get_mut(&(run.into(), node.into())) else { return Ok(false); };
        if n.status != StateMachineNodeStatus::Running || n.attempt != attempt || n.artifact_text.is_some() || n.assignee_bot_id.is_none() || n.bot_delivery_run_id.is_some()
            || now < n.timeout_deadline_ms.unwrap_or_else(|| n.started_at.unwrap_or(created).saturating_add(GRACE)) { return Ok(false); }
        let retry = n.timeout_deadline_ms.is_some() && retry_is_within_limit(attempt, n.max_attempts);
        n.status = StateMachineNodeStatus::Failed; n.error = Some(DISPATCH_ERROR.into()); n.completed_at = Some(now); n.timeout_deadline_ms = None;
        inner.node_failure_actions.insert((run.into(), node.into()), if retry { StateMachineFailureAction::Retry } else { StateMachineFailureAction::FailRun });
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn fail_startup_gap(&self, c: Command) -> ServiceResult<bool> {
        let Some(run) = self.get_run(&c.run_id).await? else { return Ok(false); };
        let fact = failure(&run, &c)?;
        if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running)
            || (c.preparation_error.is_none() && c.failed_at_ms < run.created_at.saturating_add(GRACE)) { return Ok(false); }
        let (missing, missing_id) = match c.missing {
            Missing::Snapshot => ("NOT EXISTS (SELECT 1 FROM bcs_state_machine_definition_snapshots s WHERE s.env = bcs_state_machine_runs.env AND s.run_id = ?)", c.run_id.clone()),
            Missing::Opening => ("NOT EXISTS (SELECT 1 FROM bcs_collaboration_delivery_checkpoints c WHERE c.env = bcs_state_machine_runs.env AND c.operation_key = ?)", format!("smrun:{}:opening", c.run_id)),
        };
        // The conditional Run update is the authority. The typed failure fact
        // commits with it; a failure inserting the fact rolls back the Run CAS.
        let result = self.db.transaction(vec![
            DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(format!(
                "UPDATE bcs_state_machine_runs SET status = 'failed', output_text = NULL, error_message = ?, updated_at_ms = ?, completed_at_ms = ?, {} \
                 WHERE env = ? AND run_id = ? AND created_at_ms = ? AND status IN ('pending', 'running') AND record_status = 'active' AND {missing}", self.flavor.set_modified_now()),
                vec![DbValue::from(fact.error.as_str()), DbValue::from(c.failed_at_ms), DbValue::from(c.failed_at_ms), DbValue::from(self.env.as_str()),
                    DbValue::from(c.run_id.as_str()), DbValue::from(run.created_at), DbValue::from(missing_id)]), expected_affected_rows: 1 },
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms) \
                 VALUES (?, ?, 'state_machine_run', ?, 'startup_failure', ?, 'failed', ?)",
                vec![DbValue::from(self.env.as_str()), DbValue::from(key(&c.run_id)), DbValue::from(c.run_id),
                    DbValue::from(serde_json::to_string(&fact).map_err(error)?), DbValue::from(c.failed_at_ms)])),
        ]).await;
        match result { Ok(_) => Ok(true), Err(DbError::ConditionFailed { expected: 1, actual: 0 }) => Ok(false), Err(e) => Err(error(e)) }
    }
    pub(super) async fn startup_failure(&self, run: &str) -> ServiceResult<Option<Failure>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT payload_json, aggregate_id, operation_kind, status, created_at_ms FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))])).await.map_err(error)?;
        rows.first().map(|row| {
            let value: Failure = serde_json::from_str(&db_get_column::<String>(row, "payload_json").map_err(error)?).map_err(error)?;
            if value.run_id != run || db_get_column::<String>(row, "aggregate_id").map_err(error)? != run
                || db_get_column::<String>(row, "operation_kind").map_err(error)? != "startup_failure"
                || db_get_column::<String>(row, "status").map_err(error)? != "failed"
                || db_get_column::<u64>(row, "created_at_ms").map_err(error)? != value.failed_at_ms || value.error.is_empty() {
                return Err(error("invalid startup failure fact"));
            }
            Ok(value)
        }).transpose()
    }
    pub(super) async fn fail_dispatch_gap(&self, run: &str, node: &str, attempt: i32, now: u64) -> ServiceResult<bool> {
        let result = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs SET status = 'failed', error_message = ?, completed_at_ms = ?, \
             failure_action = CASE WHEN timeout_deadline_ms IS NOT NULL AND attempt >= 0 AND attempt < CASE WHEN max_attempts > 0 THEN max_attempts ELSE 1 END - 1 THEN 'retry' ELSE 'fail_run' END, \
             timeout_deadline_ms = NULL, runtime_phase = NULL, {} WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
             AND status = 'running' AND artifact_text IS NULL AND bot_delivery_run_id IS NULL AND assignee_bot_id IS NOT NULL AND record_status = 'active' \
             AND NOT EXISTS (SELECT 1 FROM bcs_collaboration_delivery_checkpoints c WHERE c.env = bcs_state_machine_node_runs.env AND c.operation_key = ?) \
             AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id \
             AND r.status = 'running' AND r.record_status = 'active' AND COALESCE(bcs_state_machine_node_runs.timeout_deadline_ms, COALESCE(bcs_state_machine_node_runs.started_at_ms, r.created_at_ms) + {GRACE}) <= ?)", self.flavor.set_modified_now()),
            vec![DbValue::from(DISPATCH_ERROR), DbValue::from(now), DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(node),
                DbValue::from(attempt), DbValue::from(dispatch_key(run, node, attempt)), DbValue::from(now)])).await.map_err(error)?;
        Ok(result.affected_rows > 0)
    }
}

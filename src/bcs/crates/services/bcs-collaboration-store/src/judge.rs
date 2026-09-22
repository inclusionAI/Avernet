//! Node-local Judge input, lease and fenced result persistence.
use super::*;
use bcs_service_api::{FinishStateMachineJudge, StateMachineJudgeClaim, StateMachineJudgeResult};

#[derive(Debug, Clone)]
pub(super) struct JudgeLease {
    attempt: i32,
    owner: Option<String>,
    token: i64,
    until: Option<u64>,
    retired: bool,
}

pub(super) fn is_judging(inner: &StoreInner, run: &str, node: &str, attempt: i32) -> bool {
    inner.judge_leases.get(&(run.into(), node.into())).is_some_and(|lease| lease.attempt == attempt && !lease.retired)
}

pub(super) fn retire_terminal_leases(inner: &mut StoreInner, run: &str, limit: usize) -> usize {
    let leases = inner.judge_leases.iter_mut().filter(|((id, _), lease)| id == run && !lease.retired).take(limit);
    let mut retired = 0;
    for (_, lease) in leases {
        lease.owner = None; lease.until = None; lease.retired = true;
        retired += 1;
    }
    retired
}

fn validate_claim(owner: &str, now: u64, until: u64) -> ServiceResult<()> {
    if owner.is_empty() || owner.len() > 64 || until <= now || until > i64::MAX as u64 {
        return Err(ServiceError::InternalError("invalid Judge lease".into()));
    }
    Ok(())
}

impl MemoryCollaborationStore {
    pub(super) async fn begin_judging(&self, run: &str, id: &str, attempt: i32, artifact: String, responder: Option<String>) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run)? { return Ok(false); }
        let key = (run.to_string(), id.to_string());
        let existing = inner.judge_leases.get(&key).cloned();
        let node = node_mut(&mut inner, run, id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt { return Ok(false); }
        if node.artifact_text.is_some() {
            return Ok(existing.is_some_and(|lease| lease.attempt == attempt)
                && node.artifact_text.as_ref() == Some(&artifact) && node.responded_by == responder);
        }
        node.artifact_text = Some(artifact);
        node.responded_by = responder;
        node.error = None;
        inner.judge_leases.insert(key, JudgeLease {
            attempt, owner: None, token: existing.map_or(0, |lease| lease.token), until: None, retired: false,
        });
        Ok(true)
    }

    pub(super) async fn claim_judging(&self, run: &str, id: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineJudgeClaim>> {
        validate_claim(&owner, now, until)?;
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run)? { return Ok(None); }
        let node = node_mut(&mut inner, run, id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt || node.artifact_text.is_none() { return Ok(None); }
        let Some(lease) = inner.judge_leases.get_mut(&(run.into(), id.into())) else { return Ok(None); };
        if lease.retired || lease.attempt != attempt || lease.until.is_some_and(|value| value > now) { return Ok(None); }
        lease.token = lease.token.checked_add(1).ok_or_else(|| ServiceError::InternalError("Judge token overflow".into()))?;
        lease.owner = Some(owner.clone());
        lease.until = Some(until);
        Ok(Some(StateMachineJudgeClaim { run_id: run.into(), node_id: id.into(), attempt, owner, token: lease.token, lease_until_ms: until }))
    }

    pub(super) async fn release_judging(&self, claim: &StateMachineJudgeClaim) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(lease) = inner.judge_leases.get_mut(&(claim.run_id.clone(), claim.node_id.clone())) else { return Ok(false); };
        if lease.attempt != claim.attempt || lease.token != claim.token || lease.owner.as_ref() != Some(&claim.owner) { return Ok(false); }
        lease.owner = None;
        lease.until = None;
        Ok(true)
    }
}

pub(super) fn finish_memory(inner: &mut StoreInner, command: &FinishStateMachineJudge) -> ServiceResult<bool> {
    let claim = &command.claim;
    if !run_is_running(inner, &claim.run_id)? { return Ok(false); }
    let key = (claim.run_id.clone(), claim.node_id.clone());
    let Some(lease) = inner.judge_leases.get(&key) else { return Ok(false); };
    if lease.attempt != claim.attempt || lease.token != claim.token || lease.owner.as_ref() != Some(&claim.owner)
        || lease.until != Some(claim.lease_until_ms) || claim.lease_until_ms <= command.completed_at_ms { return Ok(false); }
    let node = node_mut(inner, &claim.run_id, &claim.node_id)?;
    if node.status != StateMachineNodeStatus::Running || node.attempt != claim.attempt || node.artifact_text.is_none() { return Ok(false); }
    match &command.result {
        StateMachineJudgeResult::Completed(decision) => {
            node.status = StateMachineNodeStatus::Completed;
            node.outcome = Some(decision.outcome.clone());
            node.error = None;
        }
        StateMachineJudgeResult::Failed { error, action, .. } => {
            if *action == StateMachineFailureAction::Retry && !retry_is_within_limit(node.attempt, node.max_attempts) { return Ok(false); }
            node.status = StateMachineNodeStatus::Failed;
            node.error = Some(error.clone());
        }
    }
    node.completed_at = Some(command.completed_at_ms);
    node.timeout_deadline_ms = None;
    if let StateMachineJudgeResult::Failed { action, .. } = &command.result {
        inner.node_failure_actions.insert(key.clone(), *action);
    }
    let lease = inner.judge_leases.get_mut(&key).expect("validated Judge lease");
    lease.owner = None;
    lease.until = None;
    lease.retired = true;
    let (event_type, payload) = audit(&command.result)?;
    inner.events.push(CollaborationEventRecord {
        state_machine_run_id: claim.run_id.clone(), node_id: Some(claim.node_id.clone()), attempt: Some(claim.attempt),
        event_type: event_type.into(), payload, created_at: command.completed_at_ms,
    });
    Ok(true)
}

fn audit(result: &StateMachineJudgeResult) -> ServiceResult<(&'static str, serde_json::Value)> {
    match result {
        StateMachineJudgeResult::Completed(decision) => Ok(("state_machine.judge.completed",
            serde_json::to_value(decision).map_err(|error| ServiceError::InternalError(error.to_string()))?)),
        StateMachineJudgeResult::Failed { details, .. } => Ok(("state_machine.judge.failed", details.clone())),
    }
}

const ACTIVE_RUN: &str = "EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id AND r.status = 'running' AND r.record_status = 'active')";

impl MySqlCollaborationStore {
    pub(super) async fn begin_judging(&self, run: &str, id: &str, attempt: i32, artifact: String, responder: Option<String>) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs SET runtime_phase = 'judging', artifact_text = ?, responded_by = ?, error_message = NULL, recovery_lease_owner = NULL, recovery_lease_until_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND record_status = 'active' AND artifact_text IS NULL AND (runtime_phase IS NULL OR runtime_phase IN ('dispatch_pending', 'waiting_provider')) AND {ACTIVE_RUN}", self.flavor.set_modified_now()),
            vec![DbValue::from(artifact.as_str()), DbValue::from(responder.as_deref()), DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(id), DbValue::from(attempt)]
        )).await.map_err(|error| ServiceError::InternalError(format!("Judge input save: {error}")))?;
        if changed.affected_rows > 0 { return Ok(true); }
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT artifact_text, responded_by FROM bcs_state_machine_node_runs WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
             AND status = 'running' AND runtime_phase = 'judging' AND record_status = 'active' AND {ACTIVE_RUN}"),
            vec![DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(id), DbValue::from(attempt)]
        )).await.map_err(|error| ServiceError::InternalError(format!("Judge input read: {error}")))?;
        let Some(row) = rows.first() else { return Ok(false); };
        // Compare exact text in Rust; database collations may ignore case/spaces.
        let saved: String = db_get_column(row, "artifact_text").map_err(db_error)?;
        let saved_responder: Option<String> = db_get_column_opt(row, "responded_by").map_err(db_error)?;
        Ok(saved == artifact && saved_responder == responder)
    }

    pub(super) async fn claim_judging(&self, run: &str, id: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineJudgeClaim>> {
        validate_claim(&owner, now, until)?;
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs SET recovery_lease_owner = ?, recovery_lease_token = recovery_lease_token + 1, recovery_lease_until_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND runtime_phase = 'judging' AND artifact_text IS NOT NULL \
             AND record_status = 'active' AND (recovery_lease_until_ms IS NULL OR recovery_lease_until_ms <= ?) AND {ACTIVE_RUN}", self.flavor.set_modified_now()),
            vec![DbValue::from(owner.as_str()), DbValue::from(until), DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(id), DbValue::from(attempt), DbValue::from(now)]
        )).await.map_err(|error| ServiceError::InternalError(format!("Judge claim: {error}")))?;
        if changed.affected_rows == 0 { return Ok(None); }
        let rows = self.db.query(DbStatement::with_params(
            "SELECT recovery_lease_token FROM bcs_state_machine_node_runs WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND recovery_lease_owner = ? AND recovery_lease_until_ms = ? AND status = 'running' AND runtime_phase = 'judging' AND record_status = 'active'",
            vec![DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(id), DbValue::from(attempt), DbValue::from(owner.as_str()), DbValue::from(until)]
        )).await.map_err(|error| ServiceError::InternalError(format!("Judge claim read: {error}")))?;
        rows.first().map(|row| Ok(StateMachineJudgeClaim { run_id: run.into(), node_id: id.into(), attempt, owner,
            token: db_get_column(row, "recovery_lease_token").map_err(db_error)?, lease_until_ms: until })).transpose()
    }

    pub(super) async fn release_judging(&self, claim: &StateMachineJudgeClaim) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_state_machine_node_runs SET recovery_lease_owner = NULL, recovery_lease_until_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND recovery_lease_owner = ? AND recovery_lease_token = ? AND record_status = 'active'", self.flavor.set_modified_now()),
            vec![DbValue::from(self.env.as_str()), DbValue::from(claim.run_id.as_str()), DbValue::from(claim.node_id.as_str()), DbValue::from(claim.attempt), DbValue::from(claim.owner.as_str()), DbValue::from(claim.token)]
        )).await.map_err(|error| ServiceError::InternalError(format!("Judge release: {error}")))?;
        Ok(changed.affected_rows > 0)
    }
}

fn db_error(error: DbError) -> ServiceError { ServiceError::InternalError(format!("Judge row: {error}")) }

pub(super) fn finish_sql(store: &MySqlCollaborationStore, command: &FinishStateMachineJudge) -> ServiceResult<Vec<DbTransactionStep>> {
    let claim = &command.claim;
    let lock = if store.flavor == DbSqlFlavor::Mysql { " FOR UPDATE" } else { "" };
    let (status, outcome, error, action) = match &command.result {
        StateMachineJudgeResult::Completed(decision) => ("completed", Some(decision.outcome.as_str()), None, None),
        StateMachineJudgeResult::Failed { error, action, .. } => ("failed", None, Some(error.as_str()), Some(match action {
            StateMachineFailureAction::Retry => "retry", StateMachineFailureAction::FailRun => "fail_run",
        })),
    };
    let retry_guard = if action == Some("retry") { " AND attempt >= 0 AND attempt < 2147483647 AND attempt + 1 < max_attempts" } else { "" };
    let (event_type, payload) = audit(&command.result)?;
    Ok(vec![
        DbTransactionStep::Query(DbStatement::with_params(format!(
            "SELECT run_id, node_id FROM bcs_state_machine_node_runs WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
             AND status = 'running' AND runtime_phase = 'judging' AND artifact_text IS NOT NULL AND record_status = 'active' \
             AND recovery_lease_owner = ? AND recovery_lease_token = ? AND recovery_lease_until_ms = ? AND recovery_lease_until_ms > ? \
             AND {ACTIVE_RUN}{retry_guard}{lock}"),
            vec![DbValue::from(store.env.as_str()), DbValue::from(claim.run_id.as_str()), DbValue::from(claim.node_id.as_str()), DbValue::from(claim.attempt),
                DbValue::from(claim.owner.as_str()), DbValue::from(claim.token), DbValue::from(claim.lease_until_ms), DbValue::from(command.completed_at_ms)])),
        DbTransactionStep::Execute(DbStatement::with_transaction_params(format!(
            "UPDATE bcs_state_machine_node_runs SET status = ?, outcome = ?, error_message = ?, failure_action = ?, completed_at_ms = ?, \
             timeout_deadline_ms = NULL, runtime_phase = NULL, recovery_lease_owner = NULL, recovery_lease_until_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ?", store.flavor.set_modified_now()),
            vec![DbTransactionParam::value(status), DbTransactionParam::value(DbValue::from(outcome)), DbTransactionParam::value(DbValue::from(error)),
                DbTransactionParam::value(DbValue::from(action)), DbTransactionParam::value(command.completed_at_ms), DbTransactionParam::value(store.env.as_str()),
                DbTransactionParam::query_result(0, 0, "run_id"), DbTransactionParam::query_result(0, 0, "node_id"), DbTransactionParam::value(claim.attempt)])),
        DbTransactionStep::Execute(DbStatement::with_transaction_params(
            "INSERT INTO bcs_collaboration_events (env, state_machine_run_id, node_id, attempt, event_type, payload_json, created_at_ms, record_status) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            vec![DbTransactionParam::value(store.env.as_str()), DbTransactionParam::query_result(0, 0, "run_id"), DbTransactionParam::query_result(0, 0, "node_id"),
                DbTransactionParam::value(claim.attempt), DbTransactionParam::value(event_type), DbTransactionParam::value(payload.to_string()), DbTransactionParam::value(command.completed_at_ms)])),
    ])
}

pub(super) fn accept_history_input(inner: &mut StoreInner, run: &str, id: &str, attempt: i32) {
    let key = (run.to_owned(), id.to_owned());
    let token: i64 = inner.judge_leases.get(&key).map_or(0, |lease| lease.token);
    inner.judge_leases.insert(key, JudgeLease { attempt, owner: None, token, until: None, retired: false });
}

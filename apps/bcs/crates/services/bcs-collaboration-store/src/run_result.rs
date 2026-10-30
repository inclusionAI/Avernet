//! Manager RunResult intents. `TerminateRun` writes the Pending row in the same
//! transaction as the terminal Run CAS; delivery claims it under a lease, marks
//! Delivering before any send and never resends once a send may have started.
use super::*;
use bcs_service_api::{StateMachineRunResultPayload as Payload, StateMachineRunResultCheckpoint as Checkpoint,
    StateMachineRunResultClaim as Claim, StateMachineRunResultStatus as Status, StateMachineRunResultOutcome as Outcome};

pub(super) fn key(run: &str) -> String { format!("smrun:{run}:run-result") }
fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
fn db_error(error: DbError) -> ServiceError { invalid(&format!("RunResult store: {error}")) }
const EXPIRED: &str = "RunResult deadline reached before delivery";
const UNCERTAIN: &str = "RunResult send outcome unknown after the lease expired; not resent";

fn validate_lease(owner: &str, now: u64, until: u64) -> ServiceResult<()> {
    if owner.is_empty() || owner.len() > 64 || until <= now || until > i64::MAX as u64 {
        return Err(invalid("invalid RunResult lease"));
    }
    Ok(())
}
fn owns(saved: &Checkpoint, claim: &Claim) -> bool {
    saved.payload == claim.payload && saved.lease_owner.as_ref() == Some(&claim.owner)
        && saved.lease_token == claim.token && saved.lease_until_ms == Some(claim.lease_until_ms)
}
/// Status and recorded error for a publisher outcome.
fn settle(outcome: &Outcome) -> (Status, Option<String>) {
    match outcome {
        Outcome::Delivered => (Status::Delivered, None),
        Outcome::Skipped { reason } => (Status::Skipped, Some(reason.clone())),
        Outcome::Retry { error } => (Status::Pending, Some(error.clone())),
        Outcome::Uncertain { error } => (Status::Failed, Some(error.clone())),
    }
}

/// Validate a payload before `TerminateRun` stores it for this Run.
pub(super) fn validate_for_run(payload: &Payload, run_id: &str) -> ServiceResult<()> {
    payload.validate()?;
    if payload.run_id != run_id {
        return Err(invalid("RunResult payload belongs to another Run"));
    }
    Ok(())
}

pub(super) fn pending_checkpoint(payload: Payload) -> Checkpoint {
    Checkpoint { payload, status: Status::Pending, lease_owner: None, lease_token: 0, lease_until_ms: None,
        error: None, delivered_at_ms: None }
}

impl MemoryCollaborationStore {
    pub(super) async fn run_result_get(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        Ok(self.inner.read().await.run_results.get(run).cloned())
    }
    pub(super) async fn run_result_list_pending(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        let now = current_millis();
        let inner = self.inner.read().await;
        Ok(inner.run_results.iter()
            .filter(|(run, _)| after.is_none_or(|after| run.as_str() > after))
            .filter(|(_, saved)| saved.status == Status::Pending
                || (saved.status == Status::Delivering && saved.lease_until_ms.is_none_or(|until| until <= now)))
            .take(limit).map(|(run, _)| run.clone()).collect())
    }
    pub(super) async fn run_result_claim(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        validate_lease(&owner, now, until)?;
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.run_results.get_mut(run) else { return Ok(None); };
        if saved.status != Status::Pending || saved.payload.deadline_ms <= now || saved.payload.deadline_ms < until
            || saved.lease_until_ms.is_some_and(|expiry| expiry > now) {
            return Ok(None);
        }
        saved.lease_token = saved.lease_token.checked_add(1).ok_or_else(|| invalid("RunResult token overflow"))?;
        saved.lease_owner = Some(owner.clone());
        saved.lease_until_ms = Some(until);
        Ok(Some(Claim { payload: saved.payload.clone(), owner, token: saved.lease_token, lease_until_ms: until }))
    }
    pub(super) async fn run_result_begin(&self, claim: &Claim, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.run_results.get_mut(&claim.payload.run_id) else { return Ok(false); };
        if saved.status != Status::Pending || !owns(saved, claim) || claim.lease_until_ms <= now || saved.payload.deadline_ms <= now {
            return Ok(false);
        }
        saved.status = Status::Delivering;
        Ok(true)
    }
    pub(super) async fn run_result_finish(&self, claim: &Claim, outcome: Outcome, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.run_results.get_mut(&claim.payload.run_id) else { return Ok(false); };
        if saved.status != Status::Delivering || !owns(saved, claim) || claim.lease_until_ms <= now {
            return Ok(false);
        }
        let (status, error) = settle(&outcome);
        saved.status = status;
        saved.error = error;
        saved.delivered_at_ms = (status == Status::Delivered).then_some(now);
        saved.lease_owner = None;
        saved.lease_until_ms = None;
        Ok(true)
    }
    pub(super) async fn run_result_expire(&self, run: &str, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.run_results.get_mut(run) else { return Ok(false); };
        let lease_expired = saved.lease_until_ms.is_none_or(|until| until <= now);
        let (status, error) = match saved.status {
            Status::Pending if saved.payload.deadline_ms <= now && lease_expired => (Status::Expired, EXPIRED),
            Status::Delivering if lease_expired => (Status::Failed, UNCERTAIN),
            _ => return Ok(false),
        };
        saved.status = status;
        saved.error = Some(error.into());
        saved.lease_owner = None;
        saved.lease_until_ms = None;
        Ok(true)
    }
}

/// Insert the Pending intent inside a Memory `TerminateRun` candidate.
pub(super) fn insert_memory_run_result(inner: &mut StoreInner, run_id: &str, payload: &Payload) -> ServiceResult<()> {
    validate_for_run(payload, run_id)?;
    if inner.run_results.contains_key(run_id) {
        return Err(ServiceError::Conflict(format!("RunResult already exists for Run {run_id}")));
    }
    inner.run_results.insert(run_id.to_string(), pending_checkpoint(payload.clone()));
    Ok(())
}

impl MySqlCollaborationStore {
    /// INSERT step for the Pending intent inside a SQL `TerminateRun` transaction.
    pub(super) fn run_result_insert_step(&self, run_id: &str, payload: &Payload) -> ServiceResult<DbTransactionStep> {
        validate_for_run(payload, run_id)?;
        let json = serde_json::to_string(payload).map_err(|e| invalid(&format!("encode RunResult payload: {e}")))?;
        Ok(DbTransactionStep::Execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, deadline_ms) \
             VALUES (?, ?, 'state_machine_run', ?, 'run_result', ?, 'pending', ?, ?)",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run_id)), DbValue::from(run_id), DbValue::from(json),
                DbValue::from(payload.terminated_at_ms), DbValue::from(payload.deadline_ms)])))
    }
    pub(super) async fn run_result_get(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, deadline_ms, lease_owner, lease_token, lease_until_ms, last_error, delivered_at_ms FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))])).await.map_err(db_error)?;
        rows.first().map(|row| {
            let string = |name: &str| db_get_column::<String>(row, name).map_err(db_error);
            let payload: Payload = serde_json::from_str(&string("payload_json")?).map_err(|e| invalid(&format!("invalid RunResult payload: {e}")))?;
            validate_for_run(&payload, run)?;
            let status: Status = serde_json::from_value(serde_json::Value::String(string("status")?)).map_err(|_| invalid("invalid RunResult status"))?;
            let lease_owner = db_get_column_opt::<String>(row, "lease_owner").map_err(db_error)?;
            let lease_until_ms = db_get_column_opt::<u64>(row, "lease_until_ms").map_err(db_error)?;
            let lease_token = db_get_column::<i64>(row, "lease_token").map_err(db_error)?;
            let error = db_get_column_opt::<String>(row, "last_error").map_err(db_error)?;
            let delivered_at_ms = db_get_column_opt::<u64>(row, "delivered_at_ms").map_err(db_error)?;
            if string("aggregate_id")? != run || string("aggregate_kind")? != "state_machine_run" || string("operation_kind")? != "run_result"
                || db_get_column::<u64>(row, "created_at_ms").map_err(db_error)? != payload.terminated_at_ms
                || db_get_column::<u64>(row, "deadline_ms").map_err(db_error)? != payload.deadline_ms || lease_token < 0
                || lease_owner.is_some() != lease_until_ms.is_some() || (status == Status::Delivered) != delivered_at_ms.is_some() {
                return Err(invalid("invalid RunResult checkpoint state"));
            }
            Ok(Checkpoint { payload, status, lease_owner, lease_token, lease_until_ms, error, delivered_at_ms })
        }).transpose()
    }
    pub(super) async fn run_result_list_pending(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        if limit == 0 { return Ok(Vec::new()); }
        let mut params = vec![DbValue::from(self.env.as_str()), DbValue::from(current_millis())];
        let cursor = if let Some(after) = after { params.push(DbValue::from(after)); " AND aggregate_id > ?" } else { "" };
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT aggregate_id FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_kind = 'run_result' \
             AND (status = 'pending' OR (status = 'delivering' AND (lease_until_ms IS NULL OR lease_until_ms <= ?))){cursor} \
             ORDER BY aggregate_id LIMIT {}", limit.min(i64::MAX as usize)), params)).await.map_err(db_error)?;
        rows.iter().map(|row| db_get_column(row, "aggregate_id").map_err(db_error)).collect()
    }
    pub(super) async fn run_result_claim(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        validate_lease(&owner, now, until)?;
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = ?, lease_token = lease_token + 1, lease_until_ms = ? \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'run_result' AND status = 'pending' AND deadline_ms > ? AND deadline_ms >= ? \
             AND (lease_until_ms IS NULL OR lease_until_ms <= ?)",
            vec![DbValue::from(owner.as_str()), DbValue::from(until), DbValue::from(self.env.as_str()), DbValue::from(key(run)),
                DbValue::from(now), DbValue::from(until), DbValue::from(now)])).await.map_err(db_error)?;
        if changed.affected_rows == 0 { return Ok(None); }
        let Some(saved) = self.run_result_get(run).await? else { return Ok(None); };
        if saved.lease_owner.as_ref() != Some(&owner) || saved.lease_until_ms != Some(until) || saved.status != Status::Pending { return Ok(None); }
        Ok(Some(Claim { payload: saved.payload, owner, token: saved.lease_token, lease_until_ms: until }))
    }
    fn run_result_fence(&self, claim: &Claim) -> Vec<DbValue> {
        vec![DbValue::from(self.env.as_str()), DbValue::from(key(&claim.payload.run_id)), DbValue::from(claim.owner.as_str()),
            DbValue::from(claim.token), DbValue::from(claim.lease_until_ms)]
    }
    pub(super) async fn run_result_begin(&self, claim: &Claim, now: u64) -> ServiceResult<bool> {
        let mut params = self.run_result_fence(claim);
        params.extend([DbValue::from(now), DbValue::from(now)]);
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'delivering' WHERE env = ? AND operation_key = ? \
             AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? AND deadline_ms > ? \
             AND status = 'pending' AND operation_kind = 'run_result'", params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn run_result_finish(&self, claim: &Claim, outcome: Outcome, now: u64) -> ServiceResult<bool> {
        let (status, error) = settle(&outcome);
        let mut params = vec![DbValue::from(status.as_str()), DbValue::from(error.as_deref()),
            optional_u64_value((status == Status::Delivered).then_some(now))];
        params.extend(self.run_result_fence(claim));
        params.push(DbValue::from(now));
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = ?, last_error = ?, delivered_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? \
             AND status = 'delivering' AND operation_kind = 'run_result'", params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn run_result_expire(&self, run: &str, now: u64) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = CASE status WHEN 'pending' THEN 'expired' ELSE 'failed' END, \
             last_error = CASE status WHEN 'pending' THEN ? ELSE ? END, lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'run_result' AND (lease_until_ms IS NULL OR lease_until_ms <= ?) \
             AND ((status = 'pending' AND deadline_ms <= ?) OR status = 'delivering')",
            vec![DbValue::from(EXPIRED), DbValue::from(UNCERTAIN), DbValue::from(self.env.as_str()), DbValue::from(key(run)),
                DbValue::from(now), DbValue::from(now)])).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
}

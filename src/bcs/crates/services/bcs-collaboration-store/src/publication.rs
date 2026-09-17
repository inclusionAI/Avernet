//! Chat result checkpoints use existing delivery rows and independent CAS writes.
use super::*;
use bcs_service_api::{StateMachineChatResultPayload as Payload, StateMachineChatResultCheckpoint as Checkpoint,
    StateMachineChatResultClaim as Claim, StateMachineChatResultStatus as Status, StateMachineChatResultOutcome as Outcome};

fn key(run: &str) -> String { format!("smrun:{run}:chat-result") }
fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
fn validate(p: &Payload) -> ServiceResult<()> {
    let c = &p.command;
    if c.run_id.is_empty() || c.run_id.len() > 128 || c.group_id.is_empty() || c.session_id.is_empty()
        || c.sender_bot_id.trim().is_empty() || p.deadline_ms <= c.created_at_ms || p.deadline_ms > i64::MAX as u64 {
        return Err(invalid("invalid Chat result payload"));
    }
    Ok(())
}
fn same(saved: &Payload, requested: &Payload) -> ServiceResult<bool> {
    if saved != requested { return Err(ServiceError::Conflict("Chat result payload is immutable".into())); }
    Ok(true)
}
fn active(inner: &StoreInner, run: &str) -> bool {
    inner.runs.get(run).is_some_and(|r| r.status == StateMachineRunStatus::Running)
}
fn owns(saved: &Checkpoint, claim: &Claim) -> bool {
    saved.payload == claim.payload && saved.lease_owner.as_ref() == Some(&claim.owner)
        && saved.lease_token == claim.token && saved.lease_until_ms == Some(claim.lease_until_ms)
}
fn validate_lease(owner: &str, now: u64, until: u64) -> ServiceResult<()> {
    if owner.is_empty() || owner.len() > 64 || until <= now || until > i64::MAX as u64 {
        return Err(invalid("invalid Chat result lease"));
    }
    Ok(())
}
const EXPIRED: &str = "Chat result publication deadline reached; delivery may be unknown and is not retried";

impl MemoryCollaborationStore {
    pub(super) async fn save_publication(&self, payload: Payload) -> ServiceResult<bool> {
        validate(&payload)?;
        let mut inner = self.inner.write().await;
        let c = &payload.command;
        if let Some(saved) = inner.publications.get(&c.run_id) { return same(&saved.payload, &payload); }
        if !inner.runs.get(&c.run_id).is_some_and(|r| r.status == StateMachineRunStatus::Running
            && r.group_id == c.group_id && r.session_id == c.session_id && r.created_by.as_ref() == Some(&c.sender_bot_id))
            || inner.nodes.values().any(|n| n.run_id == c.run_id && !matches!(n.status, StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped)) {
            return Ok(false);
        }
        inner.publications.insert(c.run_id.clone(), Checkpoint { payload, status: Status::Pending,
            lease_owner: None, lease_token: 0, lease_until_ms: None, error: None, delivered_at_ms: None });
        Ok(true)
    }
    pub(super) async fn get_publication(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        Ok(self.inner.read().await.publications.get(run).cloned())
    }
    pub(super) async fn claim_publication(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        validate_lease(&owner, now, until)?;
        let mut inner = self.inner.write().await;
        if !active(&inner, run) { return Ok(None); }
        let Some(saved) = inner.publications.get_mut(run) else { return Ok(None); };
        if saved.status != Status::Pending || saved.payload.deadline_ms < until || saved.payload.deadline_ms <= now
            || saved.lease_until_ms.is_some_and(|expiry| expiry > now) { return Ok(None); }
        saved.lease_token = saved.lease_token.checked_add(1).ok_or_else(|| invalid("Chat result token overflow"))?;
        saved.lease_owner = Some(owner.clone()); saved.lease_until_ms = Some(until);
        Ok(Some(Claim { payload: saved.payload.clone(), owner, token: saved.lease_token, lease_until_ms: until }))
    }
    pub(super) async fn begin_publication(&self, claim: &Claim, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !active(&inner, &claim.payload.command.run_id) || claim.lease_until_ms <= now || claim.payload.deadline_ms <= now { return Ok(false); }
        let Some(saved) = inner.publications.get_mut(&claim.payload.command.run_id) else { return Ok(false); };
        if saved.status != Status::Pending || !owns(saved, claim) { return Ok(false); }
        saved.status = Status::Delivering;
        Ok(true)
    }
    pub(super) async fn finish_publication(&self, claim: &Claim, result: Outcome, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !active(&inner, &claim.payload.command.run_id) || claim.lease_until_ms <= now { return Ok(false); }
        let Some(saved) = inner.publications.get_mut(&claim.payload.command.run_id) else { return Ok(false); };
        if !owns(saved, claim) || !(saved.status == Status::Delivering
            || (saved.status == Status::Pending && matches!(result, Outcome::Failed { .. }))) { return Ok(false); }
        match result {
            Outcome::Published => { saved.status = Status::Delivered; saved.delivered_at_ms = Some(now); }
            Outcome::Failed { error } => { saved.status = Status::Failed; saved.error = Some(error); }
        }
        saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
    pub(super) async fn expire_publication(&self, run: &str, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !active(&inner, run) { return Ok(false); }
        let Some(saved) = inner.publications.get_mut(run) else { return Ok(false); };
        if !matches!(saved.status, Status::Pending | Status::Delivering) || saved.payload.deadline_ms > now { return Ok(false); }
        saved.status = Status::Failed; saved.error = Some(EXPIRED.into());
        saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
    pub(super) async fn release_publication(&self, claim: &Claim) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.publications.get_mut(&claim.payload.command.run_id) else { return Ok(false); };
        if !owns(saved, claim) { return Ok(false); }
        saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
}

const ACTIVE: &str = "EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_collaboration_delivery_checkpoints.env AND r.run_id = bcs_collaboration_delivery_checkpoints.aggregate_id AND r.status = 'running' AND r.record_status = 'active')";
fn db_error(error: DbError) -> ServiceError { invalid(&format!("Chat result store: {error}")) }

impl MySqlCollaborationStore {
    pub(super) async fn save_publication(&self, p: Payload) -> ServiceResult<bool> {
        validate(&p)?;
        let c = &p.command;
        let suffix = match self.flavor { DbSqlFlavor::Mysql => "ON DUPLICATE KEY UPDATE operation_key = operation_key",
            DbSqlFlavor::Sqlite => "ON CONFLICT(env, operation_key) DO NOTHING" };
        self.db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, deadline_ms) \
             SELECT r.env, ?, 'state_machine_run', r.run_id, 'chat_result', ?, 'pending', ?, ? FROM bcs_state_machine_runs r \
             WHERE r.env = ? AND r.run_id = ? AND r.group_id = ? AND r.session_id = ? AND r.created_by = ? \
             AND r.status = 'running' AND r.record_status = 'active' AND NOT EXISTS (SELECT 1 FROM bcs_state_machine_node_runs n \
             WHERE n.env = r.env AND n.run_id = r.run_id AND n.record_status = 'active' AND n.status NOT IN ('completed', 'skipped')) {suffix}"),
            vec![DbValue::from(key(&c.run_id)), DbValue::from(serde_json::to_string(&p).map_err(|e| invalid(&e.to_string()))?),
                DbValue::from(c.created_at_ms), DbValue::from(p.deadline_ms), DbValue::from(self.env.as_str()), DbValue::from(c.run_id.as_str()),
                DbValue::from(c.group_id.as_str()), DbValue::from(c.session_id.as_str()), DbValue::from(c.sender_bot_id.as_str())])).await.map_err(db_error)?;
        match self.get_publication(&c.run_id).await? { Some(saved) => same(&saved.payload, &p), None => Ok(false) }
    }
    pub(super) async fn get_publication(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, deadline_ms, lease_owner, lease_token, lease_until_ms, last_error, delivered_at_ms FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))])).await.map_err(db_error)?;
        rows.first().map(|row| {
            let string = |name: &str| db_get_column::<String>(row, name).map_err(db_error);
            let payload: Payload = serde_json::from_str(&string("payload_json")?).map_err(|e| invalid(&format!("invalid Chat result payload: {e}")))?;
            validate(&payload)?;
            let status: Status = serde_json::from_value(serde_json::Value::String(string("status")?)).map_err(|_| invalid("invalid Chat result status"))?;
            let lease_owner = db_get_column_opt::<String>(row, "lease_owner").map_err(db_error)?;
            let lease_until_ms = db_get_column_opt::<u64>(row, "lease_until_ms").map_err(db_error)?;
            let lease_token = db_get_column::<i64>(row, "lease_token").map_err(db_error)?;
            let error = db_get_column_opt::<String>(row, "last_error").map_err(db_error)?;
            let delivered_at_ms = db_get_column_opt::<u64>(row, "delivered_at_ms").map_err(db_error)?;
            if payload.command.run_id != run || string("aggregate_id")? != run || string("aggregate_kind")? != "state_machine_run"
                || string("operation_kind")? != "chat_result" || db_get_column::<u64>(row, "created_at_ms").map_err(db_error)? != payload.command.created_at_ms
                || db_get_column::<u64>(row, "deadline_ms").map_err(db_error)? != payload.deadline_ms || lease_token < 0
                || lease_owner.is_some() != lease_until_ms.is_some() || lease_until_ms.is_some_and(|until| until > payload.deadline_ms)
                || (status == Status::Failed) != error.is_some() || (status == Status::Delivered) != delivered_at_ms.is_some() {
                return Err(invalid("invalid Chat result checkpoint state"));
            }
            Ok(Checkpoint { payload, status, lease_owner, lease_token, lease_until_ms, error, delivered_at_ms })
        }).transpose()
    }
    pub(super) async fn claim_publication(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        validate_lease(&owner, now, until)?;
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = ?, lease_token = lease_token + 1, lease_until_ms = ? \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'chat_result' AND status = 'pending' AND deadline_ms > ? AND deadline_ms >= ? \
             AND (lease_until_ms IS NULL OR lease_until_ms <= ?) AND {ACTIVE}"),
            vec![DbValue::from(owner.as_str()), DbValue::from(until), DbValue::from(self.env.as_str()), DbValue::from(key(run)), DbValue::from(now), DbValue::from(until), DbValue::from(now)])).await.map_err(db_error)?;
        if changed.affected_rows == 0 { return Ok(None); }
        let Some(saved) = self.get_publication(run).await? else { return Ok(None); };
        if saved.lease_owner.as_ref() != Some(&owner) || saved.lease_until_ms != Some(until) || saved.status != Status::Pending { return Ok(None); }
        Ok(Some(Claim { payload: saved.payload, owner, token: saved.lease_token, lease_until_ms: until }))
    }
    fn publication_fence(&self, claim: &Claim) -> Vec<DbValue> {
        vec![DbValue::from(self.env.as_str()), DbValue::from(key(&claim.payload.command.run_id)), DbValue::from(claim.owner.as_str()), DbValue::from(claim.token), DbValue::from(claim.lease_until_ms)]
    }
    pub(super) async fn begin_publication(&self, claim: &Claim, now: u64) -> ServiceResult<bool> {
        let mut params = self.publication_fence(claim); params.extend([DbValue::from(now), DbValue::from(now)]);
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'delivering' WHERE env = ? AND operation_key = ? \
             AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? AND deadline_ms > ? \
             AND status = 'pending' AND operation_kind = 'chat_result' AND {ACTIVE}"), params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn finish_publication(&self, claim: &Claim, result: Outcome, now: u64) -> ServiceResult<bool> {
        let (status, error, allowed) = match &result {
            Outcome::Published => ("delivered", None, "status = 'delivering'"),
            Outcome::Failed { error } => ("failed", Some(error.as_str()), "status IN ('pending', 'delivering')"),
        };
        let mut params = vec![DbValue::from(status), DbValue::from(error), optional_u64_value(if status == "delivered" { Some(now) } else { None })];
        params.extend(self.publication_fence(claim)); params.push(DbValue::from(now));
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = ?, last_error = ?, delivered_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? \
             AND {allowed} AND operation_kind = 'chat_result' AND {ACTIVE}"), params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn expire_publication(&self, run: &str, now: u64) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'failed', last_error = ?, lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'chat_result' AND status IN ('pending', 'delivering') AND deadline_ms <= ? AND {ACTIVE}"),
            vec![DbValue::from(EXPIRED), DbValue::from(self.env.as_str()), DbValue::from(key(run)), DbValue::from(now)])).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn release_publication(&self, claim: &Claim) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = NULL, lease_until_ms = NULL WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND operation_kind = 'chat_result'",
            self.publication_fence(claim))).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
}

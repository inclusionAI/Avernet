//! Immutable Bot requests and short dispatch leases. Delivering is never resent.
use super::*;
use bcs_service_api::{StateMachineDispatchPayload, StateMachineDispatchCheckpoint, StateMachineDispatchClaim,
    StateMachineDispatchStatus as Status, StateMachineDispatchResult};

fn key(run: &str, node: &str, attempt: i32) -> String { format!("smnode:{run}:{node}:{attempt}:dispatch") }
fn payload_key(p: &StateMachineDispatchPayload) -> String { key(&p.run_id, &p.node_id, p.attempt) }
fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
fn validate(p: &StateMachineDispatchPayload) -> ServiceResult<()> {
    if p.run_id.is_empty() || p.run_id.len() > 128 || p.node_id.is_empty() || p.node_id.len() > 128
        || p.attempt < 0 || p.assignee_bot_id.trim().is_empty() || p.group_id.is_empty() || p.session_id.is_empty()
        || p.delivery_request_id != format!("smnode-{}-{}-{}", p.run_id, p.node_id, p.attempt)
        || p.deadline_ms <= p.started_at_ms || p.deadline_ms > i64::MAX as u64 || !p.request.is_object() {
        return Err(invalid("invalid dispatch checkpoint payload"));
    }
    Ok(())
}
fn same(saved: &StateMachineDispatchPayload, requested: &StateMachineDispatchPayload) -> ServiceResult<bool> {
    if saved != requested { return Err(ServiceError::Conflict("Node dispatch payload is immutable".into())); }
    Ok(true)
}
fn active(inner: &StoreInner, p: &StateMachineDispatchPayload) -> bool {
    inner.runs.get(&p.run_id).is_some_and(|r| r.status == StateMachineRunStatus::Running
        && r.group_id == p.group_id && r.session_id == p.session_id)
        && inner.nodes.get(&(p.run_id.clone(), p.node_id.clone())).is_some_and(|n|
            n.status == StateMachineNodeStatus::Running && n.attempt == p.attempt && n.artifact_text.is_none()
            && n.started_at == Some(p.started_at_ms) && n.delivery_request_id.as_ref() == Some(&p.delivery_request_id)
            && n.assignee_bot_id.as_ref() == Some(&p.assignee_bot_id))
}
fn valid_lease(owner: &str, now: u64, until: u64) -> ServiceResult<()> {
    if owner.is_empty() || owner.len() > 64 || until <= now || until > i64::MAX as u64 {
        return Err(invalid("invalid dispatch lease"));
    }
    Ok(())
}
fn owns(saved: &StateMachineDispatchCheckpoint, claim: &StateMachineDispatchClaim) -> bool {
    saved.payload == claim.payload && saved.lease_owner.as_ref() == Some(&claim.owner)
        && saved.lease_token == claim.token && saved.lease_until_ms == Some(claim.lease_until_ms)
}

impl MemoryCollaborationStore {
    pub(super) async fn save_dispatch(&self, payload: StateMachineDispatchPayload) -> ServiceResult<bool> {
        validate(&payload)?;
        let mut inner = self.inner.write().await;
        let key = payload_key(&payload);
        if let Some(saved) = inner.dispatches.get(&key) { return same(&saved.payload, &payload); }
        if !active(&inner, &payload) { return Ok(false); }
        if inner.nodes.get(&(payload.run_id.clone(), payload.node_id.clone())).and_then(|n| n.timeout_deadline_ms)
            .is_some_and(|deadline| deadline != payload.deadline_ms) { return Ok(false); }
        inner.dispatches.insert(key, StateMachineDispatchCheckpoint { payload, status: Status::Pending,
            lease_owner: None, lease_token: 0, lease_until_ms: None, error: None, delivered_at_ms: None });
        Ok(true)
    }
    pub(super) async fn get_dispatch(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineDispatchCheckpoint>> {
        Ok(self.inner.read().await.dispatches.get(&key(run, node, attempt)).cloned())
    }
    pub(super) async fn claim_dispatch(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineDispatchClaim>> {
        valid_lease(&owner, now, until)?;
        let mut inner = self.inner.write().await;
        let key = key(run, node, attempt);
        let Some(saved) = inner.dispatches.get(&key) else { return Ok(None); };
        if saved.status != Status::Pending || saved.payload.deadline_ms <= now || !active(&inner, &saved.payload)
            || saved.lease_until_ms.is_some_and(|expiry| expiry > now) { return Ok(None); }
        let saved = inner.dispatches.get_mut(&key).expect("checked dispatch");
        saved.lease_token = saved.lease_token.checked_add(1).ok_or_else(|| invalid("dispatch token overflow"))?;
        saved.lease_owner = Some(owner.clone()); saved.lease_until_ms = Some(until);
        Ok(Some(StateMachineDispatchClaim { payload: saved.payload.clone(), owner, token: saved.lease_token, lease_until_ms: until }))
    }
    pub(super) async fn begin_dispatch_send(&self, claim: &StateMachineDispatchClaim, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !active(&inner, &claim.payload) || claim.payload.deadline_ms <= now || claim.lease_until_ms <= now { return Ok(false); }
        let Some(saved) = inner.dispatches.get_mut(&payload_key(&claim.payload)) else { return Ok(false); };
        if saved.status != Status::Pending || !owns(saved, claim) { return Ok(false); }
        saved.status = Status::Delivering;
        Ok(true)
    }
    pub(super) async fn finish_dispatch(&self, claim: &StateMachineDispatchClaim, result: StateMachineDispatchResult, now: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !active(&inner, &claim.payload) || claim.lease_until_ms <= now { return Ok(false); }
        let Some(saved) = inner.dispatches.get_mut(&payload_key(&claim.payload)) else { return Ok(false); };
        if !owns(saved, claim) || !(saved.status == Status::Delivering
            || (saved.status == Status::Pending && matches!(result, StateMachineDispatchResult::Rejected { .. }))) { return Ok(false); }
        match result {
            StateMachineDispatchResult::Accepted => { saved.status = Status::Delivered; saved.delivered_at_ms = Some(now); }
            StateMachineDispatchResult::Rejected { error } => { saved.status = Status::Failed; saved.error = Some(error); }
        }
        saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
    pub(super) async fn release_dispatch(&self, claim: &StateMachineDispatchClaim) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.dispatches.get_mut(&payload_key(&claim.payload)) else { return Ok(false); };
        if !owns(saved, claim) { return Ok(false); }
        saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
    pub(super) async fn supersede_dispatches(&self, run: &str) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let stale = inner.dispatches.iter().filter(|(_, saved)| saved.payload.run_id == run
            && matches!(saved.status, Status::Pending | Status::Delivering) && !active(&inner, &saved.payload))
            .map(|(key, _)| key.clone()).collect::<Vec<_>>();
        for key in stale {
            let saved = inner.dispatches.get_mut(&key).expect("checked dispatch");
            saved.status = Status::Superseded; saved.lease_owner = None; saved.lease_until_ms = None;
        }
        Ok(())
    }
}

const ACTIVE: &str = "EXISTS (SELECT 1 FROM bcs_state_machine_node_runs n JOIN bcs_state_machine_runs r ON r.env = n.env AND r.run_id = n.run_id WHERE n.env = bcs_collaboration_delivery_checkpoints.env AND n.run_id = bcs_collaboration_delivery_checkpoints.aggregate_id AND n.node_id = bcs_collaboration_delivery_checkpoints.node_id AND n.attempt = bcs_collaboration_delivery_checkpoints.aggregate_attempt AND n.status = 'running' AND n.artifact_text IS NULL AND n.record_status = 'active' AND r.status = 'running' AND r.record_status = 'active')";
fn db_error(error: DbError) -> ServiceError { invalid(&format!("dispatch store: {error}")) }

impl MySqlCollaborationStore {
    pub(super) async fn save_dispatch(&self, p: StateMachineDispatchPayload) -> ServiceResult<bool> {
        validate(&p)?;
        let suffix = match self.flavor { DbSqlFlavor::Mysql => "ON DUPLICATE KEY UPDATE operation_key = operation_key",
            DbSqlFlavor::Sqlite => "ON CONFLICT(env, operation_key) DO NOTHING" };
        let json = serde_json::to_string(&p).map_err(|error| invalid(&error.to_string()))?;
        self.db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, node_id, aggregate_attempt, payload_json, status, created_at_ms, deadline_ms) \
             SELECT n.env, ?, 'state_machine_node', n.run_id, 'bot_dispatch', n.node_id, n.attempt, ?, 'pending', n.started_at_ms, ? \
             FROM bcs_state_machine_node_runs n JOIN bcs_state_machine_runs r ON r.env = n.env AND r.run_id = n.run_id \
             WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? AND n.attempt = ? AND n.delivery_request_id = ? AND n.assignee_bot_id = ? \
             AND n.started_at_ms = ? AND (n.timeout_deadline_ms IS NULL OR n.timeout_deadline_ms = ?) \
             AND n.status = 'running' AND n.artifact_text IS NULL AND n.record_status = 'active' AND r.status = 'running' AND r.record_status = 'active' \
             AND r.group_id = ? AND r.session_id = ? {suffix}"),
            vec![DbValue::from(payload_key(&p)), DbValue::from(json), DbValue::from(p.deadline_ms), DbValue::from(self.env.as_str()),
                DbValue::from(p.run_id.as_str()), DbValue::from(p.node_id.as_str()), DbValue::from(p.attempt), DbValue::from(p.delivery_request_id.as_str()),
                DbValue::from(p.assignee_bot_id.as_str()), DbValue::from(p.started_at_ms), DbValue::from(p.deadline_ms),
                DbValue::from(p.group_id.as_str()), DbValue::from(p.session_id.as_str())])).await.map_err(db_error)?;
        match self.get_dispatch(&p.run_id, &p.node_id, p.attempt).await? { Some(saved) => same(&saved.payload, &p), None => Ok(false) }
    }
    pub(super) async fn get_dispatch(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineDispatchCheckpoint>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT aggregate_kind, aggregate_id, operation_kind, node_id, aggregate_attempt, payload_json, status, created_at_ms, deadline_ms, lease_owner, lease_token, lease_until_ms, last_error, delivered_at_ms FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run, node, attempt))])).await.map_err(db_error)?;
        rows.first().map(|row| {
            let string = |name: &str| db_get_column::<String>(row, name).map_err(db_error);
            let payload: StateMachineDispatchPayload = serde_json::from_str(&string("payload_json")?).map_err(|error| invalid(&format!("invalid dispatch payload: {error}")))?;
            validate(&payload)?;
            let status: Status = serde_json::from_value(serde_json::Value::String(string("status")?)).map_err(|_| invalid("invalid dispatch status"))?;
            let lease_owner = db_get_column_opt::<String>(row, "lease_owner").map_err(db_error)?;
            let lease_until_ms = db_get_column_opt::<u64>(row, "lease_until_ms").map_err(db_error)?;
            let lease_token = db_get_column::<i64>(row, "lease_token").map_err(db_error)?;
            let error = db_get_column_opt::<String>(row, "last_error").map_err(db_error)?;
            let delivered_at_ms = db_get_column_opt::<u64>(row, "delivered_at_ms").map_err(db_error)?;
            if payload.run_id != run || payload.node_id != node || payload.attempt != attempt || string("aggregate_id")? != run
                || string("node_id")? != node || db_get_column::<i32>(row, "aggregate_attempt").map_err(db_error)? != attempt
                || string("aggregate_kind")? != "state_machine_node" || string("operation_kind")? != "bot_dispatch"
                || db_get_column::<u64>(row, "created_at_ms").map_err(db_error)? != payload.started_at_ms
                || db_get_column::<u64>(row, "deadline_ms").map_err(db_error)? != payload.deadline_ms
                || lease_token < 0 || lease_owner.is_some() != lease_until_ms.is_some()
                || (status == Status::Failed) != error.is_some() || (status == Status::Delivered) != delivered_at_ms.is_some() {
                return Err(invalid("invalid dispatch checkpoint state"));
            }
            Ok(StateMachineDispatchCheckpoint { payload, status, lease_owner, lease_token, lease_until_ms, error, delivered_at_ms })
        }).transpose()
    }
    pub(super) async fn claim_dispatch(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineDispatchClaim>> {
        valid_lease(&owner, now, until)?;
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = ?, lease_token = lease_token + 1, lease_until_ms = ? \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'bot_dispatch' AND status = 'pending' AND deadline_ms > ? \
             AND (lease_until_ms IS NULL OR lease_until_ms <= ?) AND {ACTIVE}"),
            vec![DbValue::from(owner.as_str()), DbValue::from(until), DbValue::from(self.env.as_str()), DbValue::from(key(run, node, attempt)), DbValue::from(now), DbValue::from(now)])).await.map_err(db_error)?;
        if changed.affected_rows == 0 { return Ok(None); }
        let Some(saved) = self.get_dispatch(run, node, attempt).await? else { return Ok(None); };
        if saved.lease_owner.as_ref() != Some(&owner) || saved.lease_until_ms != Some(until) || saved.status != Status::Pending { return Ok(None); }
        Ok(Some(StateMachineDispatchClaim { payload: saved.payload, owner, token: saved.lease_token, lease_until_ms: until }))
    }
    fn fence_params(&self, claim: &StateMachineDispatchClaim) -> Vec<DbValue> {
        vec![DbValue::from(self.env.as_str()), DbValue::from(payload_key(&claim.payload)), DbValue::from(claim.owner.as_str()),
            DbValue::from(claim.token), DbValue::from(claim.lease_until_ms)]
    }
    pub(super) async fn begin_dispatch_send(&self, claim: &StateMachineDispatchClaim, now: u64) -> ServiceResult<bool> {
        let mut params = self.fence_params(claim); params.extend([DbValue::from(now), DbValue::from(now)]);
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'delivering' WHERE env = ? AND operation_key = ? \
             AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? AND deadline_ms > ? \
             AND status = 'pending' AND operation_kind = 'bot_dispatch' AND {ACTIVE}"), params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn finish_dispatch(&self, claim: &StateMachineDispatchClaim, result: StateMachineDispatchResult, now: u64) -> ServiceResult<bool> {
        let (status, error, allowed) = match &result {
            StateMachineDispatchResult::Accepted => ("delivered", None, "status = 'delivering'"),
            StateMachineDispatchResult::Rejected { error } => ("failed", Some(error.as_str()), "status IN ('pending', 'delivering')"),
        };
        let mut params = vec![DbValue::from(status), DbValue::from(error), optional_u64_value(if status == "delivered" { Some(now) } else { None })];
        params.extend(self.fence_params(claim)); params.push(DbValue::from(now));
        let update = DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = ?, last_error = ?, delivered_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? \
             AND {allowed} AND operation_kind = 'bot_dispatch' AND {ACTIVE}"), params);
        let mut steps = vec![DbTransactionStep::Execute(update)];
        if status == "delivered" {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_state_machine_node_runs SET runtime_phase = 'waiting_provider' WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND runtime_phase = 'dispatch_pending' AND artifact_text IS NULL AND record_status = 'active' AND EXISTS (SELECT 1 FROM bcs_collaboration_delivery_checkpoints c WHERE c.env = bcs_state_machine_node_runs.env AND c.operation_key = ? AND c.status = 'delivered' AND c.lease_token = ?)",
                vec![DbValue::from(self.env.as_str()), DbValue::from(claim.payload.run_id.as_str()), DbValue::from(claim.payload.node_id.as_str()), DbValue::from(claim.payload.attempt), DbValue::from(payload_key(&claim.payload)), DbValue::from(claim.token)])));
        }
        let results = self.db.transaction(steps).await.map_err(db_error)?;
        Ok(matches!(results.first(), Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0))
    }
    pub(super) async fn release_dispatch(&self, claim: &StateMachineDispatchClaim) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = NULL, lease_until_ms = NULL WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND operation_kind = 'bot_dispatch'",
            self.fence_params(claim))).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn supersede_dispatches(&self, run: &str) -> ServiceResult<()> {
        self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'superseded', lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND aggregate_id = ? AND operation_kind = 'bot_dispatch' AND status IN ('pending', 'delivering') AND NOT {ACTIVE}"),
            vec![DbValue::from(self.env.as_str()), DbValue::from(run)])).await.map_err(db_error)?;
        Ok(())
    }
}

impl MemoryCollaborationStore {
    pub(super) async fn expire_dispatch(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let operation = key(&command.run_id, &command.node_id, command.attempt);
        let Some(saved) = inner.dispatches.get(&operation) else { return Ok(false); };
        if !matches!(saved.status, Status::Pending | Status::Delivering) || saved.payload.deadline_ms > command.completed_at_ms
            || !active(&inner, &saved.payload) { return Ok(false); }
        let node = node_mut(&mut inner, &command.run_id, &command.node_id)?;
        if command.action == StateMachineFailureAction::Retry && !retry_is_within_limit(node.attempt, node.max_attempts) { return Ok(false); }
        node.status = StateMachineNodeStatus::Failed; node.error = Some(command.error);
        node.completed_at = Some(command.completed_at_ms); node.timeout_deadline_ms = None;
        inner.node_failure_actions.insert((command.run_id, command.node_id), command.action);
        let saved = inner.dispatches.get_mut(&operation).expect("checked dispatch");
        saved.status = Status::Superseded; saved.lease_owner = None; saved.lease_until_ms = None;
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn expire_dispatch(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        let action = failure_action_name(command.action);
        let operation = key(&command.run_id, &command.node_id, command.attempt);
        let lock = if self.flavor == DbSqlFlavor::Mysql { " FOR UPDATE" } else { "" };
        let results = self.db.transaction(vec![
            // Use the same checkpoint-before-Node lock order as dispatch ACKs.
            // A missing row is a normal lost race; the conditional writes do nothing.
            DbTransactionStep::Query(DbStatement::with_params(format!(
                "SELECT operation_key FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?{lock}"),
                vec![DbValue::from(self.env.as_str()), DbValue::from(operation.as_str())])),
            DbTransactionStep::Execute(DbStatement::with_params(format!(
                "UPDATE bcs_state_machine_node_runs SET status = 'failed', error_message = ?, completed_at_ms = ?, failure_action = ?, timeout_deadline_ms = NULL, runtime_phase = NULL, {} \
                 WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND artifact_text IS NULL AND record_status = 'active' \
                 AND (? = 'fail_run' OR (attempt >= 0 AND attempt < CASE WHEN max_attempts > 0 THEN max_attempts ELSE 1 END - 1)) \
                 AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id AND r.status = 'running' AND r.record_status = 'active') \
                 AND EXISTS (SELECT 1 FROM bcs_collaboration_delivery_checkpoints c WHERE c.env = bcs_state_machine_node_runs.env AND c.operation_key = ? AND c.operation_kind = 'bot_dispatch' AND c.status IN ('pending', 'delivering') AND c.deadline_ms <= ?)", self.flavor.set_modified_now()),
                vec![DbValue::from(command.error.as_str()), DbValue::from(command.completed_at_ms), DbValue::from(action), DbValue::from(self.env.as_str()),
                    DbValue::from(command.run_id.as_str()), DbValue::from(command.node_id.as_str()), DbValue::from(command.attempt), DbValue::from(action),
                    DbValue::from(operation.as_str()), DbValue::from(command.completed_at_ms)])),
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'superseded', lease_owner = NULL, lease_until_ms = NULL \
                 WHERE env = ? AND operation_key = ? AND operation_kind = 'bot_dispatch' AND status IN ('pending', 'delivering') \
                 AND EXISTS (SELECT 1 FROM bcs_state_machine_node_runs n WHERE n.env = bcs_collaboration_delivery_checkpoints.env AND n.run_id = bcs_collaboration_delivery_checkpoints.aggregate_id \
                 AND n.node_id = bcs_collaboration_delivery_checkpoints.node_id AND n.attempt = bcs_collaboration_delivery_checkpoints.aggregate_attempt AND n.status = 'failed' AND n.completed_at_ms = ? AND n.failure_action = ?)",
                vec![DbValue::from(self.env.as_str()), DbValue::from(operation), DbValue::from(command.completed_at_ms), DbValue::from(action)])),
        ]).await.map_err(db_error)?;
        Ok(matches!(results.get(1), Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0))
    }
}

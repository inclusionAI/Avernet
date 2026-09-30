//! Local opening-history checkpoint; no external-delivery lease or retry worker.
use super::*;
use bcs_service_api::{StateMachineOpeningCheckpoint, StateMachineOpeningPayload};

fn key(run: &str) -> String { format!("smrun:{run}:opening") }
fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
fn validate(payload: &StateMachineOpeningPayload) -> ServiceResult<()> {
    if payload.run_id.is_empty() || payload.client_msg_id != format!("{}:000-panel", payload.run_id) {
        return Err(invalid("invalid opening checkpoint identity"));
    }
    Ok(())
}
fn same(existing: &StateMachineOpeningPayload, requested: &StateMachineOpeningPayload) -> ServiceResult<bool> {
    if existing != requested { return Err(ServiceError::Conflict("Run opening is immutable".into())); }
    Ok(true)
}

impl MemoryCollaborationStore {
    pub(super) async fn save_opening(&self, payload: StateMachineOpeningPayload) -> ServiceResult<bool> {
        validate(&payload)?;
        let mut inner = self.inner.write().await;
        if let Some(saved) = inner.openings.get(&payload.run_id) { return same(&saved.payload, &payload); }
        let Some(run) = inner.runs.get(&payload.run_id) else { return Ok(false); };
        if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running)
            || run.group_id != payload.group_id || run.session_id != payload.session_id || run.created_at != payload.created_at_ms {
            return Ok(false);
        }
        inner.openings.insert(payload.run_id.clone(), StateMachineOpeningCheckpoint { payload, delivered_at_ms: None });
        Ok(true)
    }
    pub(super) async fn get_opening(&self, run: &str) -> ServiceResult<Option<StateMachineOpeningCheckpoint>> {
        Ok(self.inner.read().await.openings.get(run).cloned())
    }
    pub(super) async fn deliver_opening(&self, run: &str, at: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if inner.openings.get(run).is_some_and(|saved| saved.delivered_at_ms.is_some()) { return Ok(true); }
        if !inner.runs.get(run).is_some_and(|run| matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running)) {
            return Ok(false);
        }
        let Some(saved) = inner.openings.get_mut(run) else { return Ok(false); };
        saved.delivered_at_ms = Some(at);
        Ok(true)
    }
}

impl MySqlCollaborationStore {
    pub(super) async fn save_opening(&self, payload: StateMachineOpeningPayload) -> ServiceResult<bool> {
        validate(&payload)?;
        let suffix = match self.flavor {
            DbSqlFlavor::Mysql => "ON DUPLICATE KEY UPDATE operation_key = operation_key",
            DbSqlFlavor::Sqlite => "ON CONFLICT(env, operation_key) DO NOTHING",
        };
        self.db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_collaboration_delivery_checkpoints \
             (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms) \
             SELECT ?, ?, 'state_machine_run', run_id, 'run_opening', ?, 'pending', created_at_ms FROM bcs_state_machine_runs \
             WHERE env = ? AND run_id = ? AND group_id = ? AND session_id = ? AND created_at_ms = ? \
             AND status IN ('pending', 'running') AND record_status = 'active' {suffix}"),
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(&payload.run_id)),
                DbValue::from(serde_json::to_string(&payload).map_err(|error| invalid(&error.to_string()))?),
                DbValue::from(self.env.as_str()), DbValue::from(payload.run_id.as_str()), DbValue::from(payload.group_id.as_str()),
                DbValue::from(payload.session_id.as_str()), DbValue::from(payload.created_at_ms)],
        )).await.map_err(|error| invalid(&format!("opening checkpoint save: {error}")))?;
        match self.get_opening(&payload.run_id).await? {
            Some(saved) => same(&saved.payload, &payload), None => Ok(false),
        }
    }
    pub(super) async fn get_opening(&self, run: &str) -> ServiceResult<Option<StateMachineOpeningCheckpoint>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, delivered_at_ms \
             FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))],
        )).await.map_err(|error| invalid(&format!("opening checkpoint read: {error}")))?;
        rows.first().map(|row| {
            let read = |name: &str| db_get_column::<String>(row, name).map_err(|error| invalid(&error.to_string()));
            let payload: StateMachineOpeningPayload = serde_json::from_str(&read("payload_json")?)
                .map_err(|error| invalid(&format!("invalid opening payload: {error}")))?;
            validate(&payload)?;
            let created: u64 = db_get_column(row, "created_at_ms").map_err(|error| invalid(&error.to_string()))?;
            let delivered: Option<u64> = db_get_column_opt(row, "delivered_at_ms").map_err(|error| invalid(&error.to_string()))?;
            let valid_status = match read("status")?.as_str() { "pending" => delivered.is_none(), "delivered" => delivered.is_some(), _ => false };
            if payload.run_id != run || read("aggregate_id")? != run || read("aggregate_kind")? != "state_machine_run"
                || read("operation_kind")? != "run_opening" || created != payload.created_at_ms || !valid_status {
                return Err(invalid("invalid opening checkpoint state"));
            }
            Ok(StateMachineOpeningCheckpoint { payload, delivered_at_ms: delivered })
        }).transpose()
    }
    pub(super) async fn deliver_opening(&self, run: &str, at: u64) -> ServiceResult<bool> {
        self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'delivered', delivered_at_ms = ? \
             WHERE env = ? AND operation_key = ? AND aggregate_kind = 'state_machine_run' AND aggregate_id = ? \
             AND operation_kind = 'run_opening' AND status = 'pending' AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r \
             WHERE r.env = bcs_collaboration_delivery_checkpoints.env AND r.run_id = bcs_collaboration_delivery_checkpoints.aggregate_id \
             AND r.status IN ('pending', 'running') AND r.record_status = 'active')",
            vec![DbValue::from(at), DbValue::from(self.env.as_str()), DbValue::from(key(run)), DbValue::from(run)],
        )).await.map_err(|error| invalid(&format!("opening checkpoint completion: {error}")))?;
        Ok(self.get_opening(run).await?.is_some_and(|saved| saved.delivered_at_ms.is_some()))
    }
}

//! One immutable terminal notification plan plus per-recipient progress.
use super::*;
use bcs_service_api::{StateMachineTerminalImPayload as Payload, StateMachineTerminalImProgress as Progress,
    StateMachineTerminalImDelivery as Delivery, StateMachineTerminalImStatus as Status,
    StateMachineTerminalImCheckpoint as Checkpoint, StateMachineTerminalImClaim as Claim, StateMachineTerminalStatus};

fn key(run: &str) -> String { format!("smrun:{run}:im-terminal") }
fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
fn validate(p: &Payload) -> ServiceResult<()> {
    if p.event.run_id.is_empty() || p.event.run_id.len() > 128 || p.event.group_id.is_empty() || p.event.session_id.is_empty()
        || p.session_activation_count < 1 || p.deadline_ms <= p.created_at_ms || p.deadline_ms > i64::MAX as u64
        || p.notifications.iter().any(|n| n.binding_id.is_empty() || n.account_ref.is_empty() || n.im_conversation_id.is_empty()
            || n.im_conversation_type.is_empty() || n.request_id.is_empty() || n.node_id.is_empty() || n.text.is_empty()) {
        return Err(invalid("invalid terminal IM payload"));
    }
    Ok(())
}
fn initial(p: &Payload) -> Progress { Progress { cleanup_completed: false,
    deliveries: p.notifications.iter().map(|_| Delivery::Pending { next_attempt_at_ms: p.created_at_ms, last_error: None }).collect() } }
fn status(progress: &Progress) -> Status {
    if !progress.cleanup_completed || progress.deliveries.iter().any(|d| matches!(d, Delivery::Pending { .. } | Delivery::Sending)) { Status::Pending }
    else if progress.deliveries.iter().any(|d| matches!(d, Delivery::Failed { .. })) { Status::Failed } else { Status::Delivered }
}
fn valid_progress(p: &Payload, progress: &Progress) -> bool {
    progress.deliveries.len() == p.notifications.len() && progress.deliveries.iter().all(|d| match d {
        Delivery::Pending { next_attempt_at_ms, .. } => *next_attempt_at_ms <= p.deadline_ms,
        Delivery::Failed { error } => !error.is_empty(), _ => true,
    })
}
fn valid_change(p: &Payload, old: &Progress, next: &Progress) -> ServiceResult<()> {
    if !valid_progress(p, old) || !valid_progress(p, next) || (old.cleanup_completed && !next.cleanup_completed)
        || old.deliveries.iter().zip(&next.deliveries).any(|(a, b)| match (a, b) {
            (Delivery::Pending { next_attempt_at_ms: x, .. }, Delivery::Pending { next_attempt_at_ms: y, .. }) => y < x,
            (Delivery::Pending { .. }, Delivery::Sending | Delivery::Failed { .. }) => !next.cleanup_completed,
            (Delivery::Sending, Delivery::Delivered { .. } | Delivery::Failed { .. }) => false,
            _ => a != b,
        }) { return Err(invalid("invalid terminal IM progress transition")); }
    Ok(())
}
fn same(saved: &Payload, requested: &Payload) -> ServiceResult<bool> {
    if saved != requested { return Err(ServiceError::Conflict("terminal IM payload is immutable".into())); }
    Ok(true)
}
fn run_status(p: &Payload) -> StateMachineRunStatus {
    match p.event.status { StateMachineTerminalStatus::Completed => StateMachineRunStatus::Completed, StateMachineTerminalStatus::Failed => StateMachineRunStatus::Failed }
}
fn valid_run(inner: &StoreInner, p: &Payload) -> bool {
    inner.runs.get(&p.event.run_id).is_some_and(|r| r.status == run_status(p) && r.group_id == p.event.group_id
        && r.session_id == p.event.session_id && r.session_activation_count == Some(p.session_activation_count)
        && r.completed_at == Some(p.created_at_ms) && r.output == p.event.output)
}
fn lease(owner: &str, now: u64, until: u64) -> ServiceResult<()> {
    if owner.is_empty() || owner.len() > 64 || until <= now || until > i64::MAX as u64 { return Err(invalid("invalid terminal IM lease")); }
    Ok(())
}
fn owns(saved: &Checkpoint, c: &Claim) -> bool {
    saved.lease_owner.as_ref() == Some(&c.owner) && saved.lease_token == c.token && saved.lease_until_ms == Some(c.lease_until_ms)
        && saved.payload == c.checkpoint.payload
}

impl MemoryCollaborationStore {
    async fn terminal_im_session(&self, p: &Payload, completed: bool) -> ServiceResult<bool> {
        let repo = self.session_repo.as_ref().ok_or_else(|| invalid("terminal IM requires Session repository"))?;
        Ok(repo.get(&p.event.session_id).await.is_some_and(|s| s.group_id == p.event.group_id
            && s.session_kind == bcs_domain::SessionKind::ServiceInvocation && s.activation_count == p.session_activation_count
            && (s.status == bcs_domain::SessionStatus::Completed || (!completed && s.status == bcs_domain::SessionStatus::Running))))
    }
    pub(super) async fn save_terminal_notification(&self, p: Payload) -> ServiceResult<bool> {
        validate(&p)?;
        let mut inner = self.inner.write().await;
        if let Some(saved) = inner.terminal_notifications.get(&p.event.run_id) { return same(&saved.payload, &p); }
        if !valid_run(&inner, &p) || !self.terminal_im_session(&p, false).await? { return Ok(false); }
        inner.terminal_notifications.insert(p.event.run_id.clone(), Checkpoint { progress: initial(&p), payload: p,
            status: Status::Pending, lease_owner: None, lease_token: 0, lease_until_ms: None });
        Ok(true)
    }
    pub(super) async fn get_terminal_notification(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        Ok(self.inner.read().await.terminal_notifications.get(run).cloned())
    }
    pub(super) async fn list_terminal_notifications(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        Ok(self.inner.read().await.terminal_notifications.iter().filter(|(id, c)| c.status == Status::Pending
            && after.is_none_or(|after| id.as_str() > after)).take(limit).map(|(id, _)| id.clone()).collect())
    }
    pub(super) async fn claim_terminal_notification(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        lease(&owner, now, until)?;
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.terminal_notifications.get(run) else { return Ok(None); };
        if saved.status != Status::Pending || saved.lease_until_ms.is_some_and(|expiry| expiry > now)
            || !valid_run(&inner, &saved.payload) || !self.terminal_im_session(&saved.payload, true).await? { return Ok(None); }
        let saved = inner.terminal_notifications.get_mut(run).expect("checked intent");
        saved.lease_token = saved.lease_token.checked_add(1).ok_or_else(|| invalid("terminal IM token overflow"))?;
        saved.lease_owner = Some(owner.clone()); saved.lease_until_ms = Some(until);
        Ok(Some(Claim { checkpoint: saved.clone(), owner, token: saved.lease_token, lease_until_ms: until }))
    }
    pub(super) async fn update_terminal_notification(&self, c: &Claim, expected: Progress, next: Progress, now: u64) -> ServiceResult<bool> {
        valid_change(&c.checkpoint.payload, &expected, &next)?;
        let mut inner = self.inner.write().await;
        if c.lease_until_ms <= now || !valid_run(&inner, &c.checkpoint.payload) || !self.terminal_im_session(&c.checkpoint.payload, true).await? { return Ok(false); }
        let Some(saved) = inner.terminal_notifications.get_mut(&c.checkpoint.payload.event.run_id) else { return Ok(false); };
        if saved.status != Status::Pending || !owns(saved, c) || saved.progress != expected { return Ok(false); }
        saved.status = status(&next); saved.progress = next;
        if saved.status != Status::Pending { saved.lease_owner = None; saved.lease_until_ms = None; }
        Ok(true)
    }
    pub(super) async fn release_terminal_notification(&self, c: &Claim) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.terminal_notifications.get_mut(&c.checkpoint.payload.event.run_id) else { return Ok(false); };
        if !owns(saved, c) { return Ok(false); }
        saved.lease_owner = None; saved.lease_until_ms = None; Ok(true)
    }
    pub(super) async fn supersede_terminal_notification(&self, run: &str) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let Some(saved) = inner.terminal_notifications.get(run) else { return Ok(false); };
        if saved.status != Status::Pending || (valid_run(&inner, &saved.payload) && self.terminal_im_session(&saved.payload, false).await?) { return Ok(false); }
        let saved = inner.terminal_notifications.get_mut(run).expect("checked intent");
        saved.status = Status::Superseded; saved.lease_owner = None; saved.lease_until_ms = None; Ok(true)
    }
}

fn db_error(e: DbError) -> ServiceError { invalid(&format!("terminal IM store: {e}")) }
macro_rules! encoded { ($value:expr) => { serde_json::to_string($value).map_err(|e| invalid(&e.to_string())) }; }
// Run/Session identities are immutable for this intent; all sends require the
// same Completed activation. A later rerun cannot acknowledge old notifications.
fn active(completed: bool) -> String {
    format!("EXISTS (SELECT 1 FROM bcs_state_machine_runs r JOIN bcs_group_sessions s ON s.env = r.env AND s.session_id = r.session_id \
        WHERE r.env = bcs_collaboration_delivery_checkpoints.env AND r.run_id = bcs_collaboration_delivery_checkpoints.aggregate_id \
        AND r.status IN ('completed', 'failed') AND r.record_status = 'active' AND s.group_id = r.group_id \
        AND s.session_kind = 'service_invocation' AND s.activation_count = r.session_activation_count AND s.status {})",
        if completed { "= 'completed'" } else { "IN ('running', 'completed')" })
}
impl MySqlCollaborationStore {
    pub(super) async fn save_terminal_notification(&self, p: Payload) -> ServiceResult<bool> {
        validate(&p)?;
        let e = &p.event;
        let suffix = match self.flavor { DbSqlFlavor::Mysql => "ON DUPLICATE KEY UPDATE operation_key = operation_key",
            DbSqlFlavor::Sqlite => "ON CONFLICT(env, operation_key) DO NOTHING" };
        let status = match e.status { StateMachineTerminalStatus::Completed => "completed", StateMachineTerminalStatus::Failed => "failed" };
        self.db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, progress_json, status, created_at_ms, deadline_ms) \
             SELECT r.env, ?, 'state_machine_run', r.run_id, 'im_terminal', ?, ?, 'pending', ?, ? FROM bcs_state_machine_runs r \
             JOIN bcs_group_sessions s ON s.env = r.env AND s.session_id = r.session_id WHERE r.env = ? AND r.run_id = ? \
             AND r.group_id = ? AND r.session_id = ? AND r.status = ? AND r.completed_at_ms = ? AND r.session_activation_count = ? \
             AND (r.output_text = ? OR (r.output_text IS NULL AND ? IS NULL)) AND r.record_status = 'active' \
             AND s.group_id = r.group_id AND s.session_kind = 'service_invocation' AND s.activation_count = r.session_activation_count \
             AND s.status IN ('running', 'completed') {suffix}"),
            vec![DbValue::from(key(&e.run_id)), DbValue::from(encoded!(&p)?), DbValue::from(encoded!(&initial(&p))?), DbValue::from(p.created_at_ms), DbValue::from(p.deadline_ms),
                DbValue::from(self.env.as_str()), DbValue::from(e.run_id.as_str()), DbValue::from(e.group_id.as_str()), DbValue::from(e.session_id.as_str()),
                DbValue::from(status), DbValue::from(p.created_at_ms), DbValue::from(p.session_activation_count), DbValue::from(e.output.as_deref()), DbValue::from(e.output.as_deref())])).await.map_err(db_error)?;
        match self.get_terminal_notification(&e.run_id).await? { Some(saved) => same(&saved.payload, &p), None => Ok(false) }
    }
    pub(super) async fn get_terminal_notification(&self, run: &str) -> ServiceResult<Option<Checkpoint>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT aggregate_kind, aggregate_id, operation_kind, payload_json, progress_json, status, created_at_ms, deadline_ms, lease_owner, lease_token, lease_until_ms \
             FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ?",
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))])).await.map_err(db_error)?;
        rows.first().map(|row| {
            let string = |name: &str| db_get_column::<String>(row, name).map_err(db_error);
            let payload: Payload = serde_json::from_str(&string("payload_json")?).map_err(|e| invalid(&e.to_string()))?;
            let progress: Progress = serde_json::from_str(&string("progress_json")?).map_err(|e| invalid(&e.to_string()))?;
            validate(&payload)?;
            let saved_status: Status = serde_json::from_value(serde_json::Value::String(string("status")?)).map_err(|e| invalid(&e.to_string()))?;
            let lease_owner = db_get_column_opt::<String>(row, "lease_owner").map_err(db_error)?;
            let lease_token = db_get_column::<i64>(row, "lease_token").map_err(db_error)?;
            let lease_until_ms = db_get_column_opt::<u64>(row, "lease_until_ms").map_err(db_error)?;
            if payload.event.run_id != run || string("aggregate_id")? != run || string("aggregate_kind")? != "state_machine_run"
                || string("operation_kind")? != "im_terminal" || !valid_progress(&payload, &progress)
                || (saved_status != Status::Superseded && saved_status != status(&progress))
                || db_get_column::<u64>(row, "created_at_ms").map_err(db_error)? != payload.created_at_ms
                || db_get_column::<u64>(row, "deadline_ms").map_err(db_error)? != payload.deadline_ms
                || lease_token < 0 || lease_owner.is_some() != lease_until_ms.is_some() {
                return Err(invalid("invalid terminal IM checkpoint state"));
            }
            Ok(Checkpoint { payload, progress, status: saved_status, lease_owner, lease_token, lease_until_ms })
        }).transpose()
    }
    pub(super) async fn list_terminal_notifications(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        if limit == 0 { return Ok(Vec::new()); }
        let mut params = vec![DbValue::from(self.env.as_str())];
        let cursor = if let Some(after) = after { params.push(DbValue::from(after)); " AND aggregate_id > ?" } else { "" };
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT aggregate_id FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_kind = 'im_terminal' \
             AND status = 'pending'{cursor} ORDER BY aggregate_id LIMIT {}", limit.min(i64::MAX as usize)), params)).await.map_err(db_error)?;
        rows.iter().map(|row| db_get_column(row, "aggregate_id").map_err(db_error)).collect()
    }
    pub(super) async fn claim_terminal_notification(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<Claim>> {
        lease(&owner, now, until)?;
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = ?, lease_token = lease_token + 1, lease_until_ms = ? \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'im_terminal' AND status = 'pending' \
             AND (lease_until_ms IS NULL OR lease_until_ms <= ?) AND {}", active(true)),
            vec![DbValue::from(owner.as_str()), DbValue::from(until), DbValue::from(self.env.as_str()), DbValue::from(key(run)), DbValue::from(now)])).await.map_err(db_error)?;
        if changed.affected_rows == 0 { return Ok(None); }
        let Some(saved) = self.get_terminal_notification(run).await? else { return Ok(None); };
        if saved.status != Status::Pending || saved.lease_owner.as_ref() != Some(&owner) || saved.lease_until_ms != Some(until) { return Ok(None); }
        Ok(Some(Claim { token: saved.lease_token, checkpoint: saved, owner, lease_until_ms: until }))
    }
    fn terminal_im_fence(&self, c: &Claim) -> Vec<DbValue> {
        vec![DbValue::from(self.env.as_str()), DbValue::from(key(&c.checkpoint.payload.event.run_id)), DbValue::from(c.owner.as_str()), DbValue::from(c.token), DbValue::from(c.lease_until_ms)]
    }
    pub(super) async fn update_terminal_notification(&self, c: &Claim, expected: Progress, next: Progress, now: u64) -> ServiceResult<bool> {
        valid_change(&c.checkpoint.payload, &expected, &next)?;
        let terminal = status(&next) != Status::Pending;
        let status = match status(&next) { Status::Pending => "pending", Status::Delivered => "delivered", _ => "failed" };
        let progress_compare = match self.flavor { DbSqlFlavor::Mysql => "progress_json = CAST(? AS JSON)", DbSqlFlavor::Sqlite => "progress_json = ?" };
        let mut params = vec![DbValue::from(encoded!(&next)?), DbValue::from(status), optional_u64_value(if status == "delivered" { Some(now) } else { None })];
        params.extend(self.terminal_im_fence(c)); params.extend([DbValue::from(now), DbValue::from(encoded!(&expected)?)]);
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET progress_json = ?, status = ?, delivered_at_ms = ? {} \
             WHERE env = ? AND operation_key = ? AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND lease_until_ms > ? \
             AND {progress_compare} AND status = 'pending' AND operation_kind = 'im_terminal' AND {}",
             if terminal { ", lease_owner = NULL, lease_until_ms = NULL" } else { "" }, active(true)), params)).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn release_terminal_notification(&self, c: &Claim) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(
            "UPDATE bcs_collaboration_delivery_checkpoints SET lease_owner = NULL, lease_until_ms = NULL WHERE env = ? AND operation_key = ? \
             AND lease_owner = ? AND lease_token = ? AND lease_until_ms = ? AND operation_kind = 'im_terminal'", self.terminal_im_fence(c))).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
    pub(super) async fn supersede_terminal_notification(&self, run: &str) -> ServiceResult<bool> {
        let changed = self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_collaboration_delivery_checkpoints SET status = 'superseded', lease_owner = NULL, lease_until_ms = NULL \
             WHERE env = ? AND operation_key = ? AND operation_kind = 'im_terminal' AND status = 'pending' AND NOT ({})", active(false)),
            vec![DbValue::from(self.env.as_str()), DbValue::from(key(run))])).await.map_err(db_error)?;
        Ok(changed.affected_rows > 0)
    }
}

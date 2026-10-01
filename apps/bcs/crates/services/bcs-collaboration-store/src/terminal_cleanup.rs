//! Bounded retirement of terminal Run work. Saved business facts are never deleted.
use super::*;
use bcs_service_api::{StateMachineChatResultStatus as ChatStatus, StateMachineDispatchStatus as DispatchStatus};

fn terminal(status: StateMachineRunStatus) -> bool {
    matches!(status, StateMachineRunStatus::Completed | StateMachineRunStatus::Failed | StateMachineRunStatus::Aborted)
}

fn repair_key(run: &str) -> String { format!("smrun:{run}:history-repair") }
fn validate_repair_batch(runs: &[String]) -> ServiceResult<()> {
    if runs.len() > 32 || runs.iter().any(String::is_empty) {
        return Err(ServiceError::InternalError("history repair lookup requires at most 32 nonempty Run IDs".into()));
    }
    Ok(())
}

impl MemoryCollaborationStore {
    pub(super) async fn unrepaired_history_runs(&self, runs: &[String]) -> ServiceResult<Vec<String>> {
        validate_repair_batch(runs)?;
        let inner = self.inner.read().await;
        Ok(runs.iter().filter(|run| !inner.repaired_history_runs.contains(*run)).cloned().collect())
    }

    pub(super) async fn confirm_history_repair(&self, run: &str, _at: u64) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        if !inner.runs.get(run).is_some_and(|run| terminal(run.status)) {
            return Err(ServiceError::Conflict("history repair requires a terminal Run".into()));
        }
        inner.repaired_history_runs.insert(run.into());
        Ok(())
    }

    pub(super) async fn terminal_cleanup_candidates(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        let inner = self.inner.read().await;
        Ok(inner.runs.range::<str, _>((after.map_or(std::ops::Bound::Unbounded, std::ops::Bound::Excluded), std::ops::Bound::Unbounded))
            .filter(|(_, run)| terminal(run.status)).take(limit).map(|(id, _)| id.clone()).collect())
    }

    pub(super) async fn cleanup_terminal_work(&self, run: &str, limit: usize) -> ServiceResult<usize> {
        if limit == 0 { return Ok(0); }
        let mut inner = self.inner.write().await;
        if !inner.runs.get(run).is_some_and(|run| terminal(run.status)) { return Ok(0); }
        let mut changed = 0;
        for saved in inner.dispatches.values_mut().filter(|saved| saved.payload.run_id == run
            && matches!(saved.status, DispatchStatus::Pending | DispatchStatus::Delivering)).take(limit) {
            saved.status = DispatchStatus::Superseded; saved.lease_owner = None; saved.lease_until_ms = None;
            changed += 1;
        }
        if changed < limit && let Some(saved) = inner.publications.get_mut(run)
            && matches!(saved.status, ChatStatus::Pending | ChatStatus::Delivering) {
            saved.status = ChatStatus::Superseded; saved.lease_owner = None; saved.lease_until_ms = None;
            changed += 1;
        }
        Ok(changed + judge::retire_terminal_leases(&mut inner, run, limit))
    }
}

fn db_error(error: DbError) -> ServiceError { ServiceError::InternalError(format!("terminal cleanup: {error}")) }
const TERMINAL: &str = "r.status IN ('completed', 'failed', 'aborted') AND r.record_status = 'active'";

impl MySqlCollaborationStore {
    pub(super) async fn unrepaired_history_runs(&self, runs: &[String]) -> ServiceResult<Vec<String>> {
        validate_repair_batch(runs)?;
        if runs.is_empty() { return Ok(Vec::new()); }
        let mut params = vec![DbValue::from(self.env.as_str())];
        params.extend(runs.iter().map(|run| DbValue::from(repair_key(run))));
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT aggregate_id FROM bcs_collaboration_delivery_checkpoints WHERE env = ? \
             AND operation_key IN ({}) AND aggregate_kind = 'state_machine_run' \
             AND operation_kind = 'history_repair' AND status = 'delivered' AND delivered_at_ms IS NOT NULL",
            vec!["?"; runs.len()].join(", ")), params)).await.map_err(db_error)?;
        let repaired = rows.iter().map(|row| db_get_column::<String>(row, "aggregate_id"))
            .collect::<Result<BTreeSet<_>, _>>().map_err(db_error)?;
        Ok(runs.iter().filter(|run| !repaired.contains(*run)).cloned().collect())
    }

    pub(super) async fn confirm_history_repair(&self, run: &str, at: u64) -> ServiceResult<()> {
        let suffix = match self.flavor {
            DbSqlFlavor::Mysql => "ON DUPLICATE KEY UPDATE operation_key = operation_key",
            DbSqlFlavor::Sqlite => "ON CONFLICT(env, operation_key) DO NOTHING",
        };
        let result = self.db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_collaboration_delivery_checkpoints \
             (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, delivered_at_ms) \
             SELECT ?, ?, 'state_machine_run', r.run_id, 'history_repair', '{{\"schema_version\":1}}', 'delivered', ?, ? \
             FROM bcs_state_machine_runs r WHERE r.env = ? AND r.run_id = ? AND {TERMINAL} {suffix}"),
            vec![self.env.as_str().into(), repair_key(run).into(), at.into(), at.into(), self.env.as_str().into(), run.into()],
        )).await.map_err(db_error)?;
        if result.affected_rows == 0 && !self.unrepaired_history_runs(&[run.into()]).await?.is_empty() {
            return Err(ServiceError::Conflict("history repair requires a terminal Run".into()));
        }
        Ok(())
    }

    pub(super) async fn terminal_cleanup_candidates(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> {
        if limit == 0 { return Ok(Vec::new()); }
        let mut ids = Vec::new();
        // Separate status ranges use idx_sm_runs_progression without sorting all
        // historical Runs. Merge at most 3 * limit IDs in memory.
        for status in ["completed", "failed", "aborted"] {
            let rows = self.db.query(DbStatement::with_params(
                "SELECT run_id FROM bcs_state_machine_runs WHERE env = ? AND status = ? AND record_status = 'active' AND run_id > ? ORDER BY run_id LIMIT ?",
                vec![DbValue::from(self.env.as_str()), DbValue::from(status), DbValue::from(after.unwrap_or("")), DbValue::from(limit as u64)],
            )).await.map_err(db_error)?;
            for row in rows { ids.push(db_get_column::<String>(&row, "run_id").map_err(db_error)?); }
        }
        ids.sort(); ids.dedup(); ids.truncate(limit);
        Ok(ids)
    }

    pub(super) async fn cleanup_terminal_work(&self, run: &str, limit: usize) -> ServiceResult<usize> {
        if limit == 0 { return Ok(0); }
        let mut changed = 0;
        for (table, key, pending, set) in [
            ("bcs_collaboration_delivery_checkpoints", "operation_key",
                "operation_kind IN ('bot_dispatch', 'chat_result') AND status IN ('pending', 'delivering')",
                "status = 'superseded', lease_owner = NULL, lease_until_ms = NULL"),
            ("bcs_state_machine_node_runs", "node_id",
                "(runtime_phase IS NOT NULL OR recovery_lease_owner IS NOT NULL OR recovery_lease_until_ms IS NOT NULL)",
                "runtime_phase = NULL, recovery_lease_owner = NULL, recovery_lease_until_ms = NULL"),
        ] {
            let run_key = if key == "operation_key" { "aggregate_id" } else { "run_id" };
            let fence = format!("EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = {table}.env AND r.run_id = {table}.{run_key} AND {TERMINAL})");
            let params = vec![DbValue::from(self.env.as_str()), DbValue::from(run), DbValue::from(limit as u64)];
            let rows = self.db.query(DbStatement::with_params(format!(
                "SELECT {key} FROM {table} WHERE env = ? AND {run_key} = ? AND {pending} AND {fence} ORDER BY {key} LIMIT ?"), params)).await.map_err(db_error)?;
            if rows.is_empty() { continue; }
            let placeholders = vec!["?"; rows.len()].join(", ");
            let mut params = vec![DbValue::from(self.env.as_str()), DbValue::from(run)];
            for row in rows { params.push(DbValue::from(db_get_column::<String>(&row, key).map_err(db_error)?)); }
            let result = self.db.execute(DbStatement::with_params(format!(
                "UPDATE {table} SET {set} WHERE env = ? AND {run_key} = ? AND {key} IN ({placeholders}) AND {pending} AND {fence}"), params)).await.map_err(db_error)?;
            changed += result.affected_rows as usize;
        }
        Ok(changed)
    }
}

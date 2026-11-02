//! Attempt-scoped typed failure facts in the existing checkpoint journal.
use super::*;
use bcs_service_api::port::repo::StateMachineRunReason;

fn key(run: &str, node: &str, attempt: i32) -> String { format!("smnode:{run}:{node}:{attempt}:failure") }
pub(super) fn step(env: &str, run: &str, node: &str, attempt: i32, reason: StateMachineRunReason, at: u64) -> ServiceResult<DbTransactionStep> {
    let value = serde_json::json!({"schema_version":1,"reason_category":reason});
    Ok(DbTransactionStep::Execute(DbStatement::with_params(
        "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, node_id, aggregate_attempt, payload_json, status, created_at_ms) VALUES (?, ?, 'state_machine_node', ?, 'node_failure', ?, ?, ?, 'failed', ?)",
        vec![env.into(), key(run, node, attempt).into(), run.into(), node.into(), attempt.into(), value.to_string().into(), at.into()])))
}
impl MySqlCollaborationStore {
    pub(super) async fn node_failure_reason(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineRunReason>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT payload_json FROM bcs_collaboration_delivery_checkpoints WHERE env = ? AND operation_key = ? AND operation_kind = 'node_failure'",
            vec![self.env.as_str().into(), key(run, node, attempt).into()])).await.map_err(|e| ServiceError::InternalError(e.to_string()))?;
        rows.first().map(|r| {
            let value: serde_json::Value = serde_json::from_str(&db_get_column::<String>(r, "payload_json").map_err(|e| ServiceError::InternalError(e.to_string()))?)?;
            if value["schema_version"] != 1 { return Err(ServiceError::InternalError("unknown Node failure schema".into())); }
            serde_json::from_value(value["reason_category"].clone()).map_err(Into::into)
        }).transpose()
    }
}

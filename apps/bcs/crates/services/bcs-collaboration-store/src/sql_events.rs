use super::*;
#[async_trait]
impl CollaborationEventRepoPort for MySqlCollaborationStore {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()> {
        let sql = "INSERT INTO bcs_collaboration_events \
                   (env, state_machine_run_id, node_id, attempt, event_type, \
                    payload_json, created_at_ms, record_status) \
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active')";
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(node_id),
                    attempt.map(DbValue::from).unwrap_or(DbValue::Null),
                    DbValue::from(event_type),
                    DbValue::from(payload.to_string()),
                    DbValue::from(created_at),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event append: {error}"))
            })?;
        Ok(())
    }

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let sql = "SELECT state_machine_run_id, node_id, attempt, event_type, \
                          payload_json, created_at_ms \
                   FROM bcs_collaboration_events \
                   WHERE env = ? AND state_machine_run_id = ? AND event_type = ? \
                     AND record_status = 'active' \
                   ORDER BY id ASC";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(event_type),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event list: {error}"))
            })?;
        rows.into_iter().map(row_to_collaboration_event).collect()
    }

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let sql = "SELECT state_machine_run_id, node_id, attempt, event_type, \
                          payload_json, created_at_ms \
                   FROM bcs_collaboration_events \
                   WHERE env = ? AND state_machine_run_id = ? AND node_id = ? \
                     AND event_type = ? AND record_status = 'active' \
                   ORDER BY id ASC";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(node_id),
                    DbValue::from(event_type),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event list by node: {error}"))
            })?;
        rows.into_iter().map(row_to_collaboration_event).collect()
    }
}

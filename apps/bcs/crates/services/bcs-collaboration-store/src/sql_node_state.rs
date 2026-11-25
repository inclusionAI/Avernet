use super::*;
impl MySqlCollaborationStore {

    pub(super) async fn sql_list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let sql = format!(
            "SELECT {SM_NODE_SELECT_COLS} \
             FROM bcs_state_machine_node_runs \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
             ORDER BY node_id ASC"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node list: {error}"))
            })?;
        rows.into_iter()
            .map(row_to_state_machine_node_run)
            .collect()
    }

    pub(super) async fn sql_get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>> {
        let sql = format!(
            "SELECT {SM_NODE_SELECT_COLS} \
             FROM bcs_state_machine_node_runs \
             WHERE env = ? AND run_id = ? AND node_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node get: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_node_run)
            .transpose()
    }

    pub(super) async fn sql_mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'running', attempt = ?, delivery_request_id = ?, \
                 bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                 artifact_text = NULL, \
                 error_message = NULL, failure_action = NULL, started_at_ms = ?, completed_at_ms = NULL, \
                 timeout_deadline_ms = CASE \
                   WHEN node_timeout_ms IS NULL THEN NULL \
                   ELSE ? + node_timeout_ms \
                 END, \
                 {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(attempt),
                    DbValue::from(delivery_request_id.as_str()),
                    DbValue::from(started_at),
                    DbValue::from(started_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node mark running: {error}"))
            })?;
        Ok(())
    }

    pub(super) async fn sql_mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        let sql = match self.flavor {
            DbSqlFlavor::Mysql => format!(
                "UPDATE bcs_state_machine_node_runs n \
                 SET n.status = 'running', n.runtime_phase = 'dispatch_pending', n.delivery_request_id = ?, \
                     n.bot_delivery_run_id = NULL, n.outcome = NULL, \
                     n.responded_by = NULL, n.artifact_text = NULL, \
                     n.error_message = NULL, n.started_at_ms = ?, n.completed_at_ms = NULL, \
                     n.timeout_deadline_ms = CASE \
                       WHEN n.node_timeout_ms IS NULL THEN NULL \
                       ELSE ? + n.node_timeout_ms \
                     END, \
                     {} \
                 WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? \
                   AND n.attempt = ? AND n.status IN ('pending', 'ready', 'retry_scheduled') \
                   AND n.record_status = 'active' \
                   AND EXISTS ( \
                     SELECT 1 FROM bcs_state_machine_runs r \
                     WHERE r.env = n.env AND r.run_id = n.run_id \
                       AND r.status = 'running' AND r.record_status = 'active' \
                   )",
                "n.gmt_modified = NOW()",
            ),
            DbSqlFlavor::Sqlite => format!(
                "UPDATE bcs_state_machine_node_runs \
                 SET status = 'running', runtime_phase = 'dispatch_pending', delivery_request_id = ?, \
                     bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                     artifact_text = NULL, \
                     error_message = NULL, started_at_ms = ?, completed_at_ms = NULL, \
                     timeout_deadline_ms = CASE \
                       WHEN node_timeout_ms IS NULL THEN NULL \
                       ELSE ? + node_timeout_ms \
                     END, \
                     {} \
                 WHERE env = ? AND run_id = ? AND node_id = ? \
                   AND attempt = ? AND status IN ('pending', 'ready', 'retry_scheduled') \
                   AND record_status = 'active' \
                   AND EXISTS ( \
                     SELECT 1 FROM bcs_state_machine_runs r \
                     WHERE r.env = bcs_state_machine_node_runs.env \
                       AND r.run_id = bcs_state_machine_node_runs.run_id \
                       AND r.status = 'running' AND r.record_status = 'active' \
                   )",
                self.flavor.set_modified_now(),
            ),
        };
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(delivery_request_id.as_str()),
                    DbValue::from(started_at),
                    DbValue::from(started_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node mark running CAS: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'completed', runtime_phase = NULL, outcome = ?, responded_by = ?, artifact_text = ?, \
                 error_message = NULL, \
                 completed_at_ms = ?, timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' AND (runtime_phase IS NULL OR runtime_phase IN ('dispatch_pending', 'waiting_provider')) \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(outcome),
                    DbValue::from(responded_by.as_deref()),
                    DbValue::from(artifact_text),
                    DbValue::from(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node complete: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'running', delivery_request_id = NULL, \
                 bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                 artifact_text = NULL, error_message = NULL, started_at_ms = ?, \
                 completed_at_ms = NULL, timeout_deadline_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
               AND status IN ('pending', 'ready') AND assignee_bot_id = '' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(command.started_at_ms),
                    DbValue::from(command.timeout_deadline_ms),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run_id),
                    DbValue::from(command.node_id),
                    DbValue::from(command.attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "state machine human node mark running: {error}"
                ))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET artifact_text = ?, error_message = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' AND (runtime_phase IS NULL OR runtime_phase IN ('dispatch_pending', 'waiting_provider')) \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(artifact_text),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node record artifact: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool> {
        let expected_artifact = artifact_text.clone();
        let expected_responder = responded_by.clone();
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET artifact_text = ?, responded_by = ?, error_message = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' AND runtime_phase IS NULL AND artifact_text IS NULL \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(artifact_text),
                    DbValue::from(responded_by),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine human response record: {error}"))
            })?;
        if result.affected_rows > 0 {
            return Ok(true);
        }
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT {SM_NODE_SELECT_COLS} FROM bcs_state_machine_node_runs WHERE env = ? AND run_id = ? AND node_id = ? AND runtime_phase IS NULL AND record_status = 'active' \
             AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id AND r.status = 'running' AND r.record_status = 'active')"),
            vec![DbValue::from(self.env.as_str()), DbValue::from(run_id), DbValue::from(node_id)],
        )).await.map_err(|error| ServiceError::InternalError(format!("human response replay: {error}")))?;
        let Some(node) = rows.into_iter().next().map(row_to_state_machine_node_run).transpose()? else {
            return Ok(false);
        };
        Ok(node.status == StateMachineNodeStatus::Running
            && node.attempt == attempt
            && node.artifact_text.as_deref() == Some(expected_artifact.as_str())
            && node.responded_by.as_deref() == Some(expected_responder.as_str()))
    }

    pub(super) async fn sql_fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'failed', error_message = ?, completed_at_ms = ?, \
                 timeout_deadline_ms = NULL, failure_action = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(error),
                    DbValue::from(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node fail: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_fail_node_attempt_with_action(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        let action = failure_action_name(command.action);
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs SET status = 'failed', error_message = ?, \
             completed_at_ms = ?, failure_action = ?, timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'running' \
               AND record_status = 'active' \
               AND (? = 'fail_run' OR (attempt >= 0 AND attempt < CASE WHEN max_attempts > 0 THEN max_attempts ELSE 1 END - 1)) \
               AND EXISTS (SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active')",
            self.flavor.set_modified_now(),
        );
        let result = self.db.execute(DbStatement::with_params(sql, vec![
            DbValue::from(command.error), DbValue::from(command.completed_at_ms), DbValue::from(action),
            DbValue::from(self.env.as_str()), DbValue::from(command.run_id), DbValue::from(command.node_id),
            DbValue::from(command.attempt), DbValue::from(action),
        ])).await.map_err(|error| ServiceError::InternalError(format!("state machine node failure action: {error}")))?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_get_node_attempt_failure(&self, run_id: &str, node_id: &str, attempt: i32) -> ServiceResult<Option<StateMachineNodeAttemptFailure>> {
        let sql = format!("SELECT {SM_NODE_SELECT_COLS}, failure_action FROM bcs_state_machine_node_runs \
            WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? AND status = 'failed' AND record_status = 'active' LIMIT 1");
        let rows = self.db.query(DbStatement::with_params(sql, vec![DbValue::from(self.env.as_str()),
            DbValue::from(run_id), DbValue::from(node_id), DbValue::from(attempt),
        ])).await.map_err(|error| ServiceError::InternalError(format!("state machine node failure read: {error}")))?;
        rows.into_iter().next().map(|row| {
            let action = match db_get_column_opt::<String>(&row, "failure_action")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?.as_deref()
            {
                None => None,
                Some("retry") => Some(StateMachineFailureAction::Retry),
                Some("fail_run") => Some(StateMachineFailureAction::FailRun),
                Some(_) => return Err(ServiceError::InternalError("unknown persisted node failure action".into())),
            };
            Ok(StateMachineNodeAttemptFailure { node: row_to_state_machine_node_run(row)?, action })
        }).transpose()
    }

    pub(super) async fn sql_schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'retry_scheduled', attempt = ?, \
                 delivery_request_id = NULL, bot_delivery_run_id = NULL, \
                 artifact_text = NULL, error_message = NULL, failure_action = NULL, runtime_phase = NULL, recovery_lease_owner = NULL, recovery_lease_until_ms = NULL, started_at_ms = NULL, \
                 completed_at_ms = NULL, timeout_deadline_ms = NULL, \
                 {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'failed' AND (failure_action IS NULL OR failure_action = 'retry') \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(next_attempt),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(failed_attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node retry: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'skipped', completed_at_ms = ?, \
                 timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND status IN ('pending', 'ready', 'retry_scheduled') AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(skipped_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node skip: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    pub(super) async fn sql_list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let sql = "SELECT n.run_id, n.node_id, n.status, n.attempt, n.node_timeout_ms, \
                          n.timeout_deadline_ms, n.max_attempts, n.assignee_bot_id, \
                          n.delivery_request_id, n.bot_delivery_run_id, n.artifact_text, \
                          n.error_message, n.started_at_ms, n.completed_at_ms \
                   FROM bcs_state_machine_node_runs n \
                   INNER JOIN bcs_state_machine_runs r \
                     ON r.env = n.env AND r.run_id = n.run_id \
                   WHERE n.env = ? AND n.status = 'running' \
                     AND n.timeout_deadline_ms IS NOT NULL \
                     AND n.timeout_deadline_ms + ? <= ? \
                     AND n.record_status = 'active' \
                     AND r.status = 'running' AND r.record_status = 'active' \
                   ORDER BY n.timeout_deadline_ms ASC, n.run_id ASC, n.node_id ASC \
                   LIMIT ?";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(timeout_grace_ms),
                    DbValue::from(now_ms),
                    DbValue::from(limit as u64),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("expired state machine node list: {error}"))
            })?;
        rows.into_iter()
            .map(row_to_state_machine_node_run)
            .collect()
    }
}

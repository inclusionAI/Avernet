use super::*;
impl MySqlCollaborationStore {

    pub(super) async fn sql_commit_eventful_transition(
        &self,
        transition: StateMachineEventfulTransition,
    ) -> ServiceResult<bool> {
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let mut steps = Vec::new();
        let events = state_machine_transition_events(&transition);
        match &transition {
            StateMachineEventfulTransition::AcceptHistory(command) => { steps.extend(history::accept_sql(self, command)?); }
            StateMachineEventfulTransition::FinishJudge(command) => {
                steps.extend(judge::finish_sql(self, command)?);
            }
            StateMachineEventfulTransition::StartRun {
                run_id,
                started_at_ms,
                ..
            } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT run_id FROM bcs_state_machine_runs \
                         WHERE env = ? AND run_id = ? AND status = 'pending' \
                           AND record_status = 'active'{lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(run_id.as_str()),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_runs SET status = 'running', \
                             updated_at_ms = ?, {} WHERE env = ? AND run_id = ? \
                             AND status = 'pending' AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(*started_at_ms),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                        ],
                    ),
                ));
            }
            StateMachineEventfulTransition::StartBotNode {
                run_id,
                node_id,
                attempt,
                delivery_request_id,
                started_at_ms,
                ..
            } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT n.run_id, n.node_id FROM bcs_state_machine_node_runs n \
                         WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? AND n.attempt = ? \
                           AND n.status IN ('pending', 'ready', 'retry_scheduled') \
                           AND n.record_status = 'active' AND EXISTS ( \
                             SELECT 1 FROM bcs_state_machine_runs r \
                             WHERE r.env = n.env AND r.run_id = n.run_id \
                               AND r.status = 'running' AND r.record_status = 'active' \
                           ){lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(run_id.as_str()),
                        DbValue::from(node_id.as_str()),
                        DbValue::from(*attempt),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_node_runs SET status = 'running', runtime_phase = 'dispatch_pending', \
                             delivery_request_id = ?, bot_delivery_run_id = NULL, outcome = NULL, \
                             responded_by = NULL, artifact_text = NULL, error_message = NULL, \
                             started_at_ms = ?, completed_at_ms = NULL, \
                             timeout_deadline_ms = CASE WHEN node_timeout_ms IS NULL THEN NULL \
                               ELSE ? + node_timeout_ms END, {} \
                             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
                               AND status IN ('pending', 'ready', 'retry_scheduled') \
                               AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(delivery_request_id.as_str()),
                            DbTransactionParam::value(*started_at_ms),
                            DbTransactionParam::value(*started_at_ms),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                            DbTransactionParam::query_result(0, 0, "node_id"),
                            DbTransactionParam::value(*attempt),
                        ],
                    ),
                ));
            }
            StateMachineEventfulTransition::StartHumanNode { command, .. } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT n.run_id, n.node_id FROM bcs_state_machine_node_runs n \
                         WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? AND n.attempt = ? \
                           AND n.status IN ('pending', 'ready') AND n.assignee_bot_id = '' \
                           AND n.record_status = 'active' AND EXISTS ( \
                             SELECT 1 FROM bcs_state_machine_runs r \
                             WHERE r.env = n.env AND r.run_id = n.run_id \
                               AND r.status = 'running' AND r.record_status = 'active' \
                           ){lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(command.run_id.as_str()),
                        DbValue::from(command.node_id.as_str()),
                        DbValue::from(command.attempt),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_node_runs SET status = 'running', \
                             delivery_request_id = NULL, bot_delivery_run_id = NULL, outcome = NULL, \
                             responded_by = NULL, artifact_text = NULL, error_message = NULL, \
                             started_at_ms = ?, completed_at_ms = NULL, timeout_deadline_ms = ?, {} \
                             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
                               AND status IN ('pending', 'ready') AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(command.started_at_ms),
                            DbTransactionParam::value(command.timeout_deadline_ms),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                            DbTransactionParam::query_result(0, 0, "node_id"),
                            DbTransactionParam::value(command.attempt),
                        ],
                    ),
                ));
            }
            StateMachineEventfulTransition::CompleteNode {
                run_id,
                node_id,
                attempt,
                outcome,
                artifact_text,
                responded_by,
                completed_at_ms,
                ..
            } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT n.run_id, n.node_id FROM bcs_state_machine_node_runs n \
                         WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? AND n.attempt = ? \
                           AND n.status = 'running' AND (n.runtime_phase IS NULL OR n.runtime_phase IN ('dispatch_pending', 'waiting_provider')) AND n.record_status = 'active' AND EXISTS ( \
                             SELECT 1 FROM bcs_state_machine_runs r \
                             WHERE r.env = n.env AND r.run_id = n.run_id \
                               AND r.status = 'running' AND r.record_status = 'active' \
                           ){lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(run_id.as_str()),
                        DbValue::from(node_id.as_str()),
                        DbValue::from(*attempt),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_node_runs SET status = 'completed', runtime_phase = NULL, \
                             outcome = ?, responded_by = ?, artifact_text = ?, error_message = NULL, \
                             completed_at_ms = ?, timeout_deadline_ms = NULL, {} \
                             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
                               AND status = 'running' AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(outcome.as_str()),
                            DbTransactionParam::value(DbValue::from(responded_by.as_deref())),
                            DbTransactionParam::value(artifact_text.as_str()),
                            DbTransactionParam::value(*completed_at_ms),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                            DbTransactionParam::query_result(0, 0, "node_id"),
                            DbTransactionParam::value(*attempt),
                        ],
                    ),
                ));
            }
            StateMachineEventfulTransition::ScheduleNodeRetry {
                run_id,
                node_id,
                failed_attempt,
                next_attempt,
                ..
            } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT n.run_id, n.node_id FROM bcs_state_machine_node_runs n \
                         WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? AND n.attempt = ? \
                           AND n.status = 'failed' AND (n.failure_action IS NULL OR n.failure_action = 'retry') AND n.record_status = 'active' AND EXISTS ( \
                             SELECT 1 FROM bcs_state_machine_runs r \
                             WHERE r.env = n.env AND r.run_id = n.run_id \
                               AND r.status = 'running' AND r.record_status = 'active' \
                           ){lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(run_id.as_str()),
                        DbValue::from(node_id.as_str()),
                        DbValue::from(*failed_attempt),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_node_runs SET status = 'retry_scheduled', \
                             attempt = ?, delivery_request_id = NULL, bot_delivery_run_id = NULL, \
                             artifact_text = NULL, error_message = NULL, failure_action = NULL, runtime_phase = NULL, recovery_lease_owner = NULL, recovery_lease_until_ms = NULL, started_at_ms = NULL, \
                             completed_at_ms = NULL, timeout_deadline_ms = NULL, {} \
                             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
                               AND status = 'failed' AND (failure_action IS NULL OR failure_action = 'retry') AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(*next_attempt),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                            DbTransactionParam::query_result(0, 0, "node_id"),
                            DbTransactionParam::value(*failed_attempt),
                        ],
                    ),
                ));
            }
            StateMachineEventfulTransition::CompleteRun {
                run_id,
                output,
                completed_at_ms,
                ..
            } => {
                steps.push(DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT run_id FROM bcs_state_machine_runs \
                         WHERE env = ? AND run_id = ? AND status = 'running' \
                           AND record_status = 'active'{lock_suffix}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(run_id.as_str()),
                    ],
                )));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_state_machine_runs SET status = 'completed', output_text = ?, \
                             error_message = NULL, updated_at_ms = ?, completed_at_ms = ?, {} \
                             WHERE env = ? AND run_id = ? AND status = 'running' \
                               AND record_status = 'active'",
                            self.flavor.set_modified_now()
                        ),
                        vec![
                            DbTransactionParam::value(DbValue::from(output.as_deref())),
                            DbTransactionParam::value(*completed_at_ms),
                            DbTransactionParam::value(*completed_at_ms),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::query_result(0, 0, "run_id"),
                        ],
                    ),
                ));
            }
        }
        for event in &events {
            let event_plan = EventAppendTransactionPlan::build(event, self.flavor, steps.len())
                .map_err(|error| {
                    ServiceError::InternalError(format!(
                        "prepare state-machine transition Event: {error}"
                    ))
                })?;
            steps.extend(event_plan.steps);
        }
        self.db.transaction(steps).await.map_err(|error| {
            ServiceError::Conflict(format!(
                "state-machine transition changed concurrently: {error}"
            ))
        })?;
        Ok(true)
    }
}

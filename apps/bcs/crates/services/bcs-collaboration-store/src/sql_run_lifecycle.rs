use super::*;
impl MySqlCollaborationStore {

    pub(super) async fn sql_create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()> {
        let run_sql = "INSERT INTO bcs_state_machine_runs \
                       (env, run_id, root_run_id, rerun_of, definition_id, definition_version, group_id, \
                        group_version, session_id, session_activation_count, created_by, status, input_json, \
                        opening_message_override_json, output_text, error_message, created_at_ms, updated_at_ms, \
                        completed_at_ms, record_status) \
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')";
        let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
            run_sql,
            run_insert_params(&self.env, &run)?,
        ))];
        if let Some((node_sql, node_params)) =
            build_node_runs_insert(&self.env, &run.run_id, &nodes)
        {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                node_sql,
                node_params,
            )));
        }
        self.db.transaction(steps).await.map_err(|error| {
            ServiceError::InternalError(format!("state machine run create: {error}"))
        })?;
        Ok(())
    }

    pub(super) async fn sql_create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ?{lock_suffix}"
        );
        let run_sql = "INSERT INTO bcs_state_machine_runs \
                       (env, run_id, root_run_id, rerun_of, definition_id, definition_version, group_id, \
                        group_version, session_id, session_activation_count, created_by, status, input_json, \
                        opening_message_override_json, output_text, error_message, created_at_ms, updated_at_ms, \
                        completed_at_ms, record_status) \
                       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active' \
                       FROM bcs_group_sessions session_lock \
                       WHERE session_lock.env = ? AND session_lock.session_id = ? \
                         AND NOT EXISTS ( \
                           SELECT 1 FROM bcs_state_machine_runs active_run \
                           WHERE active_run.env = ? \
                             AND active_run.session_id = ? \
                             AND active_run.status IN ('pending', 'running') \
                             AND active_run.record_status = 'active' \
                         ) \
                       LIMIT 1";
        let mut run_params = run_insert_params(&self.env, &run)?;
        run_params.extend([
            DbValue::from(self.env.as_str()),
            DbValue::from(run.session_id.as_str()),
            DbValue::from(self.env.as_str()),
            DbValue::from(run.session_id.as_str()),
        ]);
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run.session_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(run_sql, run_params)),
        ];
        steps.extend(build_guarded_node_runs_inserts(
            &self.env,
            &run.run_id,
            &nodes,
        ));
        let results = self.db.transaction(steps).await.map_err(|error| {
            ServiceError::InternalError(format!("state machine session run create: {error}"))
        })?;
        let created = matches!(
            results.get(1),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0
        );
        Ok(created)
    }

    pub(super) async fn sql_create_rerun_if_session_idle(
        &self,
        command: CreateStateMachineRerun,
    ) -> ServiceResult<CreateStateMachineRerunOutcome> {
        if let Some(existing) = self.get_direct_rerun(&command.source_run_id).await? {
            return Ok(CreateStateMachineRerunOutcome::Existing(existing));
        }
        let service_activation = i32::from(command.reactivate_service_session);
        let expected_activation = match command.run.session_activation_count {
            Some(activation) if command.reactivate_service_session && activation > 1 => {
                activation - 1
            }
            Some(_) if command.reactivate_service_session => {
                return Ok(CreateStateMachineRerunOutcome::Conflict);
            }
            _ => 0,
        };
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ?{lock_suffix}"
        );
        let run_sql = "INSERT INTO bcs_state_machine_runs \
                       (env, run_id, root_run_id, rerun_of, definition_id, definition_version, \
                        group_id, group_version, session_id, session_activation_count, created_by, \
                        status, input_json, opening_message_override_json, output_text, error_message, created_at_ms, updated_at_ms, \
                        completed_at_ms, record_status) \
                       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \
                              source_run.opening_message_override_json, ?, ?, ?, ?, ?, 'active' \
                       FROM bcs_state_machine_runs source_run \
                       INNER JOIN bcs_group_sessions session_state \
                         ON session_state.env = source_run.env \
                        AND session_state.session_id = source_run.session_id \
                       WHERE source_run.env = ? AND source_run.run_id = ? \
                         AND source_run.session_id = ? \
                         AND source_run.status = 'failed' \
                         AND source_run.record_status = 'active' \
                         AND session_state.group_id = source_run.group_id \
                         AND ( \
                           (? = 0 AND session_state.session_kind = 'chat' \
                            AND session_state.status = 'running') \
                           OR \
                           (? = 1 AND session_state.session_kind = 'service_invocation' \
                            AND session_state.status = 'completed' \
                            AND session_state.activation_count = ?) \
                         ) \
                         AND NOT EXISTS ( \
                           SELECT 1 FROM bcs_state_machine_runs direct_child \
                           WHERE direct_child.env = source_run.env \
                             AND direct_child.rerun_of = source_run.run_id \
                             AND direct_child.record_status = 'active') \
                         AND NOT EXISTS ( \
                           SELECT 1 FROM bcs_state_machine_runs active_run \
                           WHERE active_run.env = source_run.env \
                             AND active_run.session_id = source_run.session_id \
                             AND active_run.status IN ('pending', 'running') \
                             AND active_run.record_status = 'active') \
                         AND EXISTS ( \
                           SELECT 1 FROM bcs_state_machine_definition_snapshots source_snapshot \
                           WHERE source_snapshot.env = source_run.env \
                             AND source_snapshot.run_id = source_run.run_id) \
                       LIMIT 1";
        let mut run_params = run_insert_params(&self.env, &command.run)?;
        // The rerun INSERT copies this value directly from the source row.
        run_params.remove(RUN_OPENING_MESSAGE_OVERRIDE_PARAM_INDEX);
        run_params.extend([
            DbValue::from(self.env.as_str()),
            DbValue::from(command.source_run_id.as_str()),
            DbValue::from(command.run.session_id.as_str()),
            DbValue::from(service_activation),
            DbValue::from(service_activation),
            DbValue::from(expected_activation),
        ]);
        let reactivate_sql = format!(
            "UPDATE bcs_group_sessions \
             SET status = 'running', output = NULL, error_message = NULL, \
                 input = ?, \
                 activation_count = activation_count + 1, completed_at = NULL, {} \
             WHERE ? = 1 AND env = ? AND session_id = ? \
               AND session_kind = 'service_invocation' AND status = 'completed' \
               AND activation_count = ? \
               AND EXISTS (SELECT 1 FROM bcs_state_machine_runs \
                           WHERE env = ? AND run_id = ? AND record_status = 'active')",
            self.flavor.set_modified_now(),
        );
        let snapshot_sql = "INSERT INTO bcs_state_machine_definition_snapshots \
                            (env, run_id, group_id, session_id, group_version, definition_id, \
                             definition_version, definition_content_hash, snapshot_blob_id, \
                             snapshot_json, resolved_participant_bindings_json, source_format, \
                             execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version) \
                            SELECT source_snapshot.env, ?, source_snapshot.group_id, \
                                   source_snapshot.session_id, source_snapshot.group_version, \
                                   source_snapshot.definition_id, source_snapshot.definition_version, \
                                   source_snapshot.definition_content_hash, source_snapshot.snapshot_blob_id, \
                                   source_snapshot.snapshot_json, \
                                   source_snapshot.resolved_participant_bindings_json, \
                                   source_snapshot.source_format, source_snapshot.execution_plan_json, \
                                   source_snapshot.execution_plan_content_hash, \
                                   source_snapshot.execution_plan_compiler_version \
                            FROM bcs_state_machine_definition_snapshots source_snapshot \
                            WHERE source_snapshot.env = ? AND source_snapshot.run_id = ? \
                              AND EXISTS (SELECT 1 FROM bcs_state_machine_runs \
                                          WHERE env = ? AND run_id = ? \
                                            AND record_status = 'active')";
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run.session_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(run_sql, run_params)),
            DbTransactionStep::Execute(DbStatement::with_params(
                reactivate_sql,
                vec![
                    DbValue::from(command.run.input.to_string()),
                    DbValue::from(service_activation),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run.session_id.as_str()),
                    DbValue::from(expected_activation),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run.run_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                snapshot_sql,
                vec![
                    DbValue::from(command.run.run_id.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.source_run_id.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run.run_id.as_str()),
                ],
            )),
        ];
        steps.extend(build_guarded_node_runs_inserts(
            &self.env,
            &command.run.run_id,
            &command.nodes,
        ));
        let results = match self.db.transaction(steps).await {
            Ok(results) => results,
            Err(error) => {
                if let Some(existing) = self.get_direct_rerun(&command.source_run_id).await? {
                    return Ok(CreateStateMachineRerunOutcome::Existing(existing));
                }
                return Err(ServiceError::InternalError(format!(
                    "state machine rerun create: {error}"
                )));
            }
        };
        if matches!(
            results.get(1),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0
        ) {
            return Ok(CreateStateMachineRerunOutcome::Created);
        }
        if let Some(existing) = self.get_direct_rerun(&command.source_run_id).await? {
            return Ok(CreateStateMachineRerunOutcome::Existing(existing));
        }
        Ok(CreateStateMachineRerunOutcome::Conflict)
    }

    pub(super) async fn sql_get_direct_rerun(
        &self,
        source_run_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} FROM bcs_state_machine_runs \
             WHERE env = ? AND rerun_of = ? AND record_status = 'active' LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(source_run_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine direct rerun get: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_run)
            .transpose()
    }

    pub(super) async fn sql_get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run get: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_run)
            .transpose()
    }

    pub(super) async fn sql_list_running_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        if limit == 0 { return Ok(Vec::new()); }
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} FROM bcs_state_machine_runs \
             WHERE env = ? AND status = 'running' AND record_status = 'active' \
               AND run_id > ? ORDER BY run_id ASC LIMIT ?"
        );
        self.db.query(DbStatement::with_params(sql, vec![
            DbValue::from(self.env.as_str()), DbValue::from(after_run_id.unwrap_or("")),
            DbValue::from(limit as u64),
        ])).await.map_err(|error| ServiceError::InternalError(format!("state machine progression candidates: {error}")))?
            .into_iter().map(row_to_state_machine_run).collect()
    }

    pub(super) async fn sql_list_pending_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        if limit == 0 { return Ok(Vec::new()); }
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} FROM bcs_state_machine_runs \
             WHERE env = ? AND status = 'pending' AND record_status = 'active' \
               AND run_id > ? ORDER BY run_id ASC LIMIT ?"
        );
        self.db.query(DbStatement::with_params(sql, vec![
            DbValue::from(self.env.as_str()), DbValue::from(after_run_id.unwrap_or("")),
            DbValue::from(limit as u64),
        ])).await.map_err(|error| ServiceError::InternalError(format!("state machine progression candidates: {error}")))?
            .into_iter().map(row_to_state_machine_run).collect()
    }

    pub(super) async fn sql_get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND session_id = ? AND record_status = 'active' \
             ORDER BY created_at_ms DESC, id DESC \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run get by session: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_run)
            .transpose()
    }

    pub(super) async fn sql_list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND session_id = ? AND record_status = 'active' \
             ORDER BY created_at_ms DESC, id DESC"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine runs list by session: {error}"))
            })?;
        rows.into_iter().map(row_to_state_machine_run).collect()
    }

    pub(super) async fn sql_update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_runs \
             SET status = ?, output_text = ?, error_message = ?, \
                 updated_at_ms = ?, completed_at_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
               AND status NOT IN ('completed', 'failed', 'aborted')",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(run_status_to_str(status)),
                    DbValue::from(output.as_deref()),
                    DbValue::from(error.as_deref()),
                    DbValue::from(updated_at),
                    optional_u64_value(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run status update: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }
}

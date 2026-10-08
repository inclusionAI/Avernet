//! Persistence operations separated from the repository port forwarding.
use super::*;

impl MySqlSessionStore {
    pub(super) async fn repo_validate_session_registry(&self) -> ServiceResult<()> { self.registry_validate().await }
}

impl MySqlSessionStore {
    pub(super) async fn repo_ensure_direct_session(&self, id: &str) -> ServiceResult<bcs_service_api::port::repo::session_registry::SessionRegistration> {
        self.registry_ensure_direct(id).await
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_session_registration(&self, id: &str) -> ServiceResult<Option<bcs_service_api::port::repo::session_registry::SessionRegistration>> {
        self.registry_get(id).await
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> {
        // Explicit id path — single attempt, no retry.
        if let Some(ref id) = params.id {
            if !validate_session_id(id, group_id) {
                return Err(ServiceError::SessionInvalidParams(format!(
                    "session_id {id} not valid for group {group_id}"
                )));
            }
            return self
                .insert_session(
                    id.clone(),
                    group_id.to_string(),
                    params,
                    current_millis(),
                    None,
                )
                .await;
        }

        // Auto-generate path: up to 3 retries on uk_session_id collision.
        for _ in 0..3 {
            let id = new_session_id(group_id)
                .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
            match self
                .insert_session(
                    id,
                    group_id.to_string(),
                    params.clone(),
                    current_millis(),
                    None,
                )
                .await
            {
                Ok(sess) => return Ok(sess),
                Err(ServiceError::InternalError(ref msg))
                    if msg.to_ascii_lowercase().contains("duplicate")
                        || msg.contains("1062")
                        || msg.contains("UNIQUE constraint failed") =>
                {
                    // uk_session_id collision — retry with a new id.
                    continue;
                }
                Err(e) => return Err(e),
            }
        }
        Err(ServiceError::SessionInvalidParams(
            "session_id collision retry exhausted (3 attempts)".to_string(),
        ))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_create_with_event(&self, command: CreateSessionWithEvent) -> ServiceResult<Session> {
        let session_id = command.params.id.clone().ok_or_else(|| {
            ServiceError::SessionInvalidParams(
                "Eventful Session creation requires a preallocated session_id".to_string(),
            )
        })?;
        if !validate_session_id(&session_id, &command.group_id) {
            return Err(ServiceError::SessionInvalidParams(format!(
                "session_id {session_id} not valid for group {}",
                command.group_id
            )));
        }
        self.insert_session(
            session_id,
            command.group_id,
            command.params,
            current_millis(),
            Some(&command.event),
        )
        .await
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_create_channel(
        &self,
        group_id: &str,
        channel_type: &str,
        params: NewSessionParams,
    ) -> ServiceResult<Session> {
        for _ in 0..3 {
            let id = new_channel_session_id(group_id, channel_type)
                .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
            match self
                .insert_session(
                    id,
                    group_id.to_string(),
                    params.clone(),
                    current_millis(),
                    None,
                )
                .await
            {
                Ok(session) => return Ok(session),
                Err(ServiceError::InternalError(ref message))
                    if message.to_ascii_lowercase().contains("duplicate")
                        || message.contains("1062")
                        || message.contains("UNIQUE constraint failed") =>
                {
                    continue;
                }
                Err(error) => return Err(error),
            }
        }
        Err(ServiceError::SessionInvalidParams(
            "session_id collision retry exhausted (3 attempts)".to_string(),
        ))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_get(&self, session_id: &str) -> Option<Session> {
        self.try_get(session_id).await.ok().flatten()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_try_get(&self, session_id: &str) -> ServiceResult<Option<Session>> {
        let select_cols = self.select_cols();
        let sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        rows.into_iter().next().map(|row| row_to_session(&row)).transpose()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_belongs_to_group(&self, session_id: &str, group_id: &str) -> bool {
        let sql = "SELECT 1 AS found FROM bcs_group_sessions \
                   WHERE env = ? AND session_id = ? AND group_id = ? LIMIT 1";
        self.db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(group_id),
                ],
            ))
            .await
            .map(|rows| !rows.is_empty())
            .unwrap_or(false)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        let now = current_millis();
        let sql = format!(
            "UPDATE bcs_group_sessions \
             SET status = 'completed', output = ?, error_message = ?, \
                 completed_at = ?, {} \
             WHERE env = ? AND session_id = ? AND status = 'running'",
            self.flavor.set_modified_now(),
        );

        let output_value = json_to_db_value(&output);
        let error_value = DbValue::from(error.as_deref());
        let completed_at_value = DbValue::I64(now as i64);

        let result = self
            .db
            .execute(DbStatement::with_params(
                &sql,
                vec![
                    output_value,
                    error_value,
                    completed_at_value,
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        if result.affected_rows == 0 {
            // Already completed — CAS short-circuit, not an error.
            return Ok(None);
        }

        // 1+ rows updated → re-SELECT to return the new state.
        let select_cols = self.select_cols();
        let select_sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &select_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        match rows.into_iter().next() {
            Some(row) => row_to_session(&row).map(Some),
            None => Err(ServiceError::SessionNotFound(session_id.to_string())),
        }
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_complete_running_service_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        let Some(mut candidate) = self.try_get(session_id).await? else { return Ok(None); };
        if candidate.session_kind != SessionKind::ServiceInvocation
            || candidate.status != SessionStatus::Running
            || candidate.activation_count != expected_activation_count
        {
            return Ok(None);
        }
        let now = current_millis();
        let sql = format!(
            "UPDATE bcs_group_sessions SET status = 'completed', output = ?, \
             error_message = ?, completed_at = ?, {} \
             WHERE env = ? AND session_id = ? AND session_kind = 'service_invocation' \
               AND status = 'running' AND activation_count = ?",
            self.flavor.set_modified_now(),
        );
        let result = self.db.execute(DbStatement::with_params(sql, vec![
            json_to_db_value(&output), DbValue::from(error.as_deref()), DbValue::U64(now),
            DbValue::from(self.env.as_str()), DbValue::from(session_id),
            DbValue::from(expected_activation_count),
        ])).await.map_err(|error| ServiceError::InternalError(format!("Session activation completion: {error}")))?;
        if result.affected_rows == 0 { return Ok(None); }
        // A post-CAS SELECT could observe a new activation. Return the snapshot
        // belonging to this transition for callback/notification callers.
        candidate.status = SessionStatus::Completed;
        candidate.output = output;
        candidate.error_message = error;
        candidate.completed_at = Some(now);
        candidate.updated_at = now;
        Ok(Some(candidate))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_complete_if_running_with_event(
        &self,
        command: CompleteSessionWithEvent,
    ) -> ServiceResult<Option<Session>> {
        let mut candidate = self
            .get(&command.session_id)
            .await
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if candidate.status == SessionStatus::Completed {
            return Ok(None);
        }
        if candidate.activation_count != command.expected_activation_count {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' activation changed during completion",
                command.session_id
            )));
        }
        let now = current_millis();
        candidate.status = SessionStatus::Completed;
        candidate.output = command.output.clone();
        candidate.error_message = command.error.clone();
        candidate.updated_at = now;
        candidate.completed_at = Some(now);
        validate_session_event_scope(&candidate, &command.event)?;

        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? AND status = 'running' \
               AND activation_count = ?{}",
            transaction_lock_suffix(self.flavor),
        );
        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET status = 'completed', output = ?, error_message = ?, \
                 completed_at = ?, {} \
             WHERE env = ? AND session_id = ? AND status = 'running' \
               AND activation_count = ?",
            self.flavor.set_modified_now(),
        );
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::I64(i64::from(command.expected_activation_count)),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                update_sql,
                vec![
                    DbTransactionParam::value(json_to_db_value(&command.output)),
                    DbTransactionParam::value(DbValue::from(command.error.as_deref())),
                    DbTransactionParam::value(DbValue::I64(now as i64)),
                    DbTransactionParam::value(self.env.as_str()),
                    DbTransactionParam::query_result(0, 0, "session_id"),
                    DbTransactionParam::value(i64::from(command.expected_activation_count)),
                ],
            )),
        ];
        let event_plan = EventAppendTransactionPlan::build(
            &command.event,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| {
            ServiceError::InternalError(format!("prepare Session completion Event: {error}"))
        })?;
        steps.extend(event_plan.steps);
        self.db.transaction(steps).await.map_err(|error| {
            ServiceError::Conflict(format!(
                "Session '{}' changed during completion: {error}",
                command.session_id
            ))
        })?;
        Ok(Some(candidate))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        // TODO(phase-2): wrap SELECT + check + UPDATE + re-SELECT in a DbPlugin transaction once supported.
        let select_cols = self.select_cols();
        let select_sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &select_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        let row = rows
            .into_iter()
            .next()
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        let current = row_to_session(&row)?;

        // Validate state machine before mutating.
        can_reactivate(
            current.status,
            current.session_kind,
            current.callback_status.as_deref(),
        )
        .map_err(|msg| {
            if msg == "callback is still pending" {
                ServiceError::SessionCallbackPending(session_id.to_string())
            } else {
                ServiceError::SessionInvalidParams(format!("{session_id}: {msg}"))
            }
        })?;

        // Use new_input if provided; otherwise preserve the existing input.
        let new_input_value = match &new_input {
            Some(j) => DbValue::String(j.to_string()),
            None => match &current.input {
                Some(j) => DbValue::String(j.to_string()),
                None => DbValue::Null,
            },
        };

        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET status = 'running', output = NULL, error_message = NULL, \
                 callback_status = 'pending', input = ?, \
                 callback_lease_owner = NULL, callback_lease_token = 0, \
                 callback_lease_until_ms = NULL, \
                 activation_count = activation_count + 1, \
                 completed_at = NULL, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    new_input_value,
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        // Re-SELECT to return the updated state.
        let rows = self
            .db
            .query(DbStatement::with_params(
                &select_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        rows.into_iter()
            .next()
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))
            .and_then(|r| row_to_session(&r))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()> {
        let sql = format!(
            "UPDATE bcs_group_sessions \
             SET callback_status = ?, callback_lease_owner = NULL, \
                 callback_lease_until_ms = NULL, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(status),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        Ok(())
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_claim_callback(
        &self,
        command: ClaimSessionCallback,
    ) -> ServiceResult<Option<SessionCallbackClaim>> {
        let sql = format!(
            "UPDATE bcs_group_sessions \
             SET callback_lease_owner = ?, \
                 callback_lease_token = callback_lease_token + 1, \
                 callback_lease_until_ms = ?, {} \
             WHERE env = ? AND session_id = ? \
               AND status = 'completed' AND session_kind = 'service_invocation' \
               AND activation_count = ? AND callback_status = 'pending' \
               AND callback_lease_token IS NOT NULL \
               AND (callback_lease_until_ms IS NULL OR callback_lease_until_ms <= ?)",
            self.flavor.set_modified_now(),
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(command.lease_owner.as_str()),
                    DbValue::from(command.lease_until_ms),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::I64(i64::from(command.expected_activation_count)),
                    DbValue::from(command.now_ms),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session callback claim: {e}")))?;
        if result.affected_rows == 0 {
            return Ok(None);
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT callback_lease_token FROM bcs_group_sessions \
                 WHERE env = ? AND session_id = ? AND activation_count = ? \
                   AND callback_lease_owner = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::I64(i64::from(command.expected_activation_count)),
                    DbValue::from(command.lease_owner.as_str()),
                ],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("session callback claim read: {e}"))
            })?;
        let row = rows.into_iter().next().ok_or_else(|| {
            ServiceError::InternalError(
                "Session callback claim disappeared before token read".to_string(),
            )
        })?;
        let lease_token: i64 = db_get_column(&row, "callback_lease_token")
            .map_err(|e| ServiceError::InternalError(format!("callback lease token: {e}")))?;
        Ok(Some(SessionCallbackClaim { lease_token }))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_complete_callback(&self, command: CompleteSessionCallback) -> ServiceResult<bool> {
        if !matches!(
            command.terminal_status.as_str(),
            "succeeded" | "partial_failed" | "failed" | "not_applicable"
        ) {
            return Err(ServiceError::SessionInvalidParams(format!(
                "invalid terminal callback status: {}",
                command.terminal_status
            )));
        }
        let sql = format!(
            "UPDATE bcs_group_sessions \
             SET callback_status = ?, callback_lease_owner = NULL, \
                 callback_lease_until_ms = NULL, {} \
             WHERE env = ? AND session_id = ? \
               AND status = 'completed' AND session_kind = 'service_invocation' \
               AND activation_count = ? AND callback_status = 'pending' \
               AND callback_lease_owner = ? AND callback_lease_token = ?",
            self.flavor.set_modified_now(),
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(command.terminal_status.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::I64(i64::from(command.expected_activation_count)),
                    DbValue::from(command.lease_owner.as_str()),
                    DbValue::I64(command.lease_token),
                ],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("session callback completion: {e}"))
            })?;
        Ok(result.affected_rows == 1)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> ServiceResult<Session> {
        let sql = format!(
            "UPDATE bcs_group_sessions \
             SET session_title = ?, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(title.as_deref()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        if result.affected_rows == 0 {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        }
        let select_cols = self.select_cols();
        let select_sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                &select_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        rows.into_iter()
            .next()
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))
            .and_then(|r| row_to_session(&r))
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session> {
        match self
            .try_list_by_group(
                group_id,
                status,
                offset,
                limit,
                title_contains,
                participant_id,
            )
            .await
        {
            Ok(sessions) => sessions,
            Err(error) => {
                tracing::warn!(%error, "list_by_group query failed");
                Vec::new()
            }
        }
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_try_list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> ServiceResult<Vec<Session>> {
        let mut conditions: Vec<String> =
            vec!["s.env = ?".to_string(), "s.group_id = ?".to_string()];
        let mut params: Vec<DbValue> =
            vec![DbValue::from(self.env.as_str()), DbValue::from(group_id)];

        if let Some(s) = status {
            let status_str = match s {
                SessionStatus::Running => "running",
                SessionStatus::Completed => "completed",
            };
            conditions.push("s.status = ?".to_string());
            params.push(DbValue::from(status_str));
        }

        if let Some(q) = title_contains {
            conditions.push("s.session_title LIKE ?".to_string());
            params.push(DbValue::from(format!("%{}%", q)));
        }

        let join_clause = if let Some(pid) = participant_id {
            conditions.push("sp.bot_uuid = ?".to_string());
            params.push(DbValue::from(pid));
            "JOIN bcs_session_participants sp ON sp.env = s.env AND sp.session_id = s.session_id"
        } else {
            ""
        };

        params.push(DbValue::U64(limit));
        params.push(DbValue::U64(offset));

        let select_cols = self.select_cols_prefixed();

        let sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions s {join} \
             WHERE {where_clause} \
             ORDER BY s.gmt_create DESC, s.id DESC LIMIT ? OFFSET ?",
            select_cols = select_cols,
            join = join_clause,
            where_clause = conditions.join(" AND "),
        );

        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "list sessions for Group '{group_id}': {error}"
                ))
            })?;
        rows.iter().map(row_to_session).collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_latest_running(&self, group_id: &str) -> Option<Session> {
        self.list_by_group(group_id, Some(SessionStatus::Running), 0, 1, None, None)
            .await
            .into_iter()
            .next()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_count_running_service(&self, group_id: &str) -> u64 {
        let sql = "SELECT COUNT(*) AS cnt FROM bcs_group_sessions \
                   WHERE env = ? AND group_id = ? AND session_kind = 'service_invocation' AND status = 'running'";
        let rows = match self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(group_id)],
            ))
            .await
        {
            Ok(r) => r,
            Err(_) => return 0,
        };
        rows.into_iter()
            .next()
            .and_then(|row| {
                db_get_column_opt::<i64>(&row, "cnt")
                    .ok()
                    .flatten()
                    .map(|v| v.max(0) as u64)
            })
            .unwrap_or(0)
    }
}

//! Persistence operations separated from the repository port forwarding.
use super::*;

impl MySqlSessionStore {
    pub(super) async fn repo_count_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> ServiceResult<u64> {
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

        let sql = format!(
            "SELECT COUNT(*) AS cnt FROM bcs_group_sessions s {join} \
             WHERE {where_clause}",
            join = join_clause,
            where_clause = conditions.join(" AND "),
        );

        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("count sessions for Group '{group_id}': {e}"))
            })?;
        let total = rows
            .into_iter()
            .next()
            .and_then(|row| {
                db_get_column_opt::<i64>(&row, "cnt")
                    .ok()
                    .flatten()
                    .map(|v| v.max(0) as u64)
            })
            .unwrap_or(0);
        Ok(total)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_list_running_service(&self, offset: u64, limit: u64) -> Vec<Session> {
        let select_cols = self.select_cols();
        let sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_kind = 'service_invocation' AND status = 'running' \
             ORDER BY gmt_create ASC LIMIT ? OFFSET ?"
        );
        let rows = match self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::U64(limit),
                    DbValue::U64(offset),
                ],
            ))
            .await
        {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        rows.iter().filter_map(|r| row_to_session(r).ok()).collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_list_running_service_after(
        &self,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> {
        if limit == 0 { return Ok(Vec::new()); }
        let select_cols = self.select_cols();
        let sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_kind = 'service_invocation' AND status = 'running' \
               AND session_id > ? ORDER BY session_id ASC LIMIT ?"
        );
        let rows = self.db.query(DbStatement::with_params(sql, vec![
            DbValue::from(self.env.as_str()), DbValue::from(after_session_id.unwrap_or("")),
            DbValue::U64(limit),
        ])).await.map_err(|error| ServiceError::InternalError(format!("Session recovery candidates: {error}")))?;
        rows.iter().map(row_to_session).collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_list_recoverable_callbacks(
        &self,
        now_ms: u64,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> {
        let select_cols = self.select_cols();
        let mut sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions \
             WHERE env = ? AND session_kind = 'service_invocation' \
             AND status = 'completed' AND callback_status = 'pending' \
             AND callback_lease_token IS NOT NULL \
             AND (callback_lease_until_ms IS NULL OR callback_lease_until_ms <= ?)"
        );
        let mut params = vec![DbValue::from(self.env.as_str()), DbValue::U64(now_ms)];
        if let Some(after_session_id) = after_session_id {
            sql.push_str(" AND session_id > ?");
            params.push(DbValue::from(after_session_id));
        }
        sql.push_str(" ORDER BY session_id ASC LIMIT ?");
        params.push(DbValue::U64(limit));

        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("list recoverable Session callbacks: {error}"))
            })?;
        rows.iter().map(row_to_session).collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session> {
        // TODO(phase-2): wrap SELECT + UPDATE + materialize INSERT in a DbPlugin transaction once supported.
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
        let mut current = row_to_session(&row)?;

        // Idempotent: if bot already in list, return current state unchanged.
        if current
            .participants
            .iter()
            .any(|p| p.bot_uuid == participant.bot_uuid)
        {
            return Ok(current);
        }
        let group_id = current.group_id.clone();
        let bot_uuid = participant.bot_uuid.clone();
        let role_str = participant_role_to_str(participant.role);

        // Record join_seq for new-participant visible message window.
        let join_seq = current.current_msg_seq;
        let mut join_map: serde_json::Map<String, serde_json::Value> = current
            .participant_join_seq
            .as_ref()
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        join_map.insert(
            bot_uuid.clone(),
            serde_json::Value::Number(serde_json::Number::from(join_seq)),
        );
        let join_seq_json = serde_json::Value::Object(join_map);
        let join_seq_str = join_seq_json.to_string();
        current.participant_join_seq = Some(join_seq_json);

        current.participants.push(participant);
        current.updated_at = current_millis();
        let new_json = serde_json::to_string(&current.participants)
            .map_err(|e| ServiceError::SessionInvalidParams(format!("participants: {e}")))?;

        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET participants = ?, participant_join_seq = ?, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    DbValue::from(new_json.as_str()),
                    DbValue::from(join_seq_str.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        // Materialized side-table: upsert presence row (idempotent).
        let upsert_sql = format!(
            "INSERT INTO bcs_session_participants \
             (env, session_id, group_id, bot_uuid, role, gmt_create) \
             VALUES (?, ?, ?, ?, ?, {}) \
             {}",
            self.flavor.now(),
            self.flavor
                .on_conflict_nothing(&["env", "session_id", "bot_uuid"]),
        );
        self.db
            .execute(DbStatement::with_params(
                &upsert_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(group_id.as_str()),
                    DbValue::from(bot_uuid.as_str()),
                    DbValue::from(role_str),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        info!(
            session_id = %session_id,
            bot_uuid = %bot_uuid,
            join_seq,
            "participant added with join_seq recorded"
        );
        Ok(current)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_add_participant_with_event(
        &self,
        command: AddSessionParticipantWithEvent,
    ) -> ServiceResult<Session> {
        let (mut candidate, stored_participants_json) = self
            .load_with_raw_participants(&command.session_id)
            .await?
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if candidate
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == command.participant.bot_uuid)
        {
            return Ok(candidate);
        }
        if serde_json::to_value(&candidate.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during addition",
                command.session_id
            )));
        }

        let bot_uuid = command.participant.bot_uuid.clone();
        let role = participant_role_to_str(command.participant.role);
        let mut join_map = candidate
            .participant_join_seq
            .as_ref()
            .and_then(serde_json::Value::as_object)
            .cloned()
            .unwrap_or_default();
        join_map.insert(
            bot_uuid.clone(),
            serde_json::json!(candidate.current_msg_seq),
        );
        let join_seq_json = serde_json::Value::Object(join_map);
        candidate.participant_join_seq = Some(join_seq_json.clone());
        candidate.participants.push(command.participant);
        candidate.updated_at = current_millis();
        validate_session_event_scope(&candidate, &command.event)?;

        let participants_json = serde_json::to_string(&candidate.participants)
            .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? AND participants = ?{}",
            transaction_lock_suffix(self.flavor),
        );
        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET participants = ?, participant_join_seq = ?, {} \
             WHERE env = ? AND session_id = ? AND participants = ?",
            self.flavor.set_modified_now(),
        );
        let upsert_sql = format!(
            "INSERT INTO bcs_session_participants \
             (env, session_id, group_id, bot_uuid, role, gmt_create) \
             VALUES (?, ?, ?, ?, ?, {}) {}",
            self.flavor.now(),
            self.flavor
                .on_conflict_nothing(&["env", "session_id", "bot_uuid"]),
        );
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::from(stored_participants_json.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                update_sql,
                vec![
                    DbTransactionParam::value(participants_json.as_str()),
                    DbTransactionParam::value(join_seq_json.to_string()),
                    DbTransactionParam::value(self.env.as_str()),
                    DbTransactionParam::query_result(0, 0, "session_id"),
                    DbTransactionParam::value(stored_participants_json.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                upsert_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::from(candidate.group_id.as_str()),
                    DbValue::from(bot_uuid.as_str()),
                    DbValue::from(role),
                ],
            )),
        ];
        let event_plan = EventAppendTransactionPlan::build(
            &command.event,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| {
            ServiceError::InternalError(format!("prepare Session participant Event: {error}"))
        })?;
        steps.extend(event_plan.steps);
        self.db.transaction(steps).await.map_err(|error| {
            if transaction_lock_row_is_missing(&error) {
                // The CAS lock row vanished: a concurrent writer changed or
                // deleted the session between the pre-check read and the lock.
                ServiceError::Conflict(format!(
                    "Session '{}' changed during participant addition",
                    command.session_id
                ))
            } else {
                ServiceError::InternalError(format!("session db: {error}"))
            }
        })?;
        Ok(candidate)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_remove_participant(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<Session> {
        // TODO(phase-2): wrap in a DbPlugin transaction once supported.
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
        let mut current = row_to_session(&row)?;

        let before = current.participants.len();
        current.participants.retain(|p| p.bot_uuid != bot_uuid);
        if current.participants.len() == before {
            return Err(ServiceError::SessionNotFound(format!(
                "participant {bot_uuid} not in session {session_id}"
            )));
        }
        current.updated_at = current_millis();
        let new_json = serde_json::to_string(&current.participants)
            .map_err(|e| ServiceError::SessionInvalidParams(format!("participants: {e}")))?;

        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET participants = ?, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    DbValue::from(new_json.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        self.db
            .execute(DbStatement::with_params(
                "DELETE FROM bcs_session_participants \
                 WHERE env = ? AND session_id = ? AND bot_uuid = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(bot_uuid),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        Ok(current)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_remove_participant_with_event(
        &self,
        command: RemoveSessionParticipantWithEvent,
    ) -> ServiceResult<Session> {
        let (mut candidate, stored_participants_json) = self
            .load_with_raw_participants(&command.session_id)
            .await?
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if serde_json::to_value(&candidate.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during removal",
                command.session_id
            )));
        }
        let before = candidate.participants.len();
        candidate
            .participants
            .retain(|participant| participant.bot_uuid != command.bot_uuid);
        if candidate.participants.len() == before {
            return Err(ServiceError::SessionInvalidParams(format!(
                "participant {} not in session {}",
                command.bot_uuid, command.session_id
            )));
        }
        candidate.updated_at = current_millis();
        validate_session_event_scope(&candidate, &command.event)?;

        let participants_json = serde_json::to_string(&candidate.participants)
            .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ? AND participants = ?{}",
            transaction_lock_suffix(self.flavor),
        );
        let update_sql = format!(
            "UPDATE bcs_group_sessions SET participants = ?, {} \
             WHERE env = ? AND session_id = ? AND participants = ?",
            self.flavor.set_modified_now(),
        );
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::from(stored_participants_json.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                update_sql,
                vec![
                    DbTransactionParam::value(participants_json.as_str()),
                    DbTransactionParam::value(self.env.as_str()),
                    DbTransactionParam::query_result(0, 0, "session_id"),
                    DbTransactionParam::value(stored_participants_json.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                "DELETE FROM bcs_session_participants \
                 WHERE env = ? AND session_id = ? AND bot_uuid = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.session_id.as_str()),
                    DbValue::from(command.bot_uuid.as_str()),
                ],
            )),
        ];
        let event_plan = EventAppendTransactionPlan::build(
            &command.event,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| {
            ServiceError::InternalError(format!("prepare Session participant Event: {error}"))
        })?;
        steps.extend(event_plan.steps);
        self.db.transaction(steps).await.map_err(|error| {
            if transaction_lock_row_is_missing(&error) {
                // The CAS lock row vanished: a concurrent writer changed or
                // deleted the session between the pre-check read and the lock.
                ServiceError::Conflict(format!(
                    "Session '{}' changed during participant removal",
                    command.session_id
                ))
            } else {
                ServiceError::InternalError(format!("session db: {error}"))
            }
        })?;
        Ok(candidate)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        // TODO(phase-2): wrap in a DbPlugin transaction once supported.
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
        let mut current = row_to_session(&row)?;

        let p = current
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == bot_uuid)
            .ok_or_else(|| {
                ServiceError::SessionNotFound(format!(
                    "participant {bot_uuid} not in session {session_id}"
                ))
            })?;
        p.mode = Some(mode);
        current.updated_at = current_millis();
        let new_json = serde_json::to_string(&current.participants)
            .map_err(|e| ServiceError::SessionInvalidParams(format!("participants: {e}")))?;

        let update_sql = format!(
            "UPDATE bcs_group_sessions \
             SET participants = ?, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    DbValue::from(new_json.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;

        // bcs_session_participants tracks presence only (not mode); no side-table update needed.
        Ok(current)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> {
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
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;
        let row = rows
            .into_iter()
            .next()
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        let mut current = row_to_session(&row)?;
        let participant = current
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {actor_id} not in session {session_id}"
                ))
            })?;
        if !message_view_scope.is_valid_for(participant.actor_kind) {
            return Err(ServiceError::SessionInvalidParams(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }
        participant.message_view_scope = message_view_scope;
        current.updated_at = current_millis();
        let participants_json = serde_json::to_string(&current.participants).map_err(|error| {
            ServiceError::SessionInvalidParams(format!("participants: {error}"))
        })?;
        let update_sql = format!(
            "UPDATE bcs_group_sessions SET participants = ?, {} \
             WHERE env = ? AND session_id = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    DbValue::from(participants_json.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;
        Ok(current)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> {
        let (mut current, stored_participants_json) = self
            .load_with_raw_participants(session_id)
            .await?
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        let participant = current
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {actor_id} not in session {session_id}"
                ))
            })?;
        if !message_view_scope.is_valid_for(participant.actor_kind)
            || mode.is_some_and(|mode| !mode.is_valid_for(participant.actor_kind))
        {
            return Err(ServiceError::SessionInvalidParams(
                "Participant mode or message_view_scope is invalid for the actor kind".to_string(),
            ));
        }
        participant.message_view_scope = message_view_scope;
        if let Some(mode) = mode {
            participant.mode = Some(mode);
        }
        current.updated_at = current_millis();
        let participants_json = serde_json::to_string(&current.participants)
            .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
        let update_sql = format!(
            "UPDATE bcs_group_sessions SET participants = ?, {} \
             WHERE env = ? AND session_id = ? AND participants = ?",
            self.flavor.set_modified_now(),
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                &update_sql,
                vec![
                    DbValue::from(participants_json.as_str()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(stored_participants_json.as_str()),
                ],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;
        if result.affected_rows != 1 {
            return Err(ServiceError::Conflict(format!(
                "Session '{session_id}' participants changed during message-view scope update"
            )));
        }
        Ok(current)
    }
}

//! Persistence operations separated from the repository port forwarding.
use super::*;

impl MySqlSessionStore {
    pub(super) async fn repo_update_participant_message_view_scope_with_event(
        &self,
        command: UpdateSessionParticipantMessageViewScopeWithEvent,
    ) -> ServiceResult<Session> {
        let (mut candidate, stored_participants_json) = self
            .load_with_raw_participants(&command.session_id)
            .await?
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if serde_json::to_value(&candidate.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during scope update",
                command.session_id
            )));
        }
        let participant = candidate
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == command.actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {} not in session {}",
                    command.actor_id, command.session_id
                ))
            })?;
        if !command
            .message_view_scope
            .is_valid_for(participant.actor_kind)
        {
            return Err(ServiceError::SessionInvalidParams(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }
        participant.message_view_scope = command.message_view_scope;
        if let Some(mode) = command.mode {
            if !mode.is_valid_for(participant.actor_kind) {
                return Err(ServiceError::SessionInvalidParams(
                    "Participant mode is invalid for the actor kind".to_string(),
                ));
            }
            participant.mode = Some(mode);
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
        ];
        let event_plan = EventAppendTransactionPlan::build(
            &command.event,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| {
            ServiceError::InternalError(format!("prepare Session participant scope Event: {error}"))
        })?;
        steps.extend(event_plan.steps);
        self.db.transaction(steps).await.map_err(|error| {
            if transaction_lock_row_is_missing(&error) {
                // The CAS lock row vanished: a concurrent writer changed or
                // deleted the session between the pre-check read and the lock.
                ServiceError::Conflict(format!(
                    "Session '{}' changed during participant scope update",
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
    pub(super) async fn repo_list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> {
        let sql = "SELECT DISTINCT group_id FROM bcs_session_participants \
                   WHERE env = ? AND bot_uuid = ?";
        let rows = match self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(bot_uuid)],
            ))
            .await
        {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        rows.iter()
            .filter_map(|row| db_get_column_opt::<String>(row, "group_id").ok().flatten())
            .collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_try_list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Vec<String>> {
        let sql = "SELECT DISTINCT group_id FROM bcs_session_participants \
                   WHERE env = ? AND bot_uuid = ?";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(bot_uuid)],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;

        rows.iter()
            .map(|row| {
                db_get_column::<String>(row, "group_id").map_err(|error| {
                    ServiceError::InternalError(format!("session db row: {error}"))
                })
            })
            .collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_delete(&self, session_id: &str) -> ServiceResult<bool> {
        let del_participants = "DELETE FROM bcs_session_participants \
                               WHERE env = ? AND session_id = ?";
        self.db
            .execute(DbStatement::with_params(
                del_participants,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;

        let del_session = "DELETE FROM bcs_group_sessions WHERE env = ? AND session_id = ?";
        let result = self
            .db
            .execute(DbStatement::with_params(
                del_session,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(format!("session db: {error}")))?;
        Ok(result.affected_rows > 0)
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_collect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        // Existence check via SELECT, NOT affected_rows: the MySQL connection does
        // not set CLIENT_FOUND_ROWS (see bcs-config-api/src/mysql.rs to_mysql_url and
        // bcs-db-mysql/src/manager.rs), so mysql_async reports CHANGED rows. A repeat
        // collect (collected already 1) would yield affected_rows=0 and falsely look
        // like a non-participant. SELECTing the side-table row first lets us
        // distinguish non-participant (row absent) from already-collected (row present)
        // independent of changed-rows semantics; the subsequent unconditional UPDATE is
        // then idempotent by construction.
        let check_sql = "SELECT 1 FROM bcs_session_participants \
                         WHERE env = ? AND session_id = ? AND bot_uuid = ? LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                check_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(bot_uuid),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        if rows.is_empty() {
            return Err(ServiceError::SessionNotFound(format!(
                "participant {bot_uuid} not in session {session_id}"
            )));
        }
        // First-collect-writes-time, repeat-collect-keeps-it (idempotent), expressed
        // via the NULL-ness of collected_at itself: COALESCE writes `now` only when
        // collected_at is NULL (never collected, or cleared by a prior uncollect) and
        // preserves the existing value otherwise. This is dialect-portable: do NOT
        // rewrite as `CASE WHEN collected = 0 THEN now ...` — MySQL evaluates a single
        // UPDATE's SET left-to-right (so `collected` is already 1 by the time the CASE
        // reads it) while SQLite evaluates all SET RHS against the pre-update row, so
        // the CASE form silently never sets collected_at on MySQL while working on
        // SQLite. Relying on collected_at's own NULL-ness avoids any cross-column
        // old-value dependency.
        let update_sql = format!(
            "UPDATE bcs_session_participants \
             SET collected = 1, \
                 collected_at = COALESCE(collected_at, {}) \
             WHERE env = ? AND session_id = ? AND bot_uuid = ?",
            self.flavor.now()
        );
        self.db
            .execute(DbStatement::with_params(
                update_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(bot_uuid),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        Ok(())
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_uncollect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        // Idempotent: the only caller-facing error is session-not-found, which
        // the application layer checks via get() before calling. Here we run the
        // UPDATE regardless of whether a side-table row / collected flag exists.
        // Clearing collected_at means a later re-collect records a fresh event time.
        let sql = "UPDATE bcs_session_participants \
                   SET collected = 0, collected_at = NULL \
                   WHERE env = ? AND session_id = ? AND bot_uuid = ?";
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(bot_uuid),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session db: {e}")))?;
        Ok(())
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        offset: u64,
        limit: u64,
    ) -> Vec<Session> {
        let mut conditions: Vec<String> = vec![
            "s.env = ?".to_string(),
            "s.group_id = ?".to_string(),
            "sp.group_id = ?".to_string(),
            "sp.bot_uuid = ?".to_string(),
            "sp.collected = 1".to_string(),
        ];
        let mut params: Vec<DbValue> = vec![
            DbValue::from(self.env.as_str()),
            DbValue::from(group_id),
            DbValue::from(group_id),
            DbValue::from(bot_uuid),
        ];

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

        params.push(DbValue::U64(limit));
        params.push(DbValue::U64(offset));

        // Collected-list-specific column list: base prefixed columns plus the
        // collect-event timestamp. We do NOT reuse select_cols_prefixed here
        // (it omits collected_at); and we CAST the timestamp to an integer so
        // MySQL's UNIX_TIMESTAMP(datetime(3)) — which returns a DOUBLE due to
        // fractional seconds — decodes cleanly to i64 instead of failing
        // row_to_session. SQLite's strftime already yields INTEGER.
        let collected_at_expr = match self.flavor {
            DbSqlFlavor::Mysql => format!("CAST((UNIX_TIMESTAMP(sp.collected_at))*1000 AS SIGNED)"),
            DbSqlFlavor::Sqlite => format!("CAST(strftime('%s', sp.collected_at) AS INTEGER)*1000"),
        };
        let select_cols = format!(
            "{}, {} AS collected_at_ms",
            self.select_cols_prefixed(),
            collected_at_expr,
        );
        let sql = format!(
            "SELECT {select_cols} FROM bcs_group_sessions s \
             JOIN bcs_session_participants sp \
               ON sp.env = s.env AND sp.session_id = s.session_id \
             WHERE {where_clause} \
             ORDER BY COALESCE(sp.collected_at, s.gmt_create) DESC, s.id DESC LIMIT ? OFFSET ?",
            select_cols = select_cols,
            where_clause = conditions.join(" AND "),
        );

        let rows = match self.db.query(DbStatement::with_params(&sql, params)).await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, "list_collected_by_group query failed");
                return Vec::new();
            }
        };
        rows.iter().filter_map(|r| row_to_session(r).ok()).collect()
    }
}

impl MySqlSessionStore {
    pub(super) async fn repo_collected_at_map(&self, session_ids: &[&str], bot_uuid: &str) -> Vec<(String, u64)> {
        if session_ids.is_empty() {
            return Vec::new();
        }
        // CAST the timestamp to an integer for the same reason as the
        // collected-list query: UNIX_TIMESTAMP(datetime(3)) is a DOUBLE on
        // MySQL and would not decode to i64. SQLite's strftime is already INTEGER.
        let collected_at_expr = match self.flavor {
            DbSqlFlavor::Mysql => "CAST((UNIX_TIMESTAMP(collected_at))*1000 AS SIGNED)",
            DbSqlFlavor::Sqlite => "CAST(strftime('%s', collected_at) AS INTEGER)*1000",
        };
        let placeholders = vec!["?"; session_ids.len()].join(", ");
        let sql = format!(
            "SELECT session_id, {ts} AS collected_at_ms FROM bcs_session_participants \
             WHERE env = ? AND bot_uuid = ? AND collected = 1 \
             AND session_id IN ({placeholders})",
            ts = collected_at_expr,
            placeholders = placeholders,
        );
        let mut params: Vec<DbValue> =
            vec![DbValue::from(self.env.as_str()), DbValue::from(bot_uuid)];
        for sid in session_ids {
            params.push(DbValue::from(*sid));
        }
        let rows = match self.db.query(DbStatement::with_params(&sql, params)).await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(error = %e, "collected_at_map query failed");
                return Vec::new();
            }
        };
        rows.iter()
            .filter_map(|r| {
                let sid: String = db_get_column(r, "session_id").ok()?;
                let ts: i64 = db_get_column_opt::<i64>(r, "collected_at_ms")
                    .ok()
                    .flatten()?;
                Some((sid, ts.max(0) as u64))
            })
            .collect()
    }
}

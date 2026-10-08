//! MySQL-backed `SessionRepoPort` implementation via `bcs-db-api`.
//!
//! Task 8a: helpers + `create` + `get` + `belongs_to_group`.
//! Task 8b: `complete_if_running`, `reactivate`, `update_callback_status`, `update_title`.
//! Task 8c: `list_by_group`, `latest_running`, `count_running_service`,
//!           `list_running_service`, `add_participant`, `remove_participant`,
//!           `update_participant_mode`, `list_group_ids_by_session_participant`.

#[path = "mysql_registry.rs"]
mod registry;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep,
    DbValue, db_get_column, db_get_column_opt,
};
use bcs_event_store::EventAppendTransactionPlan;
use tracing::info;

use bcs_service_api::core::session::{
    can_reactivate, new_channel_session_id, new_session_id, validate_session_id,
};
use bcs_service_api::port::repo::{
    AddSessionParticipantWithEvent, AppendEventRecord, ClaimSessionCallback,
    CompleteSessionCallback, CompleteSessionWithEvent, CreateSessionWithEvent, NewSessionParams,
    RemoveSessionParticipantWithEvent, SessionCallbackClaim, SessionRepoPort,
    UpdateSessionParticipantMessageViewScopeWithEvent,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    GroupSessionMetricCount, GroupSessionMetricsSnapshotPort, Participant, ParticipantMode,
    ServiceError, ServiceResult, Session, SessionKind, SessionStatus,
};

// ---------------------------------------------------------------------------
// SQL constants
// ---------------------------------------------------------------------------

const INSERT_SQL: &str = "INSERT INTO bcs_group_sessions \
    (session_id, group_id, session_title, env, status, session_kind, message_visibility_version, group_version, \
     caller_id, input, caller_principal, activation_count, \
     callback_status, callback_lease_token, callback_lease_owner, callback_lease_until_ms, \
     created_by, participants, completed_at, meta,
     current_msg_seq, participant_join_seq) \
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, NULL, ?, ?, NULL, ?, 0, NULL \
    WHERE EXISTS (SELECT 1 FROM bcs_session_registry WHERE env = ? AND session_id = ? AND session_type = 'group' AND current_msg_seq IS NULL)";

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// MySQL-backed session repository.
#[derive(Clone)]
pub struct MySqlSessionStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

impl MySqlSessionStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db,
            env,
            flavor: DbSqlFlavor::Mysql,
        }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db,
            env,
            flavor: DbSqlFlavor::Sqlite,
        }
    }

    /// Build the SELECT column list including flavor-aware timestamp expressions.
    fn select_cols(&self) -> String {
        format!(
            "session_id, group_id, session_title, group_version, env, status, \
             session_kind, message_visibility_version, caller_id, input, output, error_message, callback_status, \
             caller_principal, activation_count, created_by, participants, completed_at, meta, \
             current_msg_seq, participant_join_seq, \
             ({})*1000 AS gmt_create_ms, ({})*1000 AS gmt_modified_ms",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        )
    }

    /// Build the prefixed SELECT column list (table alias `s.`) for JOIN queries.
    fn select_cols_prefixed(&self) -> String {
        format!(
            "s.session_id, s.group_id, s.session_title, s.group_version, s.env, \
             s.status, s.session_kind, s.message_visibility_version, s.caller_id, s.input, s.output, s.error_message, \
             s.callback_status, s.caller_principal, s.activation_count, s.created_by, \
             s.participants, s.completed_at, s.meta, \
             s.current_msg_seq, s.participant_join_seq, \
             ({})*1000 AS gmt_create_ms, ({})*1000 AS gmt_modified_ms",
            self.flavor.unix_ts("s.gmt_create"),
            self.flavor.unix_ts("s.gmt_modified"),
        )
    }

    /// Internal: load a Session together with the raw stored `participants`
    /// TEXT bytes in a single read.
    ///
    /// The raw bytes are the CAS identity for scope-update optimistic locking.
    /// Comparing re-serialized JSON against stored TEXT breaks on schema
    /// evolution: a row written before a `Participant` field existed parses
    /// equal (serde defaults) but serializes differently, so a re-serialized
    /// expectation never matches the stored bytes and the CAS can never win.
    async fn load_with_raw_participants(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<(Session, String)>> {
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
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let raw = db_get_column_opt::<String>(&row, "participants")
            .map_err(|e| ServiceError::InternalError(format!("participants: {e}")))?
            .unwrap_or_default();
        let session = row_to_session(&row)?;
        Ok(Some((session, raw)))
    }

    /// Internal: execute the INSERT and return the constructed Session on success.
    /// Writes both the main session row and the `bcs_session_participants`
    /// side-table rows in a single transaction.
    async fn insert_session(
        &self,
        session_id: String,
        group_id: String,
        params: NewSessionParams,
        now: u64,
        event: Option<&AppendEventRecord>,
    ) -> ServiceResult<Session> {
        let session_kind = params.session_kind;
        let initial_cb = initial_callback_status(session_kind);
        let initial_callback_lease_token = if matches!(session_kind, SessionKind::ServiceInvocation)
        {
            DbValue::I64(0)
        } else {
            DbValue::Null
        };
        let participants_json = serde_json::to_string(&params.participants).map_err(|e| {
            ServiceError::SessionInvalidParams(format!("participants serialize: {e}"))
        })?;

        // Build the return value before the DB call so we don't need to re-query.
        let session_value = Session {
            id: session_id.clone(),
            group_id: group_id.clone(),
            session_title: params.session_title.clone(),
            env: Some(self.env.clone()),
            status: SessionStatus::Running,
            session_kind,
            message_visibility_version: params.message_visibility_version,
            participants: params.participants.clone(),
            group_version: params.group_version,
            caller_id: params.caller_id.clone(),
            input: params.input.clone(),
            output: None,
            error_message: None,
            callback_status: initial_cb.clone(),
            activation_count: 1,
            caller_principal: params.caller_principal.clone(),
            created_by: params.created_by.clone(),
            meta: params.meta.clone(),
            current_msg_seq: 0,
            participant_join_seq: None,
            created_at: now,
            updated_at: now,
            completed_at: None,
            collected_at: None,
        };

        let kind_str = kind_to_string(session_kind);
        let input_value = json_to_db_value(&params.input);
        let meta_value = json_to_db_value(&params.meta);
        let group_version_value = match params.group_version {
            Some(v) => DbValue::I64(i64::from(v)),
            None => DbValue::Null,
        };

        let env = self.env.clone();
        let participants_for_side_table = params.participants.clone();

        // The registry insert serializes cross-type claims. A mismatched existing
        // owner makes the conditional Group insert affect zero rows and rolls back.
        let insert_sql = INSERT_SQL;
        let mut steps: Vec<DbTransactionStep> = vec![
            DbTransactionStep::Execute(self.registry_claim(&session_id, bcs_service_api::port::repo::session_registry::SessionType::Group)),
            DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
                insert_sql,
                vec![
                    DbValue::from(session_id.as_str()),
                    DbValue::from(group_id.as_str()),
                    DbValue::from(params.session_title.as_deref()),
                    DbValue::from(env.as_str()),
                    DbValue::from("running"),
                    DbValue::from(kind_str),
                    DbValue::I64(i64::from(params.message_visibility_version)),
                    group_version_value,
                    DbValue::from(params.caller_id.as_deref()),
                    input_value,
                    DbValue::from(params.caller_principal.as_deref()),
                    DbValue::from(initial_cb.as_deref()),
                    initial_callback_lease_token,
                    DbValue::from(params.created_by.as_deref()),
                    DbValue::from(participants_json.as_str()),
                    meta_value,
                    env.as_str().into(), session_id.as_str().into(),
                ],
            ), expected_affected_rows: 1 }];

        // Same-transaction write to bcs_session_participants side table
        // so list_by_group JOIN queries can find participants set at creation.
        if let Some((sql, bind)) = build_session_participants_insert_sql(
            &session_id,
            &group_id,
            &env,
            &participants_for_side_table,
        ) {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                &sql, bind,
            )));
        }

        if let Some(event) = event {
            validate_session_event_scope(&session_value, event)?;
            let event_plan = EventAppendTransactionPlan::build(event, self.flavor, steps.len())
                .map_err(|error| {
                    ServiceError::InternalError(format!("prepare Session Event append: {error}"))
                })?;
            steps.extend(event_plan.steps);
        }

        self.db
            .transaction(steps)
            .await
            .map_err(|e| match e {
                DbError::ConditionFailed { .. } => ServiceError::Conflict("session_type_conflict".into()),
                other => ServiceError::InternalError(format!("session insert: {other}")),
            })?;

        Ok(session_value)
    }
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/// Serialize an `Option<serde_json::Value>` to a `DbValue` for SQL binding.
fn json_to_db_value(v: &Option<serde_json::Value>) -> DbValue {
    match v {
        None => DbValue::Null,
        Some(j) => DbValue::String(j.to_string()),
    }
}

fn validate_session_event_scope(session: &Session, event: &AppendEventRecord) -> ServiceResult<()> {
    if event.event.scope.group_id.as_deref() != Some(session.group_id.as_str())
        || event.event.scope.session_id.as_deref() != Some(session.id.as_str())
    {
        return Err(ServiceError::InvalidOperation {
            message: "Session Event scope does not match the mutated Session".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

fn transaction_lock_suffix(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => " FOR UPDATE",
        DbSqlFlavor::Sqlite => "",
    }
}

/// Whether a transaction failed because its CAS lock query (step 0) matched no
/// row, so a later step could not resolve its `query_result(0, 0, ..)` binding.
/// This means the guarded row changed or vanished concurrently — a retryable
/// conflict, not an internal error. Mirrors the bcs-group-store helper.
fn transaction_lock_row_is_missing(error: &DbError) -> bool {
    matches!(
        error,
        DbError::InvalidInput(message)
            if message.contains("references missing row 0 from step 0")
    )
}

/// Read a TEXT column from a row and parse it as JSON.
/// NULL or empty string → `Ok(None)`.
fn parse_json(col: &str, row: &DbRow) -> ServiceResult<Option<serde_json::Value>> {
    let raw: Option<String> = db_get_column_opt(row, col)
        .map_err(|e| ServiceError::InternalError(format!("column {col}: {e}")))?;
    match raw {
        None => Ok(None),
        Some(s) if s.is_empty() => Ok(None),
        Some(s) => serde_json::from_str(&s)
            .map(Some)
            .map_err(|e| ServiceError::InternalError(format!("parse json column {col}: {e}"))),
    }
}

/// Parse the `status` column string into `SessionStatus`.
fn parse_status(s: &str) -> ServiceResult<SessionStatus> {
    match s {
        "running" => Ok(SessionStatus::Running),
        "completed" => Ok(SessionStatus::Completed),
        other => Err(ServiceError::SessionInvalidParams(format!(
            "unknown session status: {other}"
        ))),
    }
}

/// Parse the `session_kind` column string into `SessionKind`.
fn parse_session_kind(s: &str) -> ServiceResult<SessionKind> {
    match s {
        "chat" => Ok(SessionKind::Chat),
        "service_invocation" => Ok(SessionKind::ServiceInvocation),
        other => Err(ServiceError::SessionInvalidParams(format!(
            "unknown session_kind: {other}"
        ))),
    }
}

fn participant_role_to_str(role: bcs_service_api::ParticipantRole) -> &'static str {
    match role {
        bcs_service_api::ParticipantRole::Driver => "driver",
        bcs_service_api::ParticipantRole::Consultant => "consultant",
        bcs_service_api::ParticipantRole::Manager => "manager",
        bcs_service_api::ParticipantRole::Worker => "worker",
        bcs_service_api::ParticipantRole::Observer => "observer",
    }
}

/// Build a multi-row INSERT for the `bcs_session_participants` side table.
/// Returns `None` when `participants` is empty (no rows to write).
fn build_session_participants_insert_sql(
    session_id: &str,
    group_id: &str,
    env: &str,
    participants: &[Participant],
) -> Option<(String, Vec<DbValue>)> {
    if participants.is_empty() {
        return None;
    }
    let placeholders = participants
        .iter()
        .map(|_| "(?, ?, ?, ?, ?)")
        .collect::<Vec<_>>()
        .join(", ");
    let mut binds: Vec<DbValue> = Vec::with_capacity(participants.len() * 5);
    for p in participants {
        binds.push(DbValue::from(session_id));
        binds.push(DbValue::from(group_id));
        binds.push(DbValue::from(p.bot_uuid.as_str()));
        binds.push(DbValue::from(participant_role_to_str(p.role)));
        binds.push(DbValue::from(env));
    }
    Some((
        format!(
            "INSERT INTO bcs_session_participants \
             (session_id, group_id, bot_uuid, role, env) VALUES {placeholders}"
        ),
        binds,
    ))
}

/// Deserialize the `participants` JSON column.
fn participants_from_json(s: &str) -> ServiceResult<Vec<Participant>> {
    serde_json::from_str(s)
        .map_err(|e| ServiceError::InternalError(format!("participants deserialize: {e}")))
}

/// Initial `callback_status` value for a new session.
fn initial_callback_status(kind: SessionKind) -> Option<String> {
    if matches!(kind, SessionKind::ServiceInvocation) {
        Some("pending".to_string())
    } else {
        None
    }
}

/// Canonical DB string for a `SessionKind`.
fn kind_to_string(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}

/// Current time in milliseconds since UNIX epoch.
fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Read a u64 timestamp column (stored as UNIX_TIMESTAMP*1000 alias).
/// Falls back to i64 → u64 for backends that return signed integers.
fn column_u64(row: &DbRow, name: &str) -> u64 {
    // Try u64 first.
    if let Ok(Some(v)) = db_get_column_opt::<i64>(row, name) {
        return v.max(0) as u64;
    }
    0
}

/// Convert a full DB row (using `select_cols()`) into a `Session`.
fn row_to_session(row: &DbRow) -> ServiceResult<Session> {
    let id: String = db_get_column(row, "session_id")
        .map_err(|e| ServiceError::InternalError(format!("session_id: {e}")))?;
    let group_id: String = db_get_column(row, "group_id")
        .map_err(|e| ServiceError::InternalError(format!("group_id: {e}")))?;

    let status_raw: String = db_get_column_opt(row, "status")
        .map_err(|e| ServiceError::InternalError(format!("status: {e}")))?
        .unwrap_or_else(|| "running".to_string());
    let status = parse_status(&status_raw)?;

    let kind_raw: String = db_get_column_opt(row, "session_kind")
        .map_err(|e| ServiceError::InternalError(format!("session_kind: {e}")))?
        .unwrap_or_else(|| "chat".to_string());
    let session_kind = parse_session_kind(&kind_raw)?;
    let message_visibility_version = db_get_column_opt::<i64>(row, "message_visibility_version")
        .map_err(|e| ServiceError::InternalError(format!("message_visibility_version: {e}")))?
        .unwrap_or(0);
    let message_visibility_version = u8::try_from(message_visibility_version)
        .ok()
        .filter(|version| *version <= 1)
        .ok_or_else(|| {
            ServiceError::InternalError("invalid message_visibility_version".to_string())
        })?;

    let participants_raw: Option<String> = db_get_column_opt(row, "participants")
        .map_err(|e| ServiceError::InternalError(format!("participants: {e}")))?;
    let participants = match participants_raw.as_deref() {
        None | Some("") => Vec::new(),
        Some(s) => participants_from_json(s)?,
    };

    let activation_count: i32 = db_get_column_opt::<i32>(row, "activation_count")
        .map_err(|e| ServiceError::InternalError(format!("activation_count: {e}")))?
        .unwrap_or(1);

    let group_version: Option<i32> = db_get_column_opt(row, "group_version")
        .map_err(|e| ServiceError::InternalError(format!("group_version: {e}")))?;

    let completed_at: Option<u64> = db_get_column_opt::<i64>(row, "completed_at")
        .map_err(|e| ServiceError::InternalError(format!("completed_at: {e}")))?
        .map(|v| v.max(0) as u64);

    Ok(Session {
        id,
        group_id,
        session_title: db_get_column_opt(row, "session_title")
            .map_err(|e| ServiceError::InternalError(format!("session_title: {e}")))?,
        env: db_get_column_opt(row, "env")
            .map_err(|e| ServiceError::InternalError(format!("env: {e}")))?,
        status,
        session_kind,
        message_visibility_version,
        participants,
        group_version,
        caller_id: db_get_column_opt(row, "caller_id")
            .map_err(|e| ServiceError::InternalError(format!("caller_id: {e}")))?,
        input: parse_json("input", row)?,
        output: parse_json("output", row)?,
        error_message: db_get_column_opt(row, "error_message")
            .map_err(|e| ServiceError::InternalError(format!("error_message: {e}")))?,
        callback_status: db_get_column_opt(row, "callback_status")
            .map_err(|e| ServiceError::InternalError(format!("callback_status: {e}")))?,
        activation_count,
        caller_principal: db_get_column_opt(row, "caller_principal")
            .map_err(|e| ServiceError::InternalError(format!("caller_principal: {e}")))?,
        created_by: db_get_column_opt(row, "created_by")
            .map_err(|e| ServiceError::InternalError(format!("created_by: {e}")))?,
        created_at: column_u64(row, "gmt_create_ms"),
        updated_at: column_u64(row, "gmt_modified_ms"),
        completed_at,
        // `collected_at_ms` is only selected by the collected-list query; other
        // queries omit the column entirely (db_get_column_opt → Ok(None)). Be
        // tolerant of decode failures too (e.g. a fractional datetime producing
        // a DOUBLE on some backends) so a non-integer value never fails the
        // whole row — collected_at is best-effort, not load-bearing.
        collected_at: db_get_column_opt::<i64>(row, "collected_at_ms")
            .ok()
            .flatten()
            .map(|v| v.max(0) as u64),
        meta: parse_json("meta", row)?,
        current_msg_seq: db_get_column_opt::<i64>(row, "current_msg_seq")
            .map_err(|e| ServiceError::InternalError(format!("current_msg_seq: {e}")))?
            .unwrap_or(0),
        participant_join_seq: parse_json("participant_join_seq", row)?,
    })
}

#[async_trait]
impl GroupSessionMetricsSnapshotPort for MySqlSessionStore {
    async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>> {
        let sql = "SELECT status, session_kind, COUNT(*) AS session_count \
                   FROM bcs_group_sessions \
                   WHERE env = ? \
                   GROUP BY status, session_kind";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!(
                    "group session metrics snapshot query failed: {e}"
                ))
            })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group session metrics status conversion failed: {e}"
                ))
            })?;
            let session_kind_raw: String = db_get_column(&row, "session_kind").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group session metrics kind conversion failed: {e}"
                ))
            })?;
            let session_count: i64 = db_get_column(&row, "session_count").map_err(|e| {
                ServiceError::InternalError(format!(
                    "group session metrics count conversion failed: {e}"
                ))
            })?;
            let count = u64::try_from(session_count).map_err(|e| {
                ServiceError::InternalError(format!("group session metrics count is invalid: {e}"))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(GroupSessionMetricCount {
                status: parse_status(&status_raw)?,
                session_kind: parse_session_kind(&session_kind_raw)?,
                count,
            });
        }

        Ok(counts)
    }
}

// ---------------------------------------------------------------------------
// SessionRepoPort impl
// ---------------------------------------------------------------------------

#[async_trait]
impl SessionRepoPort for MySqlSessionStore {
    async fn validate_session_registry(&self) -> ServiceResult<()> { self.repo_validate_session_registry().await }
    async fn ensure_direct_session(&self, id: &str) -> ServiceResult<bcs_service_api::port::repo::session_registry::SessionRegistration> { self.repo_ensure_direct_session(id).await }
    async fn session_registration(&self, id: &str) -> ServiceResult<Option<bcs_service_api::port::repo::session_registry::SessionRegistration>> { self.repo_session_registration(id).await }

    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> { self.repo_create(group_id, params).await }

    async fn create_with_event(&self, command: CreateSessionWithEvent) -> ServiceResult<Session> { self.repo_create_with_event(command).await }

    async fn create_channel(
        &self,
        group_id: &str,
        channel_type: &str,
        params: NewSessionParams,
    ) -> ServiceResult<Session> { self.repo_create_channel(group_id, channel_type, params).await }

    async fn get(&self, session_id: &str) -> Option<Session> { self.repo_get(session_id).await }

    async fn try_get(&self, session_id: &str) -> ServiceResult<Option<Session>> { self.repo_try_get(session_id).await }

    async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool { self.repo_belongs_to_group(session_id, group_id).await }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> { self.repo_complete_if_running(session_id, output, error).await }

    async fn complete_running_service_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> { self.repo_complete_running_service_activation(session_id, expected_activation_count, output, error).await }

    async fn complete_if_running_with_event(
        &self,
        command: CompleteSessionWithEvent,
    ) -> ServiceResult<Option<Session>> { self.repo_complete_if_running_with_event(command).await }

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> { self.repo_reactivate(session_id, new_input).await }

    async fn update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()> { self.repo_update_callback_status(session_id, status).await }

    async fn claim_callback(
        &self,
        command: ClaimSessionCallback,
    ) -> ServiceResult<Option<SessionCallbackClaim>> { self.repo_claim_callback(command).await }

    async fn complete_callback(&self, command: CompleteSessionCallback) -> ServiceResult<bool> { self.repo_complete_callback(command).await }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> ServiceResult<Session> { self.repo_update_title(session_id, title).await }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session> { self.repo_list_by_group(group_id, status, offset, limit, title_contains, participant_id).await }

    async fn try_list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> ServiceResult<Vec<Session>> { self.repo_try_list_by_group(group_id, status, offset, limit, title_contains, participant_id).await }

    async fn latest_running(&self, group_id: &str) -> Option<Session> { self.repo_latest_running(group_id).await }

    async fn count_running_service(&self, group_id: &str) -> u64 { self.repo_count_running_service(group_id).await }

    /// Mirrors [`SessionRepoPort::try_list_by_group`] filter conditions exactly
    /// (env + group_id + optional status / title_contains / participant_id
    /// JOIN) but runs `SELECT COUNT(*)` without LIMIT/OFFSET. Used by the V1
    /// session list endpoint to compute `total`.
    ///
    /// Propagates DB failures as `ServiceResult::Err` rather than silently
    /// returning `0`, so a nonempty page never pairs with `total=0`.
    async fn count_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> ServiceResult<u64> { self.repo_count_by_group(group_id, status, title_contains, participant_id).await }

    async fn list_running_service(&self, offset: u64, limit: u64) -> Vec<Session> { self.repo_list_running_service(offset, limit).await }

    async fn list_running_service_after(
        &self,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> { self.repo_list_running_service_after(after_session_id, limit).await }

    async fn list_recoverable_callbacks(
        &self,
        now_ms: u64,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> { self.repo_list_recoverable_callbacks(now_ms, after_session_id, limit).await }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session> { self.repo_add_participant(session_id, participant).await }

    async fn add_participant_with_event(
        &self,
        command: AddSessionParticipantWithEvent,
    ) -> ServiceResult<Session> { self.repo_add_participant_with_event(command).await }

    async fn remove_participant(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<Session> { self.repo_remove_participant(session_id, bot_uuid).await }

    async fn remove_participant_with_event(
        &self,
        command: RemoveSessionParticipantWithEvent,
    ) -> ServiceResult<Session> { self.repo_remove_participant_with_event(command).await }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session> { self.repo_update_participant_mode(session_id, bot_uuid, mode).await }

    async fn update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> { self.repo_update_participant_message_view_scope(session_id, actor_id, message_view_scope).await }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> { self.repo_update_participant_mode_and_message_view_scope(session_id, actor_id, mode, message_view_scope).await }

    async fn update_participant_message_view_scope_with_event(
        &self,
        command: UpdateSessionParticipantMessageViewScopeWithEvent,
    ) -> ServiceResult<Session> { self.repo_update_participant_message_view_scope_with_event(command).await }

    async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> { self.repo_list_group_ids_by_session_participant(bot_uuid).await }

    async fn try_list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Vec<String>> { self.repo_try_list_group_ids_by_session_participant(bot_uuid).await }

    async fn delete(&self, session_id: &str) -> ServiceResult<bool> { self.repo_delete(session_id).await }

    async fn collect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> { self.repo_collect(session_id, bot_uuid).await }

    async fn uncollect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> { self.repo_uncollect(session_id, bot_uuid).await }

    async fn list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        offset: u64,
        limit: u64,
    ) -> Vec<Session> { self.repo_list_collected_by_group(group_id, bot_uuid, status, title_contains, offset, limit).await }

    async fn collected_at_map(&self, session_ids: &[&str], bot_uuid: &str) -> Vec<(String, u64)> { self.repo_collected_at_map(session_ids, bot_uuid).await }
}

#[path = "mysql_operations/operations_1.rs"]
mod mysql_operations_operations_1;

#[path = "mysql_operations/operations_2.rs"]
mod mysql_operations_operations_2;

#[path = "mysql_operations/operations_3.rs"]
mod mysql_operations_operations_3;

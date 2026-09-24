//! SQL identity claims. The unique key serializes Group/Direct creation races.
use super::*;
use bcs_db_api::DbTransactionStepResult;
use bcs_service_api::port::repo::session_registry::{SessionRegistration, SessionType, validate_direct_session_id};

impl MySqlSessionStore {
    pub(super) fn registry_claim(&self, id: &str, kind: SessionType) -> DbStatement {
        let suffix = match self.flavor {
            DbSqlFlavor::Mysql => " ON DUPLICATE KEY UPDATE id = id",
            DbSqlFlavor::Sqlite => " ON CONFLICT(env, session_id) DO NOTHING",
        };
        DbStatement::with_params(format!("INSERT INTO bcs_session_registry (env, session_id, session_type, current_msg_seq) VALUES (?, ?, ?, ?){suffix}"), vec![
            self.env.as_str().into(), id.into(),
            match kind { SessionType::Group => "group", SessionType::DirectA2a => "direct_a2a" }.into(),
            match kind { SessionType::Group => DbValue::Null, SessionType::DirectA2a => 0_i64.into() },
        ])
    }

    pub(super) async fn registry_ensure_direct(&self, id: &str) -> ServiceResult<SessionRegistration> {
        validate_direct_session_id(id)?;
        let result = self.db.transaction(vec![
            DbTransactionStep::Execute(self.registry_claim(id, SessionType::DirectA2a)),
            DbTransactionStep::Query(self.registry_query(id)),
        ]).await.map_err(|_| ServiceError::InternalError("session registry claim failed".into()))?;
        let Some(DbTransactionStepResult::Rows(rows)) = result.get(1) else {
            return Err(ServiceError::InternalError("session registry claim result missing".into()));
        };
        let row = decode(rows.first().ok_or_else(|| ServiceError::InternalError("session registry claim missing".into()))?)?;
        crate::registry::check_type(&row, SessionType::DirectA2a)?;
        Ok(row)
    }

    fn registry_query(&self, id: &str) -> DbStatement {
        DbStatement::with_params("SELECT session_id, session_type, current_msg_seq FROM bcs_session_registry WHERE env = ? AND session_id = ?", vec![self.env.as_str().into(), id.into()])
    }

    pub(super) async fn registry_get(&self, id: &str) -> ServiceResult<Option<SessionRegistration>> {
        // Use a transaction read so a just-created identity is not read from a replica.
        let result = self.db.transaction(vec![DbTransactionStep::Query(self.registry_query(id))]).await
            .map_err(|_| ServiceError::InternalError("session registry read failed".into()))?;
        let Some(DbTransactionStepResult::Rows(rows)) = result.first() else {
            return Err(ServiceError::InternalError("session registry read result missing".into()));
        };
        rows.first().map(decode).transpose()
    }
}

fn decode(row: &DbRow) -> ServiceResult<SessionRegistration> {
    let error = |_| ServiceError::InternalError("session_registry_invalid".into());
    let kind: String = db_get_column(row, "session_type").map_err(error)?;
    let session_type = match kind.as_str() {
        "group" => SessionType::Group,
        "direct_a2a" => SessionType::DirectA2a,
        _ => return Err(ServiceError::InternalError("session_registry_invalid".into())),
    };
    let registration = SessionRegistration {
        session_id: db_get_column(row, "session_id").map_err(error)?, session_type,
        current_msg_seq: db_get_column_opt(row, "current_msg_seq").map_err(error)?,
    };
    crate::registry::check_type(&registration, session_type)?;
    Ok(registration)
}


impl MySqlSessionStore {
    pub(super) async fn registry_validate(&self) -> ServiceResult<()> {
        let rows = self.db.transaction(vec![DbTransactionStep::Query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM (SELECT g.session_id FROM bcs_group_sessions g LEFT JOIN bcs_session_registry r ON r.env = g.env AND r.session_id = g.session_id WHERE g.env = ? AND (r.session_id IS NULL OR r.session_type <> 'group' OR r.current_msg_seq IS NOT NULL) UNION ALL SELECT r.session_id FROM bcs_session_registry r WHERE r.env = ? AND (r.session_type NOT IN ('group', 'direct_a2a') OR (r.session_type = 'group' AND r.current_msg_seq IS NOT NULL) OR (r.session_type = 'direct_a2a' AND (r.current_msg_seq IS NULL OR r.current_msg_seq < 0)))) invalid_registry",
            vec![self.env.as_str().into(), self.env.as_str().into()]))]).await.map_err(|_| registry_error())?;
        let Some(DbTransactionStepResult::Rows(rows)) = rows.first() else { return Err(registry_error()); };
        let count: i64 = db_get_column(rows.first().ok_or_else(registry_error)?, "c").map_err(|_| registry_error())?;
        if count != 0 { return Err(registry_error()); }
        for (table, columns) in [("bcs_session_registry", "env,session_id"), ("bcs_group_sessions", "env,session_id"), ("bcs_messages", "env,session_id,session_seq")] {
            let sql = match self.flavor {
                DbSqlFlavor::Mysql => "SELECT COUNT(*) AS c FROM (SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND NON_UNIQUE = 0 GROUP BY INDEX_NAME HAVING GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') = ?) matching_index",
                DbSqlFlavor::Sqlite => "SELECT COUNT(*) AS c FROM pragma_index_list(?) il WHERE il.\"unique\" = 1 AND (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_index_info(il.name) ORDER BY seqno)) = ?",
            };
            let result = self.db.transaction(vec![DbTransactionStep::Query(DbStatement::with_params(sql, vec![table.into(), columns.into()]))]).await.map_err(|_| registry_error())?;
            let Some(DbTransactionStepResult::Rows(rows)) = result.first() else { return Err(registry_error()); };
            let count: i64 = db_get_column(rows.first().ok_or_else(registry_error)?, "c").map_err(|_| registry_error())?;
            if count < 1 { return Err(registry_error()); }
        }
        self.db.transaction(vec![DbTransactionStep::Query(DbStatement::with_params("SELECT delivery_id, source_message_id FROM bcs_chat_runs LIMIT 0", vec![]))]).await.map_err(|_| registry_error())?;
        Ok(())
    }
}

fn registry_error() -> ServiceError { ServiceError::InternalError("session_registry_invalid".into()) }

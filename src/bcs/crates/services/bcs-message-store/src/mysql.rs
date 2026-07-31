//! MySQL-backed `MessageRepoPort` implementation via `bcs-db-api`.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbTransactionStep, DbValue, db_get_column};
use tracing::{debug, info};

use bcs_domain::{
    MessageOwnerFilter, MessagePage, MessageQuery, NewMessage, PersistedMessage, PersistedMessageStatus, SenderType,
};
use bcs_service_api::port::repo::{MessageRepoError, MessageRepoPort};
use bcs_service_api::{ServiceError, ServiceResult};

// ---------------------------------------------------------------------------
// SQL constants
// ---------------------------------------------------------------------------

const SELECT_COLS: &str = "message_id, group_id, session_id, session_seq, env, \
    sender_id, sender_type, message_type, content, client_msg_id, status, \
    owner_bot_id, created_at, run_id";

const INSERT_SQL: &str = "INSERT INTO bcs_messages \
    (message_id, group_id, session_id, session_seq, env, sender_id, sender_type, \
     message_type, content, client_msg_id, owner_bot_id, status, created_at, run_id) \
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?)";

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// MySQL-backed message repository.
#[derive(Clone)]
pub struct MySqlMessageStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

impl MySqlMessageStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Mysql }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Sqlite }
    }

    /// Backend label for logs ("mysql" / "sqlite"), so persistence logs reflect
    /// the actual store rather than always claiming "(mysql)".
    fn backend_label(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "mysql",
            DbSqlFlavor::Sqlite => "sqlite",
        }
    }
}

fn row_to_message(row: &bcs_db_api::DbRow) -> Result<PersistedMessage, MessageRepoError> {
    let content_str: String = db_get_column(row, "content")
        .map_err(|e| MessageRepoError::StorageError(format!("content: {}", e)))?;
    let content: serde_json::Value =
        serde_json::from_str(&content_str).unwrap_or(serde_json::Value::String(content_str));

    let sender_type_str: String = db_get_column(row, "sender_type")
        .map_err(|e| MessageRepoError::StorageError(format!("sender_type: {}", e)))?;
    let sender_type = match sender_type_str.as_str() {
        "bot" => SenderType::Bot,
        "human" => SenderType::Human,
        "system" => SenderType::System,
        other => {
            return Err(MessageRepoError::StorageError(format!(
                "unknown sender_type: {}",
                other
            )));
        }
    };

    let status_str: String = db_get_column(row, "status")
        .map_err(|e| MessageRepoError::StorageError(format!("status: {}", e)))?;
    let status = match status_str.as_str() {
        "normal" => PersistedMessageStatus::Normal,
        "recalled" => PersistedMessageStatus::Recalled,
        "deleted" => PersistedMessageStatus::Deleted,
        other => {
            return Err(MessageRepoError::StorageError(format!(
                "unknown status: {}",
                other
            )));
        }
    };

    let client_msg_id: Option<String> = row
        .get_string("client_msg_id")
        .map_err(|e| MessageRepoError::StorageError(format!("client_msg_id: {}", e)))?;
    let owner_bot_id: Option<String> = row
        .get_string("owner_bot_id")
        .map_err(|e| MessageRepoError::StorageError(format!("owner_bot_id: {}", e)))?;

    let created_at_i64: i64 = db_get_column(row, "created_at")
        .map_err(|e| MessageRepoError::StorageError(format!("created_at: {}", e)))?;

    let run_id: String = db_get_column(row, "run_id")
        .map_err(|e| MessageRepoError::StorageError(format!("run_id: {}", e)))?;

    Ok(PersistedMessage {
        message_id: db_get_column(row, "message_id")
            .map_err(|e| MessageRepoError::StorageError(format!("message_id: {}", e)))?,
        group_id: db_get_column(row, "group_id")
            .map_err(|e| MessageRepoError::StorageError(format!("group_id: {}", e)))?,
        session_id: db_get_column(row, "session_id")
            .map_err(|e| MessageRepoError::StorageError(format!("session_id: {}", e)))?,
        session_seq: db_get_column(row, "session_seq")
            .map_err(|e| MessageRepoError::StorageError(format!("session_seq: {}", e)))?,
        sender_id: db_get_column(row, "sender_id")
            .map_err(|e| MessageRepoError::StorageError(format!("sender_id: {}", e)))?,
        sender_type,
        message_type: db_get_column(row, "message_type")
            .map_err(|e| MessageRepoError::StorageError(format!("message_type: {}", e)))?,
        content,
        client_msg_id,
        owner_bot_id,
        status,
        created_at: created_at_i64 as u64,
        run_id,
    })
}

#[async_trait]
impl MessageRepoPort for MySqlMessageStore {
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let message_id = uuid::Uuid::new_v4().to_string();

        // Step 1: Idempotency check
        if let Some(ref client_msg_id) = msg.client_msg_id {
            let check_sql = "SELECT message_id, session_seq FROM bcs_messages \
                WHERE group_id = ? AND session_id = ? AND sender_id = ? AND client_msg_id = ?";
            let check_stmt = DbStatement::with_params(
                check_sql,
                vec![
                    DbValue::from(msg.group_id.clone()),
                    DbValue::from(msg.session_id.clone()),
                    DbValue::from(msg.sender_id.clone()),
                    DbValue::from(client_msg_id.clone()),
                ],
            );
            let rows = self
                .db
                .query(check_stmt)
                .await
                .map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
            if let Some(row) = rows.first() {
                let existing_id: String = db_get_column(row, "message_id")
                    .map_err(|e| {
                        MessageRepoError::StorageError(format!("message_id: {}", e))
                    })?;
                debug!(
                    message_id = %existing_id,
                    "idempotent duplicate detected, returning existing message"
                );
                // Fetch full message
                let get_sql = format!(
                    "SELECT {} FROM bcs_messages WHERE message_id = ?",
                    SELECT_COLS
                );
                let get_stmt =
                    DbStatement::with_params(&get_sql, vec![DbValue::from(existing_id)]);
                let existing = self
                    .db
                    .query(get_stmt)
                    .await
                    .map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
                if let Some(row) = existing.first() {
                    return row_to_message(row);
                }
            }
        }

        // Step 2: Atomic seq allocation via transaction
        let seq_update = DbStatement::with_params(
            "UPDATE bcs_group_sessions SET current_msg_seq = current_msg_seq + 1 WHERE session_id = ?",
            vec![DbValue::from(msg.session_id.clone())],
        );
        let seq_select = DbStatement::with_params(
            "SELECT current_msg_seq FROM bcs_group_sessions WHERE session_id = ?",
            vec![DbValue::from(msg.session_id.clone())],
        );

        let steps: Vec<DbTransactionStep> = vec![
            DbTransactionStep::Execute(seq_update),
            DbTransactionStep::Query(seq_select),
        ];

        let tx_results = self
            .db
            .transaction(steps)
            .await
            .map_err(|e| MessageRepoError::StorageError(format!("transaction: {}", e)))?;

        let session_seq: i64 = match &tx_results[1] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => {
                let row = rows.first().ok_or_else(|| {
                    MessageRepoError::SessionNotFound(msg.session_id.clone())
                })?;
                db_get_column(row, "current_msg_seq").map_err(|e| {
                    MessageRepoError::StorageError(format!("seq: {}", e))
                })?
            }
            _ => {
                return Err(MessageRepoError::SessionNotFound(msg.session_id.clone()));
            }
        };

        // Step 3: INSERT the message
        let sender_type_str = match msg.sender_type {
            SenderType::Bot => "bot",
            SenderType::Human => "human",
            SenderType::System => "system",
        };
        let content_str = msg.content.to_string();

        let insert_stmt = DbStatement::with_params(
            INSERT_SQL,
            vec![
                DbValue::from(message_id.clone()),
                DbValue::from(msg.group_id.clone()),
                DbValue::from(msg.session_id.clone()),
                DbValue::from(session_seq),
                DbValue::from(self.env.clone()),
                DbValue::from(msg.sender_id.clone()),
                DbValue::from(sender_type_str),
                DbValue::from(msg.message_type.clone()),
                DbValue::from(content_str),
                DbValue::from(msg.client_msg_id.clone()),
                DbValue::from(msg.owner_bot_id.clone()),
                DbValue::from(msg.created_at),
                DbValue::from(msg.run_id.clone()),
            ],
        );

        self.db
            .execute(insert_stmt)
            .await
            .map_err(|e| MessageRepoError::StorageError(format!("insert: {}", e)))?;

        info!(
            session_id = %msg.session_id,
            message_id = %message_id,
            session_seq,
            backend = %self.backend_label(),
            "message persisted"
        );

        Ok(PersistedMessage {
            message_id,
            group_id: msg.group_id,
            session_id: msg.session_id,
            session_seq,
            sender_id: msg.sender_id,
            sender_type: msg.sender_type,
            message_type: msg.message_type,
            content: msg.content,
            client_msg_id: msg.client_msg_id,
            owner_bot_id: msg.owner_bot_id,
            status: PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id,
        })
    }

    async fn query_messages(
        &self,
        query: MessageQuery,
    ) -> Result<MessagePage, MessageRepoError> {
        let limit = query.limit as usize;

        let mut params: Vec<DbValue> = vec![
            DbValue::from(query.group_id.clone()),
            DbValue::from(query.session_id.clone()),
        ];
        let mut conditions = vec![
            "group_id = ?".to_string(),
            "session_id = ?".to_string(),
        ];

        if let Some(cursor) = query.cursor {
            conditions.push("created_at < ?".to_string());
            params.push(DbValue::from(cursor));
        }

        if let Some(ref sender_id) = query.sender_id {
            conditions.push("sender_id = ?".to_string());
            params.push(DbValue::from(sender_id.clone()));
        }

        if let Some(ref msg_type) = query.message_type {
            conditions.push("message_type = ?".to_string());
            params.push(DbValue::from(msg_type.clone()));
        }

        match &query.owner_filter {
            MessageOwnerFilter::Any => {}
            MessageOwnerFilter::IsNull => {
                conditions.push("owner_bot_id IS NULL".to_string());
            }
            MessageOwnerFilter::Eq(owner_bot_id) => {
                conditions.push("owner_bot_id = ?".to_string());
                params.push(DbValue::from(owner_bot_id.clone()));
            }
        }

        if let Some(ref keyword) = query.keyword {
            conditions.push("content LIKE ?".to_string());
            params.push(DbValue::from(format!("%{}%", keyword)));
        }

        if let Some((start, end)) = query.time_range {
            conditions.push("created_at >= ? AND created_at <= ?".to_string());
            params.push(DbValue::from(start));
            params.push(DbValue::from(end));
        }

        if let Some(visible_from) = query.visible_from_seq {
            conditions.push("session_seq >= ?".to_string());
            params.push(DbValue::from(visible_from));
        }

        // Fetch limit+1 to detect has_more
        let fetch_limit = (limit + 1) as u64;
        let sql = format!(
            "SELECT {} FROM bcs_messages WHERE {} ORDER BY created_at DESC, session_seq DESC LIMIT ?",
            SELECT_COLS,
            conditions.join(" AND ")
        );
        params.push(DbValue::from(fetch_limit));

        let stmt = DbStatement::with_params(&sql, params);
        let rows = self
            .db
            .query(stmt)
            .await
            .map_err(|e| MessageRepoError::StorageError(e.to_string()))?;

        let has_more = rows.len() > limit;
        let rows = if has_more { &rows[..limit] } else { &rows[..] };

        let mut messages = Vec::with_capacity(rows.len());
        for row in rows {
            messages.push(row_to_message(row)?);
        }

        let next_cursor = if has_more {
            messages.last().map(|m| m.created_at)
        } else {
            None
        };

        info!(
            group_id = %query.group_id,
            session_id = %query.session_id,
            count = messages.len(),
            has_more,
            "messages queried (mysql)"
        );
        Ok(MessagePage {
            messages,
            next_cursor,
            has_more,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        let sql = format!(
            "SELECT {} FROM bcs_messages WHERE message_id = ?",
            SELECT_COLS
        );
        let stmt = DbStatement::with_params(&sql, vec![DbValue::from(message_id.to_string())]);
        let rows = self
            .db
            .query(stmt)
            .await
            .map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
        if let Some(row) = rows.first() {
            Ok(Some(row_to_message(row)?))
        } else {
            Ok(None)
        }
    }

    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
        let stmt = DbStatement::with_params(
            "SELECT current_msg_seq FROM bcs_group_sessions WHERE session_id = ?",
            vec![DbValue::from(session_id.to_string())],
        );
        let rows = self
            .db
            .query(stmt)
            .await
            .map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
        if let Some(row) = rows.first() {
            let seq: i64 = db_get_column(row, "current_msg_seq")
                .map_err(|e| MessageRepoError::StorageError(format!("current_msg_seq: {}", e)))?;
            Ok(seq)
        } else {
            Ok(0)
        }
    }

    /// Direct-read session history with full visibility predicates + cursor
    /// pagination. Sort is the legacy `created_at DESC, session_seq DESC`
    /// (newest first); `before` is an exclusive `created_at` cursor.
    ///
    /// VUlao: filters reads by the store's own `env` so one env cannot leak
    /// another env's messages (matches the INSERT-time env tagging).
    async fn list_session_history(
        &self,
        session_id: &str,
        owner_filter: MessageOwnerFilter,
        visible_from_seq: Option<i64>,
        before: Option<u64>,
        limit: u32,
    ) -> ServiceResult<MessagePage> {
        let limit = limit as usize;

        let mut params: Vec<DbValue> = vec![
            DbValue::from(session_id.to_string()),
            DbValue::from(self.env.clone()),
        ];
        let mut conditions = vec![
            "session_id = ?".to_string(),
            "env = ?".to_string(),
        ];

        match &owner_filter {
            MessageOwnerFilter::Any => {}
            MessageOwnerFilter::IsNull => {
                conditions.push("owner_bot_id IS NULL".to_string());
            }
            MessageOwnerFilter::Eq(owner) => {
                conditions.push("owner_bot_id = ?".to_string());
                params.push(DbValue::from(owner.clone()));
            }
        }

        if let Some(visible_from) = visible_from_seq {
            conditions.push("session_seq >= ?".to_string());
            params.push(DbValue::from(visible_from));
        }

        if let Some(cursor) = before {
            conditions.push("created_at < ?".to_string());
            params.push(DbValue::from(cursor));
        }

        // Fetch limit+1 to detect has_more.
        let fetch_limit = (limit + 1) as u64;
        let sql = format!(
            "SELECT {SELECT_COLS} FROM bcs_messages WHERE {} ORDER BY created_at DESC, session_seq DESC LIMIT ?",
            conditions.join(" AND ")
        );
        params.push(DbValue::from(fetch_limit));

        let rows = self
            .db
            .query(DbStatement::with_params(&sql, params))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("list_session_history query: {e}"))
            })?;

        let has_more = rows.len() > limit;
        let rows = if has_more { &rows[..limit] } else { &rows[..] };

        let mut messages = Vec::with_capacity(rows.len());
        for row in rows {
            messages.push(row_to_message(row).map_err(|e| {
                ServiceError::InternalError(format!("list_session_history row: {e}"))
            })?);
        }

        let next_cursor = if has_more {
            messages.last().map(|m| m.created_at)
        } else {
            None
        };

        info!(
            session_id = %session_id,
            count = messages.len(),
            has_more,
            visible_from_seq = ?visible_from_seq,
            owner_filter = ?owner_filter,
            backend = %self.backend_label(),
            "session history listed"
        );
        Ok(MessagePage {
            messages,
            next_cursor,
            has_more,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::{collections::BTreeMap, sync::Arc};

    use bcs_db_api::{
        DbError, DbExecuteResult, DbHealth, DbResult, DbRow, DbTransactionStepResult,
    };
    use tokio::sync::Mutex;

    #[derive(Default)]
    struct CapturingDb {
        executed: Mutex<Vec<DbStatement>>,
    }

    #[async_trait]
    impl DbPlugin for CapturingDb {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Ok(Vec::new())
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.executed.lock().await.push(statement);
            Ok(DbExecuteResult::default())
        }

        async fn transaction(
            &self,
            steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            if steps.len() != 2 {
                return Err(DbError::InvalidInput(format!(
                    "unexpected transaction steps: {}",
                    steps.len()
                )));
            }
            let mut row = BTreeMap::new();
            row.insert("current_msg_seq".to_string(), DbValue::from(1_i64));
            Ok(vec![
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: 1,
                    last_insert_id: None,
                }),
                DbTransactionStepResult::Rows(vec![DbRow::new(row)]),
            ])
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    #[tokio::test]
    async fn append_message_binds_missing_client_msg_id_as_null() {
        let db = Arc::new(CapturingDb::default());
        let store = MySqlMessageStore::new(db.clone(), "dev".to_string());

        store
            .append_message(NewMessage {
                group_id: "group-1".to_string(),
                session_id: "group-1:session".to_string(),
                sender_id: "bot-worker".to_string(),
                sender_type: SenderType::Bot,
                message_type: "chat".to_string(),
                content: serde_json::json!("hello"),
                client_msg_id: None,
                owner_bot_id: Some("bot-worker".to_string()),
                created_at: 1,
                run_id: "run-1".to_string(),
            })
            .await
            .expect("append should succeed");

        let executed = db.executed.lock().await;
        let insert = executed.first().expect("expected insert statement");
        assert_eq!(insert.params().get(9), Some(&DbValue::Null));
        assert_eq!(
            insert.params().get(10),
            Some(&DbValue::from(Some("bot-worker".to_string())))
        );
    }
}

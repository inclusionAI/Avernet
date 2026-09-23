//! MySQL-backed `MessageRepoPort` implementation via `bcs-db-api`.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbPlugin, DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep, DbValue,
    db_get_column,
};
use bcs_event_store::EventAppendTransactionPlan;
use tracing::debug;

use bcs_domain::{
    HumanMessageView, MessageAudience, MessageOwnerFilter, MessagePage, MessageQuery, MessageVisibilityDomain,
    NewMessage, PersistedMessage, PersistedMessageStatus, SenderType,
};
use bcs_service_api::port::repo::{
    AppendMessageWithEvent, MessageRepoError, MessageRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

// ---------------------------------------------------------------------------
// SQL constants
// ---------------------------------------------------------------------------

#[path = "mysql_history_window.rs"]
pub(crate) mod history_window;

const SELECT_COLS: &str = "message_id, group_id, session_id, session_seq, env, \
    sender_id, sender_type, message_type, content, client_msg_id, status, \
    owner_bot_id, visibility_domain, audience_kind, audience_actor_ids_json, created_at, run_id";

const INSERT_SQL: &str = "INSERT INTO bcs_messages \
    (message_id, group_id, session_id, session_seq, env, sender_id, sender_type, \
     message_type, content, client_msg_id, owner_bot_id, status, created_at, run_id, \
     visibility_domain, audience_kind, audience_actor_ids_json) \
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?, ?, ?)";

fn worker_history_condition() -> &'static str {
    "(owner_bot_id = ? OR (owner_bot_id IS NULL AND sender_id = ? \
     AND message_type = 'chat' AND client_msg_id LIKE 'task-display:%' \
     AND visibility_domain = 'manager_worker' AND audience_kind = 'full_only'))"
}

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// MySQL-backed message repository.
#[derive(Clone)]
pub struct MySqlMessageStore {
    pub(crate) db: Arc<dyn DbPlugin>,
    pub(crate) env: String,
    pub(crate) flavor: DbSqlFlavor,
    pub(crate) delivery_writer: Arc<super::delivery::DeliveryWriterLocks>,
}

impl MySqlMessageStore {
    async fn append_with_identity(
        &self,
        msg: NewMessage,
        message_id: String,
        stable: bool,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let (visibility_domain, audience_kind, audience_actor_ids_json) =
            serialize_visibility(&msg)?;

        // Ordinary chat_error retries use their scoped identity, while Loop
        // publication retains the caller-owned stable message ID.
        let error_projection = !stable && msg.message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE;
        let message_id = if error_projection {
            use sha2::{Digest, Sha256};
            if msg.run_id.is_empty() {
                return Err(MessageRepoError::StorageError("chat_error requires run_id".into()));
            }
            let key = serde_json::json!(["chat_error", self.env, msg.group_id,
                msg.session_id, msg.sender_id, msg.run_id]).to_string();
            format!("{:x}", Sha256::digest(key.as_bytes()))
        } else { message_id };
        if error_projection {
            if let Some(existing) = self.get_message_by_id(&msg.session_id, &message_id).await? {
                return Ok(existing);
            }
        }

        // Step 1: Idempotency check
        if let Some(client_msg_id) = msg.client_msg_id.as_ref().filter(|_| !error_projection) {
            let check_sql = "SELECT message_id, session_seq FROM bcs_messages \
                WHERE env = ? AND group_id = ? AND session_id = ? AND sender_id = ? AND client_msg_id = ?";
            let check_stmt = DbStatement::with_params(
                check_sql,
                vec![
                    DbValue::from(self.env.as_str()),
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

        let sender_type_str = match msg.sender_type {
            SenderType::Bot => "bot",
            SenderType::Human => "human",
            SenderType::System => "system",
        };
        let content_str = msg.content.to_string();

        // Allocate the sequence and insert the logical message in one transaction.
        let seq_update = DbStatement::with_params(
            "UPDATE bcs_group_sessions SET current_msg_seq = current_msg_seq + 1 \
             WHERE env = ? AND session_id = ?",
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(msg.session_id.as_str()),
            ],
        );
        let seq_select = DbStatement::with_params(
            "SELECT current_msg_seq FROM bcs_group_sessions WHERE env = ? AND session_id = ?",
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(msg.session_id.as_str()),
            ],
        );
        let insert_stmt = DbStatement::with_transaction_params(
            INSERT_SQL,
            vec![
                DbTransactionParam::value(message_id.clone()),
                DbTransactionParam::value(msg.group_id.as_str()),
                DbTransactionParam::value(msg.session_id.as_str()),
                DbTransactionParam::query_result(1, 0, "current_msg_seq"),
                DbTransactionParam::value(self.env.as_str()),
                DbTransactionParam::value(msg.sender_id.as_str()),
                DbTransactionParam::value(sender_type_str),
                DbTransactionParam::value(msg.message_type.as_str()),
                DbTransactionParam::value(content_str),
                DbTransactionParam::value(DbValue::from(msg.client_msg_id.as_deref())),
                DbTransactionParam::value(DbValue::from(msg.owner_bot_id.as_deref())),
                DbTransactionParam::value(msg.created_at),
                DbTransactionParam::value(msg.run_id.as_str()),
                DbTransactionParam::value(visibility_domain),
                DbTransactionParam::value(DbValue::from(audience_kind)),
                DbTransactionParam::value(DbValue::from(audience_actor_ids_json.as_deref())),
            ],
        );

        let steps: Vec<DbTransactionStep> = vec![
            DbTransactionStep::Execute(seq_update),
            DbTransactionStep::Query(seq_select),
            DbTransactionStep::Execute(insert_stmt),
        ];

        let tx_results = match self.db.transaction(steps).await {
            Ok(results) => results,
            Err(error) => {
                if error_projection || (stable && error.is_duplicate_key()) {
                    let rows = self.db.query(DbStatement::with_params(
                        format!("SELECT {SELECT_COLS} FROM bcs_messages WHERE env = ? AND session_id = ? AND message_id = ?"),
                        vec![DbValue::from(self.env.as_str()), DbValue::from(msg.session_id.as_str()), DbValue::from(message_id.as_str())],
                    )).await.map_err(|read_error| MessageRepoError::StorageError(read_error.to_string()))?;
                    if let Some(row) = rows.first() { return row_to_message(row); }
                }
                return Err(MessageRepoError::StorageError(format!("transaction: {error}")));
            }
        };

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

        debug!(
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
            visibility_domain: Some(msg.visibility_domain),
            audience: msg.audience,
            status: PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id,
        })
    }

    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Mysql, delivery_writer: Default::default() }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env, flavor: DbSqlFlavor::Sqlite, delivery_writer: Default::default() }
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

#[path = "mysql_visibility.rs"]
mod visibility;
pub(crate) use visibility::serialize_visibility;
use visibility::row_to_message;

#[async_trait]
impl MessageRepoPort for MySqlMessageStore {
    async fn resolve_history_window_start(&self, session: &str, anchor: i64, limit: u64) -> Result<i64, MessageRepoError> {
        self.history_window_start(session, anchor, limit).await
    }
    async fn get_state_machine_messages_by_keys(
        &self, session: &str, keys: &[String],
    ) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let mut messages = std::collections::BTreeMap::new();
        for chunk in keys.chunks(200) {
            // Separate ID/client-key lookups; the latter uses existing session indexes.
            for column in ["message_id", "client_msg_id"] {
                let mut params = vec![self.env.as_str().into(), session.into()];
                params.extend(chunk.iter().map(|key| DbValue::from(key.as_str())));
                let sql = format!("SELECT {SELECT_COLS} FROM bcs_messages \
                    WHERE env = ? AND session_id = ? AND {column} IN ({}) \
                    AND message_type IN ('state_machine_panel', 'state_machine_human_input_prompt', \
                    'state_machine_human_input_response', 'state_machine_output') LIMIT 401",
                    vec!["?"; chunk.len()].join(","));
                let rows = self.db.query(DbStatement::with_params(sql, params)).await
                    .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
                if rows.len() > 400 {
                    return Err(MessageRepoError::StorageError("too many duplicate StateMachine producer keys".into()));
                }
                for row in rows {
                    let message = row_to_message(&row)?;
                    messages.insert(message.message_id.clone(), message);
                }
            }
        }
        let mut messages: Vec<_> = messages.into_values().collect();
        messages.sort_by_key(|message| message.session_seq);
        Ok(messages)
    }

    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT * FROM bcs_messages WHERE env = ? AND session_id = ? AND sender_id = ? AND run_id = ? AND message_type = 'chat' ORDER BY session_seq",
            vec![self.env.as_str().into(), session.into(), sender.into(), run.into()],
        )).await.map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
        rows.iter().map(row_to_message).collect()
    }
    fn delivery_repository(self: Arc<Self>) -> Option<Arc<dyn bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort>> {
        Some(self)
    }
    async fn append_message(&self, msg: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        self.append_with_identity(msg, uuid::Uuid::new_v4().to_string(), false).await
    }
    async fn append_message_with_id(&self, message_id: String, msg: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        if message_id.is_empty() || message_id.len() > 256 {
            return Err(MessageRepoError::StorageError("invalid stable message id".into()));
        }
        self.append_with_identity(msg, message_id, true).await
    }

    async fn append_message_with_event(
        &self,
        command: AppendMessageWithEvent,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let msg = command.message;
        let (visibility_domain, audience_kind, audience_actor_ids_json) =
            serialize_visibility(&msg)?;
        if command.event.event.subject.id != command.message_id
            || command.event.event.scope.group_id.as_deref() != Some(msg.group_id.as_str())
            || command.event.event.scope.session_id.as_deref() != Some(msg.session_id.as_str())
        {
            return Err(MessageRepoError::StorageError(
                "message Event does not match the persisted logical message".to_string(),
            ));
        }
        if let Some(client_msg_id) = msg.client_msg_id.as_deref() {
            let sql = format!(
                "SELECT {SELECT_COLS} FROM bcs_messages \
                 WHERE env = ? AND group_id = ? AND session_id = ? \
                   AND sender_id = ? AND client_msg_id = ? LIMIT 1"
            );
            let rows = self
                .db
                .query(DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(msg.group_id.as_str()),
                        DbValue::from(msg.session_id.as_str()),
                        DbValue::from(msg.sender_id.as_str()),
                        DbValue::from(client_msg_id),
                    ],
                ))
                .await
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
            if let Some(row) = rows.first() {
                return row_to_message(row);
            }
        }

        let sender_type = match msg.sender_type {
            SenderType::Bot => "bot",
            SenderType::Human => "human",
            SenderType::System => "system",
        };
        let mut steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_group_sessions SET current_msg_seq = current_msg_seq + 1 \
                 WHERE env = ? AND session_id = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(msg.session_id.as_str()),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                "SELECT current_msg_seq FROM bcs_group_sessions \
                 WHERE env = ? AND session_id = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(msg.session_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                INSERT_SQL,
                vec![
                    DbTransactionParam::value(command.message_id.as_str()),
                    DbTransactionParam::value(msg.group_id.as_str()),
                    DbTransactionParam::value(msg.session_id.as_str()),
                    DbTransactionParam::query_result(1, 0, "current_msg_seq"),
                    DbTransactionParam::value(self.env.as_str()),
                    DbTransactionParam::value(msg.sender_id.as_str()),
                    DbTransactionParam::value(sender_type),
                    DbTransactionParam::value(msg.message_type.as_str()),
                    DbTransactionParam::value(msg.content.to_string()),
                    DbTransactionParam::value(DbValue::from(msg.client_msg_id.as_deref())),
                    DbTransactionParam::value(DbValue::from(msg.owner_bot_id.as_deref())),
                    DbTransactionParam::value(msg.created_at),
                    DbTransactionParam::value(msg.run_id.as_str()),
                    DbTransactionParam::value(visibility_domain),
                    DbTransactionParam::value(DbValue::from(audience_kind)),
                    DbTransactionParam::value(DbValue::from(audience_actor_ids_json.as_deref())),
                ],
            )),
        ];
        let event_plan = EventAppendTransactionPlan::build(
            &command.event,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| {
            MessageRepoError::StorageError(format!("prepare message Event append: {error}"))
        })?;
        steps.extend(event_plan.steps);
        let results = self
            .db
            .transaction(steps)
            .await
            .map_err(|error| MessageRepoError::StorageError(format!("transaction: {error}")))?;
        let session_seq = match results.get(1) {
            Some(bcs_db_api::DbTransactionStepResult::Rows(rows)) => {
                let row = rows
                    .first()
                    .ok_or_else(|| MessageRepoError::SessionNotFound(msg.session_id.clone()))?;
                db_get_column(row, "current_msg_seq")
                    .map_err(|error| MessageRepoError::StorageError(error.to_string()))?
            }
            _ => return Err(MessageRepoError::SessionNotFound(msg.session_id.clone())),
        };
        Ok(PersistedMessage {
            message_id: command.message_id,
            group_id: msg.group_id,
            session_id: msg.session_id,
            session_seq,
            sender_id: msg.sender_id,
            sender_type: msg.sender_type,
            message_type: msg.message_type,
            content: msg.content,
            client_msg_id: msg.client_msg_id,
            owner_bot_id: msg.owner_bot_id,
            visibility_domain: Some(msg.visibility_domain),
            audience: msg.audience,
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
            DbValue::from(self.env.as_str()),
            DbValue::from(query.group_id.clone()),
            DbValue::from(query.session_id.clone()),
        ];
        let mut conditions = vec![
            "env = ?".to_string(),
            "group_id = ?".to_string(),
            "session_id = ?".to_string(),
            "message_type <> 'run_reply'".to_string(),
        ];

        if !query.human_view.as_ref().is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant) {
            conditions.push("message_type <> 'state_machine_human_input_prompt'".into());
        }

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
            MessageOwnerFilter::WorkerHistory(owner_bot_id) => {
                conditions.push(worker_history_condition().to_string());
                params.push(DbValue::from(owner_bot_id.clone()));
                params.push(DbValue::from(owner_bot_id.clone()));
            }
            MessageOwnerFilter::PublicOrOwner(owner_bot_id) => {
                conditions.push("(owner_bot_id IS NULL OR owner_bot_id = ?)".to_string());
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
            history_window::filter(&mut conditions, &mut params, visible_from);
        }

        if let Some(human_view) = query.human_view.as_ref()
            && human_view.scope == bcs_domain::MessageViewScope::Participant
        {
            let legacy_chat = if human_view.allow_legacy_unclassified_chat {
                " OR visibility_domain IS NULL"
            } else {
                ""
            };
            let directed_predicate = match self.flavor {
                DbSqlFlavor::Mysql => {
                    "JSON_CONTAINS(audience_actor_ids_json, JSON_QUOTE(?))"
                }
                DbSqlFlavor::Sqlite => {
                    "EXISTS (SELECT 1 FROM json_each(audience_actor_ids_json) WHERE value = ?)"
                }
            };
            conditions.push(format!(
                "(visibility_domain = 'chat'{legacy_chat} OR (visibility_domain IN ('manager_worker', 'state_machine') AND (audience_kind = 'public' OR (audience_kind = 'directed' AND {directed_predicate}))))"
            ));
            params.push(DbValue::from(human_view.actor_id.clone()));
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
            messages.push(super::history_projection(row_to_message(row)?));
        }

        let next_cursor = if has_more {
            messages.last().map(|m| (m.created_at, m.session_seq))
        } else {
            None
        };

        debug!(
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

    async fn list_state_machine_history(
        &self, group_id: &str, session_id: &str, human_view: Option<HumanMessageView>,
        before: Option<(u64, i64)>, limit: u32,
    ) -> Result<MessagePage, MessageRepoError> {
        if !(1..=1_000).contains(&limit) {
            return Err(MessageRepoError::StorageError("history limit must be between 1 and 1000".into()));
        }
        let mut conditions = vec!["env = ?", "group_id = ?", "session_id = ?",
            "message_type IN ('state_machine_panel', 'state_machine_output', 'state_machine_human_input_prompt', 'state_machine_human_input_response')"];
        let mut params: Vec<DbValue> = vec![self.env.as_str().into(), group_id.into(), session_id.into()];
        if let Some(view) = human_view.as_ref().filter(|v| v.scope == bcs_domain::MessageViewScope::Participant) {
            conditions.push(match self.flavor {
                DbSqlFlavor::Mysql => "visibility_domain = 'state_machine' AND (audience_kind = 'public' OR (audience_kind = 'directed' AND JSON_CONTAINS(audience_actor_ids_json, JSON_QUOTE(?))))",
                DbSqlFlavor::Sqlite => "visibility_domain = 'state_machine' AND (audience_kind = 'public' OR (audience_kind = 'directed' AND EXISTS (SELECT 1 FROM json_each(audience_actor_ids_json) WHERE value = ?)))",
            });
            params.push(view.actor_id.as_str().into());
        } else {
            conditions.push("message_type <> 'state_machine_human_input_prompt'");
        }
        if let Some((at, seq)) = before {
            conditions.push("(created_at < ? OR (created_at = ? AND session_seq < ?))");
            params.extend([at.into(), at.into(), seq.into()]);
        }
        params.push((u64::from(limit) + 1).into());
        let rows = self.db.query(DbStatement::with_params(format!(
            "SELECT {SELECT_COLS} FROM bcs_messages WHERE {} ORDER BY created_at DESC, session_seq DESC LIMIT ?", conditions.join(" AND ")
        ), params)).await.map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
        let has_more = rows.len() > limit as usize;
        let messages = rows.iter().take(limit as usize)
            .map(|row| row_to_message(row).map(super::history_projection))
            .collect::<Result<Vec<_>, _>>()?;
        let next_cursor = if has_more { messages.last().map(|m| (m.created_at, m.session_seq)) } else { None };
        Ok(MessagePage { messages, has_more, next_cursor })
    }

    async fn get_messages_by_ids(&self, session_id: &str, ids: &[String]) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let mut messages = Vec::new();
        for chunk in ids.chunks(200) {
            let mut params = vec![self.env.as_str().into(), session_id.into()];
            params.extend(chunk.iter().map(|id| DbValue::from(id.as_str())));
            let sql = format!("SELECT {SELECT_COLS} FROM bcs_messages WHERE env = ? AND session_id = ? AND message_id IN ({})", vec!["?"; chunk.len()].join(","));
            let rows = self.db.query(DbStatement::with_params(sql, params)).await.map_err(|e| MessageRepoError::StorageError(e.to_string()))?;
            for row in rows { messages.push(row_to_message(&row)?); }
        }
        Ok(messages)
    }
    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        let sql = format!(
            "SELECT {} FROM bcs_messages WHERE env = ? AND session_id = ? AND message_id = ?",
            SELECT_COLS
        );
        let stmt = DbStatement::with_params(&sql, vec![self.env.as_str().into(), session_id.into(), message_id.into()]);
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
    /// (newest first); `before` is an exclusive composite
    /// `(created_at, session_seq)` cursor so tied `created_at` rows are not
    /// skipped at a page boundary (VYQHI). SQL uses the
    /// `created_at < ? OR (created_at = ? AND session_seq < ?)` compound
    /// predicate because SQLite (used by the conformance test harness) does
    /// not support MySQL row-constructor comparison `(a, b) < (?, ?)`.
    ///
    /// VUlao: filters reads by the store's own `env` so one env cannot leak
    /// another env's messages (matches the INSERT-time env tagging).
    async fn list_session_history(
        &self,
        session_id: &str,
        owner_filter: MessageOwnerFilter,
        visible_from_seq: Option<i64>,
        human_view: Option<HumanMessageView>,
        before: Option<(u64, i64)>,
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
            "message_type <> 'run_reply'".to_string(),
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
            MessageOwnerFilter::WorkerHistory(owner) => {
                conditions.push(worker_history_condition().to_string());
                params.push(DbValue::from(owner.clone()));
                params.push(DbValue::from(owner.clone()));
            }
            MessageOwnerFilter::PublicOrOwner(owner) => {
                conditions.push("(owner_bot_id IS NULL OR owner_bot_id = ?)".to_string());
                params.push(DbValue::from(owner.clone()));
            }
        }

        if let Some(visible_from) = visible_from_seq {
            history_window::filter(&mut conditions, &mut params, visible_from);
        }

        if !human_view.as_ref().is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant) {
            conditions.push("message_type <> 'state_machine_human_input_prompt'".into());
        }

        if let Some(human_view) = human_view.as_ref()
            && human_view.scope == bcs_domain::MessageViewScope::Participant
        {
            let legacy_chat = if human_view.allow_legacy_unclassified_chat {
                " OR visibility_domain IS NULL"
            } else {
                ""
            };
            let directed_predicate = match self.flavor {
                DbSqlFlavor::Mysql => {
                    "JSON_CONTAINS(audience_actor_ids_json, JSON_QUOTE(?))"
                }
                DbSqlFlavor::Sqlite => {
                    "EXISTS (SELECT 1 FROM json_each(audience_actor_ids_json) WHERE value = ?)"
                }
            };
            conditions.push(format!(
                "(visibility_domain = 'chat'{legacy_chat} OR (visibility_domain IN ('manager_worker', 'state_machine') AND (audience_kind = 'public' OR (audience_kind = 'directed' AND {directed_predicate}))))"
            ));
            params.push(DbValue::from(human_view.actor_id.clone()));
        }

        // VYQHI: composite (created_at, session_seq) strict-less bound. The
        // compound `created_at < ? OR (created_at = ? AND session_seq < ?)`
        // is equivalent to the row-constructor `(created_at, session_seq) <
        // (?, ?)` and runs on both MySQL and SQLite.
        if let Some((cursor_ts, cursor_seq)) = before {
            conditions.push(
                "(created_at < ? OR (created_at = ? AND session_seq < ?))".to_string(),
            );
            params.push(DbValue::from(cursor_ts));
            params.push(DbValue::from(cursor_ts));
            params.push(DbValue::from(cursor_seq));
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
            messages.push(super::history_projection(row_to_message(row).map_err(|e| {
                ServiceError::InternalError(format!("list_session_history row: {e}"))
            })?));
        }

        let next_cursor = if has_more {
            messages.last().map(|m| (m.created_at, m.session_seq))
        } else {
            None
        };

        debug!(
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
#[path = "mysql_tests.rs"]
mod tests;

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

const SELECT_COLS: &str = "message_id, group_id, session_id, session_seq, env, \
    sender_id, sender_type, message_type, content, client_msg_id, status, \
    owner_bot_id, visibility_domain, audience_kind, audience_actor_ids_json, created_at, run_id";

const INSERT_SQL: &str = "INSERT INTO bcs_messages \
    (message_id, group_id, session_id, session_seq, env, sender_id, sender_type, \
     message_type, content, client_msg_id, owner_bot_id, status, created_at, run_id, \
     visibility_domain, audience_kind, audience_actor_ids_json) \
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?, ?, ?)";

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

fn visibility_domain_name(domain: MessageVisibilityDomain) -> &'static str {
    match domain {
        MessageVisibilityDomain::Chat => "chat",
        MessageVisibilityDomain::ManagerWorker => "manager_worker",
        MessageVisibilityDomain::StateMachine => "state_machine",
    }
}

pub(crate) fn serialize_visibility(
    msg: &NewMessage,
) -> Result<(&'static str, Option<&'static str>, Option<String>), MessageRepoError> {
    if matches!(
        msg.visibility_domain,
        MessageVisibilityDomain::ManagerWorker | MessageVisibilityDomain::StateMachine
    ) && msg.audience.is_none()
    {
        return Err(MessageRepoError::StorageError(
            "ManagerWorker/StateMachine messages require an audience".to_string(),
        ));
    }
    if let (Some(owner_bot_id), Some(audience)) = (msg.owner_bot_id.as_deref(), msg.audience.as_ref())
    {
        if !matches!(audience, MessageAudience::Directed { .. }) {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id requires a directed audience on classified messages".to_string(),
            ));
        }
        if !audience.contains(owner_bot_id) {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id must be included in the directed audience".to_string(),
            ));
        }
    }
    let (audience_kind, audience_actor_ids_json) = match &msg.audience {
        None => (None, None),
        Some(MessageAudience::Public) => (Some("public"), None),
        Some(MessageAudience::FullOnly) => (Some("full_only"), None),
        Some(MessageAudience::Directed { actor_ids }) => {
            msg.audience
                .as_ref()
                .expect("audience is present")
                .validate()
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
            let json = serde_json::to_string(actor_ids).map_err(|error| {
                MessageRepoError::StorageError(format!("serialize directed audience: {error}"))
            })?;
            (Some("directed"), Some(json))
        }
    };
    Ok((
        visibility_domain_name(msg.visibility_domain),
        audience_kind,
        audience_actor_ids_json,
    ))
}

fn parse_visibility_domain(
    raw: Option<&str>,
) -> Result<Option<MessageVisibilityDomain>, MessageRepoError> {
    match raw {
        None | Some("") => Ok(None),
        Some("chat") => Ok(Some(MessageVisibilityDomain::Chat)),
        Some("manager_worker") => Ok(Some(MessageVisibilityDomain::ManagerWorker)),
        Some("state_machine") => Ok(Some(MessageVisibilityDomain::StateMachine)),
        Some(other) => Err(MessageRepoError::StorageError(format!(
            "unknown visibility_domain: {other}"
        ))),
    }
}

fn parse_audience(
    kind: Option<&str>,
    actor_ids_json: Option<&str>,
) -> Result<Option<MessageAudience>, MessageRepoError> {
    match (kind, actor_ids_json.filter(|value| !value.is_empty())) {
        (None | Some(""), None) => Ok(None),
        (None | Some(""), Some(_)) => Err(MessageRepoError::StorageError(
            "audience actor ids exist without audience_kind".to_string(),
        )),
        (Some("public"), None) => Ok(Some(MessageAudience::Public)),
        (Some("full_only"), None) => Ok(Some(MessageAudience::FullOnly)),
        (Some("public" | "full_only"), Some(_)) => Err(MessageRepoError::StorageError(
            "non-directed audience must not contain actor ids".to_string(),
        )),
        (Some("directed"), Some(raw)) => {
            let actor_ids = serde_json::from_str::<Vec<String>>(raw).map_err(|error| {
                MessageRepoError::StorageError(format!("invalid directed audience JSON: {error}"))
            })?;
            MessageAudience::directed(actor_ids)
                .map(Some)
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))
        }
        (Some("directed"), None) => Err(MessageRepoError::StorageError(
            "directed audience requires actor ids".to_string(),
        )),
        (Some(other), _) => Err(MessageRepoError::StorageError(format!(
            "unknown audience_kind: {other}"
        ))),
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
    let visibility_domain = parse_visibility_domain(
        row.get_string("visibility_domain")
            .map_err(|e| MessageRepoError::StorageError(format!("visibility_domain: {e}")))?
            .as_deref(),
    )?;
    let audience = parse_audience(
        row.get_string("audience_kind")
            .map_err(|e| MessageRepoError::StorageError(format!("audience_kind: {e}")))?
            .as_deref(),
        row.get_string("audience_actor_ids_json")
            .map_err(|e| {
                MessageRepoError::StorageError(format!("audience_actor_ids_json: {e}"))
            })?
            .as_deref(),
    )?;

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
        visibility_domain,
        audience,
        status,
        created_at: created_at_i64 as u64,
        run_id,
    })
}

#[async_trait]
impl MessageRepoPort for MySqlMessageStore {
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
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let (visibility_domain, audience_kind, audience_actor_ids_json) =
            serialize_visibility(&msg)?;
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
            DbValue::from(query.group_id.clone()),
            DbValue::from(query.session_id.clone()),
        ];
        let mut conditions = vec![
            "group_id = ?".to_string(),
            "session_id = ?".to_string(),
            "message_type <> 'run_reply'".to_string(),
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
            conditions.push("session_seq >= ?".to_string());
            params.push(DbValue::from(visible_from));
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
            MessageOwnerFilter::PublicOrOwner(owner) => {
                conditions.push("(owner_bot_id IS NULL OR owner_bot_id = ?)".to_string());
                params.push(DbValue::from(owner.clone()));
            }
        }

        if let Some(visible_from) = visible_from_seq {
            conditions.push("session_seq >= ?".to_string());
            params.push(DbValue::from(visible_from));
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
            if steps.len() != 3 {
                return Err(DbError::InvalidInput(format!(
                    "unexpected transaction steps: {}",
                    steps.len()
                )));
            }
            let mut executed = self.executed.lock().await;
            for step in &steps {
                if let DbTransactionStep::Execute(statement) = step {
                    executed.push(statement.clone());
                }
            }
            let mut row = BTreeMap::new();
            row.insert("current_msg_seq".to_string(), DbValue::from(1_i64));
            Ok(vec![
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: 1,
                    last_insert_id: None,
                }),
                DbTransactionStepResult::Rows(vec![DbRow::new(row)]),
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: 1,
                    last_insert_id: None,
                }),
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
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
            })
            .await
            .expect("append should succeed");

        let executed = db.executed.lock().await;
        let insert = executed
            .iter()
            .find(|statement| statement.sql().contains("INSERT INTO bcs_messages"))
            .expect("expected insert statement");
        assert_eq!(insert.params().get(9), Some(&DbValue::Null));
        assert_eq!(
            insert.params().get(10),
            Some(&DbValue::from(Some("bot-worker".to_string())))
        );
    }
}

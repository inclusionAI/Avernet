//! MySQL-backed `SessionFileRepoPort` implementation via `bcs-db-api`.
//!
//! Uses the same `DbStatement::with_params` pattern as
//! `bcs-session-store/src/mysql.rs`. SQL is written to a common subset
//! supported by both local SQLite and MySQL-compatible backends.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_db_api::{
    DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult,
    DbValue, db_get_column, db_get_column_opt,
};
use bcs_domain::{ActorKind, ActorRef, FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::types::{BotActionAuditPhase, BotActionAuditRecord, BotOperationContext};
use bcs_service_api::{ServiceError, ServiceResult};

use crate::action_audit::{
    action_audit_row_matches, audit_slot_retry_classified, audit_slot_select,
    create_file_audit_record, delete_file_audit_record, file_action_audit_insert,
    update_file_audit_record,
};

// ---------------------------------------------------------------------------
// SQL constants
// ---------------------------------------------------------------------------

/// Base SELECT columns (everything except the timestamp projections, which are
/// flavor-aware — see [`MySqlSessionFileStore::select_cols`]).
const SELECT_BASE_COLS: &str = "file_id, session_id, file_name, mime_type, size, sha256, \
    storage_backend, object_handle, status, owner_actor_kind, owner_actor_id";

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// MySQL/SQLite-backed session file metadata repository.
///
/// `created_at`/`updated_at` are NOT stored columns — the table has only the
/// DB-managed `gmt_create`/`gmt_modified` audit timestamps. The domain fields
/// are projected from those on read (epoch seconds) in a flavor-aware way
/// (`UNIX_TIMESTAMP` on MySQL, `strftime('%s', …)` on SQLite), and `list`
/// orders by `gmt_create DESC` (newest uploads first). `json_extract` is lowercase for MySQL/SQLite
/// portability, so the dialect branches are the timestamp projection and the
/// `expires_at` JSON cast (`... AS SIGNED` on MySQL, `... AS INTEGER` on SQLite).
#[derive(Clone)]
pub struct MySqlSessionFileStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

impl MySqlSessionFileStore {
    /// MySQL-backed constructor.
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::with_flavor(db, env, DbSqlFlavor::Mysql)
    }

    /// SQLite-backed constructor (local dev via `bcs-db-local`).
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::with_flavor(db, env, DbSqlFlavor::Sqlite)
    }

    /// Flavor-explicit constructor (used by bootstrap, which knows `db_kind`).
    pub fn with_flavor(db: Arc<dyn DbPlugin>, env: String, flavor: DbSqlFlavor) -> Self {
        Self { db, env, flavor }
    }
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Read a column as `i64` and cast to `u64`, clamping negatives to 0.
fn column_u64(row: &DbRow, name: &str) -> u64 {
    db_get_column_opt::<i64>(row, name)
        .ok()
        .flatten()
        .map(|v| v.max(0) as u64)
        .unwrap_or(0)
}

/// Parse the `status` column string into `FileStatus`.
fn parse_status(raw: &str) -> ServiceResult<FileStatus> {
    serde_json::from_value(serde_json::Value::String(raw.to_string()))
        .map_err(|e| ServiceError::InternalError(format!("parse status: {e}")))
}

/// Flavor-aware SQL cast of `object_handle->'$.expires_at'` to an integer for
/// comparison. MySQL/OceanBase only accept `CAST(... AS SIGNED)` (their `CAST`
/// has no `INTEGER` target); SQLite only accepts `CAST(... AS INTEGER)`.
fn expires_at_cast(flavor: DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Mysql => "CAST(json_extract(object_handle, '$.expires_at') AS SIGNED)",
        DbSqlFlavor::Sqlite => "CAST(json_extract(object_handle, '$.expires_at') AS INTEGER)",
    }
}

impl MySqlSessionFileStore {
    /// Full SELECT column list, projecting `created_at`/`updated_at` (epoch
    /// seconds) from the DB-managed `gmt_create`/`gmt_modified` in a
    /// flavor-aware way. `UNIX_TIMESTAMP` is wrapped in `CAST(... AS SIGNED)`
    /// so MySQL's fractional-timestamp DOUBLE result decodes cleanly to i64
    /// (SQLite's `strftime('%s', …)` already yields INTEGER).
    fn select_cols(&self) -> String {
        let (created, updated) = match self.flavor {
            DbSqlFlavor::Mysql => (
                "CAST(UNIX_TIMESTAMP(gmt_create) AS SIGNED) AS created_at",
                "CAST(UNIX_TIMESTAMP(gmt_modified) AS SIGNED) AS updated_at",
            ),
            DbSqlFlavor::Sqlite => (
                "CAST(strftime('%s', gmt_create) AS INTEGER) AS created_at",
                "CAST(strftime('%s', gmt_modified) AS INTEGER) AS updated_at",
            ),
        };
        format!("{SELECT_BASE_COLS}, {created}, {updated}")
    }

    /// Build a SELECT query with the given WHERE clause suffix and params.
    fn select_sql(&self, where_suffix: &str) -> String {
        format!(
            "SELECT {} FROM bcs_session_files WHERE {where_suffix}",
            self.select_cols()
        )
    }
}

/// Convert a DB row into a `SessionFile`.
fn row_to_session(row: &DbRow) -> ServiceResult<SessionFile> {
    let actor_kind_str: String =
        db_get_column_opt(row, "owner_actor_kind")
            .map_err(|e| ServiceError::InternalError(format!("owner_actor_kind: {e}")))?
            .unwrap_or_else(|| "Human".to_string());
    let actor_kind = match actor_kind_str.as_str() {
        "Bot" => ActorKind::Bot,
        _ => ActorKind::Human,
    };
    Ok(SessionFile {
        file_id: db_get_column(row, "file_id")
            .map_err(|e| ServiceError::InternalError(format!("file_id: {e}")))?,
        session_id: db_get_column(row, "session_id")
            .map_err(|e| ServiceError::InternalError(format!("session_id: {e}")))?,
        file_name: db_get_column(row, "file_name")
            .map_err(|e| ServiceError::InternalError(format!("file_name: {e}")))?,
        mime_type: db_get_column(row, "mime_type")
            .map_err(|e| ServiceError::InternalError(format!("mime_type: {e}")))?,
        size: column_u64(row, "size"),
        sha256: db_get_column_opt(row, "sha256")
            .map_err(|e| ServiceError::InternalError(format!("sha256: {e}")))?,
        owner: ActorRef {
            actor_kind,
            actor_id: db_get_column(row, "owner_actor_id")
                .map_err(|e| ServiceError::InternalError(format!("owner_actor_id: {e}")))?,
        },
        storage_backend: db_get_column(row, "storage_backend")
            .map_err(|e| ServiceError::InternalError(format!("storage_backend: {e}")))?,
        object_handle: db_get_column(row, "object_handle")
            .map_err(|e| ServiceError::InternalError(format!("object_handle: {e}")))?,
        status: {
            let raw: String = db_get_column(row, "status")
                .map_err(|e| ServiceError::InternalError(format!("status: {e}")))?;
            parse_status(&raw)?
        },
        created_at: column_u64(row, "created_at"),
        updated_at: column_u64(row, "updated_at"),
    })
}

// ---------------------------------------------------------------------------
// SessionFileRepoPort impl
// ---------------------------------------------------------------------------

#[async_trait]
impl SessionFileRepoPort for MySqlSessionFileStore {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        // created_at/updated_at are NOT stored columns: the table carries only
        // DB-managed gmt_create/gmt_modified. The returned row's timestamps are
        // the application now (≈ DB gmt_create); re-reads (get/list) project
        // them from gmt_*. expires_at lives inside object_handle JSON.
        let now = now_secs();
        let actor_kind_str = match params.owner.actor_kind {
            ActorKind::Bot => "Bot",
            ActorKind::Human => "Human",
        };

        let sql = "INSERT INTO bcs_session_files \
            (env, file_id, session_id, owner_actor_kind, owner_actor_id, file_name, \
             mime_type, size, storage_backend, object_handle, status) \
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending')";

        let stmt = DbStatement::with_params(
            sql,
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(params.file_id.as_str()),
                DbValue::from(params.session_id.as_str()),
                DbValue::from(actor_kind_str),
                DbValue::from(params.owner.actor_id.as_str()),
                DbValue::from(params.file_name.as_str()),
                DbValue::from(params.mime_type.as_str()),
                DbValue::from(params.size),
                DbValue::from(params.storage_backend.as_str()),
                DbValue::from(params.object_handle.as_str()),
            ],
        );

        // Same-transaction ordinary-business audit (spec \u00a712.5, plan Task 11):
        // the `create/session_file/applied` audit row joins the metadata INSERT
        // in ONE DbPlugin transaction, so an audit INSERT failure rolls the
        // INSERT back leaving no residue. A same-slot byte-identical record is
        // an idempotent replay of a fully committed prepare; different content
        // under the slot is a conflict.
        let audit_record = create_file_audit_record(&params.operation, &self.env, &params.file_id);
        let steps = vec![
            DbTransactionStep::Execute(stmt),
            DbTransactionStep::Execute(file_action_audit_insert(&audit_record)),
        ];
        if let Err(error) = self.db.transaction(steps).await {
            let probe = self
                .db
                .query(audit_slot_select(&audit_record))
                .await
                .map_err(|e| ServiceError::InternalError(format!("session file insert: {e}")))?;
            audit_slot_retry_classified(
                &audit_record,
                probe,
                &format!("session file insert: {error}"),
            )?;
            // Identical slot: a previous attempt of the same operation fully
            // committed the SAME metadata row — surface the returned row.
        }

        Ok(SessionFile {
            file_id: params.file_id,
            session_id: params.session_id,
            file_name: params.file_name,
            mime_type: params.mime_type,
            size: params.size,
            sha256: None,
            owner: params.owner,
            storage_backend: params.storage_backend,
            object_handle: params.object_handle,
            status: FileStatus::Pending,
            created_at: now,
            updated_at: now,
        })
    }

    async fn get(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> ServiceResult<Option<SessionFile>> {
        let sql = self.select_sql("env = ? AND session_id = ? AND file_id = ? LIMIT 1");
        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(file_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file get: {e}")))?;
        Ok(rows.into_iter().next().map(|r| row_to_session(&r)).transpose()?)
    }

    async fn get_by_file_id(&self, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        let sql = self.select_sql("env = ? AND file_id = ? LIMIT 1");
        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(file_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file get_by_file_id: {e}")))?;
        Ok(rows.into_iter().next().map(|r| row_to_session(&r)).transpose()?)
    }

    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>> {
        let status_str = serde_json::to_string(&status)
            .map_err(|e| ServiceError::InternalError(format!("serialize status: {e}")))?;
        // The serialized form has surrounding quotes; strip them for the DB TEXT column.
        let status_str = status_str.trim_matches('"');

        // Conditional, genuinely-changing UPDATE (spec \u00a712.5, plan Task 11):
        // the WHERE excludes a row already carrying the target triple, and
        // `with_transaction_stop_on_no_rows` ends the transaction BEFORE the
        // audit step for an idempotent no-change update — so a no-op writes NO
        // audit row on EITHER dialect (no affected_rows counting reliance).
        // object_handle/status/size columns are NOT NULL in this table, so
        // plain = / <> comparisons are null-safe here.
        let update_sql = "UPDATE bcs_session_files \
            SET object_handle = ?, status = ?, size = ? \
            WHERE env = ? AND session_id = ? AND file_id = ? \
              AND NOT (object_handle = ? AND status = ? AND size = ?)";

        // Same-transaction `update/session_file/applied` audit row.
        let audit_record = update_file_audit_record(operation, &self.env, file_id);
        let steps = vec![
            DbTransactionStep::Execute(
                DbStatement::with_params(
                    update_sql,
                    vec![
                        DbValue::from(object_handle),
                        DbValue::from(status_str),
                        DbValue::from(size),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(session_id),
                        DbValue::from(file_id),
                        DbValue::from(object_handle),
                        DbValue::from(status_str),
                        DbValue::from(size),
                    ],
                )
                .with_transaction_stop_on_no_rows(),
            ),
            DbTransactionStep::Execute(file_action_audit_insert(&audit_record)),
        ];
        self.db
            .transaction(steps)
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file update: {e}")))?;

        // Re-SELECT to return the updated state.
        self.get(session_id, file_id).await
    }

    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>> {
        let status_str = serde_json::to_string(&status)
            .map_err(|e| ServiceError::InternalError(format!("serialize status: {e}")))?;
        let status_str = status_str.trim_matches('"');

        // Conditional no-op exclusion + `stop_on_no_rows` (see
        // update_object_handle_and_status); the `update/session_file/applied`
        // audit row joins the status change in ONE transaction.
        let update_sql = "UPDATE bcs_session_files \
            SET status = ? \
            WHERE env = ? AND session_id = ? AND file_id = ? AND status <> ?";

        let audit_record = update_file_audit_record(operation, &self.env, file_id);
        let steps = vec![
            DbTransactionStep::Execute(
                DbStatement::with_params(
                    update_sql,
                    vec![
                        DbValue::from(status_str),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(session_id),
                        DbValue::from(file_id),
                        DbValue::from(status_str),
                    ],
                )
                .with_transaction_stop_on_no_rows(),
            ),
            DbTransactionStep::Execute(file_action_audit_insert(&audit_record)),
        ];
        self.db
            .transaction(steps)
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file update_status: {e}")))?;

        self.get(session_id, file_id).await
    }

    async fn delete(
        &self,
        session_id: &str,
        file_id: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        // The FINAL metadata DELETE commits in ONE transaction with the
        // `delete/session_file/completed` audit row (spec \u00a712.5): the
        // external object removal already happened under the persisted
        // `delete/session_file/admitted` phase, so a metadata/audit failure
        // leaves the row IN PLACE (retained-for-retry) and the caller
        // surfaces the error — never a false completion.
        //
        // A missing row is an idempotent no-op: `stop_on_no_rows` ends the
        // transaction before the audit step, so no phantom `completed` row
        // appears for an already-deleted file. Both dialects count exactly 1
        // affected row for a real DELETE and 0 for a miss, so the
        // returned-existence flag does not rely on no-op affected_rows
        // differences.
        // The completed record joins the metadata DELETE in this same
        // transaction; `admitted` was persisted earlier via
        // record_operation_phase.
        let audit_record = delete_file_audit_record(
            operation,
            &self.env,
            file_id,
            BotActionAuditPhase::Completed,
            None,
        );
        let steps = vec![
            DbTransactionStep::Execute(
                DbStatement::with_params(
                    "DELETE FROM bcs_session_files WHERE env = ? AND session_id = ? AND file_id = ?",
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(session_id),
                        DbValue::from(file_id),
                    ],
                )
                .with_transaction_stop_on_no_rows(),
            ),
            DbTransactionStep::Execute(file_action_audit_insert(&audit_record)),
        ];
        let results = self
            .db
            .transaction(steps)
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file delete: {e}")))?;
        let deleted = match results.first() {
            Some(DbTransactionStepResult::Executed(result)) => result.affected_rows > 0,
            _ => false,
        };
        Ok(deleted)
    }

    /// Standalone phase recorder (spec §12.5, plan Task 11): the ONLY audit
    /// lane outside the atomic mutations, used by the file service to
    /// persist `admitted` (before external backend I/O) and explicit
    /// `failed`/`unknown` outcomes. A slot already carrying this
    /// byte-identical record is an idempotent no-op; a same-slot row with
    /// different content is a Conflict. A genuine INSERT failure propagates
    /// so the caller can refuse to start the external side effect.
    async fn record_operation_phase(&self, audit: BotActionAuditRecord) -> ServiceResult<()> {
        let existing = self
            .db
            .query(audit_slot_select(&audit))
            .await
            .map_err(|e| ServiceError::InternalError(format!("file audit slot probe: {e}")))?;
        if let Some(row) = existing.first() {
            if action_audit_row_matches(&audit, row)? {
                // Byte-identical replay of a previously recorded phase.
                return Ok(());
            }
            return Err(ServiceError::Conflict(format!(
                "file action audit slot '{}' already carries different content",
                audit.step_key
            )));
        }
        if let Err(error) = self
            .db
            .execute(file_action_audit_insert(&audit))
            .await
        {
            // The unique slot may have been taken concurrently between probe
            // and insert: re-probe and classify (identical = replay; anything
            // else = genuine failure).
            let probe = self
                .db
                .query(audit_slot_select(&audit))
                .await
                .map_err(|e| ServiceError::InternalError(format!("file audit insert: {e}")))?;
            audit_slot_retry_classified(&audit, probe, &format!("file audit insert: {error}"))?;
            return Ok(());
        }
        Ok(())
    }

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage> {
        let mut conditions: Vec<String> = vec![
            "env = ?".to_string(),
            "session_id = ?".to_string(),
        ];
        let mut bind_values: Vec<DbValue> = vec![
            DbValue::from(self.env.as_str()),
            DbValue::from(session_id),
        ];

        // Optional prefix filter (file_name LIKE 'prefix%')
        if let Some(ref prefix) = params.prefix {
            conditions.push("file_name LIKE ?".to_string());
            bind_values.push(DbValue::from(format!("{}%", prefix)));
        }

        // Optional status filter
        if let Some(ref status) = params.status {
            let status_str = serde_json::to_string(status)
                .map_err(|e| ServiceError::InternalError(format!("serialize status: {e}")))?;
            let status_str = status_str.trim_matches('"');
            conditions.push("status = ?".to_string());
            bind_values.push(DbValue::from(status_str));
        }

        // Clamp limit to [1, 1000], defaulting to 100.
        let limit_u32 = if params.limit == 0 {
            100
        } else {
            params.limit.min(1000)
        };

        let where_clause = conditions.join(" AND ");

        // COUNT query
        let count_sql = format!(
            "SELECT COUNT(*) AS cnt FROM bcs_session_files WHERE {where_clause}"
        );
        let count_rows = self
            .db
            .query(DbStatement::with_params(&count_sql, bind_values.clone()))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file list count: {e}")))?;
        let total = count_rows
            .first()
            .map(|r| db_get_column::<i64>(r, "cnt").unwrap_or(0) as u64)
            .unwrap_or(0);

        // PAGE query: add limit + offset
        let mut page_binds = bind_values;
        page_binds.push(DbValue::from(limit_u32));
        page_binds.push(DbValue::from(params.offset));

        let page_sql = format!(
            "SELECT {} FROM bcs_session_files \
             WHERE {where_clause} \
             ORDER BY gmt_create DESC, file_id DESC LIMIT ? OFFSET ?",
            self.select_cols()
        );

        let rows = self
            .db
            .query(DbStatement::with_params(&page_sql, page_binds))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file list: {e}")))?;

        let items: Vec<SessionFile> = rows
            .into_iter()
            .map(|r| row_to_session(&r))
            .collect::<ServiceResult<Vec<_>>>()?;

        Ok(SessionFileListPage {
            items,
            total,
        })
    }

    async fn list_expired_pending(
        &self,
        now: u64,
        limit: u32,
    ) -> ServiceResult<Vec<SessionFile>> {
        // Use lowercase `json_extract` for both MySQL and SQLite portability.
        // The `expires_at` cast must be flavor-aware (see [`expires_at_cast`]):
        // MySQL/OceanBase reject `CAST(... AS INTEGER)` (only `AS SIGNED`),
        // SQLite needs `AS INTEGER`.
        let expires_at_cast = expires_at_cast(self.flavor);
        let sql = format!(
            "SELECT {} FROM bcs_session_files \
             WHERE env = ? AND status = 'Pending' \
             AND {expires_at_cast} < ? \
             LIMIT ?",
            self.select_cols()
        );

        let rows = self
            .db
            .query(DbStatement::with_params(
                &sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(now),
                    DbValue::from(limit),
                ],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("session file list_expired_pending: {e}"))
            })?;

        rows.into_iter()
            .map(|r| row_to_session(&r))
            .collect::<ServiceResult<Vec<_>>>()
    }

    async fn delete_all_for_session(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<SessionFile>> {
        // Step 1: SELECT all rows for the session.
        let select_sql = self.select_sql("env = ? AND session_id = ?");
        let rows = self
            .db
            .query(DbStatement::with_params(
                &select_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("session file delete_all select: {e}"))
            })?;

        let removed: Vec<SessionFile> = rows
            .into_iter()
            .map(|r| row_to_session(&r))
            .collect::<ServiceResult<Vec<_>>>()?;

        // Step 2: DELETE all rows for the session.
        self.db
            .execute(DbStatement::with_params(
                "DELETE FROM bcs_session_files WHERE env = ? AND session_id = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("session file delete_all delete: {e}"))
            })?;

        Ok(removed)
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::ActorKind;

    #[test]
    fn parse_status_decodes_serde_variants() {
        // DB stores the PascalCase variant name without JSON quoting.
        assert_eq!(parse_status("Pending").unwrap(), FileStatus::Pending);
        assert_eq!(parse_status("Ready").unwrap(), FileStatus::Ready);
        assert_eq!(parse_status("Deleting").unwrap(), FileStatus::Deleting);
        assert_eq!(parse_status("Failed").unwrap(), FileStatus::Failed);
    }

    #[test]
    fn parse_status_unknown_falls_back_to_pending() {
        // serde_json::from_value for an unknown variant will fail;
        // parse_status propagates the error (it does not fall back).
        assert!(parse_status("Unknown").is_err());
    }

    #[test]
    fn actor_kind_mapping() {
        let row = DbRow::new(
            vec![
                ("file_id".to_string(), DbValue::from("f1")),
                ("session_id".to_string(), DbValue::from("s1")),
                ("file_name".to_string(), DbValue::from("test.txt")),
                ("mime_type".to_string(), DbValue::from("text/plain")),
                ("size".to_string(), DbValue::I64(100)),
                ("sha256".to_string(), DbValue::Null),
                ("storage_backend".to_string(), DbValue::from("local")),
                ("object_handle".to_string(), DbValue::from("{}")),
                ("status".to_string(), DbValue::from("Pending")),
                ("created_at".to_string(), DbValue::I64(1000)),
                ("updated_at".to_string(), DbValue::I64(2000)),
                ("owner_actor_kind".to_string(), DbValue::from("Bot")),
                ("owner_actor_id".to_string(), DbValue::from("bot_1")),
            ]
            .into_iter()
            .collect(),
        );
        let sf = row_to_session(&row).unwrap();
        assert_eq!(sf.owner.actor_kind, ActorKind::Bot);
        assert_eq!(sf.owner.actor_id, "bot_1");
        assert_eq!(sf.size, 100);
        assert_eq!(sf.created_at, 1000);
        assert_eq!(sf.updated_at, 2000);
        assert_eq!(sf.status, FileStatus::Pending);
    }
    #[test]
    fn expires_at_cast_is_flavor_aware() {
        assert!(expires_at_cast(DbSqlFlavor::Mysql).contains("AS SIGNED"));
        assert!(!expires_at_cast(DbSqlFlavor::Mysql).contains("AS INTEGER"));
        assert!(expires_at_cast(DbSqlFlavor::Sqlite).contains("AS INTEGER"));
    }
}

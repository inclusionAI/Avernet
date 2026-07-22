//! MySQL-backed `SessionFileRepoPort` implementation via `bcs-db-api`.
//!
//! Uses the same `DbStatement::with_params` pattern as
//! `bcs-session-store/src/mysql.rs`. SQL is written to a common subset
//! supported by both local SQLite and MySQL-compatible backends.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbValue, db_get_column, db_get_column_opt};
use bcs_domain::{ActorKind, ActorRef, FileStatus, SessionFile};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListPage, SessionFileListParams, SessionFileRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

// ---------------------------------------------------------------------------
// SQL constants
// ---------------------------------------------------------------------------

/// Column list for SELECT queries (includes owner columns needed to reconstruct ActorRef).
const SELECT_COLS: &str = "file_id, session_id, file_name, mime_type, size, sha256, \
    storage_backend, object_handle, status, created_at, updated_at, \
    owner_actor_kind, owner_actor_id";

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// MySQL-backed session file metadata repository.
///
/// Unlike `bcs-session-store`, this store needs no `DbSqlFlavor` field: it
/// relies on DB defaults for timestamps and uses lowercase `json_extract`
/// (valid on both MySQL and SQLite), so no dialect branching is required.
#[derive(Clone)]
pub struct MySqlSessionFileStore {
    db: Arc<dyn DbPlugin>,
    env: String,
}

impl MySqlSessionFileStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self { db, env }
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

/// Build a SELECT query with the given WHERE clause suffix and params.
fn select_sql(where_suffix: &str) -> String {
    format!("SELECT {SELECT_COLS} FROM bcs_session_files WHERE {where_suffix}")
}

// ---------------------------------------------------------------------------
// SessionFileRepoPort impl
// ---------------------------------------------------------------------------

#[async_trait]
impl SessionFileRepoPort for MySqlSessionFileStore {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        // created_at/updated_at are the business creation time (unix secs) used for
        // list ordering — NOT expires_at. expires_at lives inside object_handle JSON.
        let now = now_secs();
        let actor_kind_str = match params.owner.actor_kind {
            ActorKind::Bot => "Bot",
            ActorKind::Human => "Human",
        };

        let sql = "INSERT INTO bcs_session_files \
            (env, file_id, session_id, owner_actor_kind, owner_actor_id, file_name, \
             mime_type, size, storage_backend, object_handle, status, created_at, updated_at) \
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)";

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
                DbValue::from(now),
                DbValue::from(now),
            ],
        );

        self.db
            .execute(stmt)
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file insert: {e}")))?;

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
        let sql = select_sql("env = ? AND session_id = ? AND file_id = ? LIMIT 1");
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

    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
    ) -> ServiceResult<Option<SessionFile>> {
        let now = now_secs();
        let status_str = serde_json::to_string(&status)
            .map_err(|e| ServiceError::InternalError(format!("serialize status: {e}")))?;
        // The serialized form has surrounding quotes; strip them for the DB TEXT column.
        let status_str = status_str.trim_matches('"');

        let update_sql = "UPDATE bcs_session_files \
            SET object_handle = ?, status = ?, size = ?, updated_at = ? \
            WHERE env = ? AND session_id = ? AND file_id = ?";

        self.db
            .execute(DbStatement::with_params(
                update_sql,
                vec![
                    DbValue::from(object_handle),
                    DbValue::from(status_str),
                    DbValue::from(size),
                    DbValue::from(now),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(file_id),
                ],
            ))
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
    ) -> ServiceResult<Option<SessionFile>> {
        let now = now_secs();
        let status_str = serde_json::to_string(&status)
            .map_err(|e| ServiceError::InternalError(format!("serialize status: {e}")))?;
        let status_str = status_str.trim_matches('"');

        let update_sql = "UPDATE bcs_session_files \
            SET status = ?, updated_at = ? \
            WHERE env = ? AND session_id = ? AND file_id = ?";

        self.db
            .execute(DbStatement::with_params(
                update_sql,
                vec![
                    DbValue::from(status_str),
                    DbValue::from(now),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(file_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file update_status: {e}")))?;

        self.get(session_id, file_id).await
    }

    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                "DELETE FROM bcs_session_files WHERE env = ? AND session_id = ? AND file_id = ?",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                    DbValue::from(file_id),
                ],
            ))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file delete: {e}")))?;
        Ok(result.affected_rows > 0)
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

        // Marker pagination: cursor = "<created_at>:<file_id>"
        if let Some(ref marker) = params.marker {
            if let Some((mc_str, mf)) = marker.split_once(':') {
                let mc: u64 = mc_str.parse().unwrap_or(0);
                conditions.push(
                    "(created_at > ? OR (created_at = ? AND file_id > ?))".to_string(),
                );
                bind_values.push(DbValue::from(mc));
                bind_values.push(DbValue::from(mc));
                bind_values.push(DbValue::from(mf));
            }
        }

        // Clamp limit to [1, 1000], defaulting to 100.
        let limit_u32 = if params.limit == 0 {
            100
        } else {
            params.limit.min(1000)
        };
        // Read N+1 rows to detect truncation.
        bind_values.push(DbValue::from(limit_u32 + 1));

        let where_clause = conditions.join(" AND ");
        let sql = format!(
            "SELECT {SELECT_COLS} FROM bcs_session_files \
             WHERE {where_clause} \
             ORDER BY created_at, file_id LIMIT ?"
        );

        let rows = self
            .db
            .query(DbStatement::with_params(&sql, bind_values))
            .await
            .map_err(|e| ServiceError::InternalError(format!("session file list: {e}")))?;

        let limit = limit_u32 as usize;
        let truncated = rows.len() > limit;
        let items: Vec<SessionFile> = rows
            .into_iter()
            .take(limit)
            .map(|r| row_to_session(&r))
            .collect::<ServiceResult<Vec<_>>>()?;

        let next_marker = if truncated {
            items
                .last()
                .map(|r| format!("{}:{}", r.created_at, r.file_id))
        } else {
            None
        };

        Ok(SessionFileListPage {
            items,
            truncated,
            next_marker,
        })
    }

    async fn list_expired_pending(
        &self,
        now: u64,
        limit: u32,
    ) -> ServiceResult<Vec<SessionFile>> {
        // Use lowercase `json_extract` for both MySQL and SQLite portability.
        let sql = format!(
            "SELECT {SELECT_COLS} FROM bcs_session_files \
             WHERE env = ? AND status = 'Pending' \
             AND CAST(json_extract(object_handle, '$.expires_at') AS INTEGER) < ? \
             LIMIT ?"
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
        let select_sql = select_sql("env = ? AND session_id = ?");
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
}
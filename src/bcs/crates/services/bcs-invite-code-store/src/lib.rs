//! Invite-code persistence stores for BCS.
//!
//! Owns the persistence mapping for the invite-code access-gate feature.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_config::resolve_env;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue, db_get_column,
    db_get_column_opt,
};
use bcs_service_api::port::repo::{
    InviteCodeBindOutcome, InviteCodeRecord, InviteCodeRepoPort, InviteCodeStatus,
};
use bcs_service_api::ServiceResult;
use tracing::{debug, info, warn};

pub mod memory;

pub use memory::MemoryInviteCodeRepo;

pub type MysqlInviteCodeRepo = DbInviteCodeStore;
pub type SqliteInviteCodeRepo = DbInviteCodeStore;

/// DB-backed invite-code repository operating on `bcs_invite_codes`.
pub struct DbInviteCodeStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
}

impl DbInviteCodeStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, DbSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, DbSqlFlavor::Sqlite)
    }

    fn current_env() -> String {
        resolve_env().as_str().to_string()
    }

    fn select_ts_created(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "UNIX_TIMESTAMP(gmt_create) AS gmt_create_ts",
            DbSqlFlavor::Sqlite => "CAST(strftime('%s', gmt_create) AS INTEGER) AS gmt_create_ts",
        }
    }

    fn select_ts_modified(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "UNIX_TIMESTAMP(gmt_modified) AS gmt_modified_ts",
            DbSqlFlavor::Sqlite => {
                "CAST(strftime('%s', gmt_modified) AS INTEGER) AS gmt_modified_ts"
            }
        }
    }

    fn select_sql(&self, where_clause: &str) -> String {
        format!(
            "SELECT id, code_hash, code_hint, status, bound_user_id, bound_at, created_by, {created_at}, {modified_at} FROM bcs_invite_codes {where_clause} LIMIT 1",
            created_at = self.select_ts_created(),
            modified_at = self.select_ts_modified(),
        )
    }

    fn insert_sql(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "INSERT IGNORE INTO bcs_invite_codes (code_hash, code_hint, status, bound_user_id, bound_at, created_by, env) VALUES (?, ?, ?, ?, ?, ?, ?)",
            DbSqlFlavor::Sqlite => "INSERT OR IGNORE INTO bcs_invite_codes (code_hash, code_hint, status, bound_user_id, bound_at, created_by, env) VALUES (?, ?, ?, ?, ?, ?, ?)",
        }
    }

    fn bind_sql(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => {
                "UPDATE bcs_invite_codes SET bound_user_id = ?, bound_at = ?, status = 'bound', gmt_modified = NOW() WHERE code_hash = ? AND env = ? AND status = 'active' AND bound_user_id IS NULL"
            }
            DbSqlFlavor::Sqlite => {
                "UPDATE bcs_invite_codes SET bound_user_id = ?, bound_at = ?, status = 'bound', gmt_modified = CURRENT_TIMESTAMP WHERE code_hash = ? AND env = ? AND status = 'active' AND bound_user_id IS NULL"
            }
        }
    }

    fn status_to_db(status: InviteCodeStatus) -> &'static str {
        match status {
            InviteCodeStatus::Active => "active",
            InviteCodeStatus::Bound => "bound",
            InviteCodeStatus::Disabled => "disabled",
        }
    }

    fn record_from_row(&self, row: &DbRow) -> ServiceResult<InviteCodeRecord> {
        let status = match db_get_column::<String>(row, "status")
            .map_err(|err| service_db_error("status", err))?
            .as_str() {
            "active" => InviteCodeStatus::Active,
            "bound" => InviteCodeStatus::Bound,
            "disabled" => InviteCodeStatus::Disabled,
            other => {
                return Err(service_db_error(
                    "status",
                    DbError::Conversion(format!(
                        "column 'status' is not a valid invite-code status: {other}"
                    )),
                ));
            }
        };
        Ok(InviteCodeRecord {
            id: db_get_column(row, "id").map_err(|err| service_db_error("id", err))?,
            code_hash: db_get_column(row, "code_hash").map_err(|err| service_db_error("code_hash", err))?,
            code_hint: db_get_column(row, "code_hint").map_err(|err| service_db_error("code_hint", err))?,
            status,
            bound_user_id: db_get_column_opt(row, "bound_user_id").map_err(|err| service_db_error("bound_user_id", err))?,
            bound_at: db_get_column_opt(row, "bound_at").map_err(|err| service_db_error("bound_at", err))?,
            created_by: db_get_column_opt(row, "created_by").map_err(|err| service_db_error("created_by", err))?,
            created_at: row_ts(row, "gmt_create_ts"),
            updated_at: row_ts(row, "gmt_modified_ts"),
        })
    }

    async fn query_one(
        &self,
        sql: String,
        params: Vec<DbValue>,
    ) -> ServiceResult<Option<InviteCodeRecord>> {
        let rows = self.db.query(DbStatement::with_params(&sql, params)).await.map_err(|err| {
            warn!(error = %err, "invite_code_store query failed");
            service_db_error("query", err)
        })?;
        Ok(rows.first().map(|row| self.record_from_row(row)).transpose()?)
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|err| {
                warn!(operation, error = %err, "invite_code_store execute failed");
                service_db_error(operation, err)
            })
    }
}

#[async_trait]
impl InviteCodeRepoPort for DbInviteCodeStore {
    async fn insert_code(&self, record: InviteCodeRecord) -> ServiceResult<bool> {
        let affected = self
            .execute(
                "insert_code",
                DbStatement::with_params(
                    self.insert_sql(),
                    vec![
                        DbValue::from(record.code_hash),
                        DbValue::from(record.code_hint),
                        DbValue::from(Self::status_to_db(record.status)),
                        DbValue::from(record.bound_user_id),
                        match record.bound_at { Some(value) => DbValue::from(value as i64), None => DbValue::Null },
                        DbValue::from(record.created_by),
                        DbValue::from(Self::current_env()),
                    ],
                ),
            )
            .await?;
        if affected > 0 {
            info!("invite-code inserted");
        }
        Ok(affected > 0)
    }

    async fn bind_code(
        &self,
        code_hash: &str,
        user_id: &str,
        bound_at: u64,
    ) -> ServiceResult<InviteCodeBindOutcome> {
        let env = Self::current_env();
        let by_user_sql = self.select_sql("WHERE bound_user_id = ? AND env = ?");
        if let Some(record) = self
            .query_one(
                by_user_sql,
                vec![DbValue::from(user_id), DbValue::from(env.as_str())],
            )
            .await?
        {
            if record.code_hash == code_hash {
                return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(record));
            }
            return Ok(InviteCodeBindOutcome::AlreadyBoundToDifferentCode(record));
        }

        let code_sql = self.select_sql("WHERE code_hash = ? AND env = ?");
        let Some(existing) = self
            .query_one(
                code_sql,
                vec![DbValue::from(code_hash), DbValue::from(env.as_str())],
            )
            .await?
        else {
            return Ok(InviteCodeBindOutcome::Unavailable);
        };
        if existing.status != InviteCodeStatus::Active || existing.bound_user_id.is_some() {
            if existing.bound_user_id.as_deref() == Some(user_id) {
                return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(existing));
            }
            return Ok(InviteCodeBindOutcome::Unavailable);
        }

        let affected = self
            .db
            .execute(DbStatement::with_params(
                self.bind_sql(),
                vec![
                    DbValue::from(user_id),
                    DbValue::from(bound_at as i64),
                    DbValue::from(code_hash),
                    DbValue::from(env.as_str()),
                ],
            ))
            .await;
        match affected {
            Ok(result) if result.affected_rows == 0 => {
                let record = self
                    .query_one(
                        self.select_sql("WHERE code_hash = ? AND env = ?"),
                        vec![DbValue::from(code_hash), DbValue::from(env.as_str())],
                    )
                    .await?;
                Ok(match record {
                    Some(record) if record.bound_user_id.as_deref() == Some(user_id) => {
                        InviteCodeBindOutcome::AlreadyBoundToSameCode(record)
                    }
                    Some(record) if record.bound_user_id.is_some() => {
                        InviteCodeBindOutcome::Unavailable
                    }
                    Some(_) | None => InviteCodeBindOutcome::Unavailable,
                })
            }
            Ok(_) => {
                let Some(record) = self
                    .query_one(
                        self.select_sql("WHERE code_hash = ? AND env = ?"),
                        vec![DbValue::from(code_hash), DbValue::from(env.as_str())],
                    )
                    .await?
                else {
                    return Ok(InviteCodeBindOutcome::Unavailable);
                };
                debug!(code_hash = %code_hash, user_id = %user_id, "invite-code bound (DB)");
                Ok(InviteCodeBindOutcome::Bound(record))
            }
            Err(err) if err.is_duplicate_key() => {
                let record = self
                    .query_one(
                        self.select_sql("WHERE bound_user_id = ? AND env = ?"),
                        vec![DbValue::from(user_id), DbValue::from(env.as_str())],
                    )
                    .await?;
                if let Some(record) = record {
                    if record.code_hash == code_hash {
                        return Ok(InviteCodeBindOutcome::AlreadyBoundToSameCode(record));
                    }
                    return Ok(InviteCodeBindOutcome::AlreadyBoundToDifferentCode(record));
                }
                let record = self
                    .query_one(
                        self.select_sql("WHERE code_hash = ? AND env = ?"),
                        vec![DbValue::from(code_hash), DbValue::from(env.as_str())],
                    )
                    .await?;
                Ok(match record {
                    Some(record) if record.bound_user_id.as_deref() == Some(user_id) => {
                        InviteCodeBindOutcome::AlreadyBoundToSameCode(record)
                    }
                    Some(record) if record.bound_user_id.is_some() => {
                        InviteCodeBindOutcome::Unavailable
                    }
                    Some(_) | None => InviteCodeBindOutcome::Unavailable,
                })
            }
            Err(err) => Err(service_db_error("bind_code", err)),
        }
    }

    async fn find_by_user_id(&self, user_id: &str) -> ServiceResult<Option<InviteCodeRecord>> {
        let env = Self::current_env();
        self.query_one(
            self.select_sql("WHERE bound_user_id = ? AND env = ?"),
            vec![DbValue::from(user_id), DbValue::from(env.as_str())],
        )
        .await
    }

    async fn find_by_code_hash(&self, code_hash: &str) -> ServiceResult<Option<InviteCodeRecord>> {
        let env = Self::current_env();
        self.query_one(
            self.select_sql("WHERE code_hash = ? AND env = ?"),
            vec![DbValue::from(code_hash), DbValue::from(env.as_str())],
        )
        .await
    }
}

fn row_ts(row: &DbRow, column: &'static str) -> u64 {
    match row.get(column) {
        Some(DbValue::I64(value)) if *value >= 0 => *value as u64,
        Some(DbValue::U64(value)) => *value,
        _ => 0,
    }
}

fn service_db_error(operation: &'static str, err: DbError) -> bcs_service_api::ServiceError {
    bcs_service_api::ServiceError::InternalError(format!("invite code db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_api::DbResult;
    use bcs_db_local::LocalSqliteDbPlugin;

    async fn sqlite_db() -> Arc<LocalSqliteDbPlugin> {
        let db = LocalSqliteDbPlugin::new().expect("sqlite db");
        db.execute(DbStatement::new("CREATE TABLE bcs_invite_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT NOT NULL UNIQUE, code_hint TEXT NOT NULL, status TEXT NOT NULL, bound_user_id TEXT NULL UNIQUE, bound_at INTEGER NULL, created_by TEXT NULL, env TEXT NOT NULL, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
            .await
            .expect("create table");
        Arc::new(db)
    }

    #[tokio::test]
    async fn insert_and_bind_round_trip() {
        let db = sqlite_db().await;
        let store = DbInviteCodeStore::sqlite(db);
        let code = InviteCodeRecord {
            id: 0,
            code_hash: "hash-1".into(),
            code_hint: "B2C3".into(),
            status: InviteCodeStatus::Active,
            bound_user_id: None,
            bound_at: None,
            created_by: None,
            created_at: 1,
            updated_at: 1,
        };
        assert!(store.insert_code(code).await.expect("insert"));
        match store.bind_code("hash-1", "user-1", 11).await.expect("bind") {
            InviteCodeBindOutcome::Bound(record) => {
                assert_eq!(record.bound_user_id.as_deref(), Some("user-1"))
            }
            other => panic!("unexpected outcome: {:?}", other),
        }
        let found = store.find_by_user_id("user-1").await.expect("find");
        assert_eq!(found.unwrap().code_hash, "hash-1");
    }
}

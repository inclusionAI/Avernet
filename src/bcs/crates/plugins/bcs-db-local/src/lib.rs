//! Local database plugin implementations for the `bcs-db-api` contract.
//!
//! This crate contains dependency-light implementations for local development
//! and contract tests. Internal SDK backed implementations such as ZDAS live
//! in separate crates so they can be excluded from open-source distributions.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use rusqlite::types::{Value as SqliteValue, ValueRef};
use rusqlite::{Connection, params_from_iter};

/// SQLite in-memory implementation of [`DbPlugin`].
///
/// This is intentionally small but executes real SQL, making it useful for
/// contract tests and local experiments. Statements must be compatible with
/// SQLite. It owns one SQLite connection behind a blocking mutex, so it is
/// intended for single-box development and low-concurrency tests rather than
/// production async workloads.
#[derive(Clone)]
pub struct LocalSqliteDbPlugin {
    connection: Arc<Mutex<Connection>>,
}

#[deprecated(
    since = "0.1.0",
    note = "use LocalSqliteDbPlugin; this implementation is SQLite-backed"
)]
pub type InMemoryDbPlugin = LocalSqliteDbPlugin;

impl LocalSqliteDbPlugin {
    pub fn new() -> DbResult<Self> {
        let connection = Connection::open_in_memory()
            .map_err(|err| DbError::Backend(format!("open in-memory sqlite: {}", err)))?;
        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    /// File-backed SQLite for local development.
    ///
    /// Creates the parent directory if it does not exist, opens or creates the
    /// SQLite file, enables WAL journal mode for better read concurrency, enables
    /// foreign key enforcement, and sets a busy timeout.
    pub fn new_file(path: impl AsRef<std::path::Path>) -> DbResult<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|err| {
                    DbError::Backend(format!("create sqlite parent directory: {}", err))
                })?;
            }
        }
        let connection = Connection::open(path).map_err(|err| {
            DbError::Backend(format!("open sqlite file '{}': {}", path.display(), err))
        })?;

        connection
            .execute_batch("PRAGMA journal_mode=WAL;")
            .map_err(|err| DbError::Backend(format!("enable WAL mode: {}", err)))?;

        connection
            .execute_batch("PRAGMA foreign_keys=ON;")
            .map_err(|err| DbError::Backend(format!("enable foreign keys: {}", err)))?;

        connection
            .execute_batch("PRAGMA busy_timeout=5000;")
            .map_err(|err| DbError::Backend(format!("set busy timeout: {}", err)))?;

        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    fn connection(&self) -> DbResult<MutexGuard<'_, Connection>> {
        self.connection
            .lock()
            .map_err(|_| DbError::Backend("sqlite connection lock poisoned".to_string()))
    }
}

#[async_trait]
impl DbPlugin for LocalSqliteDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let connection = self.connection()?;
        query_with_connection(&connection, statement)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let connection = self.connection()?;
        execute_with_connection(&connection, statement)
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        let mut connection = self.connection()?;
        let tx = connection
            .transaction()
            .map_err(|err| DbError::Backend(format!("begin sqlite transaction: {}", err)))?;
        let mut results = Vec::with_capacity(steps.len());

        for step in steps {
            match step {
                DbTransactionStep::Query(statement) => {
                    results.push(DbTransactionStepResult::Rows(query_with_connection(
                        &tx, statement,
                    )?));
                }
                DbTransactionStep::Execute(statement) => {
                    results.push(DbTransactionStepResult::Executed(execute_with_connection(
                        &tx, statement,
                    )?));
                }
            }
        }

        tx.commit()
            .map_err(|err| DbError::Backend(format!("commit sqlite transaction: {}", err)))?;
        Ok(results)
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        let rows = self.query(DbStatement::new("SELECT 1 AS ok")).await?;
        if rows.len() == 1 {
            Ok(DbHealth::healthy())
        } else {
            Ok(DbHealth::unhealthy("sqlite health query returned no rows"))
        }
    }
}

fn execute_with_connection(
    connection: &Connection,
    statement: DbStatement,
) -> DbResult<DbExecuteResult> {
    let params = sqlite_params(statement.params())?;
    let affected_rows = connection
        .execute(statement.sql(), params_from_iter(params))
        .map_err(|err| DbError::Backend(format!("execute sqlite statement: {}", err)))?;
    Ok(DbExecuteResult {
        affected_rows: affected_rows as u64,
        // SQLite exposes the last insert id at connection scope, so UPDATE and
        // DELETE can report a stale value. Keep the local contract conservative.
        last_insert_id: None,
    })
}

fn query_with_connection(connection: &Connection, statement: DbStatement) -> DbResult<Vec<DbRow>> {
    let params = sqlite_params(statement.params())?;
    let mut prepared = connection
        .prepare(statement.sql())
        .map_err(|err| DbError::Backend(format!("prepare sqlite query: {}", err)))?;
    let column_names: Vec<String> = prepared
        .column_names()
        .iter()
        .map(|name| (*name).to_string())
        .collect();
    let mut rows = prepared
        .query(params_from_iter(params))
        .map_err(|err| DbError::Backend(format!("run sqlite query: {}", err)))?;
    let mut out = Vec::new();

    while let Some(row) = rows
        .next()
        .map_err(|err| DbError::Backend(format!("read sqlite row: {}", err)))?
    {
        let mut columns = BTreeMap::new();
        for (idx, name) in column_names.iter().enumerate() {
            let value = row
                .get_ref(idx)
                .map_err(|err| DbError::Conversion(format!("read sqlite column: {}", err)))?;
            columns.insert(name.clone(), db_value_from_sqlite(value));
        }
        out.push(DbRow::new(columns));
    }

    Ok(out)
}

fn sqlite_params(values: &[DbValue]) -> DbResult<Vec<SqliteValue>> {
    values.iter().map(sqlite_value).collect()
}

fn sqlite_value(value: &DbValue) -> DbResult<SqliteValue> {
    match value {
        DbValue::Null => Ok(SqliteValue::Null),
        DbValue::Bool(value) => Ok(SqliteValue::Integer(i64::from(*value))),
        DbValue::I64(value) => Ok(SqliteValue::Integer(*value)),
        DbValue::U64(value) => i64::try_from(*value)
            .map(SqliteValue::Integer)
            .map_err(|_| {
                DbError::InvalidInput(format!("u64 value too large for sqlite: {}", value))
            }),
        DbValue::F64(value) => Ok(SqliteValue::Real(*value)),
        DbValue::String(value) => Ok(SqliteValue::Text(value.clone())),
        DbValue::Bytes(value) => Ok(SqliteValue::Blob(value.clone())),
    }
}

fn db_value_from_sqlite(value: ValueRef<'_>) -> DbValue {
    match value {
        ValueRef::Null => DbValue::Null,
        ValueRef::Integer(value) => DbValue::I64(value),
        ValueRef::Real(value) => DbValue::F64(value),
        ValueRef::Text(value) => DbValue::String(String::from_utf8_lossy(value).to_string()),
        ValueRef::Blob(value) => DbValue::Bytes(value.to_vec()),
    }
}

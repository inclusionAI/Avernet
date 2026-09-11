//! Local database plugin implementations for the `bcs-db-api` contract.
//!
//! This crate contains dependency-light implementations for local development
//! and contract tests. Internal SDK backed implementations live in separate
//! crates so they can be excluded from open-source distributions.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use rusqlite::types::{Value as SqliteValue, ValueRef};
use rusqlite::{Connection, params_from_iter};

fn profile_start() -> Option<std::time::Instant> {
    tracing::enabled!(target: "bcs_reply_profile", tracing::Level::DEBUG).then(std::time::Instant::now)
}
fn profile_elapsed(stage: &'static str, start: Option<std::time::Instant>) {
    if let Some(start) = start {
        tracing::debug!(target: "bcs_reply_profile", stage, elapsed_us = start.elapsed().as_micros() as u64, "sqlite stage elapsed");
    }
}

// Diagnostic classification of repository SQL templates, never raw SQL/params.
struct SqlTimer(&'static str, Option<std::time::Instant>);
impl SqlTimer {
    fn new(sql: &str, write: bool) -> Self {
        let start = profile_start();
        if start.is_none() { return Self("sql.disabled", None); }
        let stage = if write {
            if sql.contains("bcs_message_deliveries") { "sql.delivery_write" }
            else if sql.contains("bcs_messages") { "sql.message_write" }
            else if sql.contains("bcs_group_sessions") { "sql.session_write" }
            else if sql.contains("bcs_event") { "sql.event_write" }
            else { "sql.other_write" }
        } else if sql.contains("bcs_message_deliveries") {
            if sql.contains("COUNT(") { "sql.delivery_count" }
            else if sql.contains("pending_context") { "sql.pending_context_read" }
            else { "sql.delivery_read" }
        } else if sql.contains("bcs_messages") { "sql.message_read" }
        else if sql.contains("bcs_group_sessions") { "sql.session_read" }
        else if sql.contains("bcs_event") { "sql.event_read" }
        else { "sql.other_read" };
        Self(stage, start)
    }
}
impl Drop for SqlTimer {
    fn drop(&mut self) { profile_elapsed(self.0, self.1); }
}

/// Local SQLite implementation of [`DbPlugin`].
///
/// This is intentionally small but executes real SQL, making it useful for
/// contract tests and local experiments. Statements must be compatible with
/// SQLite. File databases use five connections; in-memory databases retain one
/// isolated connection. Driver I/O runs on Tokio's blocking pool, never an async
/// executor thread. SQLite still permits only one writer at a time.
#[derive(Clone)]
pub struct LocalSqliteDbPlugin {
    pool: Arc<ConnectionPool>,
}

struct ConnectionPool {
    available: Mutex<Vec<Connection>>,
    permits: Arc<tokio::sync::Semaphore>,
}

// The blocking operation owns this lease, even if its async caller is cancelled.
// Return the connection before releasing the permit (including on unwind).
struct ConnectionLease {
    connection: Option<Connection>,
    pool: Arc<ConnectionPool>,
    _permit: tokio::sync::OwnedSemaphorePermit,
}

impl Drop for ConnectionLease {
    fn drop(&mut self) {
        if let Some(connection) = self.connection.take() {
            self.pool.available.lock().unwrap_or_else(|e| e.into_inner()).push(connection);
        }
    }
}

#[cfg(test)]
mod pool_tests {
    use super::*;

    #[tokio::test]
    async fn five_connections_share_file_and_pragmas() -> DbResult<()> {
        let dir = tempfile::tempdir().unwrap();
        let db = LocalSqliteDbPlugin::new_file(dir.path().join("pool.db"))?;
        {
            let connections = db.pool.available.lock().unwrap();
            assert_eq!(connections.len(), 5);
            for c in connections.iter() {
                assert_eq!(c.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
                assert_eq!(c.query_row("PRAGMA busy_timeout", [], |r| r.get::<_, i64>(0)).unwrap(), 5000);
                assert_eq!(c.query_row("PRAGMA journal_mode", [], |r| r.get::<_, String>(0)).unwrap(), "wal");
            }
        }
        bcs_test_support::db_plugin_contract_tests(&db).await;
        db.execute(DbStatement::new("CREATE TABLE counter (n INTEGER NOT NULL)")).await?;
        db.execute(DbStatement::new("INSERT INTO counter VALUES (0)")).await?;
        let mut tasks = tokio::task::JoinSet::new();
        for _ in 0..40 {
            let db = db.clone();
            tasks.spawn(async move {
                db.transaction(vec![
                    DbTransactionStep::Query(DbStatement::new("SELECT n FROM counter")),
                    DbTransactionStep::Execute(DbStatement::new("UPDATE counter SET n = n + 1")),
                ]).await
            });
        }
        while let Some(result) = tasks.join_next().await { result.unwrap()?; }
        for c in db.pool.available.lock().unwrap().iter() {
            assert_eq!(c.query_row("SELECT n FROM counter", [], |r| r.get::<_, i64>(0)).unwrap(), 40);
        }
        Ok(())
    }

    #[tokio::test]
    async fn cancellation_keeps_connection_until_blocking_work_finishes() {
        let db = LocalSqliteDbPlugin::new().unwrap();
        let (entered_tx, entered_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let task_db = db.clone();
        let task = tokio::spawn(async move {
            task_db.with_connection(move |_| {
                entered_tx.send(()).unwrap();
                release_rx.recv().unwrap();
                Ok(())
            }).await
        });
        entered_rx.await.unwrap();
        task.abort();
        let _ = task.await;
        assert_eq!(db.pool.permits.available_permits(), 0);
        assert!(db.pool.available.lock().unwrap().is_empty());
        release_tx.send(()).unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(5), db.health_check()).await.unwrap().unwrap();
        assert_eq!(db.pool.available.lock().unwrap().len(), 1);
    }
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
            pool: Arc::new(ConnectionPool {
                available: Mutex::new(vec![connection]),
                permits: Arc::new(tokio::sync::Semaphore::new(1)),
            }),
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
        let mut connections = Vec::with_capacity(5);
        for _ in 0..5 {
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
            connections.push(connection);
        }

        Ok(Self {
            pool: Arc::new(ConnectionPool {
                available: Mutex::new(connections),
                permits: Arc::new(tokio::sync::Semaphore::new(5)),
            }),
        })
    }

    async fn with_connection<T: Send + 'static>(
        &self,
        operation: impl FnOnce(&mut Connection) -> DbResult<T> + Send + 'static,
    ) -> DbResult<T> {
        let started = profile_start();
        let permit = self.pool.permits.clone().acquire_owned().await
            .map_err(|err| DbError::Backend(format!("sqlite pool closed: {err}")))?;
        let connection = self.pool.available.lock()
            .map_err(|_| DbError::Backend("sqlite pool poisoned".into()))?
            .pop().ok_or_else(|| DbError::Backend("sqlite pool permit without connection".into()))?;
        let mut lease = ConnectionLease {
            connection: Some(connection), pool: self.pool.clone(), _permit: permit,
        };
        profile_elapsed("sqlite.lock_wait", started);
        let span = tracing::Span::current();
        let dispatcher = tracing::dispatcher::get_default(Clone::clone);
        tokio::task::spawn_blocking(move || {
            tracing::dispatcher::with_default(&dispatcher, || span.in_scope(|| {
                operation(lease.connection.as_mut().expect("lease owns connection"))
            }))
        }).await.map_err(|err| DbError::Backend(format!("sqlite blocking operation failed: {err}")))?
    }
}

#[async_trait]
impl DbPlugin for LocalSqliteDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.with_connection(move |connection| {
            let started = profile_start();
            let result = query_with_connection(connection, statement);
            profile_elapsed("sqlite.query_exec", started);
            result
        }).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.with_connection(move |connection| {
            let started = profile_start();
            let result = execute_with_connection(connection, statement);
            profile_elapsed("sqlite.execute_exec", started);
            result
        }).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.with_connection(move |connection| {
        let transaction_started = profile_start();
        // Reserve the write lock before any reads to avoid DEFERRED transaction
        // snapshot-upgrade failures when another pooled connection commits.
        let behavior = if steps.iter().all(|step| matches!(step, DbTransactionStep::Query(_))) {
            rusqlite::TransactionBehavior::Deferred
        } else {
            rusqlite::TransactionBehavior::Immediate
        };
        let tx = connection
            .transaction_with_behavior(behavior)
            .map_err(|err| DbError::Backend(format!("begin sqlite transaction: {}", err)))?;
        profile_elapsed("sqlite.begin", transaction_started);
        let transaction_started = profile_start();
        let mut results = Vec::with_capacity(steps.len());

        for (step_index, step) in steps.into_iter().enumerate() {
            match step {
                DbTransactionStep::Query(statement) => {
                    let params = statement.resolve_transaction_params(&results, step_index)?;
                    results.push(DbTransactionStepResult::Rows(query_with_connection_params(
                        &tx,
                        statement.sql(),
                        &params,
                    )?));
                }
                DbTransactionStep::Execute(statement) => {
                    let params = statement.resolve_transaction_params(&results, step_index)?;
                    results.push(DbTransactionStepResult::Executed(
                        execute_with_connection_params(&tx, statement.sql(), &params)?,
                    ));
                }
                DbTransactionStep::ExecuteChecked { statement, expected_affected_rows } => {
                    let params = statement.resolve_transaction_params(&results, step_index)?;
                    let result = execute_with_connection_params(&tx, statement.sql(), &params)?;
                    if result.affected_rows != expected_affected_rows {
                        return Err(DbError::ConditionFailed {
                            expected: expected_affected_rows, actual: result.affected_rows,
                        });
                    }
                    results.push(DbTransactionStepResult::Executed(result));
                }
            }
        }

        profile_elapsed("sqlite.transaction_steps", transaction_started);
        let commit_started = profile_start();
        tx.commit()
            .map_err(|err| DbError::Backend(format!("commit sqlite transaction: {}", err)))?;
        profile_elapsed("sqlite.commit", commit_started);
        Ok(results)
        }).await
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
    let params = statement.standalone_params()?;
    execute_with_connection_params(connection, statement.sql(), params)
}

fn execute_with_connection_params(
    connection: &Connection,
    sql: &str,
    values: &[DbValue],
) -> DbResult<DbExecuteResult> {
    let _timing = SqlTimer::new(sql, true);
    let params = sqlite_params(values)?;
    let affected_rows = connection
        .execute(sql, params_from_iter(params))
        .map_err(|err| DbError::Backend(format!("execute sqlite statement: {}", err)))?;
    Ok(DbExecuteResult {
        affected_rows: affected_rows as u64,
        // SQLite exposes the last insert id at connection scope, so UPDATE and
        // DELETE can report a stale value. Keep the local contract conservative.
        last_insert_id: None,
    })
}

fn query_with_connection(connection: &Connection, statement: DbStatement) -> DbResult<Vec<DbRow>> {
    let params = statement.standalone_params()?;
    query_with_connection_params(connection, statement.sql(), params)
}

fn query_with_connection_params(
    connection: &Connection,
    sql: &str,
    values: &[DbValue],
) -> DbResult<Vec<DbRow>> {
    let _timing = SqlTimer::new(sql, false);
    let params = sqlite_params(values)?;
    let mut prepared = connection
        .prepare(sql)
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

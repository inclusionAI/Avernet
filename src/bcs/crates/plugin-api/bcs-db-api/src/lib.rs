//! BCS database plugin contract.
//!
//! This crate defines the infrastructure-facing database extension point used
//! by BCS services. Version 1 is intentionally a SQL-compatible plugin API:
//! it abstracts driver, connection, transaction, health, and row conversion
//! concerns, but it does not promise cross-database query portability. Table
//! names, SQL text, row-to-domain mapping, and persistence semantics remain
//! owned by services.
//!
//! This is a deliberate tradeoff for the current BCS migration because the
//! existing storage code uses MySQL/ZDAS-specific tables, joins, and upserts.
//! A non-SQL backend or query-id based persistence port should be introduced
//! above this driver-level API, for example as service repositories, a query
//! builder, or an ORM-style layer. Dialect portability should live there, not
//! as decorative metadata on a raw SQL statement.
//!
//! Services that want the same store implementation to run on both local SQLite
//! and MySQL/ZDAS must choose SQL supported by both targets. The shared
//! `db_plugin_contract_tests` in `bcs-test-support` show the small common subset
//! this contract itself relies on: positional `?` parameters, basic
//! `CREATE TABLE`, `INSERT`, `DELETE`, and `SELECT` statements. Backend-specific
//! UPSERTs, joins, and DDL belong in service-owned stores or repository code.

use std::collections::BTreeMap;
use async_trait::async_trait;
use thiserror::Error;

/// Result type for database plugin operations.
pub type DbResult<T> = Result<T, DbError>;

/// Database plugin failures.
#[derive(Debug, Error)]
pub enum DbError {
    /// The caller provided invalid SQL, parameters, or transaction steps.
    #[error("invalid database input: {0}")]
    InvalidInput(String),

    /// The operation is valid for the contract but unsupported by this backend.
    #[error("unsupported database operation: {0}")]
    Unsupported(String),

    /// A returned column could not be converted to the requested type.
    #[error("database value conversion failed: {0}")]
    Conversion(String),

    /// Backend-specific failure.
    #[error("database backend error: {0}")]
    Backend(String),
}

impl DbError {
    /// Returns true if this error represents a unique constraint violation.
    /// Checks MySQL error code 1062 and SQLite "UNIQUE constraint failed".
    pub fn is_duplicate_key(&self) -> bool {
        match self {
            Self::Backend(msg) => {
                msg.contains("1062") || msg.contains("UNIQUE constraint failed")
            }
            _ => false,
        }
    }
}

/// Database scalar value used for SQL parameters and row columns.
#[derive(Debug, Clone, PartialEq)]
pub enum DbValue {
    Null,
    Bool(bool),
    I64(i64),
    U64(u64),
    F64(f64),
    String(String),
    Bytes(Vec<u8>),
}

/// SQL syntax flavor selected by service-owned stores.
///
/// This is not a backend capability negotiation mechanism. `DbPlugin` still
/// receives raw SQL as-is; services use this enum only to choose their own SQL
/// branch when they intentionally support both MySQL/ZDAS and local SQLite.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DbSqlFlavor {
    Mysql,
    Sqlite,
}

impl DbSqlFlavor {
    /// "NOW()" or "CURRENT_TIMESTAMP"
    pub fn now(&self) -> &'static str {
        match self {
            Self::Mysql => "NOW()",
            Self::Sqlite => "CURRENT_TIMESTAMP",
        }
    }

    /// "UNIX_TIMESTAMP({col})" or "CAST(strftime('%s',{col}) AS INTEGER)"
    pub fn unix_ts(&self, col: &str) -> String {
        match self {
            Self::Mysql => format!("UNIX_TIMESTAMP({})", col),
            Self::Sqlite => format!("CAST(strftime('%s',{}) AS INTEGER)", col),
        }
    }

    /// "FROM_UNIXTIME(?)" or "datetime(?,'unixepoch')"
    pub fn from_unix_param(&self) -> &'static str {
        match self {
            Self::Mysql => "FROM_UNIXTIME(?)",
            Self::Sqlite => "datetime(?,'unixepoch')",
        }
    }

    /// "INSERT IGNORE" or "INSERT OR IGNORE"
    pub fn insert_or_ignore(&self) -> &'static str {
        match self {
            Self::Mysql => "INSERT IGNORE",
            Self::Sqlite => "INSERT OR IGNORE",
        }
    }

    /// MySQL: "ON DUPLICATE KEY UPDATE col=VALUES(col), extra=val, ..."
    /// SQLite: "ON CONFLICT(keys) DO UPDATE SET col=excluded.col, extra=val, ..."
    pub fn on_conflict_update(
        &self,
        conflict_keys: &[&str],
        update_cols: &[&str],
        extras: &[(&str, &str)],
    ) -> String {
        match self {
            Self::Mysql => {
                let mut parts: Vec<String> = update_cols
                    .iter()
                    .map(|col| format!("{}=VALUES({})", col, col))
                    .collect();
                for (col, val) in extras {
                    parts.push(format!("{}={}", col, val));
                }
                format!("ON DUPLICATE KEY UPDATE {}", parts.join(", "))
            }
            Self::Sqlite => {
                let mut parts: Vec<String> = update_cols
                    .iter()
                    .map(|col| format!("{}=excluded.{}", col, col))
                    .collect();
                for (col, val) in extras {
                    parts.push(format!("{}={}", col, val));
                }
                format!(
                    "ON CONFLICT({}) DO UPDATE SET {}",
                    conflict_keys.join(", "),
                    parts.join(", ")
                )
            }
        }
    }

    /// MySQL: "ON DUPLICATE KEY UPDATE <first_key>=<first_key>" (no-op)
    /// SQLite: "ON CONFLICT(keys) DO NOTHING"
    pub fn on_conflict_nothing(&self, conflict_keys: &[&str]) -> String {
        match self {
            Self::Mysql => {
                let col = conflict_keys.first().copied().unwrap_or("id");
                format!("ON DUPLICATE KEY UPDATE {}={}", col, col)
            }
            Self::Sqlite => format!("ON CONFLICT({}) DO NOTHING", conflict_keys.join(", ")),
        }
    }

    /// MySQL: "IF(cond, t, f)"  SQLite: "IIF(cond, t, f)"
    pub fn iif(&self, cond: &str, t: &str, f: &str) -> String {
        match self {
            Self::Mysql => format!("IF({}, {}, {})", cond, t, f),
            Self::Sqlite => format!("IIF({}, {}, {})", cond, t, f),
        }
    }

    /// "gmt_modified = NOW()" or "gmt_modified = CURRENT_TIMESTAMP"
    pub fn set_modified_now(&self) -> &'static str {
        match self {
            Self::Mysql => "gmt_modified = NOW()",
            Self::Sqlite => "gmt_modified = CURRENT_TIMESTAMP",
        }
    }
}

impl DbValue {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::String(value) => Some(value),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Self::I64(value) => Some(*value),
            Self::U64(value) => i64::try_from(*value).ok(),
            _ => None,
        }
    }

    pub fn as_u64(&self) -> Option<u64> {
        match self {
            Self::U64(value) => Some(*value),
            Self::I64(value) => u64::try_from(*value).ok(),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            // SQL backends commonly expose boolean columns as integer values,
            // e.g. MySQL TINYINT(1). Treat zero as false and non-zero as true.
            Self::I64(value) => Some(*value != 0),
            Self::U64(value) => Some(*value != 0),
            _ => None,
        }
    }
}

impl From<&str> for DbValue {
    fn from(value: &str) -> Self {
        Self::String(value.to_string())
    }
}

impl From<String> for DbValue {
    fn from(value: String) -> Self {
        Self::String(value)
    }
}

impl From<bool> for DbValue {
    fn from(value: bool) -> Self {
        Self::Bool(value)
    }
}

impl From<i64> for DbValue {
    fn from(value: i64) -> Self {
        Self::I64(value)
    }
}

impl From<i32> for DbValue {
    fn from(value: i32) -> Self {
        Self::I64(i64::from(value))
    }
}

impl From<u64> for DbValue {
    fn from(value: u64) -> Self {
        Self::U64(value)
    }
}

impl From<u32> for DbValue {
    fn from(value: u32) -> Self {
        Self::U64(u64::from(value))
    }
}

impl From<f64> for DbValue {
    fn from(value: f64) -> Self {
        Self::F64(value)
    }
}

impl From<Vec<u8>> for DbValue {
    fn from(value: Vec<u8>) -> Self {
        Self::Bytes(value)
    }
}

impl From<Option<&str>> for DbValue {
    fn from(value: Option<&str>) -> Self {
        value.map(Self::from).unwrap_or(Self::Null)
    }
}

impl From<Option<String>> for DbValue {
    fn from(value: Option<String>) -> Self {
        value.map(Self::from).unwrap_or(Self::Null)
    }
}

/// One database row keyed by column name.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct DbRow {
    columns: BTreeMap<String, DbValue>,
}

impl DbRow {
    pub fn new(columns: BTreeMap<String, DbValue>) -> Self {
        Self { columns }
    }

    pub fn empty() -> Self {
        Self::default()
    }

    pub fn columns(&self) -> &BTreeMap<String, DbValue> {
        &self.columns
    }

    pub fn get(&self, column: &str) -> Option<&DbValue> {
        self.columns.get(column)
    }

    pub fn get_string(&self, column: &str) -> DbResult<Option<String>> {
        self.get(column)
            .map(|value| match value {
                DbValue::Null => Ok(None),
                DbValue::String(value) => Ok(Some(value.clone())),
                other => Err(DbError::Conversion(format!(
                    "column '{}' is not a string: {:?}",
                    column, other
                ))),
            })
            .unwrap_or(Ok(None))
    }

    pub fn get_i64(&self, column: &str) -> DbResult<Option<i64>> {
        self.get(column)
            .map(|value| {
                if matches!(value, DbValue::Null) {
                    Ok(None)
                } else {
                    value.as_i64().map(Some).ok_or_else(|| {
                        DbError::Conversion(format!(
                            "column '{}' is not an i64: {:?}",
                            column, value
                        ))
                    })
                }
            })
            .unwrap_or(Ok(None))
    }

    pub fn get_bool(&self, column: &str) -> DbResult<Option<bool>> {
        self.get(column)
            .map(|value| {
                if matches!(value, DbValue::Null) {
                    Ok(None)
                } else {
                    value.as_bool().map(Some).ok_or_else(|| {
                        DbError::Conversion(format!(
                            "column '{}' is not a bool: {:?}",
                            column, value
                        ))
                    })
                }
            })
            .unwrap_or(Ok(None))
    }

    pub fn get_bytes(&self, column: &str) -> DbResult<Option<Vec<u8>>> {
        self.get(column)
            .map(|value| match value {
                DbValue::Null => Ok(None),
                DbValue::Bytes(value) => Ok(Some(value.clone())),
                other => Err(DbError::Conversion(format!(
                    "column '{}' is not bytes: {:?}",
                    column, other
                ))),
            })
            .unwrap_or(Ok(None))
    }
}

/// SQL statement plus positional parameters.
#[derive(Debug, Clone, PartialEq)]
pub struct DbStatement {
    sql: String,
    params: Vec<DbValue>,
}

impl DbStatement {
    /// Create a SQL statement without positional parameters.
    ///
    /// The SQL text is passed to the selected backend as-is. Callers are
    /// responsible for using syntax supported by that backend. If the same
    /// caller must run against both local SQLite and MySQL/ZDAS, keep the SQL
    /// to the documented common subset or isolate dialect-specific SQL in a
    /// service-owned store/repository.
    pub fn new(sql: impl Into<String>) -> Self {
        Self {
            sql: sql.into(),
            params: Vec::new(),
        }
    }

    /// Create a SQL statement with positional parameters.
    ///
    /// The SQL text is passed to the selected backend as-is. Callers are
    /// responsible for using syntax supported by that backend. If the same
    /// caller must run against both local SQLite and MySQL/ZDAS, keep the SQL
    /// to the documented common subset or isolate dialect-specific SQL in a
    /// service-owned store/repository.
    pub fn with_params(sql: impl Into<String>, params: Vec<DbValue>) -> Self {
        Self {
            sql: sql.into(),
            params,
        }
    }

    pub fn sql(&self) -> &str {
        &self.sql
    }

    pub fn params(&self) -> &[DbValue] {
        &self.params
    }

    pub fn into_params(self) -> Vec<DbValue> {
        self.params
    }
}

/// Result of an INSERT/UPDATE/DELETE statement.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct DbExecuteResult {
    /// Number of rows affected using the backend's native semantics.
    ///
    /// MySQL-compatible backends preserve `INSERT ... ON DUPLICATE KEY UPDATE`
    /// affected-row behavior (`1` inserted, `2` updated, `0` no change unless
    /// the connection is configured with found-rows semantics).
    pub affected_rows: u64,
    /// Last auto-increment id when the backend can report it for this
    /// statement. Backends that only expose connection-scoped stale values
    /// should return `None`.
    pub last_insert_id: Option<u64>,
}

/// A single transaction step.
#[derive(Debug, Clone, PartialEq)]
pub enum DbTransactionStep {
    /// Query within a transaction. Returning zero rows is still success.
    ///
    /// This supports read-before-write and `SELECT ... FOR UPDATE` style flows.
    /// Callers must validate cardinality themselves when "no rows" is a
    /// business failure.
    Query(DbStatement),
    Execute(DbStatement),
}

/// Result for a single transaction step.
#[derive(Debug, Clone, PartialEq)]
pub enum DbTransactionStepResult {
    Rows(Vec<DbRow>),
    Executed(DbExecuteResult),
}

/// Database health status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DbHealth {
    pub healthy: bool,
    pub message: Option<String>,
}

impl DbHealth {
    pub fn healthy() -> Self {
        Self {
            healthy: true,
            message: None,
        }
    }

    pub fn unhealthy(message: impl Into<String>) -> Self {
        Self {
            healthy: false,
            message: Some(message.into()),
        }
    }
}

/// Database plugin contract.
///
/// A `DbPlugin` instance is expected to be bound to one configured logical
/// datasource by the composition root. Services should not know concrete
/// backend names such as ZDAS datasource IDs.
#[async_trait]
pub trait DbPlugin: Send + Sync + 'static {
    /// Run a SELECT-like statement and return named rows.
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>>;

    /// Run an INSERT/UPDATE/DELETE-like statement.
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult>;

    /// Run execute statements in order without transaction semantics.
    ///
    /// Implementations must stop at the first failed statement and return that
    /// error. Statements already executed are not rolled back; callers needing
    /// all-or-nothing behavior must use [`Self::transaction`].
    async fn execute_batch(&self, statements: Vec<DbStatement>) -> DbResult<Vec<DbExecuteResult>> {
        let mut results = Vec::with_capacity(statements.len());
        for statement in statements {
            results.push(self.execute(statement).await?);
        }
        Ok(results)
    }

    /// Run steps inside one backend transaction.
    ///
    /// Implementations must commit only when every step succeeds and must roll
    /// back when any step fails. A query returning zero rows is a successful
    /// step; it is the caller's job to interpret empty result sets.
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>>;

    /// Return whether this plugin can reach its backend.
    async fn health_check(&self) -> DbResult<DbHealth>;
}

pub fn db_get_column<T: FromDbColumn>(row: &DbRow, column: &str) -> DbResult<T> {
    db_get_column_opt(row, column)?
        .ok_or_else(|| DbError::Conversion(format!("column '{}' is missing or NULL", column)))
}

pub fn db_get_column_opt<T: FromDbColumn>(row: &DbRow, column: &str) -> DbResult<Option<T>> {
    row.get(column)
        .map(|value| {
            if matches!(value, DbValue::Null) {
                Ok(None)
            } else {
                T::from_db_value(column, value).map(Some)
            }
        })
        .unwrap_or(Ok(None))
}

pub trait FromDbColumn: Sized {
    fn from_db_value(column: &str, value: &DbValue) -> DbResult<Self>;
}

impl FromDbColumn for String {
    fn from_db_value(column: &str, value: &DbValue) -> DbResult<Self> {
        match value {
            DbValue::String(value) => Ok(value.clone()),
            DbValue::Bytes(value) => String::from_utf8(value.clone()).map_err(|err| {
                DbError::Conversion(format!("column '{}' is not valid UTF-8: {}", column, err))
            }),
            other => Err(DbError::Conversion(format!(
                "column '{}' is not a string: {:?}",
                column, other
            ))),
        }
    }
}

impl FromDbColumn for i64 {
    fn from_db_value(column: &str, value: &DbValue) -> DbResult<Self> {
        value.as_i64().ok_or_else(|| {
            DbError::Conversion(format!("column '{}' is not an i64: {:?}", column, value))
        })
    }
}

impl FromDbColumn for i32 {
    fn from_db_value(column: &str, value: &DbValue) -> DbResult<Self> {
        let value = value.as_i64().ok_or_else(|| {
            DbError::Conversion(format!("column '{}' is not an i32: {:?}", column, value))
        })?;
        i32::try_from(value).map_err(|err| {
            DbError::Conversion(format!("column '{}' is out of i32 range: {}", column, err))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn must<T>(result: DbResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected Ok, got {}", err),
        }
    }

    #[test]
    fn db_plugin_is_object_safe() {
        fn _assert<T: DbPlugin>() {}
        fn _assert_dyn(_: Arc<dyn DbPlugin>) {}
    }

    #[test]
    fn db_row_typed_getters_are_predictable() {
        let row = DbRow::new(BTreeMap::from([
            ("name".to_string(), DbValue::from("alice")),
            ("count".to_string(), DbValue::from(3_i64)),
            ("big".to_string(), DbValue::from(i64::from(i32::MAX) + 1)),
            ("enabled".to_string(), DbValue::from(true)),
            ("payload".to_string(), DbValue::from(b"blob".to_vec())),
        ]));

        assert_eq!(must(row.get_string("name")), Some("alice".to_string()));
        assert_eq!(must(row.get_i64("count")), Some(3));
        assert_eq!(must(row.get_bool("enabled")), Some(true));
        assert_eq!(must(row.get_bytes("payload")), Some(b"blob".to_vec()));
        assert_eq!(must(row.get_string("missing")), None);
        assert_eq!(must(db_get_column::<String>(&row, "name")), "alice");
        assert_eq!(must(db_get_column_opt::<i64>(&row, "count")), Some(3));
        assert_eq!(must(db_get_column::<i32>(&row, "count")), 3);
        assert!(matches!(
            db_get_column::<i32>(&row, "big"),
            Err(DbError::Conversion(_))
        ));
    }

    #[test]
    fn db_sql_flavor_now() {
        assert_eq!(DbSqlFlavor::Mysql.now(), "NOW()");
        assert_eq!(DbSqlFlavor::Sqlite.now(), "CURRENT_TIMESTAMP");
    }

    #[test]
    fn db_sql_flavor_insert_or_ignore() {
        assert_eq!(DbSqlFlavor::Mysql.insert_or_ignore(), "INSERT IGNORE");
        assert_eq!(DbSqlFlavor::Sqlite.insert_or_ignore(), "INSERT OR IGNORE");
    }

    #[test]
    fn db_sql_flavor_on_conflict_update_mysql() {
        let sql = DbSqlFlavor::Mysql.on_conflict_update(
            &["group_id", "env"],
            &["status", "driver_bot"],
            &[("gmt_modified", "NOW()")],
        );
        assert_eq!(
            sql,
            "ON DUPLICATE KEY UPDATE status=VALUES(status), driver_bot=VALUES(driver_bot), gmt_modified=NOW()"
        );
    }

    #[test]
    fn db_sql_flavor_on_conflict_update_sqlite() {
        let sql = DbSqlFlavor::Sqlite.on_conflict_update(
            &["group_id", "env"],
            &["status", "driver_bot"],
            &[("gmt_modified", "CURRENT_TIMESTAMP")],
        );
        assert_eq!(
            sql,
            "ON CONFLICT(group_id, env) DO UPDATE SET status=excluded.status, driver_bot=excluded.driver_bot, gmt_modified=CURRENT_TIMESTAMP"
        );
    }

    #[test]
    fn db_sql_flavor_on_conflict_nothing() {
        assert_eq!(
            DbSqlFlavor::Mysql.on_conflict_nothing(&["group_id", "env"]),
            "ON DUPLICATE KEY UPDATE group_id=group_id"
        );
        assert_eq!(
            DbSqlFlavor::Sqlite.on_conflict_nothing(&["group_id", "env"]),
            "ON CONFLICT(group_id, env) DO NOTHING"
        );
    }

    #[test]
    fn db_sql_flavor_iif() {
        assert_eq!(DbSqlFlavor::Mysql.iif("a", "b", "c"), "IF(a, b, c)");
        assert_eq!(DbSqlFlavor::Sqlite.iif("a", "b", "c"), "IIF(a, b, c)");
    }

    #[test]
    fn db_sql_flavor_unix_ts() {
        assert_eq!(DbSqlFlavor::Mysql.unix_ts("gmt_create"), "UNIX_TIMESTAMP(gmt_create)");
        assert_eq!(
            DbSqlFlavor::Sqlite.unix_ts("gmt_create"),
            "CAST(strftime('%s',gmt_create) AS INTEGER)"
        );
    }

    #[test]
    fn db_sql_flavor_from_unix_param() {
        assert_eq!(DbSqlFlavor::Mysql.from_unix_param(), "FROM_UNIXTIME(?)");
        assert_eq!(DbSqlFlavor::Sqlite.from_unix_param(), "datetime(?,'unixepoch')");
    }

    #[test]
    fn db_sql_flavor_set_modified_now() {
        assert_eq!(DbSqlFlavor::Mysql.set_modified_now(), "gmt_modified = NOW()");
        assert_eq!(DbSqlFlavor::Sqlite.set_modified_now(), "gmt_modified = CURRENT_TIMESTAMP");
    }

    #[test]
    fn db_error_is_duplicate_key_mysql() {
        let err = DbError::Backend("Error 1062: Duplicate entry".to_string());
        assert!(err.is_duplicate_key());
    }

    #[test]
    fn db_error_is_duplicate_key_sqlite() {
        let err = DbError::Backend("UNIQUE constraint failed: bcs_bots.uk_bot_env".to_string());
        assert!(err.is_duplicate_key());
    }

    #[test]
    fn db_error_is_duplicate_key_false() {
        let err = DbError::Backend("connection refused".to_string());
        assert!(!err.is_duplicate_key());
    }
}

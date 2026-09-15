//! SQL statements and transaction execution controls.

use crate::{DbError, DbResult, DbRow, DbValue};

/// A positional parameter used only inside [`crate::DbPlugin::transaction`].
///
/// Keeping result references separate from [`DbValue`] prevents unresolved
/// transaction state from leaking into normal query and execute calls.
#[derive(Debug, Clone, PartialEq)]
pub enum DbTransactionParam {
    Value(DbValue),
    QueryResult {
        step_index: usize,
        row_index: usize,
        column: String,
    },
}

impl DbTransactionParam {
    pub fn value(value: impl Into<DbValue>) -> Self {
        Self::Value(value.into())
    }

    /// Reference one column from a query step that precedes the current step.
    pub fn query_result(step_index: usize, row_index: usize, column: impl Into<String>) -> Self {
        Self::QueryResult {
            step_index,
            row_index,
            column: column.into(),
        }
    }
}

impl From<DbValue> for DbTransactionParam {
    fn from(value: DbValue) -> Self {
        Self::Value(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DbTransactionBinding {
    parameter_index: usize,
    step_index: usize,
    row_index: usize,
    column: String,
}

/// SQL statement plus positional parameters.
#[derive(Debug, Clone, PartialEq)]
pub struct DbStatement {
    sql: String,
    params: Vec<DbValue>,
    transaction_bindings: Vec<DbTransactionBinding>,
    stop_transaction_on_no_rows: bool,
}

impl DbStatement {
    /// Create a SQL statement without positional parameters.
    ///
    /// The SQL text is passed to the selected backend as-is. Callers are
    /// responsible for using syntax supported by that backend. If the same
    /// caller must run against both local SQLite and MySQL-compatible backends, keep the SQL
    /// to the documented common subset or isolate dialect-specific SQL in a
    /// service-owned store/repository.
    pub fn new(sql: impl Into<String>) -> Self {
        Self {
            sql: sql.into(),
            params: Vec::new(),
            transaction_bindings: Vec::new(),
            stop_transaction_on_no_rows: false,
        }
    }

    /// Create a SQL statement with positional parameters.
    ///
    /// The SQL text is passed to the selected backend as-is. Callers are
    /// responsible for using syntax supported by that backend. If the same
    /// caller must run against both local SQLite and MySQL-compatible backends, keep the SQL
    /// to the documented common subset or isolate dialect-specific SQL in a
    /// service-owned store/repository.
    pub fn with_params(sql: impl Into<String>, params: Vec<DbValue>) -> Self {
        Self {
            sql: sql.into(),
            params,
            transaction_bindings: Vec::new(),
            stop_transaction_on_no_rows: false,
        }
    }

    /// Create a statement whose positional parameters may reference earlier
    /// query results in the same transaction.
    ///
    /// Passing this statement to [`crate::DbPlugin::query`] or [`crate::DbPlugin::execute`]
    /// is invalid. Query-result references must point to a strictly earlier
    /// query step; missing steps, rows, or columns fail the whole transaction.
    pub fn with_transaction_params(
        sql: impl Into<String>,
        params: Vec<DbTransactionParam>,
    ) -> Self {
        let mut values = Vec::with_capacity(params.len());
        let mut transaction_bindings = Vec::new();
        for (parameter_index, parameter) in params.into_iter().enumerate() {
            match parameter {
                DbTransactionParam::Value(value) => values.push(value),
                DbTransactionParam::QueryResult {
                    step_index,
                    row_index,
                    column,
                } => {
                    // Keep positional arity stable. This placeholder is never
                    // sent to a backend because standalone calls reject bound
                    // statements and transaction calls resolve it first.
                    values.push(DbValue::Null);
                    transaction_bindings.push(DbTransactionBinding {
                        parameter_index,
                        step_index,
                        row_index,
                        column,
                    });
                }
            }
        }
        Self {
            sql: sql.into(),
            params: values,
            transaction_bindings,
            stop_transaction_on_no_rows: false,
        }
    }

    /// End a transaction successfully when this Execute step affects zero rows.
    ///
    /// The plugin commits the executed prefix, including earlier writes, and
    /// returns only its results. Later SQL and result bindings are not executed
    /// or resolved. Any execution or commit failure remains an error.
    /// Only plain Execute steps support this option; standalone operations,
    /// Query and ExecuteChecked reject it. Callers must use changing writes
    /// so MySQL and SQLite agree on whether rows were affected.
    pub fn with_transaction_stop_on_no_rows(mut self) -> Self {
        self.stop_transaction_on_no_rows = true;
        self
    }

    pub fn stops_transaction_on_no_rows(&self) -> bool {
        self.stop_transaction_on_no_rows
    }

    pub fn sql(&self) -> &str {
        &self.sql
    }

    /// Return the statement's literal parameter storage.
    ///
    /// Transaction result references occupy `DbValue::Null` placeholders
    /// until [`Self::resolve_transaction_params`] is called. Plugin
    /// implementations must use [`Self::standalone_params`] for standalone
    /// execution and `resolve_transaction_params` inside a transaction.
    pub fn params(&self) -> &[DbValue] {
        &self.params
    }

    /// Return literal parameters for a standalone query or execute call.
    pub fn standalone_params(&self) -> DbResult<&[DbValue]> {
        if self.stop_transaction_on_no_rows {
            return Err(DbError::InvalidInput(
                "stop-on-no-rows requires a transaction Execute step".to_string(),
            ));
        }
        if self.transaction_bindings.is_empty() {
            Ok(&self.params)
        } else {
            Err(DbError::InvalidInput(
                "transaction result bindings require DbPlugin::transaction".to_string(),
            ))
        }
    }

    /// Resolve all transaction-only bindings against already completed steps.
    pub fn resolve_transaction_params(
        &self,
        completed_steps: &[DbTransactionStepResult],
        current_step_index: usize,
    ) -> DbResult<Vec<DbValue>> {
        let mut params = self.params.clone();
        for binding in &self.transaction_bindings {
            if binding.step_index >= current_step_index {
                return Err(DbError::InvalidInput(format!(
                    "transaction parameter {} references non-previous step {} from step {}",
                    binding.parameter_index, binding.step_index, current_step_index
                )));
            }
            let result = completed_steps.get(binding.step_index).ok_or_else(|| {
                DbError::InvalidInput(format!(
                    "transaction parameter {} references unavailable step {}",
                    binding.parameter_index, binding.step_index
                ))
            })?;
            let rows = match result {
                DbTransactionStepResult::Rows(rows) => rows,
                DbTransactionStepResult::Executed(_) => {
                    return Err(DbError::InvalidInput(format!(
                        "transaction parameter {} references execute step {} instead of a query",
                        binding.parameter_index, binding.step_index
                    )));
                }
            };
            let row = rows.get(binding.row_index).ok_or_else(|| {
                DbError::InvalidInput(format!(
                    "transaction parameter {} references missing row {} from step {}",
                    binding.parameter_index, binding.row_index, binding.step_index
                ))
            })?;
            let value = row.get(&binding.column).ok_or_else(|| {
                DbError::InvalidInput(format!(
                    "transaction parameter {} references missing column '{}' from step {} row {}",
                    binding.parameter_index, binding.column, binding.step_index, binding.row_index
                ))
            })?;
            params[binding.parameter_index] = value.clone();
        }
        Ok(params)
    }

    /// Consume the statement and return its literal parameter storage.
    ///
    /// Like [`Self::params`], unresolved transaction bindings are represented
    /// by `DbValue::Null` placeholders.
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
    /// Execute and require exactly this many affected rows before proceeding.
    /// Mismatch rolls back the entire transaction, including earlier writes.
    /// Callers must use genuinely changing CAS updates (e.g. increment version)
    /// to avoid backend differences for no-op UPDATEs.
    ExecuteChecked { statement: DbStatement, expected_affected_rows: u64 },
}

impl DbTransactionStep {
    /// Validate execution options before starting the transaction. SQL and
    /// parameter bindings are evaluated only when a step is reached.
    pub fn validate(&self) -> DbResult<()> {
        match self {
            Self::Query(statement) | Self::ExecuteChecked { statement, .. }
                if statement.stops_transaction_on_no_rows() =>
            {
                Err(DbError::InvalidInput(
                    "stop-on-no-rows requires a transaction Execute step".to_string(),
                ))
            }
            _ => Ok(()),
        }
    }
}

/// Result for a single transaction step.
#[derive(Debug, Clone, PartialEq)]
pub enum DbTransactionStepResult {
    Rows(Vec<DbRow>),
    Executed(DbExecuteResult),
}

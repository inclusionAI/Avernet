//! Recording/failing `DbPlugin` decorator used by the store unit tests.
//!
//! Moved verbatim out of `store_tests.rs` to keep both files within the
//! repository file-size limit. It only records what the store asked for.

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value,
};
use std::collections::VecDeque;
use std::sync::Mutex as StdMutex;

#[derive(Default)]
pub(super) struct RecordingDbPlugin {
    pub(super) transaction_sql: StdMutex<Vec<String>>,
    pub(super) transaction_statements: StdMutex<Vec<DbStatement>>,
    pub(super) execute_statements: StdMutex<Vec<DbStatement>>,
    pub(super) query_sqls: StdMutex<Vec<String>>,
    pub(super) query_params: StdMutex<Vec<Vec<Value>>>,
    pub(super) first_execute_affected_rows: u64,
    pub(super) fail_queries: bool,
    pub(super) query_rows: Vec<DbRow>,
    pub(super) query_results: StdMutex<VecDeque<Vec<DbRow>>>,
    pub(super) transaction_error: Option<String>,
    pub(super) missing_transaction_lock_row: bool,
}

impl RecordingDbPlugin {
    pub(super) fn with_first_execute_affected_rows(first_execute_affected_rows: u64) -> Self {
        Self {
            first_execute_affected_rows,
            ..Self::default()
        }
    }

    pub(super) fn failing_queries() -> Self {
        Self {
            fail_queries: true,
            ..Self::default()
        }
    }

    pub(super) fn with_query_rows(query_rows: Vec<DbRow>) -> Self {
        Self {
            query_rows,
            ..Self::default()
        }
    }

    pub(super) fn with_duplicate_transaction_error() -> Self {
        Self {
            transaction_error: Some(
                "UNIQUE constraint failed: bcs_groups.env, bcs_groups.dm_pair_key".into(),
            ),
            ..Self::default()
        }
    }

    pub(super) fn with_missing_transaction_lock_row(query_rows: Vec<DbRow>) -> Self {
        Self {
            query_rows,
            missing_transaction_lock_row: true,
            ..Self::default()
        }
    }
}

#[async_trait]
impl DbPlugin for RecordingDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.query_sqls
            .lock()
            .expect("query sqls")
            .push(statement.sql().to_string());
        self.query_params
            .lock()
            .expect("query params")
            .push(statement.params().to_vec());
        // Test-only invariant: these SQL builders contain no `?` inside
        // string literals, so every placeholder is a real bound parameter.
        debug_assert_eq!(
            statement.sql().matches('?').count(),
            statement.params().len()
        );
        if self.fail_queries {
            return Err(DbError::Backend("database unavailable".to_string()));
        }
        if let Some(rows) = self
            .query_results
            .lock()
            .expect("query results")
            .pop_front()
        {
            return Ok(rows);
        }
        Ok(self.query_rows.clone())
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.execute_statements
            .lock()
            .expect("execute statements")
            .push(statement);
        Ok(DbExecuteResult {
            affected_rows: self.first_execute_affected_rows,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.missing_transaction_lock_row {
            return Err(DbError::InvalidInput(
                "transaction parameter 2 references missing row 0 from step 0".to_string(),
            ));
        }
        if let Some(error) = &self.transaction_error {
            return Err(DbError::Backend(error.clone()));
        }
        let mut results = Vec::with_capacity(steps.len());
        let mut sql = self.transaction_sql.lock().expect("transaction sql");
        let mut execute_index = 0;

        for step in steps {
            match step {
                DbTransactionStep::Query(statement) => {
                    self.transaction_statements
                        .lock()
                        .expect("transaction statements")
                        .push(statement.clone());
                    sql.push(statement.sql().to_string());
                    results.push(DbTransactionStepResult::Rows(Vec::new()));
                }
                DbTransactionStep::Execute(statement)
                | DbTransactionStep::ExecuteChecked { statement, .. } => {
                    self.transaction_statements
                        .lock()
                        .expect("transaction statements")
                        .push(statement.clone());
                    sql.push(statement.sql().to_string());
                    let affected_rows = if execute_index == 0 {
                        self.first_execute_affected_rows
                    } else {
                        0
                    };
                    execute_index += 1;
                    results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                        affected_rows,
                        last_insert_id: None,
                    }));
                }
            }
        }

        Ok(results)
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}
